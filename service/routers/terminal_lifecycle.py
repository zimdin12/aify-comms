"""Ending a terminal: the requested stop, and the report that one already died.

Extracted from `service/routers/terminals.py` in v0.5.4. Closure measured before the move —
`api_core`, `reconcilers` and `service` leaves only, nothing local to that router.

TWO WAYS A TERMINAL ENDS, and they are not variants of each other. `stop_terminal` is a REQUEST: it
files a control and waits for a bridge to act on it, which is why it has to handle the case where no
bridge will ever claim it — an unclaimable stop reconciles itself rather than hanging as a pending
control forever. `report_terminal_dead` is a FACT arriving after the event, from whatever noticed,
and its job is to make every dependent record agree: close the active runs, clear a console binding
pointing at it, invalidate the owning agent's cached live status.

BOTH CONVERGE ON THE SAME INVARIANT, which is the argument for one file: after either, nothing may
still believe the terminal is usable. The standing rule in this repo is that cleanup which must hold
for ALL paths keys on the STATE rather than on the event — a spawn once sat "running" for 97 minutes
because one of ~26 terminal writers never called the death path. These two are the front door; the
reconcilers are the backstop.

Bodies and route decorators byte-identical.
"""

from __future__ import annotations

import json

from fastapi import HTTPException, Request

from service.api_core.events import _append_terminal_control, _append_terminal_event
from service.api_core.records import _terminal_session_to_dict
from service.api_core.routing import domain_router
from service.api_core.settings import _load_settings
from service.api_core.terminal_controls_io import (
    _clear_console_terminal_binding,
    _reconcile_stop_for_unclaimable_terminal,
)
from service.api_core.terminal_status import _TERMINAL_END_STATUSES
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db
from service.env_status import environment_effective_status as _environment_effective_status
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
from service.reconcilers.terminal_runs import _close_active_terminal_runs_for_terminal

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import TerminalControlRequest, TerminalDeadReport

router = domain_router()



@router.post("/terminals/{terminal_id}/stop")
async def stop_terminal(terminal_id: str, req: TerminalControlRequest, request: Request):
    db = await get_db()
    try:
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        now = _now()
        requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
        env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (terminal["environment_id"],))).fetchone()
        settings = await _load_settings(db)
        env_status = _environment_effective_status(
            env_row,
            offline_seconds=max(30, int(settings.get("environment_offline_seconds", 90) or 90)),
        ) if env_row else "offline"
        current_bridge_id = str((env_row["bridge_id"] if env_row else "") or "").strip()
        terminal_bridge_id = str(terminal["bridge_id"] or "").strip()
        terminal_status = str(terminal["status"] or "").strip().lower()
        bridge_can_claim = bool(
            terminal_bridge_id
            and current_bridge_id
            and terminal_bridge_id == current_bridge_id
            and env_status in {"online", "degraded"}
        )
        control_id = await _append_terminal_control(
            db,
            terminal_id=terminal_id,
            environment_id=terminal["environment_id"],
            bridge_id=terminal["bridge_id"] or "",
            action="stop",
            requested_by=requested_by,
            body=req.body or "",
        )
        await _append_terminal_event(
            db,
            terminal_id,
            "console_stop_requested",
            json.dumps({"requestedBy": requested_by, "body": req.body or "", "controlId": control_id}),
        )
        if terminal_status in {"stopped", "failed"} or not bridge_can_claim:
            return await _reconcile_stop_for_unclaimable_terminal(
                db, request, terminal, terminal_id, terminal_status, terminal_bridge_id,
                current_bridge_id, env_status, control_id, requested_by, now,
            )
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'stopping', updated_at = ?
            WHERE id = ?
            """,
            (now, terminal_id),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET terminal_status = 'stopping',
                last_seen = ?
            WHERE id = ?
            """,
            (now, terminal["session_id"]),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_stopped", {"terminalId": terminal_id, "sessionId": terminal["session_id"]})
        return {"ok": True, "terminal": _terminal_session_to_dict(updated)}
    finally:
        await db.close()



@router.post("/terminals/{terminal_id}/report-dead")
async def report_terminal_dead(terminal_id: str, req: TerminalDeadReport, request: Request):
    """Host-reported dead-PTY signal (WS4 Task 4.2).

    The server cannot probe a remote host's PID; only the OWNING environment
    bridge can. When a bridge observes that one of its `attached` console PTY
    rows has a `process_id` that is no longer alive locally, it POSTs here so the
    server can mark the row stopped, close any active runs, clear the console
    binding, and invalidate the agent's live state (a frozen/crashed console
    can otherwise keep manufacturing presence).

    SAFETY: if a `processId` is supplied it MUST match the stored process_id.
    A bridge that has since restarted the console owns a NEW pid; a stale
    report carrying the OLD pid must NOT stop the live row. Already-terminal
    rows are a harmless idempotent no-op.
    """
    db = await get_db()
    try:
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        now = _now()
        current_status = str(terminal["status"] or "").strip().lower()
        # Idempotent: already terminal → nothing to do.
        if current_status in _TERMINAL_END_STATUSES:
            return {"ok": True, "terminal": _terminal_session_to_dict(terminal), "changed": False}
        # PID guard: a supplied pid must match the stored process_id so a stale
        # report can't stop a row a restarted bridge now owns with a NEW pid.
        reported_pid = str(req.processId or "").strip()
        stored_pid = str(terminal["process_id"] or "").strip()
        if reported_pid and stored_pid and reported_pid != stored_pid:
            await _append_terminal_event(
                db,
                terminal_id,
                "console_dead_report_ignored",
                json.dumps({"reportedPid": reported_pid, "storedPid": stored_pid, "bridgeId": req.bridgeId or ""}),
            )
            await db.commit()
            return {"ok": True, "terminal": _terminal_session_to_dict(terminal), "changed": False, "ignored": "pid-mismatch"}
        reason = str(req.reason or "").strip() or "Console PTY process is no longer alive (host-reported)."
        # Close any active runs bound to this terminal before stopping the row.
        await _close_active_terminal_runs_for_terminal(
            db,
            terminal,
            "stopped",
            now=now,
            reason=reason,
        )
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'stopped',
                updated_at = ?,
                stopped_at = COALESCE(stopped_at, ?),
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ?
            """,
            (now, now, reason, terminal_id),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET owner_mode = 'managed',
                terminal_status = 'stopped',
                last_seen = ?
            WHERE id = ?
            """,
            (now, terminal["session_id"]),
        )
        await _clear_console_terminal_binding(db, terminal["agent_id"], terminal_id, now=now)
        # Bug D fix (2026-07-02): the dead PTY's in-session wrapper-child bridge died with it,
        # but its heartbeat row would otherwise look "live" for up to
        # ACTIVE_RUN_BRIDGE_STALE_SECONDS and suppress the send-path coldstart. Supersede the
        # rows now so the very next send cold-starts a fresh worker instead of queuing.
        # SCOPED to this terminal (review 2026-07-02): a stale dead-report for an OLD
        # terminal must not kill the NEW live worker's row. Rows with no terminal_id
        # (flag-only wrapper children) are still covered — nothing else supersedes them
        # at death, and a false-positive there only costs one redundant coldstart check.
        await db.execute(
            """
            UPDATE bridge_instances
            SET superseded_by = 'terminal-dead:' || ?,
                superseded_at = ?
            WHERE agent_id = ?
              AND bridge_kind = 'managed-wrapper-child'
              AND COALESCE(superseded_by, '') = ''
              AND (COALESCE(terminal_id, '') = '' OR terminal_id = ?)
            """,
            (terminal_id, now, terminal["agent_id"], terminal_id),
        )
        # Phantom-pending fix (review 2026-07-02): a `running` spawn_request is the
        # terminal SUCCESS state and its timestamps freeze at boot, so for 5 minutes
        # after boot _has_pending_or_booting_spawn_request would treat this now-dead
        # worker as "mid-boot" and suppress the very respawn its death requires (and
        # burn the backstop's one-shot rescue on a phantom). Mark the death terminal.
        # SCOPED to THIS terminal's session (review 2026-07-03): an unscoped agent-wide
        # stamp would also finish a NEW live worker's still-booting spawn (bound to a
        # different session) — a stale dead-report for an OLD terminal would then trigger a
        # DUPLICATE spawn whose registration supersedes and fails the live worker, exactly
        # what the terminal-scoped bridge supersede above exists to prevent. Set status too
        # so the row reaches a terminal state (else it lingers `running`+finished forever,
        # skipped by _fail_orphaned_running_spawn_requests). Empty-session_id arm covers
        # legacy rows written before session binding.
        await db.execute(
            """
            UPDATE spawn_requests
            SET status = 'failed',
                finished_at = ?,
                error = COALESCE(NULLIF(error, ''), 'Worker terminal died (report-dead); spawn finalized.'),
                updated_at = ?
            WHERE agent_id = ?
              AND status = 'running'
              AND COALESCE(finished_at, '') = ''
              AND (COALESCE(session_id, '') = '' OR session_id = ?)
            """,
            (now, now, terminal["agent_id"], terminal["session_id"]),
        )
        await _invalidate_agent_live_state(db, terminal["agent_id"])
        await _append_terminal_event(
            db,
            terminal_id,
            "console_dead_reported",
            json.dumps({"reportedPid": reported_pid, "bridgeId": req.bridgeId or "", "reason": reason}),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_stopped", {"terminalId": terminal_id, "sessionId": terminal["session_id"]})
        return {"ok": True, "terminal": _terminal_session_to_dict(updated), "changed": True}
    finally:
        await db.close()
