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

    def test_the_ceiling_SKIPPED_for_an_anchorless_row_grants_nothing(self):
        """DISP-L1, and it is a non-defect. Pinned so nobody "fixes" it again -- I tried twice.

        The finding is accurate as written: `if started_epoch and (now - started) > absolute_max`
        does skip the 4-hour ceiling for a row with no start anchor. The inference that this leaves
        such a turn unbounded is what does not hold.

        Without an anchor the renewable branch computes `seen = max(usable_touch, 0)`, which IS the
        touch column -- exactly what the unverified branch falls back to -- and both then face the
        same `age <= strict_seconds`.

        THE ORIGINAL REASONING WENT ON TO SAY the strict rule is "tighter than the ceiling it skips",
        AND THAT IS WRONG -- corrected 2026-09-04 by external review, Round 8 M12. The two bound
        DIFFERENT quantities: strict bounds the SILENCE GAP since anything last touched the row, and
        the ceiling bounds the DURATION of the turn. A turn touched every thirty seconds for a week
        never trips strict, and the ceiling that would have stopped it is exactly the one being
        skipped. `turn_liveness_policy`'s own docstring says the touch column cannot bound a turn.

        AND THIS TEST DOES NOT GUARD THE CONCLUSION EITHER. It asserts that a renewable row answers
        the same as an unverified one -- and an UNBOUNDED pair answers the same too, because both say
        live. It measures parity, and parity is not the property. Kept because parity IS worth
        holding, and renamed in the mind rather than the file: what it proves is that verification
        adds nothing here, not that anything is bounded.

        WHAT MAKES DISP-L1 A NON-DEFECT is that no anchorless busy row is ever WRITTEN: one writer of
        `turn_busy = 1`, and it binds `turn_started_at` in the same statement.
        `test_every_busy_turn_has_a_start_anchor.py` is the gate for that, and it is where the real
        protection now lives.

        Two repairs were written and both were wrong. Bounding against `started_epoch or
        touched_epoch` measures the ceiling from the column a timer-driven poster refreshes, so it
        never arrives. Refusing an anchorless renewable turn breaks
        `test_VERIFYING_A_LEASE_NEVER_REMOVES_LIVENESS` -- the property both status readers' shortcut
        depends on -- by making verification turn a live turn dead.

        Measured alongside: 0 of 24 `agent_turn_state` rows lack `turn_started_at`.
        """
        for touched in (1, 100, STRICT - 1, STRICT + 1, 7200, 20000):
            self.assertEqual(
                self.live(touched_ago=touched, renewable=True),
                self.live(touched_ago=touched, renewable=False),
                f"an anchorless row answers differently when verified (touched_ago={touched}), so the "
                "skipped ceiling now grants something and DISP-L1 has become real",
            )

    def test_and_the_ceiling_still_bites_a_row_that_HAS_an_anchor(self):
        """The other half: dropping DISP-L1 must not be read as "the ceiling does nothing"."""
        self.assertTrue(self.live(started_ago=ABSOLUTE - 60, touched_ago=1, renewable=True))
        self.assertFalse(self.live(started_ago=ABSOLUTE + 60, touched_ago=1, renewable=True))
