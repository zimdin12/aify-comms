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


async def rotate_messages(db, policy: RotationPolicy, *, now_ms: int) -> dict[str, int]:
    """Apply the policy once. Returns what was deleted; the caller commits."""
    stats = {"expired_messages": 0, "trimmed_messages": 0}
    if policy.retention_days > 0:
        cutoff = now_ms - policy.retention_days * DAY_MS
        stats["expired_messages"] = await _delete_messages_where(db, "timestamp < ?", (cutoff,))
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
                "id IN (SELECT id FROM messages WHERE to_agent = ? ORDER BY timestamp ASC LIMIT ?)",
                (agent_id, count - policy.cap_per_agent),
            )
    if stats["expired_messages"] or stats["trimmed_messages"]:
        # A receipt for a message that is gone would otherwise stay forever.
        await db.execute("DELETE FROM read_receipts WHERE message_id NOT IN (SELECT id FROM messages)")
    return stats
