"""The `sessions` route domain: listing, deletion, console start, and session control.

v0.5.2i. Four handlers, three domain-local helpers, 732 lines — and the FIRST BORROW RETIREMENT of
the series, which is the part worth noting.

`_spawn_request_to_dict` and `_spawn_spec_to_dict` did NOT come here. Measurement said they were
"local to sessions", but only because a borrow shim does not count as a user: their real callers were
`list_spawn_requests` (borrowing them) and `control_session` (moving now). Their natural owner is the
spawn-requests domain, so that is where they went — which retires two of the borrows
`service/routers/spawn_requests.py` was carrying, exactly as its retirement map predicted.

v0.5.4 moved them once more, to `service/api_core/spawn_requests_io.py`. The DOMAIN judgement above
still holds and is not being revisited; what changed is the LAYER. This module was importing them
from another router, and one route domain reaching into another is an edge neither of them should
have. Both now import from a leaf.

That is the measurement needing judgement rather than obedience: "no users outside this domain" is a
necessary condition for locality, not a sufficient one, because shims are invisible to it.

`start_session_console` and `control_session` both moved WHOLE into this module. `control_session`
still is whole; `start_session_console` is not — v0.5.4 lifted its terminal-capability refusal into
`service/api_core/console_capability_gate.py`, proved by `test_start_session_console_split_is_inert.py`.

BORROW TABLE with retirement map:

    _coldstart_spawn_request_for_dispatch    retires with: agents, messages
    _agent_session_to_dict                   retires with: agents
    _environment_record_to_dict              retires with: agents
    _default_console_command                 retires with: agents
    _terminal_session_to_dict                retires with: agents, terminals
    _append_dispatch_control                 retires with: agents, dispatch
    _workspace_for_environment               retires with: agents

Nearly all of it retires with `agents`, which is the last domain.

Built with `domain_router()`, and declares NO tags: the parent applies `tags=["api"]` on include.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional

from fastapi import HTTPException, Query, Request

from service.db_errors import _is_lock_error
from service.api_core.dispatch_run_state import _append_dispatch_control
from service.api_core.active_run_lookup import _get_blocking_active_run
from service.api_core.spawn_request_state import _has_claimable_spawn_request
from service.api_core.routing import domain_router
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.records import (
    _agent_session_to_dict,
    _environment_record_to_dict,
    _terminal_session_to_dict,
)
from service.api_core.virtual_rpc import VIRTUAL_RPC_COMMAND_SET
from service.api_core.serialization import _iso_from_ms, _json_loads_or
from service.api_core.capabilities import _default_console_command
from service.api_core.console_terminal_rows import (
    _insert_pty_console_terminal,
    _insert_virtual_console_terminal,
)
from service.api_core.console_capability_gate import _refuse_console_without_terminal_capability
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.api_core.session_restart import _prepare_restart_spawn
from service.api_core.agent_sessions import (
    _settle_agent_for_session_control,
    _touch_current_agent_session,
)
from service.api_core.virtual_rpc import VIRTUAL_RPC_COMMANDS_BY_RUNTIME
from service.clock import now as _now
import sqlite3
from service.api_core.events import _append_terminal_control, _append_terminal_event
from service.reconcilers.sessions import _compute_session_display_status
from service.reconcilers.terminal_consistency import _repair_terminal_session_consistency
from service.terminal_snapshot import drop_live_screen as _drop_live_terminal_screen
from service.db import get_db
from service.env_status import environment_effective_status as _environment_effective_status
# Imported for ANNOTATIONS as well as calls. Under postponed evaluation a missing model does not
# fail import -- FastAPI demotes the body to a query param and the route 422s at request time.
from service.models import ConsoleStartRequest, SessionControlRequest
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
# Retired borrows: these now have a real owner in the spawn-requests domain.
from service.api_core.spawn_requests_io import _spawn_request_to_dict, _spawn_spec_to_dict
from service.api_core.terminal_status import _TERMINAL_ACTIVE_STATUSES
from service.api_core.workspace import (
    _normalize_workspace_for_environment,
    _workspace_for_environment,
    _workspace_root_for,
)
from service.api_core.dispatch_start import _coldstart_spawn_request_for_dispatch
from service.api_core.tuning import _SESSION_DELETE_ALLOWED_STATUSES

logger = logging.getLogger("aify_comms.routers.sessions")

router = domain_router()


# Domain-local: nothing outside this module referenced them once the handlers moved.
# CLEAN history: finished AND nothing left to act on — the only rows GET /sessions hides by
# default. Deliberately NOT the same set as _borrowed_session_delete_allowed_statuses() above, and the
# difference is load-bearing (regression 2026-07-26, caught in review): "safe to eventually
# delete" is not "not worth showing".
#   * `stopped` is a session the OPERATOR stopped — Restart / Reset / Compact are precisely the
#     actions you take on it. `comms_restart` even falls back to `sessions[0]` on purpose so a
#     non-live session can be restarted; hiding `stopped` made it answer "no session to restart".
#   * `failed` / `lost` are crashed workers the operator may still restart or inspect.
# Hiding those three broke real consumers (comms_restart, comms_compact, the drawer's
# Restart/Reset/Compact buttons). Only a cleanly-finished session is pure history.
SESSION_CLEAN_HISTORY_STATUSES = {"ended", "completed", "cancelled"}
_TERMINAL_DELETE_ALLOWED_STATUSES = {"stopped", "failed", "lost", "ended", "completed", "cancelled"}




def _borrowed_session_delete_allowed_statuses():
    """BORROWED constant: one owner, never a copy — a forked status set is finding N7."""

    return _SESSION_DELETE_ALLOWED_STATUSES

















async def _agent_session_dict_live(db, row, *, agent_row=None) -> dict[str, Any]:
    """Build the session dict (identical shape/keys to _agent_session_to_dict) but
    with the `status` value DERIVED from live truth via
    _compute_session_display_status. The stored row is never mutated; only the
    served `status` becomes derived so GET /sessions matches GET /agents."""
    data = _agent_session_to_dict(row)
    try:
        data["status"] = await _compute_session_display_status(db, row, agent_row=agent_row)
    except Exception:
        # Defensive: never fail a session list because the deriver hit an edge —
        # fall back to the stored status (the prior behavior).
        pass
    return data


async def _repair_current_session_freshness(db) -> int:
    cursor = await db.execute(
        """
        SELECT id, last_seen, runtime_state
        FROM agents
        WHERE session_mode = 'managed'
          AND runtime_state IS NOT NULL
          AND runtime_state != ''
          AND runtime_state != '{}'
        """
    )
    repaired = 0
    for row in await cursor.fetchall():
        runtime_state = _json_loads_or(row["runtime_state"], {})
        if not (runtime_state.get("spawnRequestId") or runtime_state.get("environmentId")):
            continue
        before = db.total_changes
        await _touch_current_agent_session(db, row["id"], runtime_state, row["last_seen"] or _now())
        if db.total_changes > before:
            repaired += 1
    if repaired:
        await db.commit()
    return repaired


async def _repair_superseded_recovering_sessions(db) -> int:
    now = _now()
    cursor = await db.execute(
        """
        SELECT old.id
        FROM agent_sessions old
        WHERE old.status IN ('starting', 'recovering', 'restarting')
          AND EXISTS (
            SELECT 1
            FROM agent_sessions current
            WHERE current.agent_id = old.agent_id
              AND current.id != old.id
              AND current.status = 'running'
              AND COALESCE(NULLIF(current.last_seen, ''), NULLIF(current.started_at, ''), '') >=
                  COALESCE(NULLIF(old.last_seen, ''), NULLIF(old.started_at, ''), '')
          )
        """
    )
    rows = await cursor.fetchall()
    if not rows:
        return 0
    for row in rows:
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = 'ended',
                ended_at = COALESCE(NULLIF(ended_at, ''), NULLIF(last_seen, ''), ?),
                last_seen = COALESCE(NULLIF(ended_at, ''), NULLIF(last_seen, ''), ?)
            WHERE id = ?
              AND status IN ('starting', 'recovering', 'restarting')
            """,
            (now, now, row["id"]),
        )
    await db.commit()
    return len(rows)


@router.get("/sessions")
async def list_sessions(
    request: Request,
    agentId: Optional[str] = None,
    environmentId: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    includeEnded: bool = Query(
        False,
        description="Include TERMINAL (ended/stopped/failed/lost/cancelled/completed) sessions. "
                    "Off by default: this endpoint lists CURRENT sessions.",
    ),
):
    """List sessions. CURRENT ones by default — this is not a history feed.

    Why the default changed (2026-07-26): the query filtered only on agentId/environmentId, so it
    returned every historical row and the dashboard's Sessions rail became an accidental history
    dump — one entry per worker process ever started. mc-senior-dev showed 79, all of them the SAME
    native conversation (`session_handle` 20260715_001441_960b8f) resumed by 75 successive boots,
    which is exactly why it read as "duplicates". Session HISTORY already has a home: the
    spawn-requests list under Environments.

    Two latent bugs came with it, which is why this is a correctness fix and not just tidying:
      * the dashboard picks an agent's session with `state.sessions.find(...)` — the FIRST match.
        Ordering is `last_seen DESC`, and a dead row can carry a newer `last_seen` than the live
        one, so the console/drawer could bind to an ENDED session.
      * the dashboard requests `limit=80`. With 449 rows, one agent's dead history could push
        another agent's LIVE session out of the window entirely, making it invisible.

    `includeEnded=true` preserves the old behaviour for anything that genuinely wants history.
    """
    db = await get_db()
    try:
        # Best-effort consistency repairs — serve cached on a write-lock rather than 503 (see list_agents).
        # These stay on the read path (NOT moved to reconcile): they correct the console/terminal
        # binding shown in THIS response (e.g. a just-stopped terminal must immediately read as
        # unbound), so a 60s reconcile lag would surface a dead terminal as still-attached. The
        # functions no-op when nothing needs repair, so steady-state polls do not write.
        try:
            await _repair_superseded_recovering_sessions(db)
            await _repair_current_session_freshness(db)
            await _repair_terminal_session_consistency(db)
        except sqlite3.OperationalError as exc:
            if not _is_lock_error(exc):
                raise
            try:
                await db.rollback()
            except Exception:
                pass
        where = []
        params: list[Any] = []
        if agentId:
            where.append("agent_id = ?")
            params.append(agentId)
        if environmentId:
            where.append("environment_id = ?")
            params.append(environmentId)
        if not includeEnded:
            # Filter on the STORED status only. A row stored live but derived dead below stays in
            # the response on purpose — the operator should see it (and the reconcilers heal it);
            # what must not appear is a row that is finished AND has nothing left to act on.
            hidden = sorted(SESSION_CLEAN_HISTORY_STATUSES)
            where.append(
                f"LOWER(COALESCE(status,'')) NOT IN ({','.join('?' for _ in hidden)})"
            )
            params.extend(hidden)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        cursor = await db.execute(
            f"SELECT * FROM agent_sessions {where_sql} ORDER BY last_seen DESC LIMIT ?",
            (*params, limit),
        )
        rows = await cursor.fetchall()
        # Phase 3 (2026-06-03): DERIVE the served session status from live truth
        # (terminal_sessions / bridge_instances) so GET /sessions matches GET
        # /agents — the stored agent_sessions.status is a cache, never the display
        # source. Cache the agent row per agent so a multi-session list resolves
        # each agent's liveness once.
        agent_cache: dict[str, Any] = {}
        sessions: list[dict[str, Any]] = []
        for row in rows:
            aid = str(row["agent_id"] or "")
            if aid and aid not in agent_cache:
                agent_cache[aid] = await (
                    await db.execute("SELECT * FROM agents WHERE id = ?", (aid,))
                ).fetchone()
            sessions.append(await _agent_session_dict_live(db, row, agent_row=agent_cache.get(aid)))
        return {"ok": True, "sessions": sessions}
    finally:
        await db.close()


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))
        session = await cursor.fetchone()
        if not session:
            raise HTTPException(404, f'Session "{session_id}" not found')

        status = str(session["status"] or "").strip().lower()
        if status not in _borrowed_session_delete_allowed_statuses():
            raise HTTPException(
                409,
                f'Session "{session_id}" is {status or "active"}; stop or finish it before deleting the session record.',
            )

        terminal_rows = await (await db.execute("SELECT * FROM terminal_sessions WHERE session_id = ?", (session_id,))).fetchall()
        stale_active_terminal_ids = [
            terminal["id"]
            for terminal in terminal_rows
            if str(terminal["status"] or "").strip().lower() not in _TERMINAL_DELETE_ALLOWED_STATUSES
        ]

        for terminal in terminal_rows:
            # Release the live screen with the terminal it belongs to. (A merely STOPPED console
            # keeps its screen on purpose — you can still read the last thing it showed, e.g. an
            # agent that stopped sitting at a dialog. The registry is bounded regardless.)
            try:
                _drop_live_terminal_screen(str(terminal["id"]))
            except Exception:
                pass
            await db.execute("DELETE FROM terminal_controls WHERE terminal_id = ?", (terminal["id"],))
            await db.execute("DELETE FROM terminal_events WHERE terminal_id = ?", (terminal["id"],))
        await db.execute("DELETE FROM terminal_sessions WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM agent_sessions WHERE id = ?", (session_id,))
        await db.commit()

        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("session_deleted", {"sessionId": session_id, "agentId": session["agent_id"]})
        return {
            "ok": True,
            "deleted": True,
            "sessionId": session_id,
            "agentId": session["agent_id"],
            "staleActiveTerminalsDeleted": stale_active_terminal_ids,
        }
    finally:
        await db.close()


@router.post("/sessions/{session_id}/console/start")
async def start_session_console(session_id: str, req: ConsoleStartRequest, request: Request):
    db = await get_db()
    try:
        session = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
        if not session:
            raise HTTPException(404, f'Session "{session_id}" not found')
        env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (session["environment_id"],))).fetchone()
        if not env_row:
            raise HTTPException(409, f'Environment "{session["environment_id"]}" is not available')
        settings = await _load_settings(db)

        # Slice 3: reuse the existing live wrapper PTY for this agent
        # session when one is already attached. Avoids the symptom
        # where each "Start Console" click (or auto-attach via the
        # dashboard) spawns a fresh wrapper PTY even though a previous
        # one is still running — operator-visible "console pops up
        # again". The dispatch path (via _ensure_managed_pty_for_dispatch
        # -> _active_terminal_for_agent) already reuses; this brings the
        # manual-start path to parity.
        existing_terminal_id = str(session["terminal_id"] or "").strip()
        if existing_terminal_id:
            existing_terminal = await (await db.execute(
                "SELECT * FROM terminal_sessions WHERE id = ?",
                (existing_terminal_id,),
            )).fetchone()
            if existing_terminal:
                existing_status = str(existing_terminal["status"] or "").strip().lower()
                if existing_status in {"starting", "attached", "running", "active", "idle", "recovering"}:
                    await _append_terminal_event(
                        db,
                        existing_terminal_id,
                        "console_attach_reused_existing",
                        json.dumps({
                            "requestedBy": str(req.requestedBy or "dashboard").strip() or "dashboard",
                            "sessionId": session_id,
                            "agentId": session["agent_id"],
                        }),
                    )
                    await db.commit()
                    return {
                        "ok": True,
                        "terminal": _terminal_session_to_dict(existing_terminal),
                        "reused": True,
                    }

        # Agent-scoped virtual terminal reattach (Phase 2 follow-up).
        # The virtual terminal_session created by /agents/{id}/virtual-terminal/ensure
        # is canonical per-agent: ONE row per agent regardless of how many
        # agent_sessions exist over the agent's lifetime. The bridge creates
        # it tied to whichever agent_session was active at first dispatch,
        # but a later dashboard Console click on a DIFFERENT agent_session
        # for the same agent must attach to that same virtual terminal —
        # otherwise the dashboard would spawn a fresh pi-aify PTY console
        # and the operator sees a different terminal than the one actually
        # driving their dispatches. Skip the PTY env-supports check too:
        # virtual terminals don't need node-pty.
        agent_row_for_virtual = await (await db.execute(
            "SELECT id, runtime, runtime_state FROM agents WHERE id = ?",
            (session["agent_id"],),
        )).fetchone()
        if agent_row_for_virtual:
            agent_runtime_state = _json_loads_or(agent_row_for_virtual["runtime_state"], {}) or {}
            virtual_terminal_id = str(agent_runtime_state.get("virtualTerminalId") or "").strip()
            if virtual_terminal_id:
                virtual_terminal = await (await db.execute(
                    "SELECT * FROM terminal_sessions WHERE id = ?",
                    (virtual_terminal_id,),
                )).fetchone()
                if virtual_terminal:
                    virtual_status = str(virtual_terminal["status"] or "").strip().lower()
                    virtual_command = str(virtual_terminal["command"] or "")
                    if (
                        virtual_command in VIRTUAL_RPC_COMMAND_SET
                        and virtual_status in {"starting", "running", "recovering", "active", "idle"}
                    ):
                        attach_now = _now()
                        # Point the requesting session at the canonical
                        # virtual terminal so the dashboard's session view
                        # follows it.
                        await db.execute(
                            """
                            UPDATE agent_sessions
                            SET terminal_id = ?,
                                terminal_status = ?,
                                terminal_command = ?,
                                last_seen = ?
                            WHERE id = ?
                            """,
                            (virtual_terminal_id, virtual_status, virtual_command, attach_now, session_id),
                        )
                        await _append_terminal_event(
                            db,
                            virtual_terminal_id,
                            "virtual_pi_rpc_console_attached",
                            json.dumps({
                                "requestedBy": str(req.requestedBy or "dashboard").strip() or "dashboard",
                                "sessionId": session_id,
                                "agentId": session["agent_id"],
                            }),
                        )
                        await db.commit()
                        updated_session_for_virtual = await (await db.execute(
                            "SELECT * FROM agent_sessions WHERE id = ?",
                            (session_id,),
                        )).fetchone()
                        ws_for_virtual = await _get_ws(request)
                        if ws_for_virtual:
                            await ws_for_virtual.broadcast(
                                "terminal_started",
                                {
                                    "terminalId": virtual_terminal_id,
                                    "sessionId": session_id,
                                    "agentId": session["agent_id"],
                                    "virtual": True,
                                    "reused": True,
                                },
                            )
                        return {
                            "ok": True,
                            "terminal": _terminal_session_to_dict(virtual_terminal),
                            "session": _agent_session_to_dict(updated_session_for_virtual),
                            "reused": True,
                            "virtual": True,
                        }

        runtime = _normalize_runtime(session["runtime"] or "")
        if runtime == "pi":
            environment = _environment_record_to_dict(env_row, offline_seconds=settings.get("environment_offline_seconds", 90))
            if str(environment.get("status") or "").lower() != "online":
                raise HTTPException(409, f'Environment "{environment.get("id")}" is {environment.get("status") or "unknown"}')
            if not str(session["session_handle"] or "").strip() and not bool(req.freshContext):
                raise HTTPException(409, 'Pi Console needs a session handle to preserve context. Set a handle or request freshContext=true.')
            workspace, _workspace_root = _workspace_for_environment(environment, req.workspace, session["workspace"] or "")
            terminal_id = f"vterm_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            now = _now()
            bridge_id = str(environment.get("bridgeId") or "").strip()
            virtual_command = VIRTUAL_RPC_COMMANDS_BY_RUNTIME["pi"]
            requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
            await _insert_virtual_console_terminal(
                db, terminal_id, session_id, session, bridge_id, workspace, virtual_command,
                requested_by, now,
            )
            await _append_terminal_event(
                db,
                terminal_id,
                "virtual_pi_rpc_console_started",
                json.dumps({"requestedBy": requested_by, "sessionId": session_id, "workspace": workspace}),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET owner_mode = 'managed',
                    owner_bridge_id = ?,
                    terminal_id = ?,
                    terminal_status = 'running',
                    terminal_command = ?,
                    terminal_workspace = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (bridge_id, terminal_id, virtual_command, workspace, now, session_id),
            )
            next_runtime_state = _json_loads_or((agent_row_for_virtual["runtime_state"] if agent_row_for_virtual else "") or "{}", {}) or {}
            next_runtime_state["virtualTerminal"] = True
            next_runtime_state["virtualTerminalId"] = terminal_id
            await db.execute(
                "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
                (json.dumps(next_runtime_state), now, session["agent_id"]),
            )
            # The agent now has a live worker (virtualTerminalId + terminal_status
            # running). Invalidate the live-status cache so it recomputes to online
            # immediately instead of lying `available` until the 60s sweep.
            await _invalidate_agent_live_state(db, session["agent_id"])
            await db.commit()
            terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
            updated_session = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
            ws_for_virtual = await _get_ws(request)
            if ws_for_virtual:
                await ws_for_virtual.broadcast(
                    "terminal_started",
                    {"terminalId": terminal_id, "sessionId": session_id, "agentId": session["agent_id"], "virtual": True},
                )
            return {
                "ok": True,
                "terminal": _terminal_session_to_dict(terminal),
                "session": _agent_session_to_dict(updated_session),
                "reused": False,
                "virtual": True,
            }

        environment = _environment_record_to_dict(env_row, offline_seconds=settings.get("environment_offline_seconds", 90))
        if str(environment.get("status") or "").lower() != "online":
            raise HTTPException(409, f'Environment "{environment.get("id")}" is {environment.get("status") or "unknown"}')
        _refuse_console_without_terminal_capability(environment, session)
        if runtime == "pi" and not str(session["session_handle"] or "").strip() and not bool(req.freshContext):
            raise HTTPException(409, 'Pi Console needs a session handle to preserve context. Set a handle or request freshContext=true.')

        workspace, _workspace_root = _workspace_for_environment(environment, req.workspace, session["workspace"] or "")
        terminal_id = f"term_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        now = _now()
        command = str(req.command or "").strip() or _default_console_command(session, workspace, interactive=True)
        requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
        bridge_id = str(environment.get("bridgeId") or "").strip()
        await _insert_pty_console_terminal(
            db, terminal_id, session_id, session, bridge_id, workspace, command, requested_by, now,
        )
        await _append_terminal_event(
            db,
            terminal_id,
            "console_start_requested",
            json.dumps({"requestedBy": requested_by, "sessionId": session_id, "workspace": workspace, "command": command}),
        )
        await _append_terminal_control(
            db,
            terminal_id=terminal_id,
            environment_id=session["environment_id"],
            bridge_id=bridge_id,
            action="start",
            requested_by=requested_by,
            body=command,
        )

        await db.execute(
            """
            UPDATE agent_sessions
            SET owner_mode = 'console',
                owner_bridge_id = ?,
                terminal_id = ?,
                terminal_status = 'starting',
                terminal_command = ?,
                terminal_workspace = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (bridge_id, terminal_id, command, workspace, now, session_id),
        )
        await db.commit()
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        updated_session = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_started", {"terminalId": terminal_id, "sessionId": session_id, "agentId": session["agent_id"]})
        return {
            "ok": True,
            "terminal": _terminal_session_to_dict(terminal),
            "session": _agent_session_to_dict(updated_session),
        }
    finally:
        await db.close()


@router.post("/sessions/{session_id}/control")
async def control_session(session_id: str, req: SessionControlRequest, request: Request):
    action = str(req.action or "").strip().lower()
    # Lifecycle cleanup (2026-06-03): `recover` + `resume` were byte-identical
    # aliases of `restart` with NO dashboard caller — dropped. (Resident
    # wake-resume lives on POST /agents/{id}/control, a different endpoint.)
    if action not in {"stop", "restart", "recreate", "cli_takeover"}:
        raise HTTPException(400, f'Unsupported session control action "{req.action}"')

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))
        session = await cursor.fetchone()
        if not session:
            raise HTTPException(404, f'Session "{session_id}" not found')

        now = _now()
        agent_id = session["agent_id"]
        active_run = await _get_blocking_active_run(db, agent_id)
        control_id = ""
        if active_run:
            control_id = await _append_dispatch_control(
                db,
                active_run["runId"],
                from_agent=req.from_agent or "dashboard",
                action="interrupt",
                body=req.body or f"Session {action} requested from dashboard.",
            )

        spawn_request_row = None
        spawn_spec_row = None
        cancelled_spawns = 0
        coldstart_warnings: list[str] = []
        if action in {"restart", "recreate"}:
            pending_cursor = await db.execute(
                """
                SELECT *
                FROM spawn_requests
                WHERE agent_id = ?
                  AND status IN ('queued', 'claimed', 'starting')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (agent_id,),
            )
            pending_spawn = await pending_cursor.fetchone()
            if pending_spawn:
                raise HTTPException(
                    409,
                    f'Agent "{agent_id}" already has pending spawn request "{pending_spawn["id"]}" ({pending_spawn["status"]}).',
                )

        spawn_request_row, spawn_spec_row = await _prepare_restart_spawn(
            db, req, session, session_id, agent_id, action, now, coldstart_warnings,
            spawn_request_row, spawn_spec_row,
        )

        next_status = {
            "stop": "stopped",
            "restart": "restarting",
            "recreate": "ended",
            "cli_takeover": "cli-takeover",
        }[action]
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = ?, last_seen = ?, ended_at = CASE WHEN ? IN ('stopped','restarting','recovering','ended') THEN ? ELSE ended_at END
            WHERE id = ?
            """,
            (next_status, now, next_status, now, session_id),
        )
        cancelled_spawns = await _settle_agent_for_session_control(
            db, session_id, agent_id, action, now, cancelled_spawns,
        )

        # Halt the running backing (2026-06-07): Stop/Restart/Reset/CLI-takeover must PROMPTLY
        # kill the live managed PTY, not just flip DB status. Previously only the agent-control
        # stop enqueued a terminal stop, so the UI's session-control Stop left the worker running
        # as a headless orphan until a reaper / the next Restart's reap-prior. Enqueue a terminal
        # 'stop' for the session's live terminal(s). For restart/recreate the new spawn_request
        # was already queued above (and an env-offline target 409'd before reaching here), so we
        # never kill the old backing without a replacement queued. Resume is unaffected — it
        # carries via the durable session_handle, not the live PTY.
        live_terminals = await (await db.execute(
            "SELECT id, environment_id, bridge_id, status FROM terminal_sessions WHERE session_id = ?",
            (session_id,),
        )).fetchall()
        for term_row in live_terminals:
            if str(term_row["status"] or "").strip().lower() in _TERMINAL_ACTIVE_STATUSES:
                await _append_terminal_control(
                    db,
                    terminal_id=term_row["id"],
                    environment_id=term_row["environment_id"] or "",
                    bridge_id=term_row["bridge_id"] or "",
                    action="stop",
                    requested_by=req.from_agent or "dashboard",
                    body=f"Session {action} from dashboard.",
                )

        await db.commit()
        updated = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("session_control_requested", {"sessionId": session_id, "agentId": agent_id, "action": action})
            if spawn_request_row:
                await ws.broadcast(
                    "spawn_request_created",
                    {"spawnRequestId": spawn_request_row["id"], "environmentId": spawn_request_row["environment_id"]},
                )
        return {
            "ok": True,
            "action": action,
            "session": _agent_session_to_dict(updated),
            "interruptControlId": control_id,
            "cancelledSpawns": cancelled_spawns,
            "warnings": coldstart_warnings,
            "spawnRequest": _spawn_request_to_dict(spawn_request_row, _spawn_spec_to_dict(spawn_spec_row) if spawn_spec_row else None) if spawn_request_row else None,
        }
    finally:
        await db.close()
