"""Terminal-control shaping, claiming, and console-binding teardown.

Moved out of `service/routers/terminals.py` in v0.5.4, byte-identical. A router should hold routes;
these three are the only non-route declarations it had.

THE POINT OF THE MOVE IS THE BORROW IT DELETES. `service/reconcilers/terminal_consistency.py`
imported `_clear_console_terminal_binding` FROM THE ROUTER — an upward import from a leaf, recorded
in that module's own docstring as deferred debt: *"If a later slice consolidates the terminal-event
helpers, these three borrows are what it deletes."* This is that slice for one of them. The reconciler
now imports it from here, and nothing in this module imports a router or the control plane.

The three travel together because they are coupled: `_claim_terminal_controls_once` calls
`_terminal_control_to_dict`, and splitting them would only have replaced one import with another.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from service.api_core.runtime import _normalize_session_mode
from service.api_core.serialization import _json_loads_or
from service.clock import now as _now
from service.db import SQLITE_CLAIM_BUSY_TIMEOUT_MS, get_db
from service.models import TerminalControlClaim
# ALIASED ON IMPORT, exactly as the router does it. The public name is `invalidate_agent_live_state`;
# the moved body calls the underscored one, so importing it under the name it was written against is
# what keeps the body byte-identical instead of rewriting a call site to suit the move.
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state

def _terminal_control_to_dict(
    row,
    *,
    pid: str = "",
    agent_id: str = "",
    runtime: str = "",
    session_mode: str = "",
) -> dict[str, Any]:
    return {
        "id": row["id"],
        "terminalId": row["terminal_id"],
        "environmentId": row["environment_id"],
        "bridgeId": row["bridge_id"] or "",
        "action": row["action"],
        "body": row["body"] or "",
        "cols": int(row["cols"] or 0),
        "rows": int(row["rows"] or 0),
        "status": row["status"] or "",
        "requestedBy": row["requested_by"] or "",
        "requestedAt": row["requested_at"] or "",
        "claimedAt": row["claimed_at"] or "",
        "handledAt": row["handled_at"] or "",
        "error": row["error"] or "",
        # Stored PTY root pid for the target terminal (terminal_sessions.
        # process_id). Lets a claiming bridge kill an orphaned PTY by-pid on a
        # `stop` control when it never owned the PTY in its in-memory Map
        # (owning bridge restarted/died). Empty when unknown.
        "pid": str(pid or ""),
        # Target terminal's agent + runtime, and the agent's session_mode, so a
        # claiming bridge can detect a MANAGED-HERMES `stop` and run the triad
        # teardown (gateway/loop/daemon), not just the PTY stop (fix/hermes-leak
        # P2). Empty when the terminal/agent is gone (e.g. claimed after REMOVE
        # deleted the agent) — REMOVE therefore stamps the body sentinel below so
        # the triad reap still fires.
        "agentId": str(agent_id or ""),
        "runtime": str(runtime or ""),
        "sessionMode": str(session_mode or ""),
    }

async def _claim_terminal_controls_once(req: TerminalControlClaim):
    db = await get_db(busy_timeout_ms=SQLITE_CLAIM_BUSY_TIMEOUT_MS)
    try:
        now = _now()
        cursor = await db.execute(
            """
            SELECT *
            FROM terminal_controls
            WHERE environment_id = ?
              AND COALESCE(bridge_id, '') = ?
              AND status = 'pending'
            ORDER BY requested_at ASC, id ASC
            LIMIT 20
            """,
            (req.environmentId, req.bridgeId),
        )
        controls = await cursor.fetchall()
        if controls:
            ids = [row["id"] for row in controls]
            await db.executemany(
                "UPDATE terminal_controls SET status = 'claimed', claimed_at = ? WHERE id = ? AND status = 'pending'",
                [(now, control_id) for control_id in ids],
            )
            await db.commit()
            refreshed = []
            for control_id in ids:
                row = await (await db.execute("SELECT * FROM terminal_controls WHERE id = ?", (control_id,))).fetchone()
                if row:
                    refreshed.append(row)
            controls = refreshed
        # Attach the target terminal's stored PTY root pid so a claiming bridge
        # can kill-by-pid when its in-memory terminals Map misses (orphaned PTY,
        # owning bridge gone). The claim is already env+bridge scoped, so the pid
        # only ever reaches the bridge for terminals on its own machine.
        out = []
        for row in controls:
            term_row = await (await db.execute(
                "SELECT process_id, agent_id, runtime FROM terminal_sessions WHERE id = ?",
                (row["terminal_id"],),
            )).fetchone()
            pid = str((term_row["process_id"] if term_row else "") or "")
            agent_id = str((term_row["agent_id"] if term_row else "") or "")
            runtime = str((term_row["runtime"] if term_row else "") or "")
            # Surface the target agent's session_mode so a claiming bridge can
            # decide whether a `stop` control needs a MANAGED-HERMES triad teardown
            # (fix/hermes-leak P2). Best-effort; resident/unknown → "".
            session_mode = ""
            if agent_id:
                agent_row = await (await db.execute(
                    "SELECT session_mode FROM agents WHERE id = ?",
                    (agent_id,),
                )).fetchone()
                session_mode = _normalize_session_mode((agent_row["session_mode"] if agent_row else "") or "")
            out.append(_terminal_control_to_dict(
                row, pid=pid, agent_id=agent_id, runtime=runtime, session_mode=session_mode,
            ))
        return {"ok": True, "controls": out}
    finally:
        await db.close()

async def _clear_console_terminal_binding(db, agent_id: str, terminal_id: str, *, now: Optional[str] = None) -> None:
    agent_id = str(agent_id or "").strip()
    terminal_id = str(terminal_id or "").strip()
    if not agent_id or not terminal_id:
        return
    row = await (await db.execute("SELECT runtime_state, status_note FROM agents WHERE id = ?", (agent_id,))).fetchone()
    if not row:
        return
    runtime_state = _json_loads_or(row["runtime_state"], {})
    if not isinstance(runtime_state, dict):
        return
    cleared = False
    console_terminal = runtime_state.get("consoleTerminal")
    if isinstance(console_terminal, dict) and str(console_terminal.get("terminalId") or "").strip() == terminal_id:
        runtime_state.pop("consoleTerminal", None)
        cleared = True
    # The dashboard-start path writes the TOP-LEVEL runtime_state.terminalId (and the synth
    # path virtualTerminalId); leaving either pointing at a dead terminal made the dashboard
    # auto-mount an xterm over a stopped PTY's stale buffer with no Start affordance
    # (graph-tech-lead incident, 2026-07-02).
    for key in ("terminalId", "virtualTerminalId"):
        if str(runtime_state.get(key) or "").strip() == terminal_id:
            runtime_state.pop(key, None)
            cleared = True
    if not cleared:
        return
    status_note = str(row["status_note"] or "").strip()
    if status_note == "Dashboard Console PTY attached.":
        status_note = ""
    await db.execute(
        """
        UPDATE agents
        SET runtime_state = ?,
            status_note = ?,
            last_seen = ?
        WHERE id = ?
        """,
        (json.dumps(runtime_state), status_note, now or _now(), agent_id),
    )
    await _invalidate_agent_live_state(db, agent_id)
