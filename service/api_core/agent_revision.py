"""A short fingerprint of an agent's record, so a bridge re-reads the record only when it changed.

WHY THIS EXISTS. Every resident bridge fetched `GET /agents/{id}` on every dispatch-loop tick, only to
notice a Stop on a resident agent and to refresh its cached copy of the record. A managed hermes
bridge never long-polls a claim, so its tick is the 3 s poll interval and it fetched twenty times a
minute to learn nothing. The heartbeat it already sends now answers with this revision, and the
bridge fetches only when the revision moves.

EVERY COLUMN EXCEPT `last_seen`. Measured 2026-09-18 on the live fleet: over 12 s, `last_seen` was
the only `agents` column that changed. Everything else (status, mode, model, config, runtime state)
is exactly what a bridge must re-read when it moves, and a Stop moves `status`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

#: Columns that move on every beat and so say nothing about whether the record changed.
_EXCLUDED = frozenset({"last_seen"})


def agent_revision(row: Mapping[str, Any]) -> str:
    """The revision of one `agents` row: equal exactly when every non-liveness column is equal."""
    fields = {key: row[key] for key in row.keys() if key not in _EXCLUDED}
    encoded = json.dumps(fields, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
