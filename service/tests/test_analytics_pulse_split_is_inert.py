"""The `get_analytics_pulse` split, re-proved against the real code on every run.

WHAT WAS EXTRACTED: the online-agent board and the three fleet counters computed in the same pass —
38 lines that aggregate rather than route, moved to `api_core/analytics_series.py` where this
router's other eight series already live.

THIS PROOF WAS WRITTEN AFTER THE FACT, and that is worth saying plainly. The extraction shipped in
its own commit with the suites green but WITHOUT a round trip, because the existing analytics proof
covers `get_analytics` and I did not notice this handler had none. The fixture below is captured from
the commit BEFORE the move, so the comparison is the one that should have run first.

IT ALSO DID NOT PASS AT FIRST. The gate refused it twice, both times wrongly, and both refusals were
about the same `board.sort(key=lambda a: ...)`:

  * "defines a nested Lambda — it would capture the HELPER's locals". True of closures in general and
    false of this one, which reads only its own parameter. The rule is about CAPTURE, so it now asks
    whether a closure has any free name rather than whether one exists.
  * "`a` is read by the helper but never passed to it". The caller binds its own `a` in an earlier
    loop, and the lambda's PARAMETER is also `a`. Loads inside a nested scope now have that scope's
    parameters shadowed, which is the same carve-out comprehension targets already had.

Both fixes are pinned by `ClosureThatCapturesNothingTests`, including the case each rule was
originally written for — a lambda that genuinely captures is still refused.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
ANALYTICS = REPO / "service" / "routers" / "analytics.py"
SERIES = REPO / "service" / "api_core" / "analytics_series.py"

MODULES = (ANALYTICS, SERIES)
FIXTURE = Path(__file__).resolve().parent / "data" / "get_analytics_pulse_before_split.py"

SOURCE_FUNCTION = "get_analytics_pulse"
EXTRACTIONS = ["_build_online_agent_board"]
OWNERS = {"_build_online_agent_board": SERIES}


def _combined_split_source() -> str:
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


class AnalyticsPulseSplitIsInertTests(unittest.TestCase):
    def test_the_extraction_inlines_back_to_the_original(self):
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), _combined_split_source(), EXTRACTIONS)

    def test_the_fixture_is_the_function_it_claims_to_be(self):
        names = {
            n.name for n in ast.parse(FIXTURE.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(SOURCE_FUNCTION, names)

    def test_the_fixture_was_not_captured_with_a_mangled_decode(self):
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash."""
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        live = ANALYTICS.read_text(encoding="utf-8")
        expected = ast.get_source_segment(live, next(
            n for n in ast.parse(live).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )) or ""
        if expected.count("—"):
            self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helper_is_not_still_inline(self):
        declared = {
            n.name for n in ast.parse(ANALYTICS.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for helper in EXTRACTIONS:
            self.assertNotIn(helper, declared, f"{helper} is back in analytics.py; this proof is vacuous")

    def test_exactly_one_module_declares_the_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [
                path for path in MODULES
                if any(
                    isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == helper
                    for n in ast.parse(path.read_text(encoding="utf-8")).body
                )
            ]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once")

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for node in ast.walk(ast.parse(SERIES.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"analytics_series.py imports upward from {node.module}",
                )

    def test_all_four_outputs_are_bound_at_the_call_site(self):
        """One pass produces four values; dropping one silently zeroes a dashboard tile.

        The round trip cannot see this — inline-back splices the body back and the reconstruction is
        correct however few names the call site kept. `utilization` divides by `online_count`, so a
        dropped counter reads as an idle fleet rather than as an error.
        """
        call = next(
            node for node in ast.walk(ast.parse(ANALYTICS.read_text(encoding="utf-8")))
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Await)
            and isinstance(node.value.value, ast.Call)
            and isinstance(node.value.value.func, ast.Name)
            and node.value.value.func.id == EXTRACTIONS[0]
        )
        target = call.targets[0]
        self.assertIsInstance(target, ast.Tuple)
        self.assertEqual(
            ["board", "online_count", "working_now", "fleet_working"],
            [e.id for e in target.elts if isinstance(e, ast.Name)],
        )

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
