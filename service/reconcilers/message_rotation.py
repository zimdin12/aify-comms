"""Message rotation: expire messages older than a window, and cap how many each agent keeps.

Both limits are OFF at 0, which is the default. Rotation DELETES messages and nothing brings them
back, so it runs only when the operator has set a limit.

Until 2026-09-19 rotation existed only as `POST /rotate`, which nothing called, so its settings
(`retention_days` 90, `max_messages_per_agent` 1000) had never taken effect on any host. Those keys
are retired rather than reused: a host whose settings page was ever saved holds 90 and 1000 in the
table, and scheduling rotation under the old names would have started deleting there silently.

The sweep runs every minute; rotation needs to run far less often, so `RotationSchedule` holds it
to once an hour and to the first sweep after a restart.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from service.api_core.dispatch_state import _DISPATCH_TERMINAL_STATUSES
from service.api_core.message_store import _delete_messages_where

DAY_MS = 86_400_000
ROTATION_INTERVAL_SECONDS = 3600


@dataclass(frozen=True)
class RotationPolicy:
    """The two limits, as the operator set them. 0 means that limit is off."""

    retention_days: int
    cap_per_agent: int

    @classmethod
    def from_settings(cls, settings: dict) -> "RotationPolicy":
        return cls(
            retention_days=max(0, int(settings.get("message_retention_days", 0) or 0)),
            cap_per_agent=max(0, int(settings.get("message_cap_per_agent", 0) or 0)),
        )

    @property
    def active(self) -> bool:
        return self.retention_days > 0 or self.cap_per_agent > 0


class RotationSchedule:
    """When the sweep last rotated. Due on the first sweep, then once per interval."""

    def __init__(self, interval_seconds: float = ROTATION_INTERVAL_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self._interval = interval_seconds
        self._clock = clock
        self._last: float | None = None

    def due(self) -> bool:
        return self._last is None or self._clock() - self._last >= self._interval

    def mark(self) -> None:
        self._last = self._clock()


SWEEP_SCHEDULE = RotationSchedule()


#: A message an OPEN dispatch run points at is not history, whatever its age.
#:
#: EXTERNAL REVIEW, 2026-09-21, finding 4. Rotation filtered on age and recipient only, and
#: `message_store` nulls `dispatch_runs.message_id` for a message it deletes -- while the closing
#: path matches a reply on `WHERE target_agent = ? AND message_id = ?`. So rotation could delete the
#: message a live run was waiting on, leaving the run open and overdue with no id left to quote and
#: nothing able to close it. The cap half could also delete the REPLY out of the sender's inbox,
#: which `reconcilers/managed_workers.py` scans for exactly that purpose.
#:
#: A run points at TWO messages: its request (`message_id`) and, once answered, its reply
#: (`result_message_id`). An unthreaded reply is linked when it is SENT
#: (`api_core/reply_linking.py`), and a managed run stays `running` after that until its turn ends.
#: Guarding only the request left the reply trimmable, and deleting it NULLs `result_message_id`:
#: the run owed an answer again that had already been given. The reconciler's own scan runs in the
#: sweep BEFORE rotation, so a reply it can link is a `result_message_id` by the time this reads.
#:
#: DERIVED from `_DISPATCH_TERMINAL_STATUSES`, so a fourth ending added later is excluded here
#: without anyone remembering this file. Open means "not ended": that is the eager direction, and
#: eager is correct for a guard whose failure mode is deleting somebody's in-flight work.
_OPEN_RUN_MESSAGE_IDS = (
    " AND id NOT IN (SELECT pointed FROM ("
    "   SELECT message_id AS pointed, status FROM dispatch_runs"
    "   UNION ALL SELECT result_message_id, status FROM dispatch_runs)"
    " WHERE COALESCE(pointed, '') != ''"
    f"   AND status NOT IN ({','.join('?' * len(_DISPATCH_TERMINAL_STATUSES))}))"
)
_OPEN_RUN_PARAMS = tuple(sorted(_DISPATCH_TERMINAL_STATUSES))


async def rotate_messages(db, policy: RotationPolicy, *, now_ms: int) -> dict[str, int]:
    """Apply the policy once. Returns what was deleted; the caller commits.

    Nothing an open dispatch run still points at is deleted, however old it is -- see
    `_OPEN_RUN_MESSAGE_IDS`.
    """
    stats = {"expired_messages": 0, "trimmed_messages": 0}
    if policy.retention_days > 0:
        cutoff = now_ms - policy.retention_days * DAY_MS
        stats["expired_messages"] = await _delete_messages_where(
            db, "timestamp < ?" + _OPEN_RUN_MESSAGE_IDS, (cutoff, *_OPEN_RUN_PARAMS)
        )
    if policy.cap_per_agent > 0:
        # The cap is per RECIPIENT, and trimming takes the OLDEST: an inbox trimmed from the other
        # end leaves an agent holding only history it has already dealt with.
        cursor = await db.execute(
            "SELECT to_agent, COUNT(*) FROM messages WHERE to_agent IS NOT NULL AND to_agent != ''"
            " GROUP BY to_agent HAVING COUNT(*) > ?",
            (policy.cap_per_agent,),
        )
        for agent_id, count in await cursor.fetchall():
            stats["trimmed_messages"] += await _delete_messages_where(
                db,
                "id IN (SELECT id FROM messages WHERE to_agent = ? ORDER BY timestamp ASC LIMIT ?)"
                + _OPEN_RUN_MESSAGE_IDS,
                (agent_id, count - policy.cap_per_agent, *_OPEN_RUN_PARAMS),
            )
    # NO SWEEP OF read_receipts HERE. `_delete_messages_by_ids` already deletes the receipts for the
    # exact ids it removes, by id, in the same transaction -- so the `NOT IN (SELECT id FROM
    # messages)` that used to run here re-answered a question already settled, as a FULL SCAN of
    # both tables under the single writer lock. That is the shape `sweep.py` records as the
    # "database is locked" incident, on a path that runs hourly (review finding 12).
    return stats
