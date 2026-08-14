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

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
ANALYTICS = REPO / "service" / "routers" / "analytics.py"
#: The eight helpers were RELOCATED out of the router in v0.5.4 — byte-identical, so the round trip
#: still closes, but only if the proof reads the file they live in now.
#:
#: THE THIRD PROOF IN THIS SERIES TO NEED THIS FIX. Each of them named the one or two modules it
#: expected to find things in, so a helper landing anywhere else made the round trip find nothing to
#: inline while the test kept passing. One tuple, read by every check, is the shape that survives the
#: next relocation.
SERIES = REPO / "service" / "api_core" / "analytics_series.py"
MODULES = (ANALYTICS, SERIES)


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)
FIXTURE = Path(__file__).resolve().parent / "data" / "get_analytics_before_split.py"

#: The function every extraction below came out of, and the helpers extracted from it.
#:
#: ONE fixture and ONE comparison, not a chain of per-extraction fixtures. Verifying extraction N
#: against "the state just before extraction N" needs a second copy of the function per split, each
#: of which rots independently and proves the wrong thing while staying green. Inlining ALL the
#: helpers back and comparing once against the TRUE original is both a stronger claim and the one
#: that keeps working as more blocks come out.
SOURCE_FUNCTION = "get_analytics"
EXTRACTIONS = [
    "_hourly_message_series",
    "_append_daily_message_buckets",
    "_monthly_message_series",
    "_fleet_median_reply_minutes",
    "_dispatch_outcomes_series",
    "_agent_leaderboard",
    "_busiest_channels",
    "_failure_reasons",
]


class AnalyticsSplitIsInertTests(unittest.TestCase):
    def test_every_extraction_together_inlines_back_to_the_original(self):
        split_src = _combined_split_source()
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), split_src, EXTRACTIONS)

    def test_the_fixture_is_the_function_it_claims_to_be(self):
        """A fixture that stopped containing the function would make the test above vacuous."""
        names = {
            n.name for n in ast.parse(FIXTURE.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(SOURCE_FUNCTION, names)

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip above would pass by having nothing to inline.

        Two claims, and the relocation separated them. Each helper must still EXIST somewhere the
        proof reads — otherwise the inline-back has nothing to substitute — and it must no longer be
        declared in the ROUTER, or the split has been undone. Checking only the first against only
        analytics.py conflated them and went red on a move that changed no behaviour.
        """
        declared_by_module = {
            path: {
                n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for path in MODULES
        }
        for helper in EXTRACTIONS:
            self.assertTrue(
                any(helper in names for names in declared_by_module.values()),
                f"{helper} is gone — was the split reverted?",
            )
            self.assertNotIn(
                helper, declared_by_module[ANALYTICS],
                f"{helper} is back in the router; it was moved to {SERIES.name} in v0.5.4",
            )


if __name__ == "__main__":
    unittest.main()
