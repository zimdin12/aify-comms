"""One policy, and the property that lets its callers take a shortcut.

DELIVERY AND STATUS ANSWERED THE SAME QUESTION DIFFERENTLY. Delivery implemented the operator's
"trust only verifiable renewals" ruling — a verifiable bridge renews to four hours, anything else is
cut at the strict thirty-minute anchor. The status clamp cut everything at thirty minutes. So a
working agent with a live bridge kept its queued work for hours, correctly, while the dashboard said
it had stopped after half an hour. The reviewer caught it as a policy mismatch; this file is the
single answer both now call.

MONOTONICITY IS LOAD-BEARING, not a nicety. Both status readers ask the strict question FIRST and
pay for the ownership query only when the answer is no — because that query is per-agent and the
prefetch module beside them exists to stop per-agent round-trips. That shortcut is exact only while
verifying a lease can ADD liveness and never remove it, so it is asserted over the input grid rather
than argued for in a comment.
"""

from __future__ import annotations

import time
import unittest

from service.api_core.turn_liveness_policy import turn_is_still_live

STRICT = 1800.0
ABSOLUTE = 4 * 60 * 60.0


class TurnLivenessPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()

    def live(self, *, started_ago=None, touched_ago=None, renewable=False):
        return turn_is_still_live(
            started_epoch=(self.now - started_ago) if started_ago is not None else 0.0,
            touched_epoch=(self.now - touched_ago) if touched_ago is not None else 0.0,
            renewable=renewable,
            now_epoch=self.now,
            strict_seconds=STRICT,
            absolute_max_seconds=ABSOLUTE,
        )

    def test_an_unverified_turn_is_cut_at_the_strict_anchor(self):
        self.assertTrue(self.live(started_ago=STRICT - 5, touched_ago=1))
        self.assertFalse(self.live(started_ago=STRICT + 5, touched_ago=1))

    def test_a_re_stamping_poster_cannot_postpone_an_unverified_turn(self):
        """THE DEFECT, in one line: two hours in, touched a second ago."""
        self.assertFalse(self.live(started_ago=7200, touched_ago=1))

    def test_a_VERIFIED_turn_renews_against_the_last_renewal(self):
        self.assertTrue(self.live(started_ago=7200, touched_ago=1, renewable=True))

    def test_but_a_verified_turn_is_still_bounded_absolutely(self):
        """A renewable lease with no ceiling is the permanent strand again in a better hat."""
        self.assertTrue(self.live(started_ago=ABSOLUTE - 60, touched_ago=1, renewable=True))
        self.assertFalse(self.live(started_ago=ABSOLUTE + 60, touched_ago=1, renewable=True))

    def test_no_timestamps_at_all_is_not_a_live_turn(self):
        """Every writer stamps something, so a blank pair is a corrupt row. Both callers prefer the
        recoverable failure: one message delivered mid-turn beats an agent that never receives work
        again."""
        self.assertFalse(self.live(renewable=False))
        self.assertFalse(self.live(renewable=True))

    def test_a_FUTURE_timestamp_must_not_hold_the_turn_open(self):
        """`now - seen` goes negative for a clock-skewed write, which trivially satisfies `<=
        ceiling` — so the turn would be live for ever, the exact strand the ceiling bounds."""
        self.assertFalse(self.live(started_ago=-600, touched_ago=-600))
        # Verified, a future TOUCH falls back to the start anchor rather than trusting it.
        self.assertTrue(self.live(started_ago=60, touched_ago=-600, renewable=True))
        self.assertFalse(self.live(started_ago=7200, touched_ago=-600, renewable=True))

    def test_a_touch_OLDER_than_the_start_is_a_corrupt_row_not_a_stale_renewal(self):
        """A renewal cannot precede the turn it renews. Taking it would make a turn that began ten
        seconds ago look half an hour idle — and that inversion is what would break the callers'
        strict-first shortcut."""
        self.assertTrue(self.live(started_ago=10, touched_ago=7200, renewable=True))

    def test_VERIFYING_A_LEASE_NEVER_REMOVES_LIVENESS(self):
        """The property the callers' shortcut depends on, asserted over the grid rather than argued.

        Both status readers evaluate the strict verdict first and only pay for the per-agent
        ownership query when it says no. If verification could ever turn a live turn dead, that
        shortcut would silently disagree with delivery on those rows.
        """
        starts = (None, 1, 10, 100, STRICT - 1, STRICT, STRICT + 1, 3600, 7200,
                  ABSOLUTE - 1, ABSOLUTE + 1)
        touches = (None, -600, -1, 1, 5, 100, STRICT - 1, STRICT + 1, 7200, 20000)
        inversions = []
        for started in starts:
            for touched in touches:
                strict = self.live(started_ago=started, touched_ago=touched, renewable=False)
                verified = self.live(started_ago=started, touched_ago=touched, renewable=True)
                if strict and not verified:
                    inversions.append((started, touched))
        self.assertEqual(inversions, [], f"verification removed liveness at {inversions}")

    def test_the_grid_is_not_vacuous(self):
        """ANTI-VACUITY for the test above: it would pass trivially if the policy said no to
        everything, or yes to everything."""
        self.assertTrue(self.live(started_ago=10, touched_ago=1))
        self.assertFalse(self.live(started_ago=ABSOLUTE + 60, touched_ago=1, renewable=True))
