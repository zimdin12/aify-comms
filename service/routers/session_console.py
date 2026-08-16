"""Starting a console for a session: pick a terminal, or reuse the one already there.

Extracted from `service/routers/sessions.py` in v0.5.4. Closure measured before the move —
`api_core` and `service` leaves only, nothing local to that router.

ONE ENDPOINT, THREE WAYS TO GET A TERMINAL, which is why it is 146 lines and why it is its own file.
A PTY console is inserted; a virtual-RPC runtime REUSES an existing console terminal rather than
opening a second one; and a virtual pi console is started differently again. Choosing wrongly does
not fail loudly — it produces a session with a console nobody is attached to.

IT REFUSES BEFORE IT STARTS when the runtime cannot host a terminal at all. That gate is the reason
`_refuse_console_without_terminal_capability` exists as a named leaf: an agent asking for a console
on a runtime that has none must be told so, not handed an empty terminal that never emits anything.

Body and route decorator are byte-identical to what stood in `sessions.py`. The router is built
through `domain_router()`, which rejects a hand-passed `route_class`, so a new surface cannot opt out
of the bounded SQLite write-lock retry.
"""

from __future__ import annotations

import json
import time
import uuid

from fastapi import HTTPException, Request

from service.api_core.capabilities import _default_console_command
from service.api_core.console_capability_gate import _refuse_console_without_terminal_capability
from service.api_core.console_terminal_rows import (
    _insert_pty_console_terminal,
    _reuse_virtual_rpc_console_terminal,
    _start_virtual_pi_console,
)
from service.api_core.events import _append_terminal_control, _append_terminal_event
from service.api_core.records import (
    _agent_session_to_dict,
    _environment_record_to_dict,
    _terminal_session_to_dict,
)
from service.api_core.routing import domain_router
from service.api_core.runtime import _normalize_runtime
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import _load_settings
from service.api_core.virtual_rpc import VIRTUAL_RPC_COMMAND_SET
from service.api_core.workspace import _workspace_for_environment
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import ConsoleStartRequest

router = domain_router()



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
                        return await _reuse_virtual_rpc_console_terminal(
                            db, req, request, session, session_id,
                            virtual_terminal, virtual_terminal_id, virtual_status, virtual_command,
                        )

        runtime = _normalize_runtime(session["runtime"] or "")
        if runtime == "pi":
            return await _start_virtual_pi_console(
                db, req, request, session, session_id,
                settings, env_row, agent_row_for_virtual,
            )

        environment = _environment_record_to_dict(env_row, offline_seconds=settings.get("environment_offline_seconds", 90))
        if str(environment.get("status") or "").lower() != "online":
            raise HTTPException(409, f'Environment "{environment.get("id")}" is {environment.get("status") or "unknown"}')
        _refuse_console_without_terminal_capability(environment, session)
        # The pi session-handle guard USED TO BE REPEATED HERE and could never fire: `runtime` is
        # assigned once, twelve lines up, and `if runtime == "pi"` returns before this point. The
        # live copy is the one inside `_start_virtual_pi_console`, which is where a pi console is
        # actually built; this one was dead before the v0.5.4 split too (it is in the pre-split
        # fixture at the same relative place). Removed rather than left as a second opinion: an
        # unreachable copy of a guard is worse than no copy, because the next person to change the
        # rule can edit it, see a green suite, and ship nothing.

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
