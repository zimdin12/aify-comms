"""Session MODE and HANDLE: which mode an agent runs in, and which native session it owns.

v0.5.2m, one surface of the agents package. Built with `domain_router()`;
declares NO tags — the parent applies `tags=["api"]` once when api_v2 includes the package.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException, Request

from service.api_core.session_mode_gates import (
    _enforce_switch_not_blocked_by_active_run,
    _start_managed_backing_after_switch,
)
from service.api_core.runtime_state import _runtime_state_replacing_handle, _runtime_state_with_handle
from service.api_core.session_handle_change import (
    _detect_fresh_start_terminal,
    _mirror_handle_onto_live_session,
)
from service.api_core.session_mode_audit import _record_session_mode_switch_audit
from service.api_core.session_mode_env_binding import _infer_environment_binding_for_managed_switch
from service.api_core.status_events import _apply_status_event
from service.api_core.routing import domain_router

logger = logging.getLogger("aify_comms.routers.agents.session_mode")

# Imported for the ANNOTATIONS. Under postponed evaluation these are strings, so a
# missing one does not fail import -- FastAPI demotes the body to a query parameter and
# the endpoint 422s at request time. The route annotation gate caught 17 of these here.
from service.models import AgentSessionHandleUpdate, AgentSessionModeSwitchRequest

from service.api_core.resume_command import _resume_command_for
from service.routers.agents.shared import (
    DEFAULT_SETTINGS,
    _SESSION_MODES,
    _agent_record_to_dict,
    _agent_tombstone,
    _compute_agent_status,
    _compute_live_status_cache,
    _default_capabilities_for,
    _get_dispatch_state_for_agent,
    _get_ws,
    _json_loads_or,
    _load_settings,
    _normalize_machine_id,
    _normalize_runtime,
    _normalize_session_mode,
    _now,
    _render_live_terminal_screen,
    _sanitize_session_handle,
    _session_handle_live_owner,
    get_db,
    logger,
    sqlite3,
    validate_name,
)

router = domain_router()


@router.patch("/agents/{agent_id}/session-handle")
async def update_agent_session_handle(agent_id: str, req: AgentSessionHandleUpdate, request: Request):
    validate_name(agent_id, "agent ID")
    # Drop unexpanded shell placeholders ("$HERMES_SESSION_ID", "${VAR}") so a
    # literal is never stored as the resume handle — see _sanitize_session_handle.
    session_handle = _sanitize_session_handle(req.sessionHandle)
    if len(session_handle) > 512:
        raise HTTPException(400, "sessionHandle must be 512 characters or fewer")
    db = await get_db()
    try:
        now = _now()
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            tombstone = await _agent_tombstone(db, agent_id)
            if tombstone:
                raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
            raise HTTPException(404, f"Agent '{agent_id}' not found")

        # ── Sticky session identity + new-id guard (governance, 2026-05-30) ──
        # The bridge heartbeat (session-handle-heartbeat.js, requestedBy=
        # "bridge-heartbeat") continuously reports the runtime's *discovered*
        # session id. We must NOT silently overwrite the persisted handle when
        # that discovered id DRIFTS from what we already pinned — a drift is the
        # observable symptom of a split (agent landed on a fresh id) or a merge
        # (two agents converging on one id). Instead we park the proposed id in
        # `pending_session_id`, flag the agent `session-changed`, and KEEP
        # delivery pointed at the old handle until the operator resolves it.
        #
        # Scope is deliberately narrow so we never break the existing flows:
        #   • First-id auto-accept — no persisted handle yet → accept (current).
        #   • Same id re-reported → no-op (no pending, no churn).
        #   • Clearing (empty handle) → allowed (heal paths clear poisoned ids).
        #   • Deliberate operator re-pin (any other requestedBy, e.g. dashboard
        #     manual set, console attach) → unguarded, as before.
        #   • Re-register (POST /agents) is a separate write site and remains a
        #     full state refresh — it is NOT routed through here.
        requested_by = str(req.requestedBy or "").strip()
        persisted_handle = str(row["session_handle"] or "").strip()

        # ── Cross-agent collision guard (root-cause fix, 2026-05-31) ──
        # A runtime session id must be owned by at most ONE live agent. Never let
        # agent X ADOPT a session id that a DIFFERENT LIVE agent already owns —
        # the resident<->managed invariant. (Incident: graph-tech-lead adopted
        # comms-tech-lead's live resident id 651b895f at 06:07; the kill-prior
        # reaper then turned that collision fatal.) This fires for ANY source
        # (capture, heartbeat, manual set) and covers the first-id case too. Park
        # the colliding id as `pending_session_id` and KEEP this agent's own
        # handle (empty stays empty → the agent launches fresh and captures its
        # OWN id, which won't collide). A stale/dead owner is NOT a collision
        # (the id is free to reassign) — _session_handle_live_owner gates on
        # heartbeat freshness.
        if session_handle and session_handle != persisted_handle:
            _settings_g = await _load_settings(db)
            _owner = await _session_handle_live_owner(
                db, session_handle, exclude_agent_id=agent_id,
                lease_seconds=_settings_g.get("resident_lease_seconds", 150),
            )
            if _owner:
                _note = (
                    f"session-collision: reported id '{session_handle}' is already owned by live "
                    f"agent '{_owner['agentId']}' ({_owner['sessionMode']}); kept own handle. "
                    "Two live agents must not share one session id."
                )
                await db.execute(
                    "UPDATE agents SET pending_session_id = ?, status_note = ?, last_seen = ? WHERE id = ?",
                    (session_handle, _note, now, agent_id),
                )
                await db.commit()
                updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
                settings = await _load_settings(db)
                status = await _compute_agent_status(updated, db)
                dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
                ws = await _get_ws(request)
                if ws:
                    await ws.broadcast("agent_session_changed", {
                        "agentId": agent_id,
                        "sessionHandle": persisted_handle,
                        "pendingSessionId": session_handle,
                        "collisionWith": _owner["agentId"],
                    })
                return {
                    "ok": True,
                    "agentId": agent_id,
                    "state": "session-collision",
                    "collisionWith": _owner["agentId"],
                    # Delivery keeps targeting THIS agent's own handle; the
                    # colliding id is NOT adopted.
                    "sessionHandle": persisted_handle,
                    "pendingSessionId": session_handle,
                    "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
                }

        # Auto-confirm (2026-06-04): when ON (default), a SAFE self-change — the
        # cross-agent collision guard above already returned for a live-owned id —
        # is adopted immediately (fall through to the bind path below) instead of
        # parked. This breaks the managed-claude session-changed → stale-console-
        # owner → recycle loop. When OFF, park as `pending_session_id` and wait for
        # a manual Confirm (the original sticky-identity governance behavior).
        _auto_confirm_sid = bool(
            (await _load_settings(db)).get(
                "auto_confirm_session_id", DEFAULT_SETTINGS["auto_confirm_session_id"]
            )
        )
        _fresh_start_terminal = await _detect_fresh_start_terminal(
            db, agent_id, _auto_confirm_sid, requested_by, session_handle, persisted_handle
        )
        if (
            requested_by == "bridge-heartbeat"
            and session_handle
            and persisted_handle
            and session_handle != persisted_handle
            and (not _auto_confirm_sid or _fresh_start_terminal)
        ):
            await db.execute(
                """
                UPDATE agents
                SET pending_session_id = ?,
                    status_note = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    session_handle,
                    (
                        f"session-changed: reported id '{session_handle}' differs from "
                        f"pinned '{persisted_handle}'. Confirm new or keep current."
                    ),
                    now,
                    agent_id,
                ),
            )
            await db.commit()
            updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
            settings = await _load_settings(db)
            status = await _compute_agent_status(updated, db)
            dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
            ws = await _get_ws(request)
            if ws:
                await ws.broadcast("agent_session_changed", {
                    "agentId": agent_id,
                    "sessionHandle": persisted_handle,
                    "pendingSessionId": session_handle,
                })
            return {
                "ok": True,
                "agentId": agent_id,
                "state": "session-changed",
                # Delivery still targets the OLD (persisted) handle — unchanged.
                "sessionHandle": persisted_handle,
                "pendingSessionId": session_handle,
                "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
            }

        runtime = _normalize_runtime(row["runtime"] or "generic")
        session_mode = _normalize_session_mode(row["session_mode"] or "resident")
        runtime_config = _json_loads_or(row["runtime_config"], {})
        runtime_state = _runtime_state_replacing_handle(runtime, row["runtime_state"], session_handle)
        capabilities = _default_capabilities_for(runtime, session_mode, session_handle, runtime_config)
        registered_handle = _runtime_state_with_handle(runtime, {}, session_handle)

        # G3 (2026-06-03): advisory (non-blocking) warning when the handle being
        # bound is already owned by a DIFFERENT live agent. The strict cross-agent
        # collision guard above already HARD-BLOCKS the `handle != persisted` live
        # case; this warning covers the remaining binds (e.g. re-pinning the same
        # handle another live agent already shares) so the operator sees that two
        # live agents are pointing at one native session id.
        handle_share_warning = ""
        if session_handle:
            _settings_g3 = await _load_settings(db)
            _owner_g3 = await _session_handle_live_owner(
                db, session_handle, exclude_agent_id=agent_id,
                lease_seconds=_settings_g3.get("resident_lease_seconds", 150),
            )
            if _owner_g3:
                handle_share_warning = (
                    f"session id '{session_handle}' is also owned by live agent "
                    f"'{_owner_g3['agentId']}' ({_owner_g3['sessionMode']}); two live agents "
                    "should not share one native session."
                )
        await db.execute(
            """
            UPDATE agents
            SET session_handle = ?,
                pending_session_id = '',
                runtime_state = ?,
                capabilities = ?,
                status_note = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (
                session_handle,
                json.dumps(runtime_state),
                json.dumps(capabilities),
                f"Session handle set by {req.requestedBy or 'operator'}." if session_handle else f"Session handle cleared by {req.requestedBy or 'operator'}.",
                now,
                agent_id,
            ),
        )
        await _mirror_handle_onto_live_session(
            db, agent_id, runtime, session_handle, registered_handle, now
        )
        await db.commit()

        updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        settings = await _load_settings(db)
        status = await _compute_agent_status(updated, db)
        dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_session_handle_updated", {"agentId": agent_id, "sessionHandle": session_handle})
        handle_response = {
            "ok": True,
            "agentId": agent_id,
            "sessionHandle": session_handle,
            "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
        }
        if handle_share_warning:
            handle_response["warning"] = handle_share_warning
        return handle_response
    finally:
        await db.close()


@router.patch("/agents/{agent_id}/session-mode")
async def switch_agent_session_mode(agent_id: str, req: AgentSessionModeSwitchRequest, request: Request):
    """Plan 6 C1 (2026-05-26): operator-driven resident/managed mode flip.

    Today the wrapper auto-detects via `[ -t 0 ]`; this endpoint lets the
    operator override the agent's `session_mode` regardless of how the
    wrapper was launched. Edge cases the server protects against (unless
    `force=true` is passed):

    - Active dispatch run in flight -> 409 (switching mid-turn would
      stall the run; wait for it to finish).

    (The former hermes-without-gatewayUrl 409 guard was removed: under the
    api_server model resident hermes resumes its pinned session via
    `--resume` and never needs a gateway URL — it was a tui_gateway-era
    requirement.)

    Audit log: a `dispatch_events` row of type
    `mode_switch_<old>_to_<new>` is appended with body
    `agentId=<id> by=<requestedBy>`, providing traceability without a
    new table.

    State-transition side effects (C2):
    - resident -> managed: best-effort eager-spawn of a wrapper PTY so
      the next dispatch lands in a ready Console (mirrors the spawn
      path used by `_ensure_managed_pty_for_dispatch` during /dispatch).
    - managed -> resident: best-effort release of any active managed
      PTY by flipping its status to 'stopping' so the bridge reconciles
      the close cleanly. Operator must launch a resident `*-aify`
      session themselves for the agent to come back online.

    Side-effect failures do not roll back the mode change itself —
    operators can always re-attach manually. The `sideEffects` field in
    the response surfaces what happened (or what failed).
    """
    validate_name(agent_id, "agent ID")
    new_mode = _normalize_session_mode(req.mode)
    requested_raw = str(req.mode or "").strip().lower()
    if requested_raw not in _SESSION_MODES:
        raise HTTPException(400, "mode must be 'resident' or 'managed'")
    db = await get_db()
    try:
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            tombstone = await _agent_tombstone(db, agent_id)
            if tombstone:
                raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
            raise HTTPException(404, f"Agent '{agent_id}' not found")

        current_mode = _normalize_session_mode(row["session_mode"] or "resident")
        runtime = _normalize_runtime(row["runtime"] or "generic")
        current_runtime_state = _json_loads_or(row["runtime_state"], {})
        resident_candidate = current_runtime_state.get("manualResidentCandidate")
        if not isinstance(resident_candidate, dict):
            resident_candidate = {}
        row_runtime_config = _json_loads_or(row["runtime_config"], {})
        candidate_runtime_config = resident_candidate.get("runtimeConfig") if isinstance(resident_candidate.get("runtimeConfig"), dict) else {}
        switch_runtime_config = (
            {**row_runtime_config, **candidate_runtime_config}
            if new_mode == "resident" and candidate_runtime_config
            else row_runtime_config
        )
        switch_session_handle = str(
            (resident_candidate.get("sessionHandle") if new_mode == "resident" else "")
            or row["session_handle"]
            or ""
        ).strip()
        # Adopt the resident candidate's runtime when switching to resident. A
        # resident wrapper of a different runtime (e.g. a hermes hermes-aify
        # session registering against an agent last seen as managed pi) records
        # itself as a manualResidentCandidate with runtime="hermes". Without
        # this, the switch promoted the candidate's bridge/handle/config but
        # kept the stale runtime, producing an inconsistent pi-resident agent
        # pointing at a hermes bridge — the switch appeared to do nothing.
        effective_runtime = runtime
        if new_mode == "resident":
            candidate_runtime = str(resident_candidate.get("runtime") or "").strip()
            if candidate_runtime:
                effective_runtime = _normalize_runtime(candidate_runtime)

        if current_mode == new_mode:
            return {
                "ok": True,
                "agentId": agent_id,
                "mode": new_mode,
                "previousMode": current_mode,
                "changed": False,
            }

        # G2 (2026-06-03): block managed->resident for runtimes that don't
        # support a resident bridge (pi, opencode). Without this guard the flip
        # produces a `presence-only` agent whose every dispatch is rejected as
        # undeliverable — a silent footgun. Source resident support from the
        # runtime adapter (claude/codex/hermes = True; pi/opencode = False).
        # `force=true` may override (operator-initiated metadata-only flip), but
        # we still attach a clear warning to the response so the limbo is visible.
        forced_resident_warning = ""
        switch_warnings = []
        if new_mode == "resident":
            try:
                from service.runtimes import adapter_for
                _resident_supported = bool(adapter_for(effective_runtime).supports_resident)
            except Exception:
                _resident_supported = False
            if not _resident_supported:
                if not req.force:
                    raise HTTPException(
                        409,
                        (
                            f"resident mode is not supported for {effective_runtime}; "
                            "it is managed-only. Keep this agent managed, or pass "
                            "force=true to change metadata only (it will be undeliverable)."
                        ),
                    )
                forced_resident_warning = (
                    f"resident mode is not supported for {effective_runtime} (managed-only); "
                    "forced switch leaves this agent presence-only and every dispatch will be "
                    "rejected until it is switched back to managed."
                )

        await _enforce_switch_not_blocked_by_active_run(
            db, req, agent_id, new_mode, runtime, switch_warnings,
        )

        now = _now()
        requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
        runtime_config = switch_runtime_config
        runtime_state = dict(current_runtime_state)
        runtime_state.pop("pendingResidentTakeover", None)
        if new_mode == "resident":
            if resident_candidate.get("bridgeId"):
                runtime_state["bridgeInstanceId"] = str(resident_candidate.get("bridgeId") or "")
            if resident_candidate:
                runtime_state["manualResidentCandidate"] = resident_candidate
        else:
            runtime_state.pop("manualResidentCandidate", None)
        runtime_state["ownership"] = {
            "mode": new_mode,
            "previousMode": current_mode,
            "reason": "manual_session_mode_switch",
            "requestedBy": requested_by,
            "at": now,
        }
        await _infer_environment_binding_for_managed_switch(
            db, agent_id, row, new_mode, runtime_state, switch_warnings
        )
        next_launch_mode = "managed" if new_mode == "managed" else "detached"
        capabilities = _default_capabilities_for(
            effective_runtime,
            new_mode,
            switch_session_handle,
            runtime_config,
        )
        next_machine_id = _normalize_machine_id(
            resident_candidate.get("machineId") or row["machine_id"] or ""
            if new_mode == "resident"
            else row["machine_id"] or ""
        )
        next_cwd = (
            str(resident_candidate.get("cwd") or row["cwd"] or "")
            if new_mode == "resident"
            else str(row["cwd"] or "")
        )
        await db.execute(
            """
            UPDATE agents
            SET session_mode = ?,
                runtime = ?,
                launch_mode = ?,
                session_handle = ?,
                machine_id = ?,
                cwd = ?,
                capabilities = ?,
                runtime_config = ?,
                runtime_state = ?,
                driver_state = ?,
                status = CASE WHEN status = 'stopped' THEN 'idle' ELSE status END,
                status_note = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (
                new_mode,
                effective_runtime,
                next_launch_mode,
                switch_session_handle,
                next_machine_id,
                next_cwd,
                json.dumps(capabilities),
                json.dumps(runtime_config),
                json.dumps(runtime_state),
                # Switching TO resident while adopting a LIVE resident bridge keeps that
                # session as the active driver. The previous unconditional 'idle' clobbered
                # the 'driving' the just-registered resident session had set, so its OWN
                # channel sidecar was told to RELEASE on its next claim/heartbeat and
                # resident delivery silently died — sends said "sent", runs queued forever
                # (sc-manager, 2026-06-12: launch terminal first, click switch second).
                ("driving" if (new_mode == "resident" and str(resident_candidate.get("bridgeId") or "").strip()) else "idle"),
                f"Manually switched from {current_mode} to {new_mode} by {requested_by}"
                + (f" (runtime {runtime}->{effective_runtime})" if effective_runtime != runtime else "")
                + ".",
                now,
                agent_id,
            ),
        )
        await _record_session_mode_switch_audit(
            db, agent_id, current_mode, new_mode, effective_runtime, requested_by, now
        )

        # C2 state-transition side effects. Wrapped in try/except so a side
        # effect failure (e.g., environment offline, no live agent_sessions
        # row) does NOT roll back the mode change — operators can still
        # re-spawn/attach manually. Failures surface in the response's
        # `sideEffects.error` field.
        settings = await _load_settings(db)
        side_effects: dict[str, Any] = {}
        await _start_managed_backing_after_switch(
            db, agent_id, new_mode, runtime, settings, requested_by, now, logger, side_effects,
        )

        await db.commit()
        # Takeover/resume command for the operator. On a managed -> resident
        # switch this is the command the operator runs to drive the SAME session
        # interactively; mirrored in the dashboard. Best-effort (empty if the
        # adapter has none).
        resume_command = _resume_command_for(effective_runtime, switch_session_handle, agent_id)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast(
                "agent_session_mode_updated",
                {"agentId": agent_id, "mode": new_mode, "previousMode": current_mode},
            )
        updated_agent = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        updated_status = await _compute_agent_status(updated_agent, db)
        updated_dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        response_payload = {
            "ok": True,
            "agentId": agent_id,
            "mode": new_mode,
            "previousMode": current_mode,
            "changed": True,
            "resumeCommand": resume_command,
            "sideEffects": side_effects,
            "agent": _agent_record_to_dict(updated_agent, updated_status, 0, updated_dispatch_state),
        }
        if forced_resident_warning:
            switch_warnings.insert(0, forced_resident_warning)
        if switch_warnings:
            response_payload["warning"] = " ".join(switch_warnings)
        return response_payload
    finally:
        await db.close()
