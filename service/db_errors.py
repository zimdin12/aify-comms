"""Is this exception SQLite lock contention? One question, its own leaf.

v0.5.4. It went to `service/db.py` first — the module that owns the connection, and the right subject —
and the reviewer refused that: `db.py` was 995 lines and the move took it to 1006. Reducing
`control_plane.py` by creating a NEW over-1000 file is a shell game, and the oversized-file goal does not
care which file is oversized.

So the subject is preserved without bloating the connection owner. This module imports NOTHING — not even
sqlite3 — because the check is duck-typed on the exception's text, which is also why it can sit below
everything else.

Its two readers, `service/routers/sessions.py` and `service/routers/agents/shared.py`, reached it through
borrow shims when it lived in the control plane.
"""

from __future__ import annotations


def _is_lock_error(exc: BaseException) -> bool:
    """True for a transient SQLite contention error (`database is locked` / `busy`). Used by
    the read endpoints to skip their best-effort cache writes and serve cached data rather than
    503 — a SELECT never takes the write lock in WAL, so a read can always succeed."""
    message = str(exc or "").lower()
    return "locked" in message or "busy" in message
