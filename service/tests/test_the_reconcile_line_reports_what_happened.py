r"""The once-a-minute reconcile line says what the pass DID, and stays quiet otherwise.

`service/main.py` logs one INFO line per sweep, filtered to the entries that are truthy so a pass that
repaired nothing prints nothing. One entry defeated that filter for as long as it has existed:
`wal_checkpoint` holds the `(busy, log_pages, ckpt_pages)` tuple from `PRAGMA wal_checkpoint`, and
`(0, 0, 0)` is a non-empty tuple, which is truthy.

MEASURED on the operator's running container, 2026-08-29: 507 reconcile lines, all 507 carrying
`wal_checkpoint`, exactly ONE of them not `(0, 0, 0)` -- and 53 lines where it was the ONLY entry.
Fifty-three log lines that reported nothing while looking like they reported something, and 454 more
carrying a field that said nothing.

THE BUSY ALARM IS SOMEWHERE ELSE, which is what makes this safe to drop: the sweep logs its own
WARNING when the checkpoint is blocked or takes over a second. Quietening the INFO line cannot hide a
starved checkpoint.

Two call sites in `main.py` had their own copy of the filter -- startup and the periodic loop -- so
the rule now has one owner they both import, and a future entry with an awkward truthiness is fixed
once rather than in whichever copy somebody remembers.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.reconcilers.sweep import reportable

REPO = Path(__file__).resolve().parents[2]
MAIN = REPO / "service" / "main.py"


class TheReconcileLineReportsWhatHappened(unittest.TestCase):
    def test_THE_DEFECT_an_all_zero_tuple_is_not_something_that_happened(self):
        self.assertEqual(reportable({"wal_checkpoint": (0, 0, 0)}), {})

    def test_a_checkpoint_that_DID_something_is_still_reported(self):
        """The one line in 507 that mattered. A fix that silenced the field entirely would lose it,
        and nothing else in this file would notice."""
        self.assertEqual(
            reportable({"wal_checkpoint": (0, 12, 12)}), {"wal_checkpoint": (0, 12, 12)},
        )
        # busy=1 is the WAL-starvation signal. It must survive even with no pages moved.
        self.assertEqual(reportable({"wal_checkpoint": (1, 0, 0)}), {"wal_checkpoint": (1, 0, 0)})

    def test_the_skipped_string_branch_survives(self):
        """`checkpoint_result` becomes `f"skipped: {exc}"` when the PRAGMA raises. A string is always
        something -- and a container rule that swallowed it would hide the one case where the
        checkpoint did not run at all."""
        self.assertEqual(
            reportable({"wal_checkpoint": "skipped: database is locked"}),
            {"wal_checkpoint": "skipped: database is locked"},
        )

    def test_ordinary_counters_are_unchanged(self):
        """Everything else in the dict is an int or a list, and the previous behaviour was right for
        all of them. A change to the log filter that also changed those would be a regression nobody
        asked for."""
        self.assertEqual(
            reportable({"a": 0, "b": 3, "c": [], "d": ["x"], "e": None, "f": ""}),
            {"b": 3, "d": ["x"]},
        )

    def test_an_empty_or_missing_result_is_not_an_error(self):
        self.assertEqual(reportable({}), {})
        self.assertEqual(reportable(None), {})

    def test_MAIN_USES_IT_AT_BOTH_CALL_SITES(self):
        """The two log sites had a copy of the filter each. A predicate proven in isolation while one
        call site keeps its own inline copy is this repo's most repeated defect shape, so the call is
        checked rather than only the rule."""
        tree = ast.parse(MAIN.read_text(encoding="utf-8"))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "reportable"
        ]
        self.assertEqual(len(calls), 2, (
            f"expected both reconcile log sites to call reportable(), found {len(calls)}"
        ))
        comprehensions = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.DictComp)
        ]
        self.assertEqual(comprehensions, [], (
            "a dict comprehension is back in main.py; if it is the reconcile filter again, the rule "
            "has two owners and they will disagree"
        ))


if __name__ == "__main__":
    unittest.main()
