"""Agent identity: registration, listing, lookup, rename, description, favourite, removal.

`register_agent` (684 lines) is the largest handler in the product and moves WHOLE here.

v0.5.2m, one surface of the agents package. Built with `domain_router()`;
declares NO tags — the parent applies `tags=["api"]` once when api_v2 includes the package.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException, Query, Request

from service.api_core.runtime_state import _runtime_state_with_handle
from service.api_core.routing import domain_router

logger = logging.getLogger("aify_comms.routers.agents.identity")

# Imported for the ANNOTATIONS. Under postponed evaluation these are strings, so a
# missing one does not fail import -- FastAPI demotes the body to a query parameter and
# the endpoint 422s at request time. The route annotation gate caught 17 of these here.
from service.models import (
    AgentDescribeRequest,
    AgentFavoriteUpdate,
    AgentRegister,
    AgentRenameRequest,
    AgentStatusUpdate,
)

from service.api_core.agent_sessions import _record_registered_session_handle
from service.api_core.registration_gates import (
    _enforce_driving_mode_switch_gate,
    _enforce_tombstone_registration_gate,
    _enforce_same_mode_bridge_gate,
    _enforce_tombstone_resurrection_gate,
)
from service.api_core.resume_command import _resume_command_for
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
from service.api_core.channel_delivery import _CHANNEL_CLAIM_RUNTIMES
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


@router.get("/agents")
async def list_agents(request: Request):
    db = await get_db()
    try:
        settings = await _load_settings(db)
        # The cache refresh/repair below is BEST-EFFORT: a SELECT never takes SQLite's write
        # lock (WAL), so when the single writer is briefly contended we serve slightly-stale
        # cached rows instead of 503ing the whole roster — a 503 here broke the dashboard load
        # entirely (the browser surfaces it as "Failed to fetch"). The 60s reconcile sweep
        # persists the refresh on its next pass. (2026-06-18 — read paths must never 503 on a lock.)
        try:
            repaired_active_runs = await _repair_unusable_active_runs(db)
            refreshed_live_states = await _refresh_expired_agent_live_states(db, settings=settings, limit=_borrowed_list_agents_refresh_limit())
            if repaired_active_runs or refreshed_live_states:
                await db.commit()
        except sqlite3.OperationalError as exc:
            if not _is_lock_error(exc):
                raise
            try:
                await db.rollback()
            except Exception:
                pass
        cursor = await db.execute("SELECT * FROM agents")
        agents = await cursor.fetchall()
        agent_ids = [row["id"] for row in agents]
        unread_map = await _get_unread_count_map(db, agent_ids)
        dispatch_map = await _get_dispatch_state_map(db, agent_ids)
        # Roster: cheap half only — see include_runs.
        outbound_map = await _get_outbound_activity_map(db, agent_ids, include_runs=False)
        result = {}
        for row in agents:
            aid = row["id"]
            entry = _live_state_get(aid) or {}
            payload = _agent_record_to_dict(row, entry.get("status") or row["status"], unread_map.get(aid, 0), dispatch_map.get(aid), live_reason=entry.get("reason"), outbound=outbound_map.get(aid))
            # Plan 5 Section C: read-path live-worker gate — see
            # _enforce_live_worker_gate for full rationale. (In-memory correction
            # only; the writeback was removed 2026-06-18 to cut read-path writes.)
            payload = await _enforce_live_worker_gate(payload, db, settings, aid)
            payload = await _enforce_env_reachable_gate(payload, db, settings, aid)
            result[aid] = payload
        return {"agents": result}
    finally:
        await db.close()


@router.post("/agents")
async def register_agent(req: AgentRegister, request: Request):
    validate_name(req.agentId, "agent ID")
    db = await get_db()
    try:
        normalized_runtime = _normalize_runtime(req.runtime or "generic")
        normalized_session_mode = _normalize_session_mode(req.sessionMode or "resident")
        resolved_cwd = req.cwd or ""
        runtime_config = req.runtimeConfig or {}
        _validate_registration_cwd(
            agent_id=req.agentId,
            runtime=normalized_runtime,
            session_mode=normalized_session_mode,
            machine_id=req.machineId or "",
            cwd=resolved_cwd,
            runtime_config=runtime_config,
        )
        now = _now()
        tombstone = await _agent_tombstone(db, req.agentId)
        await _enforce_tombstone_registration_gate(req, tombstone)
        await _enforce_tombstone_resurrection_gate(db, req, tombstone)
        existing = await db.execute("SELECT * FROM agents WHERE id = ?", (req.agentId,))
        row = await existing.fetchone()
        bridge_id = (req.bridgeId or "").strip()
        terminal_id = str(req.terminalId or "").strip()
        # Mutual-exclusion collision guard (Task 4.1, 2026-05-30). One-driver
        # invariant: at most one driver per session at a time. If a process tries
        # to attach in a DIFFERENT session_mode than the one currently DRIVING
        # the session, reject with an actionable error so the operator switches
        # mode in the dashboard first (which releases the prior driver) rather
        # than silently colliding N wrappers / overwriting an active session.
        #
        # Scope: the guard fires ONLY on a cross-mode attach to a session that
        # is actively `driving`. Two cases are deliberately NOT hard-rejected
        # here because each is handled gracefully elsewhere, preserving the
        # invariant without an error:
        #   - SAME-mode re-attach/supersession by the same logical agent (a
        #     managed restart, or a second resident window) -> existing
        #     machine_id bridge supersession.
        #   - a RESIDENT registration against a DRIVING MANAGED agent -> the
        #     established `manualResidentCandidate` flow below parks the resident
        #     and returns `ownershipTransition=manual_switch_required` (it never
        #     lets the resident drive; the operator switches in the dashboard).
        # That leaves the genuinely-unhandled collision — a MANAGED registration
        # against a DRIVING RESIDENT session (which would otherwise silently
        # overwrite the live resident driver) — which is hard-rejected here.
        await _enforce_driving_mode_switch_gate(req, row, normalized_runtime, normalized_session_mode)
        # Same-mode race guard (Phase 4, 2026-05-31). A fresh resident bridge of
        # the SAME mode, owned by a DIFFERENT bridge_id, is already driving this
        # identity — a second live wrapper would race it. Hard-reject (operator-
        # chosen) unless force=true: the operator deliberately takes over after
        # restarting the prior wrapper (wrappers surface this via the
        # AIFY_FORCE_REGISTER escape hatch). Stale prior bridges fall through and
        # are superseded normally (self-heal). Same-process periodic re-register
        # keeps its bridge_id and is excluded by `id != ?` in the helper.
        # NB: do NOT gate this on restoreDeleted — the bridge's auto-register
        # sends restoreDeleted=true unconditionally, so gating here would make
        # the guard dead in production. Restoring a tombstone is orthogonal: a
        # tombstoned agent has no live bridge to conflict with, so the freshness
        # check below simply finds nothing and the register proceeds.
        await _enforce_same_mode_bridge_gate(
            db, req, row, bridge_id, normalized_runtime, normalized_session_mode, logger
        )
        managed_wrapper_child = bool(req.managedWrapperChild) or (
            normalized_session_mode == "managed"
            and bool(terminal_id)
            and normalized_runtime in _CHANNEL_CLAIM_RUNTIMES
        )
        if managed_wrapper_child and row:
            runtime_config = _merge_runtime_policy_for_wrapper_reregister(
                _json_loads_or(row["runtime_config"], {}),
                runtime_config,
            )
        model_value = req.model or ""
        if managed_wrapper_child and not model_value and row and "model" in row.keys():
            model_value = row["model"] or ""
        # Re-register is a full state refresh: sessionHandle and runtime_state come
        # from the new request only. Preserving them across re-register let stale
        # Codex thread IDs survive a fresh codex-aify start, which then made
        # thread/resume fail with AbsolutePathBuf or "no rollout found".
        # Reject unexpanded shell placeholders (e.g. "$HERMES_SESSION_ID") so a
        # literal never gets stored as the resume handle — see
        # _sanitize_session_handle.
        session_handle = _sanitize_session_handle(req.sessionHandle or "")
        existing_state = json.dumps(_runtime_state_with_handle(normalized_runtime, {}, session_handle))
        # Description is team-facing metadata that survives re-register when the
        # caller does not pass a new value. Passing "" explicitly clears it.
        if req.description is None:
            description_value = (row["description"] if row and "description" in row.keys() else "") or ""
        else:
            description_value = req.description
        capabilities = req.capabilities
        if capabilities is None:
            capabilities = _default_capabilities_for(normalized_runtime, normalized_session_mode, session_handle, runtime_config)
        console_terminal = None
        if terminal_id and normalized_session_mode == "resident":
            console_terminal = await (
                await db.execute(
                    """
                    SELECT *
                    FROM terminal_sessions
                    WHERE id = ?
                      AND agent_id = ?
                      AND status IN ('starting','attached','running','active','idle')
                    """,
                    (terminal_id, req.agentId),
                )
            ).fetchone()
        if console_terminal:
            existing_mode = _normalize_session_mode((row["session_mode"] if row else "") or "managed")
            existing_state = _json_loads_or((row["runtime_state"] if row else "") or "{}", {})
            existing_capabilities = (row["capabilities"] if row and "capabilities" in row.keys() else "") or json.dumps(capabilities or [])
            existing_runtime_config = (row["runtime_config"] if row and "runtime_config" in row.keys() else "") or json.dumps(runtime_config)
            next_state = _runtime_state_with_handle(normalized_runtime, existing_state, session_handle)
            next_state["consoleTerminal"] = {
                "terminalId": terminal_id,
                "bridgeId": bridge_id,
                "sessionHandle": session_handle,
                "at": now,
            }
            await db.execute(
                """
                UPDATE agents
                SET role = ?,
                    name = ?,
                    cwd = ?,
                    runtime = ?,
                    machine_id = ?,
                    session_handle = CASE WHEN ? != '' THEN ? ELSE session_handle END,
                    capabilities = ?,
                    runtime_config = ?,
                    runtime_state = ?,
                    status = CASE WHEN status = 'stopped' THEN status ELSE 'active' END,
                    status_note = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    req.role,
                    req.name or req.agentId,
                    resolved_cwd,
                    normalized_runtime,
                    req.machineId or "",
                    session_handle,
                    session_handle,
                    existing_capabilities,
                    existing_runtime_config,
                    json.dumps(next_state),
                    "Dashboard Console PTY attached.",
                    now,
                    req.agentId,
                ),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET owner_mode = 'console',
                    owner_bridge_id = ?,
                    terminal_id = ?,
                    terminal_status = ?,
                    session_handle = CASE WHEN ? != '' THEN ? ELSE session_handle END,
                    -- A live console PTY attaching IS the authoritative "backing (re)started"
                    -- event: promote a dead-state denorm back to running, else the session row
                    -- stays 'stopped' from the PREVIOUS backing's death and the Console label
                    -- reads "Console stopped" for a live attached terminal forever (cms-manager,
                    -- 2026-06-10 — the display deriver deliberately never promotes, so the bind
                    -- moment must). Operator disable is enforced on agents.status, not here.
                    status = CASE WHEN status IN ('cli-takeover','stopped','ended','failed','lost','cancelled','completed')
                                  THEN 'running' ELSE status END,
                    ended_at = CASE WHEN status IN ('cli-takeover','stopped','ended','failed','lost','cancelled','completed')
                                    THEN NULL ELSE ended_at END,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    console_terminal["bridge_id"] or "",
                    terminal_id,
                    console_terminal["status"] or "attached",
                    session_handle,
                    session_handle,
                    now,
                    console_terminal["session_id"],
                ),
            )
            if bridge_id:
                await _record_bridge_registration(
                    db,
                    bridge_id=bridge_id,
                    agent_id=req.agentId,
                    machine_id=req.machineId or "",
                    runtime=normalized_runtime,
                    session_mode="managed",
                    session_handle=session_handle,
                    terminal_id=terminal_id,
                    now=now,
                )
            await _invalidate_agent_live_state(db, req.agentId)
            await db.commit()
            ws = await _get_ws(request)
            if ws:
                await ws.broadcast("agent_registered", {
                    "agentId": req.agentId,
                    "role": req.role,
                    "runtime": normalized_runtime,
                    "machineId": req.machineId or "",
                    "sessionMode": existing_mode,
                    "ownershipTransition": "console_terminal_attached",
                })
            return {
                "ok": True,
                "agentId": req.agentId,
                "role": req.role,
                "status": req.status or "idle",
                "runtime": normalized_runtime,
                "machineId": req.machineId or "",
                "bridgeId": bridge_id,
                "sessionMode": existing_mode,
                "ownershipTransition": "console_terminal_attached",
            }
        fresh_state = _runtime_state_with_handle(normalized_runtime, {}, session_handle)
        if bridge_id:
            fresh_state["bridgeInstanceId"] = bridge_id
        if normalized_session_mode == "resident":
            fresh_state["ownership"] = {
                "mode": "resident",
                "previousMode": _normalize_session_mode(row["session_mode"] or "managed") if row else "",
                "reason": "registered_cli",
                "at": now,
            }
        elif normalized_session_mode == "managed" and req.launchMode == "managed":
            fresh_state["ownership"] = {
                "mode": "managed",
                "previousMode": _normalize_session_mode(row["session_mode"] or "resident") if row else "",
                "reason": "registered_managed",
                "at": now,
            }
        # Plan 2 (2026-05-25) pi flip mechanics: pi-runtime no longer
        # supports a true resident session, but operators may still try
        # to register one (e.g. via legacy wrapper). Mark it pending-flip
        # so _drain_and_flip_pi_resident_agents (Task 17) can migrate it
        # to managed once any active runs drain. Once flipped, the agent
        # row's session_mode becomes "managed" and capabilities are
        # recomputed from PiAdapter (supports_resident=False).
        if normalized_runtime == "pi" and normalized_session_mode == "resident":
            fresh_state["pi_resident_pending_flip"] = True
        existing_state = json.dumps(fresh_state)
        if row and normalized_session_mode == "resident" and _normalize_session_mode(row["session_mode"] or "resident") == "managed":
            active_run = await _get_blocking_active_run(db, req.agentId)
            existing_state_dict = _json_loads_or(row["runtime_state"], {})
            existing_state_dict.pop("pendingResidentTakeover", None)
            existing_state_dict["manualResidentCandidate"] = {
                "bridgeId": bridge_id,
                "machineId": req.machineId or "",
                "runtime": normalized_runtime,
                "sessionHandle": session_handle,
                "runtimeConfig": runtime_config,
                "capabilities": capabilities or [],
                "cwd": resolved_cwd,
                "launchMode": req.launchMode or "detached",
                "registeredAt": now,
            }
            await db.execute(
                """
                UPDATE agents
                SET runtime_state = ?,
                    status_note = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    json.dumps(existing_state_dict),
                    (
                        f"Resident CLI registered, but agent remains managed. Use Switch to resident when ready."
                        + (f" Active run {active_run.get('runId') or ''} is still running." if active_run else "")
                    ),
                    now,
                    req.agentId,
                ),
            )
            if session_handle:
                await db.execute(
                    """
                    UPDATE agent_sessions
                    SET session_handle = ?,
                        telemetry = CASE
                            WHEN COALESCE(NULLIF(telemetry, ''), '{}') = '{}' THEN ?
                            ELSE telemetry
                        END,
                        last_seen = ?
                    WHERE id = (
                        SELECT id
                        FROM agent_sessions
                        WHERE agent_id = ?
                          AND runtime = ?
                          AND status = 'cli-takeover'
                        ORDER BY last_seen DESC
                        LIMIT 1
                    )
                    """,
                    (
                        session_handle,
                        json.dumps({"registeredHandle": _runtime_state_with_handle(normalized_runtime, {}, session_handle)}),
                        now,
                        req.agentId,
                        normalized_runtime,
                    ),
                )
            if bridge_id:
                await _record_bridge_registration(
                    db,
                    bridge_id=bridge_id,
                    agent_id=req.agentId,
                    machine_id=req.machineId or "",
                    runtime=normalized_runtime,
                    session_mode="resident",
                    session_handle=session_handle,
                    terminal_id=terminal_id,
                    now=now,
                )
            await _invalidate_agent_live_state(db, req.agentId)
            await db.commit()
            ws = await _get_ws(request)
            if ws:
                await ws.broadcast("agent_registered", {
                    "agentId": req.agentId,
                    "role": req.role,
                    "runtime": normalized_runtime,
                    "machineId": req.machineId or "",
                    "sessionMode": "managed",
                    "residentBridgeId": bridge_id,
                })
            return {
                "ok": True,
                "agentId": req.agentId,
                "role": req.role,
                "status": row["status"] or "active",
                "runtime": normalized_runtime,
                "machineId": req.machineId or "",
                "bridgeId": bridge_id,
                "sessionMode": "managed",
                "ownershipTransition": "manual_switch_required",
                # Task 4.1: the takeover command the operator runs after flipping
                # the agent to resident in the dashboard (one-driver invariant).
                "resumeCommand": _resume_command_for(normalized_runtime, session_handle, req.agentId),
                "blockedByRun": active_run,
            }
        await db.execute(
            """
            INSERT INTO agents (
                id, role, name, cwd, model, description, instructions, status, status_note, runtime, machine_id,
                launch_mode, session_mode, session_handle, managed_by, capabilities,
                runtime_config, runtime_state, driver_state, registered_at, last_seen
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                role = excluded.role,
                name = excluded.name,
                cwd = excluded.cwd,
                model = excluded.model,
                description = excluded.description,
                instructions = excluded.instructions,
                status = excluded.status,
                status_note = excluded.status_note,
                runtime = excluded.runtime,
                machine_id = excluded.machine_id,
                launch_mode = excluded.launch_mode,
                session_mode = excluded.session_mode,
                session_handle = excluded.session_handle,
                managed_by = excluded.managed_by,
                capabilities = excluded.capabilities,
                runtime_config = excluded.runtime_config,
                runtime_state = excluded.runtime_state,
                driver_state = excluded.driver_state,
                last_seen = excluded.last_seen
            """,
            (
                req.agentId, req.role, req.name or req.agentId, resolved_cwd, model_value,
                description_value, req.instructions or "", req.status or "idle",
                (row["status_note"] if row and "status_note" in row.keys() else "") or "",
                normalized_runtime,
                req.machineId or "", req.launchMode or "detached",
                normalized_session_mode, session_handle, req.managedBy or "",
                json.dumps(capabilities or []), json.dumps(runtime_config),
                existing_state,
                # One-driver FSM: an attaching process carrying a bridge_id is a
                # live driver for this session -> mark driving. A metadata-only
                # (re)register without a bridge keeps the prior driver_state.
                ("driving" if bridge_id else (str((row["driver_state"] if row and "driver_state" in row.keys() else "") or "idle"))),
                row["registered_at"] if row and row["registered_at"] else now, now
            )
        )
        await _record_registered_session_handle(db, req, normalized_runtime, runtime_config, session_handle, now)
        if bridge_id:
            await _record_bridge_registration(
                db,
                bridge_id=bridge_id,
                agent_id=req.agentId,
                machine_id=req.machineId or "",
                runtime=normalized_runtime,
                session_mode=normalized_session_mode,
                session_handle=session_handle,
                terminal_id=terminal_id,
                managed_wrapper_child=managed_wrapper_child,
                now=now,
            )
        await _invalidate_agent_live_state(db, req.agentId)
        # Universal rule: when a *-aify wrapper registers an agent as
        # resident, the operator's real terminal owns it. ANY managed
        # wrapper PTY that exists for this agent must be torn down at
        # that moment — no time-based detection, just the resident-
        # register event itself triggers it. Mark active terminal_sessions
        # as stopped with a clear reason; clear the agent_session
        # terminal_id binding so the dashboard stops displaying a ghost
        # console; send a 'stop' terminal_control to the owning bridge
        # so the underlying PTY process is killed if still alive.
        if normalized_session_mode == "resident":
            stale_terminals = await (
                await db.execute(
                    """
                    SELECT id, environment_id, bridge_id
                    FROM terminal_sessions
                    WHERE agent_id = ?
                      AND status IN ('starting','attached','running','active','idle','recovering')
                      AND (? = '' OR id != ?)
                    """,
                    (req.agentId, terminal_id, terminal_id),
                )
            ).fetchall()
            for term in stale_terminals:
                await db.execute(
                    """
                    UPDATE terminal_sessions
                    SET status = 'stopped',
                        stopped_at = ?,
                        updated_at = ?,
                        error = COALESCE(NULLIF(error, ''), 'superseded_by_resident_takeover')
                    WHERE id = ?
                    """,
                    (now, now, term["id"]),
                )
                await _append_terminal_event(
                    db,
                    term["id"],
                    "superseded_by_resident_takeover",
                    json.dumps({
                        "agentId": req.agentId,
                        "residentBridge": bridge_id,
                        "newSessionMode": "resident",
                    }),
                )
                # Best-effort kill: enqueue 'stop' so the owning bridge
                # tears down the wrapper subprocess if still alive. If
                # the bridge is dead, the row is already marked stopped
                # so it doesn't matter that the control is never claimed.
                await _append_terminal_control(
                    db,
                    terminal_id=term["id"],
                    environment_id=term["environment_id"] or "",
                    bridge_id=term["bridge_id"] or "",
                    action="stop",
                    requested_by="resident-takeover",
                    body="",
                )
            if stale_terminals:
                # Clear agent_sessions.terminal_id binding for sessions
                # that pointed at any of the just-stopped terminals so
                # the dashboard stops rendering a ghost Console.
                stopped_ids = [t["id"] for t in stale_terminals]
                placeholders = ",".join(["?"] * len(stopped_ids))
                await db.execute(
                    f"""
                    UPDATE agent_sessions
                    SET terminal_id = '',
                        terminal_status = ''
                    WHERE agent_id = ?
                      AND terminal_id IN ({placeholders})
                    """,
                    (req.agentId, *stopped_ids),
                )
            await _upsert_resident_agent_session(
                db,
                agent_id=req.agentId,
                runtime=normalized_runtime,
                workspace=resolved_cwd,
                machine_id=req.machineId or "",
                session_handle=session_handle,
                runtime_config=runtime_config,
                bridge_id=bridge_id,
                capabilities=capabilities or [],
                now=now,
            )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_registered", {
                "agentId": req.agentId,
                "role": req.role,
                "runtime": normalized_runtime,
                "machineId": req.machineId or "",
                "sessionMode": normalized_session_mode,
            })
        return {
            "ok": True,
            "agentId": req.agentId,
            "role": req.role,
            "status": req.status or "idle",
            "runtime": normalized_runtime,
            "machineId": req.machineId or "",
            "bridgeId": bridge_id,
            "sessionMode": normalized_session_mode,
        }
    finally:
        await db.close()


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request):
    db = await get_db()
    try:
        settings = await _load_settings(db)
        # Best-effort cache refresh — serve cached on a write-lock rather than 503 (see list_agents).
        try:
            refreshed_live_states = await _refresh_expired_agent_live_states(db, settings=settings, agent_ids=[agent_id])
            if refreshed_live_states:
                await db.commit()
        except sqlite3.OperationalError as exc:
            if not _is_lock_error(exc):
                raise
            try:
                await db.rollback()
            except Exception:
                pass
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        row = await cursor.fetchone()
        if not row:
            tombstone = await _agent_tombstone(db, agent_id)
            if tombstone:
                raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        unread_map = await _get_unread_count_map(db, [agent_id])
        dispatch_map = await _get_dispatch_state_map(db, [agent_id])
        outbound_map = await _get_outbound_activity_map(db, [agent_id])
        entry = _live_state_get(agent_id) or {}
        payload = _agent_record_to_dict(row, entry.get("status") or row["status"], unread_map.get(agent_id, 0), dispatch_map.get(agent_id), live_reason=entry.get("reason"), outbound=outbound_map.get(agent_id))
        # Plan 5 Section C: read-path live-worker gate (in-memory correction only; the
        # writeback was removed 2026-06-18 to cut read-path writes — see the gate bodies).
        payload = await _enforce_live_worker_gate(payload, db, settings, agent_id)
        payload = await _enforce_env_reachable_gate(payload, db, settings, agent_id)
        return {"ok": True, "agentId": agent_id, "agent": payload}
    finally:
        await db.close()


@router.post("/agents/{agent_id}/rename")
async def rename_agent(agent_id: str, req: AgentRenameRequest, request: Request):
    validate_name(agent_id, "agent ID")
    new_agent_id = str(req.newAgentId or "").strip()
    validate_name(new_agent_id, "new agent ID")
    if new_agent_id == agent_id:
        return {"ok": True, "agentId": agent_id, "newAgentId": new_agent_id, "changed": False}

    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        agent = await cursor.fetchone()
        if not agent:
            await db.rollback()
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        existing = await (await db.execute("SELECT id FROM agents WHERE id = ?", (new_agent_id,))).fetchone()
        if existing:
            await db.rollback()
            raise HTTPException(409, f'Agent "{new_agent_id}" already exists')
        tombstone = await _agent_tombstone(db, new_agent_id)
        if tombstone:
            await db.rollback()
            raise HTTPException(409, f'Agent "{new_agent_id}" was intentionally removed before; clear that ID before reusing it')

        now = _now()
        await db.execute(
            """
            INSERT INTO agents (
                id, role, name, cwd, model, description, instructions, status, status_note,
                runtime, machine_id, launch_mode, session_mode, session_handle, managed_by,
                capabilities, runtime_config, runtime_state, registered_at, last_seen
            )
            SELECT ?, role, CASE WHEN name = id THEN ? ELSE name END, cwd, model, description,
                   instructions, status, status_note, runtime, machine_id, launch_mode,
                   session_mode, session_handle, managed_by, capabilities, runtime_config,
                   runtime_state, registered_at, ?
            FROM agents
            WHERE id = ?
            """,
            (new_agent_id, new_agent_id, now, agent_id),
        )
        for table, column in (
            ("agent_sessions", "agent_id"),
            ("spawn_specs", "agent_id"),
            ("spawn_requests", "agent_id"),
            ("bridge_instances", "agent_id"),
            ("read_receipts", "agent_id"),
            ("channel_members", "agent_id"),
        ):
            await db.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE messages SET from_agent = ? WHERE from_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE messages SET to_agent = ? WHERE to_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE shared_artifacts SET from_agent = ? WHERE from_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE dispatch_runs SET from_agent = ? WHERE from_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE dispatch_runs SET target_agent = ? WHERE target_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE dispatch_controls SET from_agent = ? WHERE from_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE channels SET created_by = ? WHERE created_by = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE agents SET managed_by = ? WHERE managed_by = ?", (new_agent_id, agent_id))
        await db.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        await db.execute(
            """
            INSERT OR REPLACE INTO agent_tombstones (agent_id, removed_at, removed_by, bridge_id, reason)
            VALUES (?,?,?,?,?)
            """,
            (agent_id, now, req.requestedBy or "dashboard", "", f"renamed_to:{new_agent_id}"),
        )
        await db.commit()
        # Rename is DB-only: a still-running session is bootstrapped under the OLD id (now
        # tombstoned), so it is orphaned — its heartbeats bounce and it does NOT keep the new id
        # live. Surface that + the recovery in the response so the caller/dashboard doesn't have to
        # rediscover it by hand (2026-07-07: a rename silently orphaned the live session and notified
        # nobody). We report facts + a plain note; the dashboard can format the exact relaunch command.
        session_mode = str(agent["session_mode"] or "resident").strip().lower()
        runtime = str(agent["runtime"] or "").strip()
        # "Live" needs a FRESHNESS predicate, not merely a row that was never superseded.
        #
        # This asked `bridge_instances` for any row with an empty `superseded_by`, which is not the
        # same question. Those rows accumulate BY DESIGN (KNOWN_ISSUES.md, 2026-08-07 retraction) and
        # a bridge that died without a clean supersede keeps `superseded_by = ''` until the sweep's
        # `_reap_stale_orphan_bridges` gets to it. So a rename minutes after a crashed wrapper told
        # the operator "A live session is still running as '<old>' and is now orphaned — relaunch it",
        # sending them to recover a session that had already been dead for hours.
        #
        # `_agent_liveness` is the repo's single liveness predicate and already applies the exact
        # leases the status engine uses, so the note now agrees with the dot the operator is looking
        # at. Advisory text only — nothing here changes state either way — but a wrong instruction is
        # the same class of defect as a wrong status, and this file has spent a week on that class.
        liveness = await _agent_liveness(db, new_agent_id)
        had_live_bridge = bool(
            liveness.get("worker_live")
            or liveness.get("sidecar_live")
            or liveness.get("resident_bridge_fresh")
        )
        note = (
            f"History + session handle preserved under '{new_agent_id}'; old id '{agent_id}' is "
            f"tombstoned (sends to it are now rejected). "
            + (
                (
                    f"A live {session_mode} session is still running as '{agent_id}' and is now orphaned — "
                    f"re-register/relaunch it as '{new_agent_id}' "
                    + ("(dashboard Restart, or delete-session then send to cold-start a fresh worker) "
                       if session_mode == "managed"
                       else f"(relaunch the wrapper with the new id, e.g. --aify-agent {new_agent_id}) ")
                    + "so the live identity matches. "
                )
                if had_live_bridge else ""
            )
            + "Notify teammates to address the new id."
        )
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_renamed", {"oldAgentId": agent_id, "newAgentId": new_agent_id})
        return {
            "ok": True, "agentId": agent_id, "newAgentId": new_agent_id, "changed": True,
            "hadLiveBridge": had_live_bridge, "sessionMode": session_mode, "runtime": runtime,
            "note": note,
        }
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        raise
    finally:
        await db.close()


@router.delete("/agents/{agent_id}")
async def unregister_agent(agent_id: str, request: Request):
    db = await get_db()
    try:
        # fix/hermes-leak P2 (REMOVE): for a MANAGED agent, tear the triad down by
        # signalling the bridge BEFORE the agent record is gone. We cannot use a
        # terminal_control here: deleting the agent cascades agents → agent_sessions
        # → terminal_sessions → terminal_controls, so any control emitted in this
        # request is wiped by the same delete. Instead REMOVE drives the triad reap
        # through the SAME agent-control STOP path (status=stopped + the bridge's
        # managed-hermes terminal stop reaps the triad), committed in its own
        # transaction, THEN tombstones. This makes REMOVE = STOP-then-tombstone, so
        # the surviving stop control (claimed before the tombstone delete) carries
        # the triad-reap. Resident agents are skipped (operator's own session).
        cursor = await db.execute("SELECT session_mode FROM agents WHERE id = ?", (agent_id,))
        agent_row = await cursor.fetchone()
        managed = bool(agent_row) and _normalize_session_mode(agent_row["session_mode"] or "resident") == "managed"
        if managed:
            now = _now()
            await db.execute(
                "UPDATE agents SET status = 'stopped', status_note = ?, launch_mode = 'none', last_seen = ? WHERE id = ?",
                ("Removed from dashboard; tearing down managed session.", now, agent_id),
            )
            await _request_stop_agent_terminals(
                db, agent_id, requested_by="api", now=now, reap_triad=True,
            )
            await db.commit()
        deleted = await _remove_agent_record(
            db,
            agent_id,
            removed_by="api",
            reason="delete_agent",
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("agent_removed", {"agentId": agent_id})
        return {"ok": deleted > 0, "agentId": agent_id}
    finally:
        await db.close()


@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: str, req: AgentStatusUpdate, request: Request):
    db = await get_db()
    try:
        note = getattr(req, 'note', None) or ''
        status_val = f"{req.status}: {note}" if note else req.status
        cursor = await db.execute(
            "UPDATE agents SET status = ?, status_note = ?, last_seen = ? WHERE id = ?",
            (req.status, note, _now(), agent_id)
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        ws = await _get_ws(request)
        if ws:
            # Keep req.status authoritative (operator-set), enrich with the note
            # so dashboards can render it on the agent's row without a refetch.
            await ws.broadcast("agent_status", {"agentId": agent_id, "status": req.status, "statusNote": note})
        return {"ok": True, "agentId": agent_id, "status": status_val, "statusRaw": req.status, "statusNote": note}
    finally:
        await db.close()


@router.patch("/agents/{agent_id}/description")
async def update_agent_description(agent_id: str, req: AgentDescribeRequest, request: Request):
    """Update an agent's team-facing description without re-registering."""
    validate_name(agent_id, "agent ID")
    description = str(req.description or "")
    if len(description) > 2000:
        raise HTTPException(400, "description must be 2000 chars or fewer")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        await db.execute(
            "UPDATE agents SET description = ?, last_seen = ? WHERE id = ?",
            (description, _now(), agent_id),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_description_updated", {"agentId": agent_id, "description": description})
        return {"ok": True, "agentId": agent_id, "description": description}
    finally:
        await db.close()


@router.patch("/agents/{agent_id}/favorite")
async def update_agent_favorite(agent_id: str, req: AgentFavoriteUpdate, request: Request):
    """Dashboard favorites — pin/unpin an agent in the chat list.

    Operator-set per-deployment flag (not synced across remote
    dashboards). Dashboard renders favorited agents at the top of the
    list and shows a visual marker. Pure metadata — no behavior change.
    """
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        flag = 1 if bool(req.favorited) else 0
        await db.execute(
            "UPDATE agents SET favorited = ?, last_seen = ? WHERE id = ?",
            (flag, _now(), agent_id),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_favorite_updated", {"agentId": agent_id, "favorited": bool(flag)})
        return {"ok": True, "agentId": agent_id, "favorited": bool(flag)}
    finally:
        await db.close()
