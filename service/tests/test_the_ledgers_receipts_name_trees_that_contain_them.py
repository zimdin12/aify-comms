"""Every receipt in the acceptance ledger names a tree that can actually contain the row it binds.

THREE ROUNDS OF REVIEW FOUND THIS SECTION WRONG, each time differently, and each time the repair was
to rewrite it by hand from memory. That is the shape this repo answers with a mechanism rather than
a repeated instruction: the claim is mechanically checkable, and every one of the three failures
would have been caught by checking it.

  - the section bound EVERY row to one commit, and three rows' test files do not exist there;
  - replacing that with per-row bindings, the cells were filled in from recollection and three rows
    were bound to a run whose tree predates them;
  - the run letters were assigned in writing order rather than measurement order, so two of them
    named trees measured before the letter above them.

WHAT IS ASSERTED. For each row of the binding table, the commit named as carrying it must be an
ancestor of the tree the named run was taken on -- because a receipt saying "measured on run X" when
run X could not have executed that test is a receipt for something else. And the run letters must be
in ancestry order, since a reader follows them as a sequence.

WHAT IS NOT ASSERTED, deliberately: that the numbers in a run row are what those suites really
printed. Nothing here can know that -- they are an author's observation, and the ledger says so.
This gate covers the part that IS derivable, which is the part that kept being wrong.

AN EMPTY POPULATION IS A FAILURE, NOT A SKIP. If the tables are reworded past these patterns every
check below quantifies over nothing and passes, which is this repo's own definition of a false
green -- so the count is asserted. Only an ABSENT ledger skips, and it skips by name.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "docs" / "V063_ACCEPTANCE_LEDGER.md"

#: `| A | `09e8df6a` | ...` — a run, its letter and the tree it was taken on.
RUN = re.compile(r"^\| ([A-Z]) \| `([0-9a-f]{7,40})`", re.M)

#: `| X-4 | `5b7855b1`, repaired at ... | run B onward |` — a row, the commit that carries it, and
#: the first run that covers it.
BINDING = re.compile(r"^\| (X-\d+) \| `([0-9a-f]{7,40})`[^|]*\| run ([A-Z])", re.M)

#: The acceptance table names each row and then describes it in prose, so its rows are the
#: ones whose second cell is NOT a commit. That is what separates the two tables without
#: either being identified by a heading that could be reworded.
ACCEPTANCE = re.compile(r"^\| (X-\d+) \| (?!`[0-9a-f]{7,40}`)[^|]+\|", re.M)


def _is_ancestor(commit: str, tree: str) -> bool:
    """Whether `tree` contains `commit`. A commit git does not know answers False, not an error."""
    done = subprocess.run(["git", "merge-base", "--is-ancestor", commit, tree],
                          cwd=ROOT, capture_output=True)
    return done.returncode == 0


class TheLedgersReceiptsNameTreesThatContainThem(unittest.TestCase):
    def setUp(self):
        if not LEDGER.is_file():
            self.skipTest(f"{LEDGER.name} is not in this checkout, so no receipt was judged")
        self.text = LEDGER.read_text(encoding="utf-8")
        self.runs = dict(RUN.findall(self.text))
        self.bindings = BINDING.findall(self.text)

    def test_the_tables_are_there_to_judge(self):
        """POSITIVE CONTROL, and it fails rather than skips.

        Every assertion below quantifies over rows parsed out of the document. If the tables are
        renamed or reformatted past these patterns the population goes empty and each of them
        passes over nothing — which is the false green this repo refuses to let a gate report.
        """
        self.assertGreaterEqual(
            len(self.runs), 2,
            "fewer than two runs were parsed out of the ledger, so the ordering check below judges "
            f"nothing. Parsed: {sorted(self.runs)}")
        self.assertGreaterEqual(
            len(self.bindings), 3,
            "fewer than three row bindings were parsed out of the ledger, so the receipt check "
            f"below judges nothing. Parsed: {[row for row, _, _ in self.bindings]}")

    def test_every_acceptance_row_HAS_a_binding(self):
        """A row can leave the binding table without leaving the document, and nothing would say so.

        The count guard above only catches the table VANISHING; driving a mutant that removed one
        row showed it passing on five of six. So the two populations are compared instead: every
        `X-N` the acceptance table judges must appear in the binding table, because a PASSES IN
        TESTS row with no receipt is the exact thing this section exists to forbid.
        """
        judged = set(ACCEPTANCE.findall(self.text))
        bound = {row for row, _, _ in self.bindings}
        self.assertTrue(judged, "no acceptance rows were parsed, so this compares nothing")
        self.assertEqual(
            judged - bound, set(),
            f"these rows are judged in the acceptance table and bound to no run: "
            f"{sorted(judged - bound)}. A result with no candidate attached is not a receipt")

    def test_every_row_names_a_run_whose_tree_contains_it(self):
        """THE CLAIM. "Measured on run X" is false if run X's tree could not run that test."""
        impossible = []
        for row, commit, letter in self.bindings:
            tree = self.runs.get(letter)
            if tree is None:
                impossible.append(f"{row} names run {letter}, which no run row declares")
                continue
            if not _is_ancestor(commit, tree):
                impossible.append(
                    f"{row} is carried by {commit} and bound to run {letter} ({tree}), "
                    "whose tree does not contain it")
        self.assertEqual(impossible, [], (
            "the ledger claims a result was measured on a tree that cannot contain the test that "
            "produced it, which is a receipt for something else:" + chr(10) + "  "
            + (chr(10) + "  ").join(impossible)))

    def test_the_run_letters_are_in_the_order_the_runs_were_taken(self):
        """A reader follows the letters as a sequence, so they have to BE one.

        They were not: an earlier version lettered runs in the order they were written down, and two
        named trees measured before the letter above them.
        """
        ordered = [(letter, tree) for letter, tree in sorted(self.runs.items())]
        out_of_order = []
        for (earlier, earlier_tree), (later, later_tree) in zip(ordered, ordered[1:]):
            if not _is_ancestor(earlier_tree, later_tree):
                out_of_order.append(
                    f"run {later} ({later_tree}) does not descend from run {earlier} "
                    f"({earlier_tree})")
        self.assertEqual(out_of_order, [], (
            "the run letters are not in the order the runs were taken, so a reader following them "
            "reads the sequence backwards:" + chr(10) + "  " + (chr(10) + "  ").join(out_of_order)))


if __name__ == "__main__":
    unittest.main()
