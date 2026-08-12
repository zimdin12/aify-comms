"""The `spawn-requests` route domain: create, list, claim and update a spawn request.

v0.5.2g. FOUR HANDLERS MOVE; FOURTEEN HELPERS DO NOT. This tag is explicitly "handlers move, helpers
borrowed" and NOT "the spawn family is retired" — the reviewer required that distinction be stated
rather than implied, because fourteen undocumented borrows is how carry debt becomes invisible
permanent architecture.

Every borrow below was measured, not assumed: each still has users in domains that have not moved.
None is copied; each is reached through a function-scope import so there is exactly one owner and no
module-level cycle.

BORROW TABLE, and the retirement map that stops this being permanent:

    _default_capabilities_for               7 users   retires with: agents
    _environment_record_to_dict             7 users   retires with: agents, sessions
    _managed_via_wrapper_for_runtime        7 users   retires with: agents, dispatch, messages
    _create_dispatch_runs                   5 users   retires with: channels, dispatch, messages
    _ensure_managed_pty_for_dispatch        4 users   retires with: agents, dispatch, messages
    _runtime_state_with_handle              4 users   retires with: agents
    _apply_channel_routing_to_claude_runs   3 users   retires with: dispatch, messages
    _insert_messages_via_console            3 users   retires with: dispatch, messages
    _normalize_workspace_for_environment    3 users   retires with: sessions
    _wake_agent                             3 users   retires with: channels, dispatch, messages
    _managed_terminal_backing_enabled       2 users   retires with: dispatch, messages
    _workspace_root_for                     2 users   retires with: sessions

Read that as: most of this debt retires when `agents`, `dispatch` and `messages` move, which are the
last three domains in the plan. `_create_dispatch_runs` (233 lines) and
`_ensure_managed_pty_for_dispatch` (159) are the two that matter most; both are dispatch
orchestration the reviewer specifically ruled should NOT be pulled into a shared core, so they move
with their own domain or not at all.

`_claim_spawn_request_once` had no users outside this domain and moved with the handlers.

`update_spawn_request` is 384 lines and moves WHOLE, byte-identical. It is not method-split here —
the first method split is `get_analytics`, in its own tag, with characterization tests.

Built with `domain_router()`, and declares NO tags: the parent applies `tags=["api"]` on include.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional

from fastapi import HTTPException, Query, Request

from service import longpoll
from service.api_core.routing import domain_router
from service.api_core.runtime import (
    _normalize_runtime,
    _normalize_session_mode,
    _runtime_capability_for_environment,
)
from service.api_core.records import _environment_record_to_dict
from service.api_core.serialization import _json_loads_or
from service.api_core.capabilities import (
    _default_capabilities_for,
    _managed_via_wrapper_for_runtime,
)
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import SQLITE_CLAIM_BUSY_TIMEOUT_MS, get_db
from service.env_status import environment_effective_status as _environment_effective_status
from service.models import SpawnRequestClaim, SpawnRequestCreate, SpawnRequestUpdate
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
from service.api_core.channel_delivery import (
    _apply_channel_routing_to_claude_runs,
    _insert_messages_via_console,
)

logger = logging.getLogger("aify_comms.routers.spawn_requests")

router = domain_router()


# v0.5.2i: RETIRED BORROWS. Both were borrowed from the router until the sessions
# domain moved; their real owner is here, and this module is now that owner.
def _spawn_request_to_dict(row, spec: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    payload = {
        "id": row["id"],
        "spawnSpecId": row["spawn_spec_id"],
        "createdBy": row["created_by"] or "",
        "environmentId": row["environment_id"],
        "agentId": row["agent_id"],
        "role": row["role"] or "coder",
        "name": row["name"] or "",
        "runtime": row["runtime"],
        "workspace": row["workspace"] or "",
        "workspaceRoot": row["workspace_root"] or "",
        "initialMessage": row["initial_message"] or "",
        "priority": row["priority"] or "normal",
        "subject": row["subject"] or "",
        "mode": row["mode"] or "managed-warm",
        "resumePolicy": row["resume_policy"] or "native_first",
        "status": row["status"] or "queued",
        "claimedByBridgeId": row["claimed_by_bridge_id"] or "",
        "claimMachineId": row["claim_machine_id"] or "",
        "processId": row["process_id"] or "",
        "sessionHandle": row["session_handle"] or "",
        "sessionId": row["session_id"] or "",
        "error": row["error"] or "",
        "createdAt": row["created_at"] or "",
        "updatedAt": row["updated_at"] or "",
        "claimedAt": row["claimed_at"] or "",
        "startedAt": row["started_at"] or "",
        "finishedAt": row["finished_at"] or "",
    }
    if spec is not None:
        payload["spawnSpec"] = spec
    return payload


def _spawn_spec_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "agentId": row["agent_id"],
        "environmentId": row["environment_id"],
        "runtime": row["runtime"],
        "workspace": row["workspace"] or "",
        "model": row["model"] or "",
        "profile": row["profile"] or "",
        "mode": row["mode"] or "managed-warm",
        "systemPrompt": row["system_prompt"] or "",
        "instructions": row["standing_instructions"] or "",
        "envVars": _json_loads_or(row["env_vars"], {}),
        "channelIds": _json_loads_or(row["channel_ids"], []),
        "budgetPolicy": _json_loads_or(row["budget_policy"], {}),
        "contextPolicy": _json_loads_or(row["context_policy"], {}),
        "restartPolicy": _json_loads_or(row["restart_policy"], {}),
        "metadata": _json_loads_or(row["metadata"], {}),
        "createdAt": row["created_at"] or "",
        "updatedAt": row["updated_at"] or "",
    }

# Domain-local: after the handlers moved, nothing outside this module referenced either.
_SPAWN_TERMINAL_STATUSES = {"running", "failed", "cancelled"}
_SPAWN_MODES = {"managed-warm"}








async def _create_dispatch_runs(*a, **k):
    from service.control_plane import _create_dispatch_runs as _impl

    return await _impl(*a, **k)


async def _ensure_managed_pty_for_dispatch(*a, **k):
    from service.control_plane import _ensure_managed_pty_for_dispatch as _impl

    return await _impl(*a, **k)


def _runtime_state_with_handle(*a, **k):
    from service.control_plane import _runtime_state_with_handle as _impl

    return _impl(*a, **k)






def _normalize_workspace_for_environment(*a, **k):
    from service.control_plane import _normalize_workspace_for_environment as _impl

    return _impl(*a, **k)


def _wake_agent(*a, **k):
    from service.control_plane import _wake_agent as _impl

    return _impl(*a, **k)


def _managed_terminal_backing_enabled(*a, **k):
    from service.control_plane import _managed_terminal_backing_enabled as _impl

    return _impl(*a, **k)




def _workspace_root_for(*a, **k):
    from service.control_plane import _workspace_root_for as _impl

    return _impl(*a, **k)


@router.get("/spawn-requests")
async def list_spawn_requests(
    request: Request,
    status: Optional[str] = None,
    environmentId: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
):
    db = await get_db()
    try:
        # Read-path-write fix (2026-06-29): these two WRITE repairs used to run on EVERY dashboard
        # poll of this GET endpoint (~every 15s), opening write transactions that contended with all
        # concurrent reads — the #1 SLOW-REQ source and a "database is locked" contributor. They now
        # run in the 60s reconcile loop instead; this endpoint is a pure read.
        where = []
        params: list[Any] = []
        if status:
            where.append("sr.status = ?")
            params.append(status)
        if environmentId:
            where.append("sr.environment_id = ?")
            params.append(environmentId)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        cursor = await db.execute(
            f"""
            SELECT sr.*, ss.id AS spec_row_id
            FROM spawn_requests sr
            LEFT JOIN spawn_specs ss ON ss.id = sr.spawn_spec_id
            {where_sql}
            ORDER BY sr.created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            spec_cursor = await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (row["spawn_spec_id"],))
            spec_row = await spec_cursor.fetchone()
            result.append(_spawn_request_to_dict(row, _spawn_spec_to_dict(spec_row) if spec_row else None))
        return {"ok": True, "spawnRequests": result}
    finally:
        await db.close()


@router.post("/spawn-requests")
async def create_spawn_request(req: SpawnRequestCreate, request: Request):
    validate_name(req.agentId, "agent ID")
    normalized_runtime = _normalize_runtime(req.runtime)
    mode = str(req.mode or "managed-warm").strip()
    if mode not in _SPAWN_MODES:
        raise HTTPException(400, f'Unsupported spawn mode "{mode}"')

    db = await get_db()
    try:
        env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (req.environmentId,))
        env_row = await env_cursor.fetchone()
        if not env_row:
            raise HTTPException(404, f'Environment "{req.environmentId}" not found')
        environment = _environment_record_to_dict(env_row)
        if str(environment.get("status") or "").lower() != "online":
            raise HTTPException(409, f'Environment "{req.environmentId}" is {environment.get("status") or "unknown"}; restart its bridge before spawning.')
        runtime_capability = _runtime_capability_for_environment(environment, normalized_runtime)
        if not runtime_capability:
            raise HTTPException(400, f'Environment "{req.environmentId}" does not advertise runtime "{normalized_runtime}"')
        workspace = _normalize_workspace_for_environment(environment, req.workspace or "")
        workspace_root = _workspace_root_for(environment, workspace)
        if not workspace and workspace_root:
            workspace = workspace_root
        settings = await _load_settings(db)
        model = str(req.model or "").strip()
        if not model:
            if normalized_runtime == "codex":
                model = str(settings.get("managed_codex_model", DEFAULT_SETTINGS["managed_codex_model"])).strip()
            elif normalized_runtime == "claude-code":
                model = str(settings.get("managed_claude_model", DEFAULT_SETTINGS["managed_claude_model"])).strip()
            elif normalized_runtime == "pi":
                model = str(settings.get("managed_pi_model", DEFAULT_SETTINGS["managed_pi_model"])).strip()
        runtime_config = req.runtimeConfig or {}
        if normalized_runtime == "codex" and not str(runtime_config.get("effort") or "").strip():
            runtime_config = {**runtime_config, "effort": str(settings.get("managed_codex_effort") or DEFAULT_SETTINGS["managed_codex_effort"]).strip()}
        elif normalized_runtime == "claude-code" and not str(runtime_config.get("effort") or "").strip():
            runtime_config = {**runtime_config, "effort": str(settings.get("managed_claude_effort") or DEFAULT_SETTINGS["managed_claude_effort"]).strip()}
        elif normalized_runtime == "pi" and not str(runtime_config.get("effort") or runtime_config.get("thinking") or "").strip():
            pi_effort = str(settings.get("managed_pi_effort") or DEFAULT_SETTINGS["managed_pi_effort"]).strip()
            if pi_effort:
                runtime_config = {**runtime_config, "effort": pi_effort}
        metadata = req.metadata or {}
        if runtime_config:
            metadata = {**metadata, "runtimeConfig": runtime_config}

        now = _now()
        spec_id = f"spec_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        request_id = f"spawn_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
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
                req.agentId,
                req.environmentId,
                normalized_runtime,
                workspace,
                model,
                req.profile or "",
                mode,
                req.systemPrompt or "",
                req.instructions or "",
                json.dumps(req.envVars or {}),
                json.dumps(req.channelIds or []),
                json.dumps(req.budgetPolicy or {}),
                json.dumps(req.contextPolicy or {}),
                json.dumps(req.restartPolicy or {}),
                json.dumps(metadata),
                now,
                now,
            ),
        )
        await db.execute(
            """
            INSERT INTO spawn_requests (
                id, spawn_spec_id, created_by, environment_id, agent_id, role, name, runtime,
                workspace, workspace_root, initial_message, priority, subject, mode,
                resume_policy, status, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                request_id,
                spec_id,
                req.createdBy or "dashboard",
                req.environmentId,
                req.agentId,
                req.role or "coder",
                req.name or req.agentId,
                normalized_runtime,
                workspace,
                workspace_root,
                req.initialMessage or "",
                req.priority or "normal",
                req.subject or "",
                mode,
                req.resumePolicy or "native_first",
                "queued",
                now,
                now,
            ),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (request_id,))).fetchone()
        spec = await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (spec_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("spawn_request_created", {"spawnRequestId": request_id, "environmentId": req.environmentId})
        return {"ok": True, "spawnRequest": _spawn_request_to_dict(row, _spawn_spec_to_dict(spec))}
    finally:
        await db.close()


@router.post("/spawn-requests/claim")
async def claim_spawn_request(req: SpawnRequestClaim, request: Request):
    # Long-poll wrapper — see claim_dispatch / service/longpoll.py. Wait only when there
    # is nothing to spawn; a claimed request OR a blockedBy directive returns immediately.
    return await longpoll.longpoll(
        getattr(req, "waitMs", 0),
        lambda: _claim_spawn_request_once(req, request),
        lambda r: r.get("spawnRequest") is None and not r.get("blockedBy") and "spawnRequest" in r,
        scope="spawn",
        fallback_s=3.0,
        is_disconnected=request.is_disconnected,
        lock_result={"ok": True, "spawnRequest": None},
    )


async def _claim_spawn_request_once(req: SpawnRequestClaim, request: Request):
    db = await get_db(busy_timeout_ms=SQLITE_CLAIM_BUSY_TIMEOUT_MS)
    try:
        await db.execute("BEGIN IMMEDIATE")
        env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (req.environmentId,))
        env_row = await env_cursor.fetchone()
        if not env_row:
            await db.rollback()
            raise HTTPException(404, f'Environment "{req.environmentId}" not found')
        env_bridge_id = str(env_row["bridge_id"] or "").strip()
        if env_bridge_id and env_bridge_id != str(req.bridgeId or "").strip():
            await db.commit()
            return {
                "ok": True,
                "spawnRequest": None,
                "blockedBy": {
                    "reason": "bridge_not_current",
                    "environmentId": req.environmentId,
                    "bridgeId": req.bridgeId,
                    "currentBridgeId": env_bridge_id,
                },
            }

        row_cursor = await db.execute(
            """
            SELECT *
            FROM spawn_requests
            WHERE environment_id = ? AND status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (req.environmentId,),
        )
        row = await row_cursor.fetchone()
        if not row:
            await db.commit()
            return {"ok": True, "spawnRequest": None}

        claimed_at = _now()
        await db.execute(
            """
            UPDATE spawn_requests
            SET status = 'claimed', claimed_by_bridge_id = ?, claim_machine_id = ?,
                claimed_at = ?, updated_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (req.bridgeId, req.machineId or "", claimed_at, claimed_at, row["id"]),
        )
        await db.execute(
            "UPDATE environments SET last_seen = ? WHERE id = ?",
            (claimed_at, req.environmentId),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (row["id"],))).fetchone()
        spec_row = await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (updated["spawn_spec_id"],))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("spawn_request_claimed", {"spawnRequestId": row["id"], "environmentId": req.environmentId})
        return {"ok": True, "spawnRequest": _spawn_request_to_dict(updated, _spawn_spec_to_dict(spec_row) if spec_row else None)}
    finally:
        await db.close()


@router.patch("/spawn-requests/{spawn_request_id}")
async def update_spawn_request(spawn_request_id: str, req: SpawnRequestUpdate, request: Request):
    status_value = str(req.status or "").strip().lower()
    if status_value not in {"claimed", "starting", "running", "failed", "cancelled"}:
        raise HTTPException(400, f'Unsupported spawn request status "{req.status}"')
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (spawn_request_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f'Spawn request "{spawn_request_id}" not found')
        current_status = str(row["status"] or "").strip().lower()
        if current_status in {"failed", "cancelled"} and status_value != current_status:
            raise HTTPException(
                409,
                f'Spawn request "{spawn_request_id}" is already {current_status}; late bridge update "{status_value}" was ignored.',
            )
        if req.bridgeId and row["claimed_by_bridge_id"] and row["claimed_by_bridge_id"] != req.bridgeId:
            raise HTTPException(409, f'Spawn request "{spawn_request_id}" is claimed by another bridge')

        now = _now()
        session_id = row["session_id"] or ""
        finished_at = row["finished_at"]
        started_at = row["started_at"]
        if status_value == "starting" and not started_at:
            started_at = now
        if status_value in _SPAWN_TERMINAL_STATUSES:
            finished_at = now if status_value in {"failed", "cancelled"} else finished_at

        spec_row = await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (row["spawn_spec_id"],))).fetchone()
        if not spec_row:
            raise HTTPException(500, f'Spawn spec "{row["spawn_spec_id"]}" missing')

        runtime_state = req.runtimeState or {}
        if req.bridgeId:
            runtime_state = {**runtime_state, "bridgeInstanceId": req.bridgeId}

        if status_value == "running":
            session_id = session_id or f"sess_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            effective_session_handle = req.sessionHandle or row["session_handle"] or ""
            if effective_session_handle:
                runtime_state = _runtime_state_with_handle(row["runtime"], runtime_state, effective_session_handle)
            spec_metadata = _json_loads_or(spec_row["metadata"], {})
            runtime_config = spec_metadata.get("runtimeConfig") if isinstance(spec_metadata, dict) else {}
            if not isinstance(runtime_config, dict):
                runtime_config = {}
            agent_capabilities = _default_capabilities_for(row["runtime"], "managed", effective_session_handle, runtime_config)
            await db.execute(
                """
                INSERT INTO agents (
                    id, role, name, cwd, model, description, instructions, status, status_note,
                    runtime, machine_id, launch_mode, session_mode, session_handle, managed_by,
                    capabilities, runtime_config, runtime_state, registered_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    role = excluded.role,
                    name = excluded.name,
                    cwd = excluded.cwd,
                    model = excluded.model,
                    instructions = excluded.instructions,
                    status = excluded.status,
                    runtime = excluded.runtime,
                    machine_id = excluded.machine_id,
                    launch_mode = excluded.launch_mode,
                    session_mode = excluded.session_mode,
                    session_handle = excluded.session_handle,
                    managed_by = excluded.managed_by,
                    capabilities = excluded.capabilities,
                    runtime_config = excluded.runtime_config,
                    runtime_state = excluded.runtime_state,
                    last_seen = excluded.last_seen
                """,
                (
                    row["agent_id"],
                    row["role"] or "coder",
                    row["name"] or row["agent_id"],
                    row["workspace"] or "",
                    spec_row["model"] or "",
                    "",
                    spec_row["standing_instructions"] or "",
                    "idle",
                    "",
                    row["runtime"],
                    row["claim_machine_id"] or "",
                    "managed",
                    "managed",
                    effective_session_handle,
                    row["created_by"] or "dashboard",
                    json.dumps(agent_capabilities),
                    json.dumps(runtime_config),
                    json.dumps(runtime_state),
                    now,
                    now,
                ),
            )
            await db.execute(
                """
                INSERT OR REPLACE INTO bridge_instances (
                    id, agent_id, machine_id, runtime, session_mode, registered_at, last_seen, superseded_by, superseded_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    req.bridgeId or row["claimed_by_bridge_id"] or "",
                    row["agent_id"],
                    row["claim_machine_id"] or "",
                    row["runtime"],
                    "managed",
                    now,
                    now,
                    "",
                    None,
                ),
            )
            # UPSERT, not INSERT OR REPLACE (bughunt 2026-07-03, HIGH): a duplicate/retried
            # 'running' PATCH (routine on the slow 9p/WSL host, where the bridge marks all
            # PATCHes retriable) re-ran this block. INSERT OR REPLACE DELETES the existing
            # row on the reused session_id, and foreign_keys=ON then CASCADE-dropped the
            # live terminal_sessions + terminal_events + pending terminal_controls — the
            # dashboard showed "Console not started" for a live PTY and queued keystrokes/
            # Stop were lost. ON CONFLICT DO UPDATE omits terminal_id/terminal_status so a
            # console bound between PATCHes survives (mirrors the resident path ~15051).
            await db.execute(
                """
                INSERT INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode,
                    owner_mode, owner_bridge_id, terminal_id, terminal_status, terminal_command, terminal_workspace,
                    process_id, session_handle,
                    app_server_url, spawn_spec_id, spawn_request_id, capabilities, telemetry, status,
                    started_at, last_seen, ended_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    runtime = excluded.runtime,
                    workspace = excluded.workspace,
                    mode = excluded.mode,
                    owner_mode = excluded.owner_mode,
                    owner_bridge_id = excluded.owner_bridge_id,
                    process_id = excluded.process_id,
                    session_handle = excluded.session_handle,
                    app_server_url = excluded.app_server_url,
                    capabilities = excluded.capabilities,
                    telemetry = excluded.telemetry,
                    status = 'running',
                    last_seen = excluded.last_seen,
                    ended_at = NULL
                """,
                (
                    session_id,
                    row["agent_id"],
                    row["environment_id"],
                    row["runtime"],
                    row["workspace"] or "",
                    row["mode"] or "managed-warm",
                    "managed",
                    req.bridgeId or row["claimed_by_bridge_id"] or "",
                    "",
                    "",
                    "",
                    "",
                    req.processId or "",
                    effective_session_handle,
                    "",
                    row["spawn_spec_id"],
                    row["id"],
                    json.dumps(req.capabilities or {"persistent": True, "bridgeResume": True}),
                    json.dumps(req.telemetry or {}),
                    "running",
                    started_at or now,
                    now,
                    None,
                ),
            )
            # Migrate a live terminal orphaned by this rotation (operator-reported
            # 2026-05-31, sc-architect). A managed respawn's bridge can create the
            # visible-TUI/console terminal a few seconds BEFORE this running
            # transition mints the new session, so the live terminal stays bound to
            # the prior (about-to-be-ended) session and the new running session gets
            # terminal_id=''. The dashboard then shows "Console not started" while
            # the real TUI is alive — and the live terminal row hangs off an ended
            # session, so the FK ON DELETE CASCADE could later drop a running TUI's
            # tracking. Re-point this agent's freshest LIVE, same-bridge terminal
            # onto the new session BEFORE ending the prior sessions.
            #
            # BOUNDED BY THIS SPAWN'S OWN AGE (2026-08-03). "A few seconds BEFORE this
            # transition" was the intent but never a constraint, so the same-bridge match also
            # adopted the PREVIOUS generation's terminal — and on a Restart that is precisely
            # the terminal being killed. Live on ef-manager: the adopted terminal predated its
            # spawn request by 10h16m, the restart's own stop landed one second later, and
            # _close_active_terminal_runs_for_terminal (which keys on the CURRENT session's
            # terminal) then failed the replacement's queued brief. Every dashboard Restart
            # destroyed the brief it was created to deliver, the spawn died on "Initial brief
            # failed", and the reaper killed the leftover sidecar as a headless orphan.
            #
            # A terminal this respawn produced cannot predate the spawn request that ordered
            # it, so that is the bound: same clock (_now() on both inserts), and it still
            # admits the whole legitimate window — bridge claims, creates the terminal, then
            # PATCHes running. COALESCE keeps a row with no created_at migrating as before
            # rather than silently disabling the rescue.
            migrate_bridge_id = req.bridgeId or row["claimed_by_bridge_id"] or ""
            if migrate_bridge_id:
                live_terminal = await (await db.execute(
                    """
                    SELECT id, status, command, workspace, session_id FROM terminal_sessions
                    WHERE agent_id = ?
                      AND bridge_id = ?
                      AND id NOT LIKE 'vterm_%'
                      AND status IN ('starting', 'attached', 'running', 'active', 'idle', 'recovering')
                      AND datetime(COALESCE(NULLIF(created_at, ''), '1970-01-01'))
                          >= datetime(COALESCE(NULLIF(?, ''), '1970-01-01'))
                    ORDER BY datetime(COALESCE(updated_at, created_at, '1970-01-01')) DESC, rowid DESC
                    LIMIT 1
                    """,
                    (row["agent_id"], migrate_bridge_id, row["created_at"]),
                )).fetchone()
                if live_terminal and str(live_terminal["session_id"] or "") != session_id:
                    await db.execute(
                        "UPDATE terminal_sessions SET session_id = ? WHERE id = ?",
                        (session_id, live_terminal["id"]),
                    )
                    await db.execute(
                        """
                        UPDATE agent_sessions
                        SET terminal_id = ?, terminal_status = ?,
                            terminal_command = ?, terminal_workspace = ?,
                            -- Binding a LIVE terminal is the authoritative "backing (re)started"
                            -- event: promote a dead-state denorm back to running, else the row
                            -- keeps the PREVIOUS backing's 'stopped' and the Console label reads
                            -- "Console stopped" for a live attached terminal forever (cms-manager,
                            -- 2026-06-10; the display deriver deliberately never promotes).
                            status = CASE WHEN status IN ('stopped','ended','failed','lost','cancelled','completed')
                                          THEN 'running' ELSE status END,
                            ended_at = CASE WHEN status IN ('stopped','ended','failed','lost','cancelled','completed')
                                            THEN NULL ELSE ended_at END
                        WHERE id = ?
                        """,
                        (
                            live_terminal["id"],
                            live_terminal["status"] or "",
                            live_terminal["command"] or "",
                            live_terminal["workspace"] or "",
                            session_id,
                        ),
                    )
            await db.execute(
                """
                UPDATE agent_sessions
                SET status = 'ended',
                    ended_at = COALESCE(NULLIF(ended_at, ''), ?),
                    last_seen = COALESCE(NULLIF(ended_at, ''), NULLIF(last_seen, ''), ?)
                WHERE agent_id = ?
                  AND id != ?
                  AND status IN ('starting', 'running', 'recovering', 'restarting')
                """,
                (now, now, row["agent_id"], session_id),
            )
            if row["status"] != "running" and str(row["initial_message"] or "").strip():
                settings_for_runs = await _load_settings(db)
                runs = await _create_dispatch_runs(
                    db,
                    [row["agent_id"]],
                    from_agent=row["created_by"] or "dashboard",
                    message_type="request",
                    subject=row["subject"] or f"Spawn {row['agent_id']}",
                    body=row["initial_message"],
                    priority=row["priority"] or "normal",
                    in_reply_to=None,
                    dispatch_mode="start_if_possible",
                    execution_mode=(
                        "channel"
                        if _managed_via_wrapper_for_runtime(settings_for_runs, row["runtime"] or "")
                        else "managed"
                    ),
                    requested_runtime=row["runtime"],
                    message_id=None,
                    require_reply=True,
                )
                # Spawn-time initial-message dispatches for managed claude
                # must honor insert_messages_via_console=false (the channel-
                # route default). Deep-test caught this earlier — without
                # the helper here e2e-test-claude's initial run stayed
                # execution_mode='managed' and claude-channel.js never
                # claimed it.
                await _apply_channel_routing_to_claude_runs(db, runs, settings_for_runs)
                for run in runs:
                    _wake_agent(run["targetAgentId"])

            # Slices 1/2/4 (architectural): when managed_terminal_backing
            # is enabled, proactively launch the wrapper PTY for this
            # newly-registered managed agent. The wrapper stays alive
            # across dispatches; subsequent sends reuse it via slice 3's
            # console-attach reuse + the existing
            # _active_terminal_for_agent lookup in
            # _ensure_managed_pty_for_dispatch. Operator-visible win: no
            # "console pops up when I send" — the console pre-exists by
            # the time the first dispatch arrives. Best-effort: a
            # wrapper-launch failure here does NOT fail the spawn-request
            # running transition (the dispatch path's lazy spawn is the
            # fallback).
            settings_for_pty = await _load_settings(db)
            _is_claude_managed = _normalize_runtime(row["runtime"]) == "claude-code"
            _eager_flag = bool(settings_for_pty.get("managed_pty_eager_spawn", DEFAULT_SETTINGS["managed_pty_eager_spawn"]))
            # When insert_messages_via_console=false (the default), managed
            # claude needs a wrapper PTY hosting claude-aify so its
            # claude-channel.js child polls /dispatch/claim for this
            # specific agent. Without it, channel dispatches sit queued
            # forever (originally observed in run_1779309370301).
            _claude_needs_wrapper = _is_claude_managed and not _insert_messages_via_console(settings_for_pty)
            # Unified-backing refactor 2026-05-24: when this runtime is
            # wrapper-backed, the wrapper PTY MUST pre-exist by spawn-request
            # running transition — otherwise nothing claims dispatches (the
            # main bridge dispatch loop drops 'managed' from supportedExecutionModes
            # for this runtime, and the wrapper's child bridge doesn't exist
            # until the PTY launches).
            _wrapper_backed = _managed_via_wrapper_for_runtime(settings_for_pty, row["runtime"] or "")
            if _managed_terminal_backing_enabled(settings_for_pty) and (_eager_flag or _claude_needs_wrapper or _wrapper_backed):
                try:
                    await _ensure_managed_pty_for_dispatch(
                        db,
                        row["agent_id"],
                        runtime=row["runtime"],
                        settings=settings_for_pty,
                        requested_by="spawn-request",
                        # Scope adoption to THIS spawn's session. Without it a restart adopts the
                        # outgoing worker's terminal — which is killed two seconds later — and the
                        # agent ends up `running` with no worker at all. Reproduced live.
                        for_session_id=str(row["session_id"] or ""),
                    )
                except Exception as exc:
                    # The dispatch path's lazy spawn is still the fallback — this must never fail a
                    # spawn-request transition. But it must not be SILENT either: a bare `pass` here
                    # hid an AttributeError of mine for two live restarts, during which the operator
                    # saw an agent with no worker and the logs said nothing at all. A best-effort
                    # step that fails invisibly is indistinguishable from one that had nothing to do.
                    logger.warning(
                        "eager managed PTY for %s failed (%s: %s); falling back to lazy spawn on dispatch",
                        row["agent_id"], type(exc).__name__, exc,
                    )

        # TOCTOU guard (bughunt 2026-07-03): the status check above read `current_status`
        # ONCE; between that read and this write a concurrent operator Stop/CLI-takeover
        # can commit status='cancelled'. Without a WHERE guard this write would clobber it
        # back to 'running' AFTER the PTY was already spawned — silently losing the Stop and
        # leaving a live zombie worker. Make the write CONDITIONAL on the row not already
        # being terminal; a 0-rowcount means a concurrent finalize won, so we return that
        # real state instead of the phantom success (and skip the running/registered casts).
        upd = await db.execute(
            """
            UPDATE spawn_requests
            SET status = ?, process_id = ?, session_handle = ?, session_id = ?, error = ?,
                updated_at = ?, started_at = ?, finished_at = ?
            WHERE id = ? AND status NOT IN ('cancelled', 'failed')
            """,
            (
                status_value,
                req.processId or row["process_id"] or "",
                req.sessionHandle or row["session_handle"] or "",
                session_id,
                req.error or "",
                now,
                started_at,
                finished_at,
                spawn_request_id,
            ),
        )
        await db.commit()
        if (upd.rowcount or 0) == 0 and status_value not in {"cancelled", "failed"}:
            # A concurrent Stop/fail finalized the row first — honor it, don't resurrect.
            concurrent = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (spawn_request_id,))).fetchone()
            concurrent_status = str((concurrent["status"] if concurrent else "") or "").strip().lower()
            if concurrent_status in {"cancelled", "failed"}:
                raise HTTPException(
                    409,
                    f'Spawn request "{spawn_request_id}" was concurrently {concurrent_status}; the "{status_value}" update was dropped to avoid resurrecting a stopped worker.',
                )
        updated = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (spawn_request_id,))).fetchone()
        updated_spec = await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (updated["spawn_spec_id"],))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("spawn_request_updated", {"spawnRequestId": spawn_request_id, "status": status_value})
            if status_value == "running":
                await ws.broadcast("agent_registered", {"agentId": row["agent_id"], "runtime": row["runtime"], "sessionMode": "managed"})
                if row["status"] != "running" and str(row["initial_message"] or "").strip():
                    await ws.broadcast("dispatch_queued", {"targetAgentId": row["agent_id"]})
        return {"ok": True, "spawnRequest": _spawn_request_to_dict(updated, _spawn_spec_to_dict(updated_spec) if updated_spec else None)}
    finally:
        await db.close()
