"""Per-agent configuration: environment assignment, runtime state, usage source, listen.

v0.5.2m, one surface of the agents package. Built with `domain_router()`;
declares NO tags — the parent applies `tags=["api"]` once when api_v2 includes the package.
"""

from __future__ import annotations

from service.api_core.virtual_rpc import VIRTUAL_PI_RPC_COMMAND
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException, Query, Request

from service.api_core.routing import domain_router

logger = logging.getLogger("aify_comms.routers.agents.config")

# Imported for the ANNOTATIONS. Under postponed evaluation these are strings, so a
# missing one does not fail import -- FastAPI demotes the body to a query parameter and
# the endpoint 422s at request time. The route annotation gate caught 17 of these here.
from service.models import AgentEnvironmentAssignRequest, AgentRuntimeStateUpdate

from service.routers.agents.shared import (
    DEFAULT_SETTINGS,
    LIVE_SESSION_STATUSES,
    _SESSION_MODES,
    _agent_liveness,
    _agent_record_to_dict,
    _agent_session_to_dict,
    _agent_tombstone,
    _append_dispatch_control,
    _append_dispatch_event,
    _append_terminal_control,
    _append_terminal_event,
    _apply_status_event,
    _auto_return_resident_to_managed_if_possible,
    _borrowed_console_tail_max_bytes,
    _borrowed_console_tail_max_lines,
    _borrowed_list_agents_refresh_limit,
    _borrowed_listen_events,
    _borrowed_live_session_statuses,
    _borrowed_manual_statuses,
    _borrowed_runtime_config_live_keys,
    _borrowed_shell_placeholder_handle_re,
    _broadcast_agent_status,
    _broadcast_engine_status,
    _clear_status_state_in_turn,
    _coldstart_refusal_message,
    _compute_agent_status,
    _compute_live_status_cache,
    _default_capabilities_for,
    _environment_effective_status,
    _environment_record_to_dict,
    _fail_active_runs_for_superseded_bridges,
    _get_blocking_active_run,
    _get_dispatch_state_for_agent,
    _get_dispatch_state_map,
    _get_outbound_activity_map,
    _get_unread_count_map,
    _get_ws,
    _has_codex_live_app_server,
    _has_live_terminal_session,
    _has_pending_or_booting_spawn_request,
    _invalidate_agent_live_state,
    _is_lock_error,
    _iso_to_epoch,
    _json_loads_or,
    _live_state_get,
    _load_settings,
    _managed_owning_environment_row,
    _managed_via_wrapper_for_runtime,
    _merge_runtime_policy_for_wrapper_reregister,
    _normalize_machine_id,
    _normalize_runtime,
    _normalize_session_mode,
    _now,
    _record_bridge_registration,
    _record_channel_sidecar_heartbeat,
    _record_claimer_lease,
    _refresh_expired_agent_live_states,
    _render_live_terminal_screen,
    _render_terminal_snapshot,
    _repair_unusable_active_runs,
    _row_status_note,
    _runtime_capability_for_environment,
    _runtime_handle_from_state,
    _runtime_state_replacing_handle,
    _runtime_state_with_handle,
    _sanitize_session_handle,
    _session_capabilities_replacing_handle,
    _session_handle_live_owner,
    _stop_virtual_terminals_for_superseded_bridges,
    _synth_terminal_should_be_created,
    _terminal_failure_line,
    _terminal_failure_tail,
    _terminal_session_to_dict,
    _timestamp_sort_key,
    _touch_current_agent_session,
    _upsert_resident_agent_session,
    apply_event,
    derive,
    engine_status,
    get_db,
    logger,
    re,
    sqlite3,
    validate_name,
)
from service.api_core.workspace import _workspace_for_environment
from service.api_core.terminal_ownership import _active_terminal_for_agent
from service.api_core.dispatch_start import (
    _coldstart_spawn_request_for_dispatch,
    _ensure_managed_pty_for_dispatch,
)
from service.api_core.registration_gates import (
    _enforce_env_reachable_gate,
    _enforce_live_worker_gate,
    _fresh_same_mode_bridge_conflict,
    _machine_family,
    _validate_registration_cwd,
)
from service.api_core.agent_terminal_ops import (
    _request_stop_agent_terminals,
    _resolve_live_console_terminal,
)
from service.api_core.agent_sessions import _adopt_live_resident_driver
from service.api_core.agent_removal import _remove_agent_record

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


@router.post("/agents/{agent_id}/environment")
async def assign_agent_environment(agent_id: str, req: AgentEnvironmentAssignRequest, request: Request):
    validate_name(agent_id, "agent ID")
    environment_id = str(req.environmentId or "").strip()
    if not environment_id:
        raise HTTPException(400, "environmentId is required")

    db = await get_db()
    try:
        agent_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        agent = await agent_cursor.fetchone()
        if not agent:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
        env_row = await env_cursor.fetchone()
        if not env_row:
            raise HTTPException(404, f'Environment "{environment_id}" not found')
        environment = _environment_record_to_dict(env_row)
        if str(environment.get("status") or "").lower() != "online":
            raise HTTPException(409, f'Environment "{environment_id}" is {environment.get("status") or "unknown"}, not online')

        runtime = _normalize_runtime(req.runtime or agent["runtime"] or "generic")
        if not _runtime_capability_for_environment(environment, runtime):
            raise HTTPException(400, f'Environment "{environment_id}" does not advertise runtime "{runtime}"')
        workspace, workspace_root = _workspace_for_environment(environment, req.workspace, agent["cwd"] or "")
        settings = await _load_settings(db)
        model = str(req.model if req.model is not None else (agent["model"] or "")).strip()
        if not model:
            if runtime == "codex":
                model = str(settings.get("managed_codex_model", DEFAULT_SETTINGS["managed_codex_model"])).strip()
            elif runtime == "claude-code":
                model = str(settings.get("managed_claude_model", DEFAULT_SETTINGS["managed_claude_model"])).strip()
            elif runtime == "pi":
                model = str(settings.get("managed_pi_model", DEFAULT_SETTINGS["managed_pi_model"])).strip()
        existing_runtime_config = _json_loads_or(agent["runtime_config"], {})
        requested_runtime_config = req.runtimeConfig or {}
        runtime_config = {**existing_runtime_config, **requested_runtime_config}
        if runtime == "codex" and not str(runtime_config.get("effort") or "").strip():
            runtime_config = {**runtime_config, "effort": str(settings.get("managed_codex_effort") or DEFAULT_SETTINGS["managed_codex_effort"]).strip()}
        elif runtime == "claude-code" and not str(runtime_config.get("effort") or "").strip():
            runtime_config = {**runtime_config, "effort": str(settings.get("managed_claude_effort") or DEFAULT_SETTINGS["managed_claude_effort"]).strip()}
        elif runtime == "pi" and not str(runtime_config.get("effort") or runtime_config.get("thinking") or "").strip():
            pi_effort = str(settings.get("managed_pi_effort") or DEFAULT_SETTINGS["managed_pi_effort"]).strip()
            if pi_effort:
                runtime_config = {**runtime_config, "effort": pi_effort}
        now = _now()
        previous_runtime = _normalize_runtime(agent["runtime"] or runtime)
        latest_session = await (await db.execute(
            """
            SELECT *
            FROM agent_sessions
            WHERE agent_id = ?
            ORDER BY
                CASE WHEN COALESCE(NULLIF(session_handle, ''), '') != '' THEN 0 ELSE 1 END,
                last_seen DESC
            LIMIT 1
            """,
            (agent_id,),
        )).fetchone()
        latest_session_handle = str((latest_session["session_handle"] if latest_session else "") or "").strip()
        agent_runtime_state = _json_loads_or(agent["runtime_state"], {})
        state_handle = _runtime_handle_from_state(previous_runtime, agent_runtime_state)
        preserve_handle = ""
        if previous_runtime == runtime:
            preserve_handle = str(agent["session_handle"] or latest_session_handle or state_handle or "").strip()
        preserved_runtime_state = _runtime_state_with_handle(runtime, {}, preserve_handle)

        spec_cursor = await db.execute(
            "SELECT * FROM spawn_specs WHERE agent_id = ? ORDER BY updated_at DESC LIMIT 1",
            (agent_id,),
        )
        spec = await spec_cursor.fetchone()
        if spec:
            spec_id = spec["id"]
            await db.execute(
                """
                UPDATE spawn_specs
                SET environment_id = ?, runtime = ?, workspace = ?, model = ?, metadata = ?, updated_at = ?
                WHERE agent_id = ?
                """,
                (
                    environment_id,
                    runtime,
                    workspace,
                    model,
                    json.dumps({**_json_loads_or(spec["metadata"], {}), **({"runtimeConfig": runtime_config} if runtime_config else {})}),
                    now,
                    agent_id,
                ),
            )
        else:
            spec_id = f"spec_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            await db.execute(
                """
                INSERT INTO spawn_specs (
                    id, agent_id, environment_id, runtime, workspace, model, profile, mode,
                    system_prompt, standing_instructions, env_vars, channel_ids, budget_policy,
                    context_policy, restart_policy, metadata, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    spec_id,
                    agent_id,
                    environment_id,
                    runtime,
                    workspace,
                    model,
                    "",
                    "managed-warm",
                    "",
                    agent["instructions"] or "",
                    "{}",
                    "[]",
                    "{}",
                    "{}",
                    "{}",
                    json.dumps({"createdBy": req.requestedBy or "dashboard", "assignedFromDashboard": True, **({"runtimeConfig": runtime_config} if runtime_config else {})}),
                    now,
                    now,
                ),
            )

        await db.execute(
            """
            UPDATE agent_sessions
            SET environment_id = ?,
                runtime = ?,
                workspace = ?,
                session_handle = ?,
                spawn_spec_id = COALESCE(NULLIF(spawn_spec_id, ''), ?),
                status = CASE WHEN status IN ('starting','running','recovering','restarting') THEN 'lost' ELSE status END,
                ended_at = CASE WHEN status IN ('starting','running','recovering','restarting') THEN COALESCE(ended_at, ?) ELSE ended_at END,
                last_seen = ?
            WHERE agent_id = ?
            """,
            (environment_id, runtime, workspace, preserve_handle, spec_id, now, now, agent_id),
        )
        session_cursor = await db.execute(
            "SELECT id FROM agent_sessions WHERE agent_id = ? ORDER BY last_seen DESC LIMIT 1",
            (agent_id,),
        )
        existing_session = await session_cursor.fetchone()
        if not existing_session:
            session_id = f"sess_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            await db.execute(
                """
                INSERT INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode,
                    owner_mode, owner_bridge_id, terminal_id, terminal_status, terminal_command, terminal_workspace,
                    process_id, session_handle,
                    app_server_url, spawn_spec_id, spawn_request_id, capabilities, telemetry, status,
                    started_at, last_seen, ended_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id,
                    agent_id,
                    environment_id,
                    runtime,
                    workspace,
                    "managed-warm",
                    "managed",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    preserve_handle,
                    "",
                    spec_id,
                    None,
                    json.dumps({"persistent": True, "nativeResume": bool(preserve_handle), "bridgeResume": True, "adopted": True}),
                    "{}",
                    "stopped",
                    now,
                    now,
                    now,
                ),
            )
        await db.execute(
            """
            UPDATE spawn_requests
            SET environment_id = ?,
                runtime = ?,
                workspace = ?,
                workspace_root = ?,
                updated_at = ?
            WHERE agent_id = ?
              AND status IN ('queued','claimed','starting')
            """,
            (environment_id, runtime, workspace, workspace_root, now, agent_id),
        )
        capabilities = _default_capabilities_for(runtime, "managed", preserve_handle, runtime_config)
        await db.execute(
            """
            UPDATE agents
            SET cwd = ?,
                model = ?,
                runtime = ?,
                machine_id = ?,
                launch_mode = 'none',
                session_mode = 'managed',
                session_handle = ?,
                capabilities = ?,
                runtime_config = ?,
                runtime_state = ?,
                status = CASE WHEN status = 'stopped' THEN status ELSE 'offline' END,
                last_seen = ?
            WHERE id = ?
            """,
            (
                workspace,
                model,
                runtime,
                _normalize_machine_id(environment.get("machineId")),
                preserve_handle,
                json.dumps(capabilities),
                json.dumps(runtime_config),
                json.dumps(preserved_runtime_state),
                now,
                agent_id,
            ),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_environment_assigned", {"agentId": agent_id, "environmentId": environment_id})
        return {
            "ok": True,
            "agentId": agent_id,
            "environmentId": environment_id,
            "runtime": runtime,
            "workspace": workspace,
            "spawnSpecId": spec_id,
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
        if _normalize_session_mode(current["session_mode"] or "resident") == "managed":
            current_bridge = str(current_state.get("bridgeInstanceId") or "").strip()
            next_bridge = str(next_state.get("bridgeInstanceId") or "").strip()
            if current_bridge and next_bridge and current_bridge != next_bridge:
                next_state["bridgeInstanceId"] = current_bridge
                if current_state.get("environmentId"):
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


@router.get("/agents/{agent_id}/listen")
async def listen_for_messages(agent_id: str, request: Request, timeout: int = Query(300, ge=1, le=600)):
    """Long-poll: blocks until agent has unread messages or timeout. Returns the messages."""
    validate_name(agent_id, "agent ID")

    # Set status to idle (waiting for work)
    db = await get_db()
    try:
        await db.execute("UPDATE agents SET status = 'idle', last_seen = ? WHERE id = ?", (_now(), agent_id))
        await db.commit()
    finally:
        await db.close()

    # Create/get wake-up event for this agent
    if agent_id not in _borrowed_listen_events():
        _borrowed_listen_events()[agent_id] = asyncio.Event()
    event = _borrowed_listen_events()[agent_id]
    event.clear()

    # Poll for unread messages, waiting on the event
    deadline = time.time() + timeout
    while time.time() < deadline:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM messages m LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ? WHERE m.to_agent = ? AND r.message_id IS NULL",
                (agent_id, agent_id)
            )
            unread = (await cursor.fetchone())[0]
            if unread > 0:
                # Fetch and return the messages (mark as read)
                now = _now()
                mc = await db.execute(
                    "SELECT m.* FROM messages m LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ? WHERE m.to_agent = ? AND r.message_id IS NULL ORDER BY m.timestamp DESC",
                    (agent_id, agent_id)
                )
                rows = await mc.fetchall()
                messages = []
                for row in rows:
                    msg = {
                        "id": row["id"], "from": row["from_agent"], "type": row["type"],
                        "source": row["source"], "channel": row["channel"],
                        "subject": row["subject"], "body": row["body"],
                        "priority": row["priority"], "timestamp": row["timestamp"],
                        "inReplyTo": row["in_reply_to"],
                        "dispatchRequested": bool(row["dispatch_requested"]) if "dispatch_requested" in row.keys() else False,
                    }
                    # Parent context for replies
                    if row["in_reply_to"]:
                        pc = await db.execute("SELECT from_agent, subject, body FROM messages WHERE id = ?", (row["in_reply_to"],))
                        parent = await pc.fetchone()
                        if parent:
                            msg["parentContext"] = {"from": parent["from_agent"], "subject": parent["subject"], "preview": (parent["body"] or "")[:100]}
                    messages.append(msg)
                    await db.execute("INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)", (row["id"], agent_id, now))

                # Set status to working
                await db.execute("UPDATE agents SET status = 'working', last_seen = ? WHERE id = ?", (now, agent_id))
                await db.commit()
                return {"total": len(messages), "messages": messages}
        finally:
            await db.close()

        # Wait for wake-up signal or check every 2 seconds
        try:
            await asyncio.wait_for(event.wait(), timeout=2.0)
            event.clear()
        except asyncio.TimeoutError:
            pass

    # Timeout — no messages arrived
    return {"total": 0, "messages": []}
