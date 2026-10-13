"""Session MODE and HANDLE: which mode an agent runs in, and which native session it owns.

v0.5.2m, one surface of the agents package. Built with `domain_router()`;
declares NO tags — the parent applies `tags=["api"]` once when api_v2 includes the package.
"""

from __future__ import annotations


from fastapi import HTTPException, Request

from service.api_core.session_mode_switch import switch_session_mode
from service.api_core.operator_authz import prove_operator, recorded_operator_actor
from service.api_core.definition_guard import defined_on
from service.api_core.definition_requests import queued_for_its_host
from service.api_core.routing import domain_router


# Imported for the ANNOTATIONS. Under postponed evaluation these are strings, so a
# missing one does not fail import -- FastAPI demotes the body to a query parameter and
# the endpoint 422s at request time. The route annotation gate caught 17 of these here.
from service.models import AgentSessionModeSwitchRequest

from service.api_core.resume_command import _resume_command_for
from service.api_core.agent_sessions import _agent_tombstone
from service.api_core.dispatch_state import _get_dispatch_state_for_agent
from service.api_core.records import _agent_record_to_dict
from service.api_core.runtime import _normalize_session_mode
from service.api_core.status_refresh import _compute_agent_status
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.db import get_db
from service.api_core.vocabulary import SESSION_MODES as _SESSION_MODES
from service.clock import now as _now

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
    # Recorded as `dashboard` when unnamed, and only the dashboard calls this route.
    requested_by = recorded_operator_actor(req.requestedBy, request, action="switching an agent's session mode as the operator")
    # THE OPERATOR'S, for every agent (external review of 0.8.4): a switch moves an undefined agent's
    # workspace and effort, which Steven's 2026-10-02 ruling makes operator-protected.
    operator = prove_operator(requested_by, request, action="change an agent's session mode")
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
        # A DEFINED AGENT'S MODE IS ITS DEFINITION'S (P0 C5): the switch becomes a request for its host.
        # An agent defined after this check is switched as before: the switch writes only effective
        # columns, which registration writes for a defined agent too, and its definition's desired
        # mode is untouched and governs its next start (C7).
        if await defined_on(db, agent_id):
            return await queued_for_its_host(db, agent_id, {"mode": new_mode}, operator, _now())

        now = _now()
        result = await switch_session_mode(db, agent_id, row, new_mode, requested_by=requested_by,
                                           force=bool(req.force), now=now)
        current_mode = result["previousMode"]
        if not result["changed"]:
            return {"ok": True, "agentId": agent_id, "mode": new_mode, "previousMode": current_mode, "changed": False}

        await db.commit()
        # Takeover/resume command for the operator. On a managed -> resident
        # switch this is the command the operator runs to drive the SAME session
        # interactively; mirrored in the dashboard. Best-effort (empty if the
        # adapter has none).
        resume_command = _resume_command_for(result["effectiveRuntime"], result["sessionHandle"], agent_id)
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
            "sideEffects": result["sideEffects"],
            "agent": _agent_record_to_dict(updated_agent, updated_status, 0, updated_dispatch_state),
        }
        if result["warnings"]:
            response_payload["warning"] = " ".join(result["warnings"])
        return response_payload
    finally:
        await db.close()
