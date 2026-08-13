"""The status-derivation split, re-proved against the real code on every run.

`_decide_effective_status` is the 147-line decision lifted out of the 551-line
`_compute_live_status_cache`. This is the highest-stakes extraction in the v0.5.4 series: it is what
decides whether an agent reads as offline, blocked, working or online, and a silent change here does
not raise — it misreports the fleet.

FIRST USE OF THE MULTI-OUTPUT DIALECT (`service/tests/test_extract_method_multi_output.py`). The
decision has three live-outs, so the single-value VALUE form could not express it. The dialect landed
as its own probed slice before this extraction relied on it, and the transposition probe there is what
makes the ordering of these three returns trustworthy rather than assumed.

DECLARED SUBSTITUTION: the helper deliberately lives in `service/api_core/status_decision.py`, so the
caller and the helper are CONCATENATED for the proof — the same declared substitution used by the four
earlier split proofs.

WHAT THIS DOES NOT DO: it proves the extraction is mechanically inert. It does not test the branches.
Branch characterization comes next and is the reason the extraction happened at all — the decision was
previously reachable only through a database and a route.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
CARRIER = REPO / "service" / "control_plane.py"
DECISION = REPO / "service" / "api_core" / "status_decision.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "compute_live_status_cache_before_split.py"

SOURCE_FUNCTION = "_compute_live_status_cache"
EXTRACTIONS = ["_decide_effective_status"]


def _combined_split_source() -> str:
    return "\n\n".join(p.read_text(encoding="utf-8") for p in (CARRIER, DECISION))


class StatusDecisionSplitIsInertTests(unittest.TestCase):
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
        self.assertGreater(text.count("—"), 5, "fixture looks locale-mangled, not utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")

    def test_the_helper_is_not_still_inline(self):
        declared = {
            n.name for n in ast.parse(CARRIER.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for helper in EXTRACTIONS:
            self.assertNotIn(helper, declared, f"{helper} is back in the carrier; this proof is vacuous")

    def test_exactly_one_module_declares_the_helper(self):
        owners = [
            path for path in (CARRIER, DECISION)
            if any(
                isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in EXTRACTIONS
                for n in ast.parse(path.read_text(encoding="utf-8")).body
            )
        ]
        self.assertEqual([DECISION], owners)

    def test_the_decision_leaf_does_not_import_upward(self):
        for node in ast.walk(ast.parse(DECISION.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"status_decision.py imports upward from {node.module}",
                )

    def test_the_three_outputs_are_returned_in_the_order_the_caller_unpacks(self):
        """The multi-output dialect's one real risk, asserted here as well as in the verifier probes.

        A transposed return has the same names, arity and types and silently swaps values. The
        inline-back round trip above catches it, but this states the invariant where a reader of THIS
        file will see it, rather than only in the gate's synthetic fixtures.
        """
        decision = next(
            n for n in ast.parse(DECISION.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
        )
        returned = decision.body[-1]
        self.assertIsInstance(returned, ast.Return)
        self.assertEqual(
            [e.id for e in returned.value.elts],
            ["effective_status", "reason", "awaiting_reply"],
        )
        call = next(
            n for n in ast.walk(ast.parse(CARRIER.read_text(encoding="utf-8")))
            if isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Await)
            and getattr(n.value.value.func, "id", "") == EXTRACTIONS[0]
        )
        self.assertEqual(
            [e.id for e in call.targets[0].elts],
            ["effective_status", "reason", "awaiting_reply"],
            "the caller must unpack in the same order the helper returns",
        )

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
