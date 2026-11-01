"""Whether a session row is its agent's CURRENT one, or a newer row has superseded it (action audit #2, 2026-11-02).

A session's Stop, Restart and Reset become the agent's lifecycle action, which acts on the lifetime its host
publishes now. From a superseded row that would stop or restart the agent's newer worker, so those controls are
admitted only for the current row: the agent's live session (`_current_agent_session_row`, the picker every live
read uses), or, when none is live, its latest. The latest ended row keeps its cold Start agent and Reset.
"""
from __future__ import annotations

from fastapi import HTTPException

from service.api_core.agent_sessions import _current_agent_session_row


async def superseding_session(db, session_id: str, agent_id: str) -> str:
    """The id of the session that supersedes `session_id` for `agent_id`, or "" when `session_id` is current."""
    live = await _current_agent_session_row(db, agent_id)
    if live is not None:
        return "" if live["id"] == session_id else live["id"]
    latest = await (await db.execute(
        "SELECT id FROM agent_sessions WHERE agent_id = ? ORDER BY started_at DESC, rowid DESC LIMIT 1",
        (agent_id,))).fetchone()
    return "" if latest is None or latest["id"] == session_id else latest["id"]


def refuse_unless_current(session_id: str, agent_id: str):
    """An admission check for `delegate`: re-reads the row under the admission's lock, so a newer session or a rename
    landing after the route first read it is seen, and refuses a superseded row's control with 409."""
    async def admit(db):
        row = await (await db.execute("SELECT agent_id FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
        if row is None or row["agent_id"] != agent_id:
            raise HTTPException(409, f'Session "{session_id}" no longer belongs to "{agent_id}"; reload and act on '
                                     f'the agent\'s current session')
        newer = await superseding_session(db, session_id, agent_id)
        if newer:
            raise HTTPException(409, f'Session "{session_id}" was superseded by "{newer}", so its controls would act on '
                                     f'that newer session\'s worker; act on "{newer}" instead')
    return admit
