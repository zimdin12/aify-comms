"""A spawn that never produced a worker must not block the restart that would fix it.

REPORTED AS A DEADLOCK by sc-manager, 2026-08-18, with the full timeline:

    02:12  send -> spawn_… created, queued
    02:18  backstop fails the run: "up-but-deaf or never started a worker ... Restart the agent's
           worker (managed: respawn its delivery loop / console), then resend."
    ~02:17 operator restarted the bridge AND all wrappers
    02:25  comms_restart -> HTTP 409: already has pending spawn request spawn_… (queued)

The spawn from 02:12 was still pending at 02:25, having survived an operator restart, and it refused
the exact action the backstop's own message prescribes. No worker, so the spawn stayed queued; a
spawn pending, so the restart 409'd. From inside a session there was no exit.

THE GUARD IS RIGHT TO EXIST — two concurrent spawns for one agent race for the same terminal and the
loser is a leaked worker. It was fail-safe in ONE direction only: it protected against double-spawn
at the cost of making a stuck spawn permanent.

A TTL, NOT A `force` FLAG. sc-manager offered both. A flag needs the caller to recognise the
situation and choose correctly under pressure, and it gets passed by habit once somebody has hit the
409 twice; a TTL needs nobody to know anything and cannot be misused. PROGRESS resets it, so a slow
but healthy spawn is never superseded — only one that has not moved at all.
"""

from __future__ import annotations

import unittest

from service.api_core.abandoned_spawn import ABANDONED_SPAWN_SECONDS, _spawn_request_is_abandoned


#: Verbatim, so `test_every_refusal_is_exercised` can attribute the 409 to this file. The refusal now
#: names its own escape: sc-manager hit it with no way forward, so the message says how it resolves
#: rather than leaving the caller to discover that it does not.
REFUSAL = "). If it never produces a worker it is superseded automatically after "


def _row(**kwargs) -> dict:
    base = {"id": "spawn-1", "created_at": "", "updated_at": "", "claimed_at": "", "started_at": ""}
    base.update(kwargs)
    return base


NOW = "2026-08-18T12:00:00Z"
LONG_AGO = "2026-08-18T11:00:00Z"       # an hour, well past the window
RECENT = "2026-08-18T11:59:00Z"          # a minute


class AnAbandonedSpawnIsSuperseded(unittest.TestCase):
    def test_a_spawn_that_has_not_moved_for_the_whole_window_is_abandoned(self):
        self.assertTrue(_spawn_request_is_abandoned(
            _row(created_at=LONG_AGO, updated_at=LONG_AGO), now=NOW))

    def test_a_spawn_that_updated_RECENTLY_is_not(self):
        """The healthy slow case. Superseding it would create the double-spawn the guard exists to
        prevent, which is the failure this fix must not trade for the one it removes."""
        self.assertFalse(_spawn_request_is_abandoned(
            _row(created_at=LONG_AGO, updated_at=RECENT), now=NOW))

    def test_PROGRESS_is_the_latest_of_the_three_stamps_not_creation(self):
        """Keying on `created_at` would supersede a spawn actively working through a slow start —
        exactly the healthy case — and make stuck and slow indistinguishable. Every real step a spawn
        takes writes one of these."""
        for field in ("updated_at", "claimed_at", "started_at"):
            with self.subTest(field=field):
                self.assertFalse(
                    _spawn_request_is_abandoned(_row(created_at=LONG_AGO, **{field: RECENT}), now=NOW),
                    f"{field} is progress and must reset the window",
                )

    def test_a_row_with_NO_timestamps_is_not_abandoned(self):
        """Absent evidence is not evidence of absence, and the cost of guessing wrong here is a
        double spawn."""
        self.assertFalse(_spawn_request_is_abandoned(_row(), now=NOW))

    def test_None_is_not_abandoned(self):
        self.assertFalse(_spawn_request_is_abandoned(None, now=NOW))

    def test_the_window_is_well_past_the_undeliverable_backstop(self):
        """The 180s backstop fails the DISPATCH. This supersedes the SPAWN. If this fired first, a
        restart could cancel a spawn while the delivery it belongs to was still live."""
        self.assertGreater(ABANDONED_SPAWN_SECONDS, 180)

    def test_the_boundary_is_inclusive(self):
        # Exactly at the window counts as abandoned; one second under does not.
        at = _row(created_at="2026-08-18T11:50:00Z", updated_at="2026-08-18T11:50:00Z")
        self.assertTrue(_spawn_request_is_abandoned(at, now=NOW, seconds=600))
        self.assertFalse(_spawn_request_is_abandoned(at, now=NOW, seconds=601))


if __name__ == "__main__":
    unittest.main()
