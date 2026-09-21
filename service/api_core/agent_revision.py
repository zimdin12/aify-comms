"""A short fingerprint of an agent's record, so a bridge re-reads the record only when it changed.

WHY THIS EXISTS. Every resident bridge fetched `GET /agents/{id}` on every dispatch-loop tick, only to
notice a Stop on a resident agent and to refresh its cached copy of the record. A managed hermes
bridge never long-polls a claim, so its tick is the 3 s poll interval and it fetched twenty times a
minute to learn nothing. The heartbeat it already sends now answers with this revision, and the
bridge fetches only when the revision moves.

EVERY COLUMN EXCEPT THE TWO THAT MOVE ON EVERY BEAT. Measured 2026-09-18 on the live fleet: over
12 s, `last_seen` was the only `agents` column that changed. Everything else (status, mode, model,
config, runtime state) is exactly what a bridge must re-read when it moves, and a Stop moves
`status`.

`last_present_at` joined it on 2026-09-21 and had to be excluded in the same change: the heartbeat
writes it, so leaving it in would move the revision on every beat and send every bridge back to
fetching the record twenty times a minute -- the exact cost this file exists to remove, restored
silently by an unrelated column. The suite caught it; the reasoning did not.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

#: Columns that move on every beat and so say nothing about whether the record changed.
_EXCLUDED = frozenset({"last_seen", "last_present_at"})


def agent_revision(row: Mapping[str, Any]) -> str:
    """The revision of one `agents` row: equal exactly when every non-liveness column is equal."""
    fields = {key: row[key] for key in row.keys() if key not in _EXCLUDED}
    encoded = json.dumps(fields, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
