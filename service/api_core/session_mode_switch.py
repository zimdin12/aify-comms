"""Switching an agent between resident and managed: the one place the switch is applied.

The operator's switch for an agent no host defines (`PATCH /agents/{id}/session-mode`) and a defined agent whose
definition now asks for another mode (at its resident's registration, or when its managed worker's terminal ends
with a resident parked) both apply it here, so the two cannot drift (status fix A, 0.9.2: a definition flipped to resident left the record managed and parked the resident).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from service.api_core.capabilities import _default_capabilities_for
from service.api_core.channel_delivery import live_resident_session
from service.api_core.definition_guard import defined_mode
from service.api_core.live_process_probes import (
    PROBE_ERROR,
    _resident_bridge_is_fresh,
    channel_sidecar_state,
    terminal_state,
)
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.serialization import _json_loads_or, _normalize_machine_id
from service.api_core.session_mode_audit import _record_session_mode_switch_audit
from service.api_core.session_mode_env_binding import _infer_environment_binding_for_managed_switch
from service.api_core.session_mode_gates import (
    _enforce_switch_not_blocked_by_active_run,
    _start_managed_backing_after_switch,
)
from service.api_core.session_mode_writes import _apply_session_mode_switch_to_agent
from service.api_core.settings import _load_settings
from service.api_core.terminal_status import TERMINAL_STOPPABLE_STATUSES

logger = logging.getLogger("aify_comms.api_core.session_mode_switch")


async def switch_session_mode(db, agent_id: str, row, new_mode: str, *, requested_by: str, force: bool,
                              now: str, start_backing: bool = True) -> dict[str, Any]:
    """Apply a switch of `agent_id` (its `agents` `row`) to `new_mode` inside the caller's transaction.
    `start_backing=False` moves only the record: a switch that follows a definition starts nothing, since a start is
    the host's.

    Returns `{changed: False, previousMode}` when it is already there, else `{changed: True, previousMode,
    effectiveRuntime, sessionHandle, warnings, sideEffects}`. Raises HTTPException where the switch is refused."""
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
        return {"changed": False, "previousMode": current_mode}

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
            if not force:
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
        db, force, agent_id, new_mode, runtime, switch_warnings,
    )

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
    side_effects: dict[str, Any] = {}
    if start_backing:
        settings = await _load_settings(db)
        await _start_managed_backing_after_switch(
            db, agent_id, new_mode, runtime, settings, requested_by, now, logger, side_effects,
        )
    if forced_resident_warning:
        switch_warnings.insert(0, forced_resident_warning)
    return {"changed": True, "previousMode": current_mode, "effectiveRuntime": effective_runtime,
            "sessionHandle": switch_session_handle, "warnings": switch_warnings, "sideEffects": side_effects}


async def managed_worker_drives(db, agent_id: str) -> bool:
    """Whether a managed worker may still run for this agent: a terminal of its own that is not yet gone, `stopping`
    included, since a stop only requested is no departure its host confirmed (0.9.2 review, A P1). Not `driver_state`,
    which outlives the worker, and not a channel bridge, which a resident Claude registers too."""
    marks = ", ".join("?" * len(TERMINAL_STOPPABLE_STATUSES))
    row = await (await db.execute(
        f"SELECT 1 FROM terminal_sessions WHERE agent_id = ? AND status IN ({marks}) LIMIT 1",
        (agent_id, *sorted(TERMINAL_STOPPABLE_STATUSES)))).fetchone()
    return row is not None


async def follow_definition_mode(db, agent_id: str, *, now: str, hold: bool = False) -> dict[str, Any] | None:
    """Switch a defined agent to resident when its definition asks for resident and no managed worker drives it.

    The definition flip is the operator's deliberate switch, so the resident it asks for is adopted rather than parked as
    a candidate. Toward managed nothing is switched here: that mode governs the next start, which the host makes. None
    when there is nothing to do or the switch is refused (an active run); the next registration asks again.

    `hold`: a switch made here is left uncommitted for the caller to commit with its own writes. Registration holds it,
    because committed alone the record read resident with no driving bridge, and a hermes delivery loop claiming in that
    gap was told to release and exited (whole-diff review of 0.9.2)."""
    if await defined_mode(db, agent_id) != "resident":
        return None
    # THE CHECK AND THE SWITCH HOLD THE WRITE LOCK TOGETHER: a managed terminal committed between "no worker drives it"
    # and the switch would be stopped under the resident it was just told to make way for (0.9.2 review, A race). A
    # caller already in a transaction has written, so it holds the lock; otherwise this takes it and commits.
    own = not db.in_transaction
    if own:
        await db.execute("BEGIN IMMEDIATE")
    try:
        result = await _follow_under_lock(db, agent_id, now=now)
    except BaseException:
        if own:
            await db.rollback()
        raise
    if own and not (hold and result):
        await db.commit()
    return result


async def adopt_parked_resident(db, agent_id: str, *, now: str) -> dict[str, Any] | None:
    """Follow the definition once a managed worker's terminal ends, when that worker kept a resident parked.

    A resident registering while the worker was still `stopping` is parked as a candidate, and a resident bridge does not
    register again on its own, so without this it read `available` until relaunched (whole-diff review of 0.9.2)."""
    row = await (await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (agent_id,))).fetchone()
    if row is None or not isinstance(_json_loads_or(row["runtime_state"], {}).get("manualResidentCandidate"), dict):
        return None
    return await follow_definition_mode(db, agent_id, now=now)


async def _follow_under_lock(db, agent_id: str, *, now: str) -> dict[str, Any] | None:
    row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
    if row is None or _normalize_session_mode(row["session_mode"] or "resident") == "resident":
        return None
    if await managed_worker_drives(db, agent_id):
        return None
    try:
        return await switch_session_mode(db, agent_id, row, "resident", requested_by="definition", force=False, now=now)
    except HTTPException:
        return None


async def resident_drives(db, row):
    """True, False, or PROBE_ERROR: whether a resident may still drive this agent, from a live console, a live channel
    sidecar, a live resident session, or a fresh resident bridge. A read that failed is PROBE_ERROR, never False."""
    terminal = await terminal_state(db, row["id"])
    sidecar = await channel_sidecar_state(db, row["id"])
    if True in (terminal, sidecar):
        return True
    if PROBE_ERROR in (terminal, sidecar):
        return PROBE_ERROR
    try:
        if await live_resident_session(db, row["id"]):
            return True
        lease = int((await _load_settings(db)).get("resident_lease_seconds", 150) or 150)
        return await _resident_bridge_is_fresh(db, row, lease_seconds=lease)
    except Exception:
        return PROBE_ERROR


async def follow_definition_to_managed(db, agent_id: str, *, now: str) -> dict[str, Any] | None:
    """Switch a defined agent's record to managed when its definition asks for managed and no resident drives it (B3).

    The definition is the authority: a record left resident by its last run read offline while its host offered it as
    startable. Only the record moves; nothing is started, since the start is the host's. A resident still driving it
    keeps it, and the next push asks again; a liveness read that failed switches nothing. Inside the caller's
    transaction (the definition push). None when nothing switched."""
    if await defined_mode(db, agent_id) != "managed":
        return None
    row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
    if row is None or _normalize_session_mode(row["session_mode"] or "resident") != "resident":
        return None
    if await resident_drives(db, row) is not False:
        return None
    try:
        return await switch_session_mode(db, agent_id, row, "managed", requested_by="definition", force=False, now=now,
                                         start_backing=False)
    except HTTPException:
        return None  # refused before any write (an active run); the next push asks again
