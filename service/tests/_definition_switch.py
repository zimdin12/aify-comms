"""Run `switch_session_mode` the way a definition's desired mode does (`follow_definition_to_managed`).

Since 2026-11-02 the session-mode route refuses an undefined agent's switch to managed, so a test of
what the switch itself writes (columns, environment binding, what dispatch sees afterwards) calls the
function here rather than the route.
"""
from __future__ import annotations

import asyncio

from service.api_core.session_mode_switch import switch_session_mode
from service.clock import now
from service.db import get_db


def switch_as_definition(agent_id: str, mode: str) -> dict:
    """Switch `agent_id` to `mode` as `requested_by="definition"`, commit, and return the result."""
    async def switch():
        db = await get_db()
        try:
            row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
            result = await switch_session_mode(db, agent_id, row, mode, requested_by="definition",
                                               force=False, now=now())
            await db.commit()
            return result
        finally:
            await db.close()

    return asyncio.run(switch())
