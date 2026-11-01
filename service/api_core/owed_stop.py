"""Whether a session still owes a stop to its terminal, so its record must not be deleted yet (action audit #3).

A session's Stop marks the session `stopped` at once and queues each live terminal's stop for its host. Deleting the
session erases its terminal controls with it, so a stop still pending or claimed would never reach the worker and
nothing would record that it was owed. A stop that FAILED while its terminal still reads live is no evidence the
worker ended either. Either keeps the record; once the stop is handled, deletion proceeds as before.
"""
from __future__ import annotations

from service.api_core.terminal_status import _TERMINAL_END_STATUSES_ORDERED
from service.lifecycle_models import OPEN_STATUSES


async def owed_stop(db, session_id: str) -> str:
    """Why `session_id` still owes a stop to one of its terminals, or "" when it owes none."""
    open_marks = ", ".join("?" for _ in OPEN_STATUSES)
    owed = await (await db.execute(
        f"SELECT c.terminal_id, c.status FROM terminal_controls c JOIN terminal_sessions t ON t.id = c.terminal_id "
        f"WHERE t.session_id = ? AND c.action = 'stop' AND c.status IN ({open_marks}) LIMIT 1",
        (session_id, *OPEN_STATUSES))).fetchone()
    if owed:
        return f'a stop for terminal {owed["terminal_id"]} is still {owed["status"]}'
    end_marks = ", ".join("?" for _ in _TERMINAL_END_STATUSES_ORDERED)
    failed = await (await db.execute(
        f"SELECT c.terminal_id FROM terminal_controls c JOIN terminal_sessions t ON t.id = c.terminal_id "
        f"WHERE t.session_id = ? AND c.action = 'stop' AND c.status = 'failed' "
        f"AND LOWER(COALESCE(t.status, '')) NOT IN ({end_marks}) LIMIT 1",
        (session_id, *_TERMINAL_END_STATUSES_ORDERED))).fetchone()
    if failed:
        return f'the stop for terminal {failed["terminal_id"]} failed and that terminal still reads live'
    return ""
