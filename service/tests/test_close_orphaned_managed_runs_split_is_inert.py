"""The `_close_orphaned_managed_runs` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: the query that decides which claimed-or-running runs have been orphaned. It is
the decision; everything after it in the reaper is bookkeeping over whatever rows came back.

THIS REAPER IS THE DANGEROUS KIND OF SWEEP — it FAILS runs that a bridge may still be holding — so
the query being one clause too wide kills live work, and the failure reads to an operator as an agent
that gave up. That is the argument for pulling it out and testing it directly rather than through the
sweep.

THE SUBSTITUTION, declared rather than left to be noticed: the helper lives in
`service/api_core/orphaned_runs_query.py`. The extract-method gate needs the caller and the helper in
one tree, so the sources are CONCATENATED for the proof.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
#: v0.5.4: the reaper moved OUT of dispatch_lifecycle.py to its own module. This proof reads
#: whichever file the CALLER lives in, so the pin had to be re-aimed with the move.
LIFECYCLE = REPO / "service" / "reconcilers" / "orphaned_managed_runs.py"
QUERY = REPO / "service" / "api_core" / "orphaned_runs_query.py"
FIXTURE = (Path(__file__).resolve().parent / "data"
           / "close_orphaned_managed_runs_before_split.py")

SOURCE_FUNCTION = "_close_orphaned_managed_runs"
EXTRACTIONS = ["_select_orphaned_managed_runs"]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {"_select_orphaned_managed_runs": QUERY}

MODULES = (LIFECYCLE, QUERY)


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


def _declared(path: Path) -> set[str]:
    return {
        n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class CloseOrphanedManagedRunsSplitIsInertTests(unittest.TestCase):
    def test_the_extraction_inlines_back_to_the_original(self):
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), _combined_split_source(), EXTRACTIONS)

    def test_the_fixture_is_the_function_it_claims_to_be(self):
        """A fixture that stopped containing the function would make the test above vacuous."""
        self.assertIn(SOURCE_FUNCTION, _declared(FIXTURE))

    def test_the_fixture_was_not_captured_with_a_mangled_decode(self):
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash."""
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        for helper in EXTRACTIONS:
            self.assertNotIn(
                helper, _declared(LIFECYCLE),
                f"{helper} is back in dispatch_lifecycle.py; this proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_query_leaf_imports_NOTHING(self):
        """Same standard as `reconcilable_runs_query.py`: it takes only `db` and two cutoffs.

        A query that grew a dependency would be a query that had started deciding something other
        than which rows qualify — and in a reaper, deciding more than that is how live work gets
        failed.
        """
        imports = [
            node for node in ast.walk(ast.parse(QUERY.read_text(encoding="utf-8")))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and getattr(node, "module", None) != "__future__"
        ]
        self.assertEqual([], imports, "the orphaned-runs query must stay dependency-free")

    def test_the_CUTOFFS_are_parameters_and_not_computed_here(self):
        """What makes the query testable without a clock, asserted so it stays that way.

        A test hands it two timestamps and asserts which rows come back. If the helper started
        computing them from `now()`, every test of it would have to arrange for wall time to pass —
        and the ones that exist would start passing for the wrong reason.
        """
        helper = next(
            n for n in ast.parse(QUERY.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
        )
        parameters = [a.arg for a in helper.args.args]
        self.assertEqual(["db", "cutoff_param", "ceiling_param", "limit"], parameters)
        assigned = {
            t.id for n in ast.walk(helper) if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)
        }
        for cutoff in ("cutoff_param", "ceiling_param"):
            self.assertNotIn(cutoff, assigned, f"{cutoff} must arrive from the caller, not be derived")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
