"""Every input `derive()` can be given, and every status it can produce.

THE MODULE CLAIMS THIS PROPERTY AND NOTHING CHECKED IT. `status_engine.py` opens: "`derive()` is a
pure function of explicit inputs (no DB, no clock) so it is exhaustively table-testable". The suite
tests the matrix rows somebody wrote down. This tests the whole space -- 4,096 combinations, which
takes under a second -- because the two failures below are invisible to any hand-written table:

  - A STATUS IN THE VOCABULARY THAT NO INPUT PRODUCES. `VALID_STATUSES` is a promise about what an
    agent can be. A state nothing can reach is a promise the engine cannot keep, and every consumer
    that switches on it carries a dead branch. `idle` and `stale` were removed for being time-decay
    artifacts; this is the guard against the next one arriving and never being reachable.
  - AN INPUT NOBODY READS. `StatusInputs` has twelve fields, each gathered at real cost by
    `_gather_status_inputs` -- DB queries, liveness probes. A field whose value never changes the
    answer is that cost paid for nothing, and the same defect class as `managedClaudeMaxTurns`: a knob
    that exists, is set, and does nothing.

MEASURED 2026-08-26: all 8 statuses reachable, none produced outside the vocabulary, and all 12 inputs
influence the result. `disabled` decides all 4,096 (it short-circuits); `spawn_starting` decides 80,
which is the narrowest and appropriate for a bounded transient.

WHAT THIS DOES NOT CHECK: whether each mapping is CORRECT. That is what the matrix tests beside this
one are for, and they encode judgements no enumeration can derive. This file asks only that the
function is total, closed, and has no dead inputs.
"""

from __future__ import annotations

import itertools
import unittest
from collections import Counter

from service.status_engine import VALID_STATUSES, StatusInputs, derive

#: The boolean fields, in declaration order. Read off the dataclass rather than typed, so a field
#: added to `StatusInputs` joins the enumeration instead of being silently skipped -- which is exactly
#: how a dead input would hide from this test.
BOOL_FIELDS = tuple(
    name for name, field in StatusInputs.__dataclass_fields__.items()
    if field.type in ("bool", bool)
)
MODES = ("managed", "resident")
#: Empty and non-empty is the whole distinction the engine draws on this field; the TEXT is a message,
#: not a branch.
DEFECTS = ("", "no wake path")


def _all_inputs():
    for mode, defect in itertools.product(MODES, DEFECTS):
        for combo in itertools.product([False, True], repeat=len(BOOL_FIELDS)):
            yield StatusInputs(mode=mode, config_defect=defect, **dict(zip(BOOL_FIELDS, combo)))


class DeriveIsExhaustivelyCoveredTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = [(i, derive(i)) for i in _all_inputs()]
        cls.produced = Counter(status for _, status in cls.results)

    def test_the_enumeration_covers_a_real_space(self):
        """Anti-vacuity. An empty or tiny enumeration proves nothing about totality, and the field
        list is discovered rather than typed -- so a rename that emptied it would show up here."""
        self.assertGreaterEqual(len(BOOL_FIELDS), 10, f"only {len(BOOL_FIELDS)} boolean fields found")
        self.assertEqual(len(self.results), 2 * 2 * 2 ** len(BOOL_FIELDS))

    def test_every_declared_status_is_reachable(self):
        never = [status for status in VALID_STATUSES if status not in self.produced]
        self.assertEqual(never, [], (
            f"these statuses are declared in VALID_STATUSES and no input can produce them: {never}. "
            "Either a rule that used to produce one was removed and the name outlived it, or a name "
            "was added without a rule. Every consumer switching on it carries a dead branch."
        ))

    def test_no_input_produces_a_status_outside_the_vocabulary(self):
        stray = sorted({status for _, status in self.results if status not in VALID_STATUSES})
        self.assertEqual(stray, [], (
            f"derive() returned {stray}, which VALID_STATUSES does not declare. The vocabulary is what "
            "the dashboard, the status chip and every consumer switch on."
        ))

    def test_every_input_field_changes_the_answer_for_something(self):
        """A field nobody reads is a cost paid for nothing.

        Each of these is gathered by `_gather_status_inputs` at the price of real work -- queries and
        liveness probes. Flipping one and getting the same answer for all 4,096 combinations means the
        gathering could be deleted and no status would move.
        """
        dead = []
        for field in BOOL_FIELDS:
            changed = False
            for inputs, status in self.results:
                flipped = StatusInputs(**{**inputs.__dict__, field: not getattr(inputs, field)})
                if derive(flipped) != status:
                    changed = True
                    break
            if not changed:
                dead.append(field)
        self.assertEqual(dead, [], (
            f"these StatusInputs fields never change derive()'s answer: {dead}. Each is gathered at "
            "real cost. Wire it into a rule, or delete it and stop gathering it."
        ))

    def test_mode_and_config_defect_both_matter_too(self):
        """The two non-boolean inputs, checked the same way. `mode` splits the managed and resident
        rule sets; `config_defect` is the only input that can assert `misconfigured`."""
        for field, other in (("mode", "resident"), ("config_defect", "no wake path")):
            changed = any(
                derive(StatusInputs(**{
                    **inputs.__dict__,
                    field: other if getattr(inputs, field) != other else ("managed" if field == "mode" else ""),
                })) != status
                for inputs, status in self.results
            )
            self.assertTrue(changed, f"{field} never changes derive()'s answer")

    def test_derive_is_total(self):
        """No input raises, and none returns empty. A status the caller has to special-case for
        emptiness is a fourth thing to render, and nothing downstream expects one."""
        for inputs, status in self.results:
            self.assertIsInstance(status, str)
            self.assertTrue(status.strip(), f"empty status from {inputs}")

    def test_disabled_wins_over_everything(self):
        """The short-circuit, pinned because it is the strongest rule in the engine: an operator who
        stopped an agent must not see it reported as working because a stale turn event survived."""
        stopped = [
            status for inputs, status in self.results
            if inputs.disabled and inputs.config_defect == ""
        ]
        self.assertTrue(stopped)
        self.assertEqual(set(stopped), {"stopped"}, "a disabled agent reported as something else")


if __name__ == "__main__":
    unittest.main()
