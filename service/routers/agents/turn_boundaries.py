"""The two ends of a turn, as a launcher not yet relaunched still posts them: accepted and ignored.

SINCE THE 0.9.8 SWITCH aify-env owns turns. Its hooks post each turn event to aify-env, which publishes the record the
operational view serves, so nothing here decides a word any more. 0.9.9 S2 stopped applying these posts: a launcher
that still sends them gets 200 and changes no turn state. They still prove the agent was here (`last_present_at`,
which the away briefing reads), because the agent authored the request.

The router is built through `domain_router()`, which rejects a hand-passed `route_class`, so a new surface cannot opt out
of the bounded SQLite write-lock retry.
"""

from __future__ import annotations

from fastapi import HTTPException

from service.api_core.agent_sessions import _agent_tombstone, _mark_agent_present
from service.api_core.routing import domain_router
from service.db import get_db

router = domain_router()

IGNORED = "aify-env-owns-turns"


async def accept_and_ignore(agent_id: str) -> dict:
    """Answer a legacy turn post: a removed agent is 410 and an unknown one 404, as before; any other is present."""
    db = await get_db()
    try:
        if await _agent_tombstone(db, agent_id):
            raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
        if not await (await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))).fetchone():
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        await _mark_agent_present(db, agent_id)
        await db.commit()
        return {"ok": True, "agentId": agent_id, "ignored": IGNORED}
    finally:
        await db.close()


@router.post("/agents/{agent_id}/turn-start")
async def agent_turn_start(agent_id: str):
    return await accept_and_ignore(agent_id)


@router.post("/agents/{agent_id}/turn-end")
async def agent_turn_end(agent_id: str):
    return await accept_and_ignore(agent_id)
