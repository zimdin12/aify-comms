"""Every receipt in the acceptance ledger names a run whose tree actually CONTAINS its test.

FOUR ROUNDS OF REVIEW FOUND THAT SECTION WRONG, each time differently, and each repair was to
rewrite it by hand from memory. This is the mechanism that replaces the rewriting.

  - the section bound EVERY row to one commit, and three rows' test files do not exist there;
  - replacing that with per-row bindings, the cells were filled in from recollection and three rows
    were bound to a run whose tree predates them;
  - the run letters were assigned in writing order rather than measurement order;
  - a row was hidden inside an "every row before X-3" catch-all, judged and bound to nothing.

AND THE FIRST VERSION OF THIS GATE WAS ITSELF SATISFIED BY LESS, which review reproduced three ways.
It asked only whether the introduction commit was an ANCESTOR of the run's tree -- two self-declared
tokens compared against each other, with the TEST never mentioned. So a row could name a run whose
tree lacks its test entirely (ancestry does not imply the file survived: an ancestor may delete a
file on the way to a descendant), a repair could name `deadbeef`, and an acceptance row could cite a
test file that has never existed. All three passed.

SO CONTAINMENT IS ASKED DIRECTLY. For each judged row: the test file it names must EXIST in the tree
of the run it is bound to, read with `git cat-file -e <tree>:<path>`; and every commit token in its
binding -- the introduction and each repair -- must be a commit this repository has.

WHAT IS NOT ASSERTED, and the wording matters because the earlier version overclaimed. That the
numbers in a run row are what those suites printed: nothing here can know that, and the ledger says
they are an author's observation. And the letter ordering is checked as ANCESTRY, which is not proof
of measurement chronology -- a later letter must descend from an earlier one, which catches a table
shuffled out of order and cannot establish when anybody ran anything.

AN EMPTY POPULATION IS A FAILURE, NOT A SKIP. If the tables are reworded past these patterns every
check quantifies over nothing and passes, which is this repo's own definition of a false green. Only
an ABSENT ledger skips, and it skips by name.
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

#: `| X-4 | `5b7855b1`, repaired at `40dc8794` (F1) and `fba7d437` (S2) | run B onward |` — the row,
#: its whole commit cell (so REPAIRS are read too, which the first version ignored), and its run.
BINDING = re.compile(r"^\| (X-\d+) \| (`[0-9a-f]{7,40}`[^|]*)\| run ([A-Z])", re.M)

#: The acceptance table names each row and then describes it, so its rows are the ones whose second
#: cell is NOT a commit. That separates the two tables without either being identified by a heading
#: somebody could reword.
ACCEPTANCE = re.compile(r"^\| (X-\d+) \| (?!`[0-9a-f]{7,40}`)([^|]*\|[^|]*\|[^|]*)\|", re.M)

COMMIT = re.compile(r"`([0-9a-f]{7,40})`")
TEST_FILE = re.compile(r"`(test_[a-z0-9_]+\.py)`")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def _is_a_commit(sha: str) -> bool:
    return _git("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def _tree_contains(tree: str, path: str) -> bool:
    """Whether `tree` really holds `path`. ASKED DIRECTLY, because ancestry does not imply it."""
    return _git("cat-file", "-e", f"{tree}:{path}").returncode == 0


class TheLedgersReceiptsNameTreesThatContainThem(unittest.TestCase):
    def setUp(self):
        if not LEDGER.is_file():
            self.skipTest(f"{LEDGER.name} is not in this checkout, so no receipt was judged")
        self.text = LEDGER.read_text(encoding="utf-8")
        self.runs = dict(RUN.findall(self.text))
        self.bindings = {row: (cell, letter) for row, cell, letter in BINDING.findall(self.text)}
        self.judged = {row: body for row, body in ACCEPTANCE.findall(self.text)}
        #: Where a named test file lives, resolved once against the working tree so the check below
        #: asks about a real path rather than a guessed one.
        listed = _git("ls-files", "service/tests", "mcp/stdio/tests", "service/new_dashboard")
        self.paths = {Path(line).name: line for line in listed.stdout.splitlines() if line}

    def test_the_tables_are_there_to_judge(self):
        """POSITIVE CONTROL, and it fails rather than skips.

        Every assertion below quantifies over rows parsed out of the document. Reworded past these
        patterns the population goes empty and each of them passes over nothing.
        """
        self.assertGreaterEqual(
            len(self.runs), 2,
            f"fewer than two runs parsed, so the ordering check judges nothing: {sorted(self.runs)}")
        self.assertGreaterEqual(
            len(self.bindings), 3,
            f"fewer than three bindings parsed: {sorted(self.bindings)}")
        self.assertGreaterEqual(
            len(self.judged), 3,
            f"fewer than three acceptance rows parsed: {sorted(self.judged)}")

    def test_every_acceptance_row_HAS_a_binding(self):
        """A row can leave the binding table without leaving the document, and X-1 did.

        It sat inside an "every row before X-3" catch-all — judged, and bound to no run, which is
        the condition the section forbids. The count guard cannot see one row go: driving that
        mutant showed it passing on five of six.
        """
        unbound = sorted(set(self.judged) - set(self.bindings))
        self.assertEqual(unbound, [], (
            f"these rows are judged and bound to no run: {unbound}. A result with no candidate "
            "attached is not a receipt"))

    def test_every_commit_a_binding_names_is_a_real_commit(self):
        """Introductions AND repairs, because the first version read only the first token.

        A repair could name `deadbeef` and nothing noticed — reproduced by review. A binding citing
        a commit this repository does not have is not checkable by anyone.
        """
        unknown = []
        for row, (cell, _) in sorted(self.bindings.items()):
            for sha in COMMIT.findall(cell):
                if not _is_a_commit(sha):
                    unknown.append(f"{row} names {sha}, which is not a commit in this repository")
        self.assertEqual(unknown, [], (
            "a binding cites a commit that does not exist, so the receipt cannot be checked:"
            + chr(10) + "  " + (chr(10) + "  ").join(unknown)))

    def test_every_row_names_a_run_whose_tree_CONTAINS_its_test(self):
        """THE CLAIM, and the first version of this gate did not ask it.

        It compared the introduction commit's ANCESTRY against the run's tree — two self-declared
        tokens, with the test never mentioned — so a row could be bound to a tree that does not hold
        its test file at all. Ancestry is not content retention: an ancestor may delete a file on
        the way to a descendant. So the tree is asked whether it has the file.
        """
        problems = []
        for row, body in sorted(self.judged.items()):
            named = TEST_FILE.findall(body)
            if not named:
                problems.append(f"{row} names no test file, so nothing binds it to a tree")
                continue
            cell, letter = self.bindings.get(row, ("", ""))
            tree = self.runs.get(letter)
            if tree is None:
                problems.append(f"{row} names run {letter!r}, which no run row declares")
                continue
            for basename in named:
                path = self.paths.get(basename)
                if path is None:
                    problems.append(
                        f"{row} cites {basename}, which is not a test file in this checkout")
                    continue
                if not _tree_contains(tree, path):
                    problems.append(
                        f"{row} is bound to run {letter} ({tree}), whose tree does not contain "
                        f"{path}")
        self.assertEqual(problems, [], (
            "the ledger claims a result was measured on a tree that does not hold the test that "
            "produced it, which is a receipt for something else:" + chr(10) + "  "
            + (chr(10) + "  ").join(problems)))

    def test_the_run_letters_are_in_ANCESTRY_order(self):
        """A reader follows the letters as a sequence, so they have to be one.

        NARROWED WORDING, because the earlier version called this measurement order and it is not:
        commit ancestry cannot establish when anybody ran anything. What it catches is a table
        shuffled so an earlier letter names a tree the later one does not descend from.
        """
        ordered = sorted(self.runs.items())
        out_of_order = []
        for (earlier, earlier_tree), (later, later_tree) in zip(ordered, ordered[1:]):
            if _git("merge-base", "--is-ancestor", earlier_tree, later_tree).returncode != 0:
                out_of_order.append(
                    f"run {later} ({later_tree}) does not descend from run {earlier} "
                    f"({earlier_tree})")
        self.assertEqual(out_of_order, [], (
            "the run letters are not in ancestry order, so a reader following them reads the "
            "sequence backwards:" + chr(10) + "  " + (chr(10) + "  ").join(out_of_order)))


if __name__ == "__main__":
    unittest.main()
