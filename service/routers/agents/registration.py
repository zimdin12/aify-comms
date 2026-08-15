"""Registering an agent — the largest single handler in the router layer, and why.

Extracted from `service/routers/agents/identity.py` in v0.5.4, which kept the reads and the
unregister. Closure measured before the move: `api_core` and `service` leaves plus three helpers
from `agents/shared.py`, nothing local.

RE-REGISTERING IS A FULL STATE REFRESH, not an upsert, and that is the fact the whole handler is
organised around. Everything about a session is wiped and rebuilt from what the caller sent —
capabilities, runtime, handle, environment binding — with `description` the single documented
exception, which is why THAT has its own endpoint next door. A test or workflow that assumes state
survives a re-register is assuming something this handler deliberately does not promise.

FOUR GATES RUN BEFORE ANYTHING IS WRITTEN, and each exists because a registration got through that
should not have: a tombstoned id being resurrected, a bridge registering under a mode that
contradicts the one already driving the agent, a driving-mode switch smuggled in as a re-register,
and a cwd the service cannot use. They live in `api_core/registration_gates.py` and
`api_core/same_mode_bridge_gate.py` rather than here, so the handler reads as the sequence it is.

THEN ONE OF THREE PATHS. A plain row upsert; adoption of a console terminal that is already running;
or a manual takeover of a resident session somebody else was driving — the destructive one, which
moved to `api_core/resident_takeover_writes.py` last week for that reason.

Body and route decorator are byte-identical to what stood in `identity.py`. The router is built
through `domain_router()`, which rejects a hand-passed `route_class`, so a new surface cannot opt out
of the bounded SQLite write-lock retry.
"""

from __future__ import annotations

import json

from fastapi import Request

from service.api_core.agent_registration_writes import (
    _record_registered_session_handle,
    _register_via_adopted_console_terminal,
    _upsert_registered_agent_row,
)
from service.api_core.agent_sessions import _agent_tombstone
from service.api_core.bridge_registration import _record_bridge_registration
from service.api_core.capabilities import _default_capabilities_for
from service.api_core.channel_delivery import _CHANNEL_CLAIM_RUNTIMES
from service.api_core.registration_gates import (
    _enforce_driving_mode_switch_gate,
    _enforce_tombstone_registration_gate,
    _enforce_tombstone_resurrection_gate,
    _validate_registration_cwd,
)
from service.api_core.resident_session_upsert import _upsert_resident_agent_session
from service.api_core.resident_takeover_writes import (
    _register_via_manual_resident_takeover,
    _supersede_stale_resident_terminals,
)
from service.api_core.routing import domain_router
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.runtime_state import _runtime_state_with_handle
from service.api_core.same_mode_bridge_gate import _enforce_same_mode_bridge_gate
from service.api_core.serialization import _json_loads_or
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
from service.routers.agents.shared import (
    _merge_runtime_policy_for_wrapper_reregister,
    _sanitize_session_handle,
    logger,
)

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import AgentRegister

router = domain_router()



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
            return await _register_via_adopted_console_terminal(
                db, req, request, row, console_terminal, terminal_id,
                bridge_id, normalized_runtime, session_handle, resolved_cwd, capabilities, runtime_config, now,
            )
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
            return await _register_via_manual_resident_takeover(
                bridge_id, capabilities, db, normalized_runtime, now, req,
                request, resolved_cwd, row, runtime_config, session_handle, terminal_id,
            )
        await _upsert_registered_agent_row(
            db, req, row, normalized_runtime, normalized_session_mode, session_handle,
            resolved_cwd, description_value, model_value, capabilities, runtime_config,
            existing_state, bridge_id, now,
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
            await _supersede_stale_resident_terminals(db, req, terminal_id, now, bridge_id)
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
