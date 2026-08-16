"""`_stronger_priority` decides a merged buffer's priority, and was not commutative.

When a message merges into an already-queued buffered run, the run keeps the stronger of the two
priorities: `_stronger_priority(mergeable_run["priority"], priority)` — existing on the left, new on
the right. The rank table was `{"normal": 0, "high": 1, "urgent": 2}` and unknown labels defaulted to
`0`, the SAME rank as normal. With the comparison written `>=`, any tie returned the LEFT argument:

    _stronger_priority("low", "normal")  -> "low"
    _stronger_priority("normal", "low")  -> "normal"

`priority` is a free-form string (`priority: str = "normal"` on the models, no enum, and the bridge
does not constrain it), so `low` and typos like `urgnet` are both sendable. Because the call site puts
the EXISTING priority on the left, a run that once carried an unranked label kept it through every
later `normal` merge, and showed its recipient a priority that no message in the buffer had.

SCOPE, stated so this is not read as bigger than it is: nothing orders by priority — there is no
`ORDER BY priority` in the service. It is the `Priority:` line rendered into the buffer item the
recipient reads, plus a field in the claim payload. So the cost is a mislabelled run, not misrouted
work. The function is still wrong on its own terms: a thing called "stronger" must not depend on
which argument you passed first.

FIX: `low` is ranked explicitly below `normal`, and an unrecognised label ranks below every
recognised one — including `low`, because leaving them equal puts the tie-break back in charge and
reintroduces the same asymmetry one rung down.
"""

from __future__ import annotations

import unittest

from service.api_core.dispatch_runs import _PRIORITY_ORDER, _stronger_priority

RECOGNISED = ["low", "normal", "high", "urgent"]
#: Deliberately excludes "" and None: those are not unrecognised labels, they are the DEFAULT, and
#: normalise to `normal` (asserted separately in test_blank_and_none_are_normal). A first draft
#: listed "" here and failed — correctly, on the test rather than the code.
UNRECOGNISED = ["urgnet", "bogus", "critical"]


class StrongerPriorityTests(unittest.TestCase):
    def test_it_is_commutative_whenever_a_recognised_label_is_involved(self):
        """The defect, stated as the property it broke."""
        values = RECOGNISED + UNRECOGNISED
        asymmetric = []
        for left in values:
            for right in values:
                if _stronger_priority(left, right) == _stronger_priority(right, left):
                    continue
                keys = {(left or "normal").strip().lower(), (right or "normal").strip().lower()}
                if keys & set(RECOGNISED):
                    asymmetric.append((left, right))
        self.assertEqual(
            asymmetric, [],
            "the answer depends on argument order. At the call site the existing buffer priority is "
            "always on the left, so this is how a run keeps a label none of its messages carried",
        )

    def test_the_escalation_order_holds_both_ways(self):
        for weaker, stronger in [
            ("low", "normal"), ("normal", "high"), ("high", "urgent"), ("low", "urgent"),
        ]:
            with self.subTest(pair=(weaker, stronger)):
                self.assertEqual(_stronger_priority(weaker, stronger), stronger)
                self.assertEqual(_stronger_priority(stronger, weaker), stronger)

    def test_an_unrecognised_label_never_outranks_a_recognised_one(self):
        """A typo must not survive a merge with a real priority — in either direction."""
        for junk in UNRECOGNISED:
            for known in RECOGNISED:
                with self.subTest(junk=junk, known=known):
                    expected = (known or "normal").strip().lower()
                    self.assertEqual(_stronger_priority(junk, known), expected)
                    self.assertEqual(_stronger_priority(known, junk), expected)

    def test_low_is_ranked_below_normal_rather_than_equal_to_it(self):
        """The specific pair from the report. Equal ranks made this order-dependent."""
        self.assertLess(_PRIORITY_ORDER["low"], _PRIORITY_ORDER["normal"])
        self.assertEqual(_stronger_priority("low", "normal"), "normal")
        self.assertEqual(_stronger_priority("normal", "low"), "normal")

    def test_blank_and_none_are_normal(self):
        for blank in ("", "   ", None):
            with self.subTest(blank=blank):
                self.assertEqual(_stronger_priority(blank, "low"), "normal")
                self.assertEqual(_stronger_priority("low", blank), "normal")

    def test_case_and_padding_do_not_change_the_answer(self):
        self.assertEqual(_stronger_priority("  URGENT  ", "normal"), "urgent")
        self.assertEqual(_stronger_priority("normal", "  URGENT  "), "urgent")

    def test_two_different_unrecognised_labels_keep_the_existing_one(self):
        """Documented as deliberate: there is no basis for ranking one unknown string above another,
        so the tie keeps the LEFT argument — which at the call site is the run's current priority."""
        self.assertEqual(_stronger_priority("urgnet", "bogus"), "urgnet")
        self.assertEqual(_stronger_priority("bogus", "urgnet"), "bogus")

    def test_identical_inputs_are_returned_normalised(self):
        self.assertEqual(_stronger_priority("HIGH", "high"), "high")
