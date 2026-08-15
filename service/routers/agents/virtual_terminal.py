"""Making sure an agent HAS a virtual terminal, and reanchoring the one it already has.

Extracted from `service/routers/agents/console.py` in v0.5.4. Closure measured before the move:
`api_core` and `service` leaves plus one predicate from `agents/shared.py`, and nothing local.

PROVISIONING A TERMINAL IS NOT USING ONE, which is the split. What stays in `console.py` reads an
agent's console and writes input to it — operations on a terminal that exists. This one decides
whether a terminal should exist at all, creates it, or REANCHORS an existing row onto the session
now asking for it. Those are different failure modes: the console verbs fail by talking to the wrong
terminal, this one fails by creating a second one.

`_synth_terminal_should_be_created` IS THE WHOLE DECISION and it is deliberately not local. A synth
terminal (`aify://virtual-rpc/<runtime>`) is deprecated for wrapper-backed runtimes — the wrapper PTY
IS the terminal — and survives only for native managed runtimes like pi and opencode. Getting that
wrong in either direction is visible: a duplicate terminal for a wrapper-backed agent, or no
terminal at all for a native one.

Body and route decorator are byte-identical to what stood in `console.py`. The router is built
through `domain_router()`, which rejects a hand-passed `route_class`, so a new surface cannot opt out
of the bounded SQLite write-lock retry.
"""

from __future__ import annotations

import json
import time
import uuid

from fastapi import HTTPException, Request

from service.api_core.console_terminal_rows import _reanchor_existing_virtual_terminal
from service.api_core.events import _append_terminal_event
from service.api_core.records import _agent_session_to_dict, _terminal_session_to_dict
from service.api_core.routing import domain_router
from service.api_core.runtime import _normalize_runtime
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import _load_settings
from service.api_core.virtual_rpc import VIRTUAL_RPC_COMMANDS_BY_RUNTIME
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
from service.routers.agents.shared import _synth_terminal_should_be_created

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import VirtualTerminalEnsureRequest

router = domain_router()



@router.post("/agents/{agent_id}/virtual-terminal/ensure")
async def ensure_virtual_terminal(agent_id: str, req: VirtualTerminalEnsureRequest, request: Request):
    """Bridge-driven creation of a synthesized terminal_session row.

    Managed pi runs use a persistent `omp --mode rpc` child whose AgentSessionEvent
    stream is synthesized by the bridge into a human-readable terminal_output
    feed. There is no real PTY — the bridge owns the lifecycle. This endpoint is
    idempotent: a second call for the same agent on the same bridge returns the
    existing virtual terminal row. See docs/plans/pi-persistent-rpc.md.
    """
    db = await get_db()
    try:
        agent = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        bridge_id = str(req.bridgeId or "").strip()
        if not bridge_id:
            raise HTTPException(400, "bridgeId is required")
        runtime = _normalize_runtime(req.runtime or agent["runtime"] or "pi")
        virtual_command = VIRTUAL_RPC_COMMANDS_BY_RUNTIME.get(runtime)
        if not virtual_command:
            raise HTTPException(
                409,
                f'Virtual terminal is available for runtimes {sorted(VIRTUAL_RPC_COMMANDS_BY_RUNTIME)} only (got runtime="{runtime}")',
            )

        env_row = await (await db.execute(
            "SELECT * FROM environments WHERE bridge_id = ? ORDER BY last_seen DESC LIMIT 1",
            (bridge_id,),
        )).fetchone()
        if not env_row:
            raise HTTPException(404, f'No environment registered for bridgeId "{bridge_id}"')
        environment_id = env_row["id"]

        session_row = await (await db.execute(
            """
            SELECT *
            FROM agent_sessions
            WHERE agent_id = ?
              AND environment_id = ?
              AND status IN ('running', 'recovering', 'starting', 'managed-warm')
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (agent_id, environment_id),
        )).fetchone()
        if not session_row:
            raise HTTPException(
                409,
                f'No active agent_session for "{agent_id}" on environment "{environment_id}". '
                f'The bridge should dispatch at least once before requesting a virtual terminal.',
            )
        session_id = session_row["id"]

        # Agent-scoped lookup: one virtual terminal per agent across all of
        # its agent_sessions. If a prior session created the row and is now
        # stale, re-anchor the terminal's session_id (and the new session's
        # terminal_id pointer) to the requesting session so the
        # CASCADE-on-delete FK keeps the row alive once the original
        # session row is eventually cleaned up.
        existing = await (await db.execute(
            """
            SELECT *
            FROM terminal_sessions
            WHERE agent_id = ?
              AND command = ?
              AND status NOT IN ('stopped', 'failed')
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (agent_id, virtual_command),
        )).fetchone()
        if existing:
            return await _reanchor_existing_virtual_terminal(
                db, existing, session_row, session_id, bridge_id, virtual_command,
            )

        # Plan 4 (2026-05-25) synth-terminal deprecation: when this runtime
        # routes through a *-aify wrapper PTY, the wrapper IS the terminal —
        # don't create a synth row in parallel. Reuse of a pre-existing synth
        # row (handled above) is still allowed for backwards compatibility
        # and for the hard-failure fallback path that may seed one explicitly.
        settings_for_synth_gate = await _load_settings(db)
        if not _synth_terminal_should_be_created(runtime, settings_for_synth_gate):
            raise HTTPException(
                409,
                f'Synth terminal creation skipped for wrapper-backed runtime "{runtime}" '
                f'(Plan 4 deprecation — the wrapper PTY is the terminal).',
            )

        workspace = str(req.workspace or session_row["workspace"] or "").strip()
        terminal_id = f"vterm_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        now = _now()
        requested_by = str(req.requestedBy or "bridge-rpc").strip() or "bridge-rpc"
        await db.execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime, workspace, command,
                output, status, requested_by, created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                terminal_id,
                session_id,
                agent_id,
                environment_id,
                bridge_id,
                runtime,
                workspace,
                virtual_command,
                "",
                "running",
                requested_by,
                now,
                now,
                None,
                "",
            ),
        )
        await _append_terminal_event(
            db,
            terminal_id,
            f"virtual_{runtime}_rpc_attached",
            json.dumps({
                "requestedBy": requested_by,
                "sessionId": session_id,
                "bridgeId": bridge_id,
                "sessionHandle": req.sessionHandle or "",
            }),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET terminal_id = ?,
                terminal_status = 'running',
                terminal_command = ?,
                terminal_workspace = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (terminal_id, virtual_command, workspace, now, session_id),
        )
        next_runtime_state = _json_loads_or(agent["runtime_state"], {}) or {}
        next_runtime_state["virtualTerminal"] = True
        next_runtime_state["virtualTerminalId"] = terminal_id
        await db.execute(
            """
            UPDATE agents
            SET runtime_state = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (json.dumps(next_runtime_state), now, agent_id),
        )
        # The agent now has a live worker (virtualTerminalId + terminal_status
        # running). Invalidate the live-status cache so it recomputes to online
        # immediately instead of lying `available` until the 60s sweep.
        await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        updated_session = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast(
                "terminal_started",
                {
                    "terminalId": terminal_id,
                    "sessionId": session_id,
                    "agentId": agent_id,
                    "virtual": True,
                },
            )
        return {
            "ok": True,
            "terminal": _terminal_session_to_dict(terminal),
            "session": _agent_session_to_dict(updated_session),
            "reused": False,
        }
    finally:
        await db.close()
