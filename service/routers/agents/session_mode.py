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
from service.api_core.session_mode_writes import _apply_session_mode_switch_to_agent
from service.api_core.session_mode_audit import _record_session_mode_switch_audit
from service.api_core.session_mode_env_binding import _infer_environment_binding_for_managed_switch
from service.api_core.routing import domain_router

logger = logging.getLogger("aify_comms.routers.agents.session_mode")

# Imported for the ANNOTATIONS. Under postponed evaluation these are strings, so a
# missing one does not fail import -- FastAPI demotes the body to a query parameter and
# the endpoint 422s at request time. The route annotation gate caught 17 of these here.
from service.models import AgentSessionModeSwitchRequest

from service.api_core.resume_command import _resume_command_for
from service.api_core.agent_sessions import _agent_tombstone
from service.api_core.capabilities import _default_capabilities_for
from service.api_core.dispatch_state import _get_dispatch_state_for_agent
from service.api_core.records import _agent_record_to_dict
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.serialization import _json_loads_or, _normalize_machine_id
from service.api_core.settings import _load_settings
from service.api_core.status_refresh import _compute_agent_status
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.db import get_db
from service.api_core.vocabulary import SESSION_MODES as _SESSION_MODES
from service.clock import now as _now
import sqlite3
from service.routers.agents.shared import logger

router = domain_router()

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
        await _apply_session_mode_switch_to_agent(
            db, agent_id, new_mode, current_mode,
            runtime, effective_runtime, runtime_config, runtime_state,
            capabilities, switch_session_handle, next_cwd, next_launch_mode,
            next_machine_id, resident_candidate, requested_by, now,
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
