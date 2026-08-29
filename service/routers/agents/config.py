"""Per-agent configuration: environment assignment, runtime state, usage source, listen.

v0.5.2m, one surface of the agents package. Built with `domain_router()`;
declares NO tags — the parent applies `tags=["api"]` once when api_v2 includes the package.
"""

from __future__ import annotations

from service.api_core.runtime_state import _runtime_handle_from_state
from service.api_core.virtual_rpc import VIRTUAL_PI_RPC_COMMAND
import json
import logging
import time
from typing import Any, Optional

from fastapi import HTTPException, Request

from service.api_core.routing import domain_router

logger = logging.getLogger("aify_comms.routers.agents.config")

# Imported for the ANNOTATIONS. Under postponed evaluation these are strings, so a
# missing one does not fail import -- FastAPI demotes the body to a query parameter and
# the endpoint 422s at request time. The route annotation gate caught 17 of these here.
from service.models import AgentRuntimeStateUpdate

from service.api_core.agent_sessions import _touch_current_agent_session
from service.api_core.capabilities import _default_capabilities_for
from service.api_core.records import _terminal_session_to_dict
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.ownership_authority import patched_owner_bridge_id
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import _load_settings
from service.env_status import live_environment_bridge_ids
from service.db import get_db
from service.clock import now as _now
import sqlite3
from service.routers.agents.shared import logger

router = domain_router()


@router.patch("/agents/{agent_id}/usage-source")
async def patch_usage_source(agent_id: str, request: Request):
    """Operator override of an agent's quota-pool binding. Empty value clears the
    override, reverting to the runtime-derived source."""
    body = await request.json()
    source = str((body or {}).get("usageSource") or "").strip()
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("SELECT runtime_config FROM agents WHERE id = ?", (agent_id,))
        row = await cursor.fetchone()
        if not row:
            await db.rollback()
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        rc = _json_loads_or(row["runtime_config"], {})
        if not isinstance(rc, dict):
            rc = {}
        if source:
            rc["usageSource"] = source
        else:
            rc.pop("usageSource", None)
        await db.execute("UPDATE agents SET runtime_config = ? WHERE id = ?", (json.dumps(rc), agent_id))
        await db.commit()
        return {"ok": True, "agentId": agent_id, "usageSource": source}
    finally:
        await db.close()


@router.get("/agents/{agent_id}/pi-session-state")
async def get_agent_pi_session_state(agent_id: str):
    """Watchdog readout for omp-aify (Phase 4).

    Reports whether the aify-comms bridge currently drives this agent's pi
    session through a persistent RPC child. The omp-aify wrapper queries this
    before exec'ing omp; if the bridge owns the session it refuses to start,
    avoiding two processes racing on the same OMP session-id (the upstream
    RPC channel has no multiplexing — see DECISIONS.md). Soft mutex: this
    endpoint never kills anything. It is a fast read against
    terminal_sessions + agents.runtime_state.
    """
    db = await get_db()
    try:
        agent_row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent_row:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        runtime_state = _json_loads_or(agent_row["runtime_state"], {}) or {}
        virtual_terminal_id = str(runtime_state.get("virtualTerminalId") or "").strip()
        bridge_owned = False
        terminal_payload: Optional[dict[str, Any]] = None
        if virtual_terminal_id:
            row = await (await db.execute(
                "SELECT * FROM terminal_sessions WHERE id = ?",
                (virtual_terminal_id,),
            )).fetchone()
            if row and (row["command"] or "") == VIRTUAL_PI_RPC_COMMAND:
                status = str(row["status"] or "").strip().lower()
                if status in {"starting", "running", "recovering", "active", "idle"}:
                    bridge_owned = True
                    terminal_payload = _terminal_session_to_dict(row)
        return {
            "ok": True,
            "agentId": agent_id,
            "runtime": _normalize_runtime(agent_row["runtime"] or ""),
            "bridgeOwned": bridge_owned,
            "virtualTerminalId": virtual_terminal_id if bridge_owned else "",
            "terminal": terminal_payload,
        }
    finally:
        await db.close()

@router.patch("/agents/{agent_id}/runtime-state")
async def update_agent_runtime_state(agent_id: str, req: AgentRuntimeStateUpdate, request: Request):
    db = await get_db()
    try:
        now = _now()
        current = await (await db.execute("SELECT runtime, session_mode, session_handle, capabilities, runtime_config, runtime_state FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not current:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        next_state = dict(req.runtimeState or {})
        current_state = _json_loads_or(current["runtime_state"], {})
        # Preserve service-managed runtime_state keys that the bridge
        # doesn't know about (or won't repopulate on every PATCH).
        # Without this, a bridge PATCH from the dispatch path (which
        # only carries sessionId/sessionFile etc.) silently clobbers
        # virtualTerminalId set earlier by /virtual-terminal/ensure —
        # the dashboard Console-reattach then looks up a stale pointer.
        # Bridges that genuinely need to clear these should send
        # explicit null (handled below).
        SERVICE_MANAGED_RUNTIME_STATE_KEYS = ("virtualTerminal", "virtualTerminalId", "manualResidentCandidate")
        for key in SERVICE_MANAGED_RUNTIME_STATE_KEYS:
            if key not in next_state and key in current_state:
                next_state[key] = current_state[key]
            elif next_state.get(key) is None and key in next_state:
                # Caller explicitly passed null → honor the clear.
                next_state.pop(key, None)
        session_mode = _normalize_session_mode(current["session_mode"] or "resident")
        if session_mode == "managed":
            current_bridge = str(current_state.get("bridgeInstanceId") or "").strip()
            next_bridge = str(next_state.get("bridgeInstanceId") or "").strip()
            # The live set is only consulted when the answer can turn on it -- a genuine attempt to
            # REPLACE a recorded owner. Every other managed PATCH keeps what is stored and costs no
            # query. `patched_owner_bridge_id` stays the single decision either way.
            live_bridge_ids = ()
            if current_bridge and next_bridge and current_bridge != next_bridge:
                settings = await _load_settings(db)
                environment_rows = await (await db.execute(
                    "SELECT * FROM environments WHERE COALESCE(bridge_id, '') != ''"
                )).fetchall()
                live_bridge_ids = live_environment_bridge_ids(
                    environment_rows,
                    offline_seconds=settings.get("environment_offline_seconds", 90),
                )
            owner, _owner_reason = patched_owner_bridge_id(
                session_mode=session_mode,
                current_bridge_instance_id=current_bridge,
                incoming_bridge_instance_id=next_bridge,
                live_environment_bridge_ids=live_bridge_ids,
            )
            if owner:
                next_state["bridgeInstanceId"] = owner
            if owner == current_bridge and owner != next_bridge and current_state.get("environmentId"):
                # The claim was REFUSED, so the environment it named is refused with it. An ACCEPTED
                # claim brings its own environmentId (managed-environment-sync writes both together).
                next_state["environmentId"] = current_state.get("environmentId")
        # Automatic resident takeover is disabled. A resident bridge heartbeat
        # must not stash or preserve pending takeover state; operators flip
        # ownership explicitly with PATCH /agents/{id}/session-mode.
        next_state.pop("pendingResidentTakeover", None)
        reported_handle = _runtime_handle_from_state(current["runtime"], next_state)
        if reported_handle:
            capabilities = _default_capabilities_for(
                current["runtime"],
                current["session_mode"] or "resident",
                reported_handle,
                _json_loads_or(current["runtime_config"], {}),
            )
            await db.execute(
                "UPDATE agents SET runtime_state = ?, session_handle = ?, capabilities = ?, last_seen = ? WHERE id = ?",
                (json.dumps(next_state), reported_handle, json.dumps(capabilities), now, agent_id)
            )
        else:
            await db.execute(
                "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
                (json.dumps(next_state), now, agent_id)
            )
        await _touch_current_agent_session(db, agent_id, next_state, now)
        await db.commit()
        return {"ok": True, "agentId": agent_id, "runtimeState": next_state}
    finally:
        await db.close()
