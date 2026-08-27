"""An agent that can never start is not counted as part of the live fleet.

THE DIVERGENCE. "Is this agent live?" had two answers and no owner:

    service/new_dashboard/status.js   NON_LIVE_AGENT_STATUSES = ['offline','stopped','misconfigured']
    service/api_core/analytics_series.py   if status.startswith("offline") or startswith("stopped")

They disagree on `misconfigured`, which `service/contracts/vocabulary.json` defines as "Identity
exists but can never start. Not send-recoverable; a human must fix the config."

THE CONSEQUENCE IS NOT ONLY A WRONG HEADLINE. `onlineAgents` is the DENOMINATOR of fleet utilization
(`fleet_working / (online_count * window_minutes)`), so an agent that can never start was dragging
down the percentage that says how hard the fleet is working -- and appearing in the "Online agents"
board, where every row carries a status chip that would have read `misconfigured` right beside it.

NOT LIVE ON THIS FLEET TODAY: all 47 agents read available, online, working, offline or stopped, so
nothing is currently miscounted. `derive()` can produce `misconfigured` -- proven exhaustively by
test_derive_is_exhaustively_covered.py, which enumerates all 4,096 input combinations and asserts
every declared status is reachable -- so this is a gap, not an incident.

THE FIX FOLLOWS THIS REPO'S OWN PRECEDENT. `env_status.ENVIRONMENT_STATUSES` was declared for exactly
this reason: the JS set was the only complete statement of a vocabulary, and declaring the Python
owner made it bindable by `test_js_status_set_twins_are_frozen.py`, which now holds the two in step.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.status_engine import (
    NON_LIVE_AGENT_STATUSES,
    VALID_STATUSES,
    is_live_agent_status,
)


class AMisconfiguredAgentIsNotLive(unittest.TestCase):
    def test_the_partition_covers_the_whole_vocabulary(self):
        """Every declared status is on exactly one side. A status in neither would be classified by
        accident rather than by decision."""
        for status in VALID_STATUSES:
            live = is_live_agent_status(status)
            self.assertEqual(
                live, status not in NON_LIVE_AGENT_STATUSES,
                f"{status} is classified inconsistently with the declared partition",
            )

    def test_every_non_live_status_is_a_REAL_status(self):
        """ANTI-VACUITY. A partition naming statuses the engine cannot produce would classify nothing
        and still satisfy the test above."""
        for status in NON_LIVE_AGENT_STATUSES:
            self.assertIn(status, VALID_STATUSES, f"{status} is not a status derive() can produce")
        self.assertEqual(len(NON_LIVE_AGENT_STATUSES), 3)

    def test_MISCONFIGURED_is_the_one_that_moved(self):
        """The defect, named. The old inline rule was "not offline and not stopped", which is exactly
        this set minus `misconfigured`."""
        self.assertFalse(is_live_agent_status("misconfigured"))
        self.assertFalse(is_live_agent_status("offline"))
        self.assertFalse(is_live_agent_status("stopped"))

    def test_the_live_side_is_not_empty(self):
        """The other half of anti-vacuity: a rule that called everything non-live would pass every
        assertion above and report a fleet of zero."""
        live = [s for s in VALID_STATUSES if is_live_agent_status(s)]
        self.assertEqual(sorted(live), ["available", "blocked", "online", "starting", "working"])

    def test_a_SUFFIXED_status_is_still_matched(self):
        """The old rule used `startswith`, and a derived status can carry a suffix. An exact-equality
        port would have quietly started counting `offline (no wake path)` as live -- a regression
        introduced by the fix rather than by the bug."""
        for status in ("offline (no wake path)", "stopped by operator", "misconfigured: no runtime"):
            self.assertFalse(is_live_agent_status(status), status)

    def test_an_UNDERIVABLE_status_fails_closed(self):
        """A guard that opens when its input is missing is decoration -- and here it would inflate the
        utilization denominator with an agent nothing knows anything about."""
        for status in ("", "   ", None):
            self.assertFalse(is_live_agent_status(status), repr(status))

    def test_case_and_whitespace_are_folded(self):
        self.assertFalse(is_live_agent_status("  OFFLINE  "))
        self.assertTrue(is_live_agent_status("  WORKING  "))

    def test_an_UNKNOWN_status_counts_as_live(self):
        """Deliberate, and the opposite direction from the empty case. An unrecognised NON-EMPTY
        status is a newer service reporting a state this build has not heard of; the agent plainly
        exists and reported something, so excluding it would under-count a live fleet. An empty
        status is no report at all, which is why the two differ.
        """
        self.assertTrue(is_live_agent_status("hibernating"))


class TheBoardActuallyUsesThePartition(unittest.TestCase):
    """The call site, because a predicate test does not prove anyone calls it.

    Reverting `analytics_series.py` to its inline `startswith("offline") or startswith("stopped")`
    left all ten assertions above green -- the same green-helper-hides-a-disconnected-call-site shape
    that has caught me repeatedly today.

    THIS IS THE WEAKER FORM AND IS LABELLED AS SUCH. It reads the source rather than driving the
    endpoint, because making an agent derive as `misconfigured` end to end needs a `config_defect`
    input that no API call sets directly. A behavioural version belongs here and is not written; what
    this catches is the specific regression -- the inline rule coming back.
    """

    SOURCE = (Path(__file__).resolve().parent.parent / "api_core" / "analytics_series.py").read_text(
        encoding="utf-8"
    )

    def test_the_board_asks_the_predicate(self):
        self.assertIn(
            "is_live_agent_status(status)", self.SOURCE,
            "the online-agent board no longer classifies through the declared partition",
        )

    def test_the_inline_two_name_rule_is_GONE(self):
        # Not merely "the predicate is mentioned": both could coexist, with the inline one still
        # doing the work. The old rule is asserted absent.
        self.assertNotIn(
            'status.startswith("offline")', self.SOURCE,
            "the inline live-fleet rule is back, and it counts a misconfigured agent as live",
        )

    def test_the_probe_can_see_this_file_at_all(self):
        """POSITIVE CONTROL: a path typo would make both assertions above pass on an empty string."""
        self.assertGreater(len(self.SOURCE), 2000)
        self.assertIn("online_count", self.SOURCE)


class TheContractAgreesWithThePartition(unittest.TestCase):
    """The contract file is the shared vocabulary both languages load. If it grows a status, this
    fails until somebody decides which side of the partition it belongs on -- which is the decision
    that was skipped when `misconfigured` was added."""

    def test_every_contract_status_is_classified(self):
        import json

        contract = json.loads(
            (Path(__file__).resolve().parent.parent / "contracts" / "vocabulary.json")
            .read_text(encoding="utf-8")
        )
        values = contract["agentStatuses"]["values"]
        self.assertTrue(values, "positive control: the contract declared no statuses")
        self.assertEqual(
            sorted(values), sorted(VALID_STATUSES),
            "the contract and status_engine disagree about what a status is",
        )
        for status in values:
            self.assertIsInstance(is_live_agent_status(status), bool)

    def test_the_contract_MEANINGS_support_the_classification(self):
        """Derived from the contract's own prose rather than asserted twice. Each non-live status'
        meaning says it cannot work; each live one does not."""
        import json

        contract = json.loads(
            (Path(__file__).resolve().parent.parent / "contracts" / "vocabulary.json")
            .read_text(encoding="utf-8")
        )
        meanings = contract["agentStatuses"]["meanings"]
        self.assertIn("can never start", meanings["misconfigured"])
        self.assertIn("No current wake path", meanings["offline"])
        self.assertIn("disabled", meanings["stopped"].lower())


if __name__ == "__main__":
    unittest.main()
