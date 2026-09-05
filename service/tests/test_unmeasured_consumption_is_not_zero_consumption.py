"""`GET /usage/consumption` could not tell "nobody consumed anything" from "nobody ever measured".

MEASURED 2026-09-05, by grepping both repos: **nothing posts to `/usage/consumption`.**
`usage-collector.js`'s `collectConsumptionOnce` is documented as PARKED and has had no caller since
v0.6.2, and no other writer exists in aify-comms or aify-env. So the endpoint has been returning a
summary built from an empty list — a confident-looking answer about agents nothing ever looked at.

THE SIBLING POOL DEGRADES HONESTLY AND THIS ONE DID NOT, which is what makes it worth fixing rather
than noting. `usage_all()` stamps `stale` from each snapshot's `updated_at`, so a reader can tell an
old number from a fresh one. `consumption_summary()` returned a bare list with no stamp at all, so
the half of the pair with NO data was the half that looked most certain.

This repo has shipped "no evidence reads as a result" three times in its health checks — `env-bridge`
counting registered rows, `bridge-current` green-by-default, and `spawn-queue`/`tier-version`
answering about a service they never reached. The rule is no weaker for a data endpoint than for a
doctor row: a caller rendering a zero for an unmeasured population is making a claim it cannot
support, and it has no way to know.
"""

from __future__ import annotations

import unittest

from service.usage_cache import (
    consumption_set,
    consumption_summary,
    reset_consumption_for_tests,
)


class UnmeasuredConsumptionIsNotZeroConsumption(unittest.TestCase):
    def setUp(self):
        reset_consumption_for_tests()

    def tearDown(self):
        reset_consumption_for_tests()

    def test_NEVER_MEASURED_says_so(self):
        summary = consumption_summary()
        self.assertIs(summary["measured"], False)
        self.assertIsNone(summary["measuredAt"])

    def test_MEASURED_AND_EMPTY_is_a_different_answer(self):
        """The control. If these two rendered the same, the field would be decoration."""
        consumption_set([])
        summary = consumption_summary()
        self.assertIs(summary["measured"], True, "a real measurement reported as never measured")
        self.assertTrue(summary["measuredAt"], "a measurement carried no timestamp")

    def test_the_two_states_are_DISTINGUISHABLE(self):
        """Stated as its own assertion because it is the whole point: a reader must be able to tell
        them apart, and before this they could not."""
        never = consumption_summary()
        consumption_set([])
        empty = consumption_summary()
        self.assertNotEqual(
            never["measured"], empty["measured"],
            "an unmeasured population and a measured-empty one still render identically",
        )

    def test_rows_still_reach_the_summary(self):
        """The stamp must not have displaced the data it is about."""
        consumption_set([{"agent_id": "a1", "tokens": 5}])
        summary = consumption_summary()
        self.assertIs(summary["measured"], True)
        # The shape of the rest belongs to `summarize_consumption`; this only asserts it survived.
        self.assertGreater(len(summary), 2, "the summary lost its own content when stamped")


if __name__ == "__main__":
    unittest.main()
