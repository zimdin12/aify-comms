"""Is this exception SQLite lock contention? One question, its own leaf.

v0.5.4. It went to `service/db.py` first — the module that owns the connection, and the right subject —
and the reviewer refused that: `db.py` was 995 lines and the move took it to 1006. Reducing
`control_plane.py` by creating a NEW over-1000 file is a shell game, and the oversized-file goal does not
care which file is oversized.

So the subject is preserved without bloating the connection owner. This module imports nothing from this
service and no database driver — not even sqlite3 — because the check is duck-typed on the exception's
text, which is why it can sit below everything else. (`re` arrived 2026-08-17 with the left-hand guard
below; the point of the original claim was the absence of a driver dependency, and that still holds.)

Its two readers, `service/routers/sessions.py` and `service/routers/agents/shared.py`, reached it through
borrow shims when it lived in the control plane.
"""

from __future__ import annotations

import re

# The marker must START a word. Guarded on the LEFT only, and that asymmetry is the whole fix.
#
# 2026-08-17: this was `"locked" in message or "busy" in message`, and `"blocked"[1:]` is exactly
# `"locked"` — so ANY exception whose text mentioned a block was classified as SQLite contention and
# swallowed by all four callers. Nothing on those paths raised such a message (measured), so it was
# latent; but `blockedBy` is live vocabulary in this service's dispatch layer, and the two were one
# refusal away from meeting. `unlocked` had the same shape.
#
# A `\b` word boundary would have been the obvious guard and would have BROKEN a real form:
# `sqlite_busy` has an underscore before the marker, and `_` is a word character, so `\bbusy\b` does
# not match it. A lookbehind on letters does. The right-hand side is left unguarded on purpose —
# `busy_timeout` should still match, and erring broad on the tail is the safe direction for a
# predicate whose other failure mode is a 503 on a read endpoint.
_CONTENTION_MARKER = re.compile(r"(?<![a-z])(?:locked|busy)")


def _is_lock_error(exc: BaseException) -> bool:
    """True for a transient SQLite contention error (`database is locked` / `busy`). Used by
    the read endpoints to skip their best-effort cache writes and serve cached data rather than
    503 — a SELECT never takes the write lock in WAL, so a read can always succeed."""
    message = str(exc or "").lower()
    return bool(_CONTENTION_MARKER.search(message))
