"""An active dispatch run does not make a non-live agent read as `working`.

THE PROMOTION. `_status_with_dispatch` upgrades an agent's displayed status to `working` when it has
an activeRun in `running` state. This is deliberate and load-bearing: it exists so a just-delivered
turn reads `working` before the bridge's turn-start event lands.

THE GUARD WAS A HAND-LISTED SET: `status not in {"stale", "offline", "blocked"}`, with `stopped`
covered separately by `_MANUAL_STATUSES`. It has the same two faults as the analytics count fixed in
22e471f7:

  - It names `stale`, a status this engine stopped producing. The vocabulary's own comment reads
    "Proof-based: no time-decay states, no `idle`, no `stale`". That member guards nothing.
  - It omits `misconfigured` -- which the contract defines as "Identity exists but can never start.
    Not send-recoverable; a human must fix the config." An agent in that state carrying a stale
    `running` dispatch row was promoted to `working` on the chip an operator reads.

The comment above the guard is itself about stale claimed runs, so the input it fails on is exactly
the input it was written for.

`blocked` is excluded explicitly and is NOT a partition question. It is a LIVE status -- a turn
awaiting operator input -- and promoting it to `working` would hide that the agent is waiting on the
person reading the chip.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.api_core.records import _status_with_dispatch
from service.status_engine import NON_LIVE_AGENT_STATUSES, VALID_STATUSES

RUNNING = {"activeRun": {"status": "running"}}


class ADeadAgentIsNotPromotedToWorking(unittest.TestCase):
    def test_no_NON_LIVE_status_is_ever_promoted(self):
        """Derived from the partition rather than listed, so a fourth non-live status added later is
        covered without anyone remembering this file."""
        for status in NON_LIVE_AGENT_STATUSES:
            self.assertEqual(
                _status_with_dispatch(status, RUNNING), status,
                f"{status} was promoted to working by an active run",
            )

    def test_MISCONFIGURED_is_the_one_that_moved(self):
        """The defect, named. It was absent from the hand-listed set and `_MANUAL_STATUSES` holds
        only `stopped`, so nothing else caught it."""
        self.assertEqual(_status_with_dispatch("misconfigured", RUNNING), "misconfigured")

    def test_BLOCKED_is_still_excluded_and_for_a_different_reason(self):
        """A live status that must not be promoted anyway. If this ever reads `working`, an operator
        being waited on cannot tell."""
        self.assertEqual(_status_with_dispatch("blocked", RUNNING), "blocked")

    def test_LIVE_statuses_are_STILL_promoted(self):
        """ANTI-VACUITY, and the whole point of the promotion. A guard that refused everything would
        satisfy every assertion above and break the case the feature exists for -- a just-delivered
        turn reading `working` before the bridge's turn-start event lands."""
        promotable = [
            s for s in VALID_STATUSES
            if s not in NON_LIVE_AGENT_STATUSES and s != "blocked"
        ]
        self.assertGreaterEqual(len(promotable), 4, promotable)
        for status in promotable:
            self.assertEqual(
                _status_with_dispatch(status, RUNNING), "working",
                f"{status} should promote to working and did not",
            )

    def test_only_a_RUNNING_active_run_promotes(self):
        """The other half of the condition, pinned so a fix here cannot widen it by accident."""
        for run_status in ("queued", "claimed", "delivered", "completed", "failed", "cancelled"):
            self.assertEqual(
                _status_with_dispatch("online", {"activeRun": {"status": run_status}}), "online",
                f"an activeRun in {run_status} promoted the agent",
            )

    def test_no_dispatch_state_changes_nothing(self):
        for state in (None, {}, {"activeRun": None}, {"activeRun": {}}):
            self.assertEqual(_status_with_dispatch("online", state), "online")

    def test_an_UNKNOWN_or_EMPTY_status_is_still_promotable(self):
        """The distinction that caught a conflation. This guard uses the SET, not
        `is_live_agent_status`, because that predicate fails closed on '' -- it answers "should this
        agent count toward the live fleet", where an agent that reported nothing is no evidence of a
        live worker. Here the question is whether a running run may promote an UNKNOWN status, and
        the answer is yes: the run is the best evidence available, which is what the promotion is
        for. `test_status_with_dispatch.py` had that pinned and caught it.
        """
        for status in ("", "some-future-status"):
            self.assertEqual(
                _status_with_dispatch(status, RUNNING), "working",
                f"{status!r} stopped being promotable",
            )

    def test_the_RETIRED_status_is_not_what_this_turns_on(self):
        """`stale` was in the old set and cannot be produced. Asserted so the removal is a recorded
        decision rather than a silent tidy."""
        self.assertNotIn("stale", VALID_STATUSES)


if __name__ == "__main__":
    unittest.main()
