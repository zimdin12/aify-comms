"""A registration may not bind a session id that a DIFFERENT live agent already holds.

`PATCH /agents/{id}/session-handle` has refused this since 2026-05-31 (`_session_handle_live_owner`),
but `POST /agents` wrote whatever `sessionHandle` it was sent. The bridge fills that field on every
`comms_register` from the session it runs in, so registering a second name from inside a live
session gave both rows one conversation, with no refusal and no warning. Measured 2026-09-16 against
this route: agent `b` registered with live agent `a`'s id and was stored holding it.

The refusal matches the heartbeat route's: the agent registers WITHOUT the id, the id is parked in
`pending_session_id`, and `status_note` names the live owner, so the dashboard's Confirm is the way
to adopt it deliberately. A dead owner is not a collision, exactly as there.

DB ACCESS: `db` is passed in. `park_registration_handle_collision` does not commit and joins the
handler's transaction. `after_registration_path` DOES commit, because it runs after a path that has
already committed its own writes and returned.
"""

from __future__ import annotations

from service.api_core.agent_sessions import _session_handle_live_owner
from service.api_core.settings import _load_settings


async def registration_handle_owner(db, agent_id: str, session_handle: str):
    """The live agent OTHER than `agent_id` holding `session_handle`, or None."""
    if not session_handle:
        return None
    settings = await _load_settings(db)
    return await _session_handle_live_owner(
        db, session_handle, exclude_agent_id=agent_id,
        lease_seconds=settings.get("resident_lease_seconds", 150),
    )


async def park_registration_handle_collision(db, agent_id: str, session_handle: str, owner, now: str) -> dict:
    """Record the refused id on the registered row; return the fields the response carries."""
    note = (
        f"session-collision: registered id '{session_handle}' is already owned by live "
        f"agent '{owner['agentId']}' ({owner['sessionMode']}); registered without it. "
        "Two live agents must not share one session id."
    )
    await db.execute(
        "UPDATE agents SET pending_session_id = ?, status_note = ? WHERE id = ?",
        (session_handle, note, agent_id),
    )
    return {"sessionCollision": {"pendingSessionId": session_handle, "collisionWith": owner["agentId"], "note": note}}


async def after_registration_path(db, agent_id: str, session_handle: str, owner, now: str, response: dict) -> dict:
    """For the two early-return registration paths: park the refused id once the path has committed."""
    if not owner:
        return response
    collision = await park_registration_handle_collision(db, agent_id, session_handle, owner, now)
    await db.commit()
    return {**response, **collision}
