"""Pushing an agent's status to dashboards without waiting for the next poll.

WHY PUSH AT ALL. An operator-driven transition -- a stop, a mode switch, a confirmed session -- is
invisible until something recomputes status, and the reconcile sweep runs every 60 seconds. Without
a push the dashboard shows the old state for up to a minute after the operator clicked, which reads
as the click not having worked.

ONE WORD, TWO NAMES. `_broadcast_agent_status` served the polled compute and `_broadcast_engine_status`
the proof-based engine, each pushing what its own read returned. Since the 0.9.8 switch both reads are
the operational view's, and 0.9.9 S3b deleted the legacy derivation each also ran, so both push the
same word, note and last-known the poll serves. Both names stay because their callers name the moment
(an operator action, an engine event), not the path.

BEST-EFFORT AND SWALLOWS EVERYTHING. A failed push must never fail the request that triggered it --
the state change already happened, and the next poll will show it.
"""
from __future__ import annotations

from service.api_core import switched_view


async def _broadcast_agent_status(ws, db, agent_id: str) -> None:
    """Push one agent's word to dashboards, as the next poll will serve it."""
    await _push_word(ws, db, agent_id)


async def _broadcast_engine_status(ws, db, agent_id: str, *, settings=None) -> None:
    """Push one agent's word to dashboards the moment an event moved it. `settings` is accepted from callers that
    hold them and is no longer read."""
    await _push_word(ws, db, agent_id)


async def _push_word(ws, db, agent_id: str) -> None:
    if ws is None:
        return
    try:
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            return
        # THE PUSHED WORD is the operational view's, the same one the poll serves: aify-env's record, unknown first,
        # the D8 rows (an undefined agent's operator stop among them) and row 5, with the poll's note
        # (switched_view.served_note). An unreadable view pushes `unknown`, never a word comms made up.
        try:
            view = await switched_view.view_for(db, row)
            status, note, last_known = view.status, await switched_view.served_note(db, row, view), switched_view.last_known_api(view)
        except Exception:
            status, note, last_known = "unknown", "comms could not read aify-env's record of this agent", None
        await ws.broadcast("agent_status", {
            "agentId": agent_id,
            "status": status or "",
            "statusNote": note or "",
            "lastKnown": last_known,
        })
    except Exception:
        pass
