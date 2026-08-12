"""The `get_analytics` split, re-proved against the real code on every run.

The extract-method gate proves a split is inert by INLINING IT BACK: substitute the helper's body
over the call and the result must reproduce the pre-split function exactly. Running that once at
refactor time proves the commit. Running it in the suite proves it stays true — if someone later
edits `_hourly_message_series` or the call site and the two drift, the round trip stops closing.

The pre-split source is committed as a FIXTURE rather than recovered from git, deliberately: the
route gates in v0.5 shipped unable to run from a clean clone because their snapshots were
gitignored, and a proof that needs `.git` to run is the same mistake. `test_fixtures_are_tracked`
covers the file itself.

WHAT THIS DOES NOT DO: it does not re-verify the whole handler. It verifies the ONE extraction named
here. `test_analytics_characterization.py` is the behavioural net around the endpoint; this is the
structural proof of the split.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extraction_preserves_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
ANALYTICS = REPO / "service" / "routers" / "analytics.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "get_analytics_before_split.py"

#: Each extraction: (helper name, the pre-split source of the function it came out of).
EXTRACTIONS = [("_hourly_message_series", "get_analytics")]


class AnalyticsSplitIsInertTests(unittest.TestCase):
    def test_each_extraction_still_inlines_back_to_the_original(self):
        split_src = ANALYTICS.read_text(encoding="utf-8")
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        for helper, function in EXTRACTIONS:
            with self.subTest(helper=helper):
                original = next(
                    n for n in ast.parse(fixture_src).body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function
                )
                assert_extraction_preserves_behaviour(
                    ast.get_source_segment(fixture_src, original), split_src, helper)

    def test_the_fixture_is_the_function_it_claims_to_be(self):
        """A fixture that stopped containing the function would make the test above vacuous."""
        names = {
            n.name for n in ast.parse(FIXTURE.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for _, function in EXTRACTIONS:
            self.assertIn(function, names)

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip above would pass by having nothing to inline."""
        split_src = ANALYTICS.read_text(encoding="utf-8")
        declared = {
            n.name for n in ast.parse(split_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for helper, _ in EXTRACTIONS:
            self.assertIn(helper, declared, f"{helper} is gone — was the split reverted?")


if __name__ == "__main__":
    unittest.main()
