"""The `control_agent` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: what the dashboard's Stop and Resume buttons actually do — cancel the runs queued
for the agent, mark it stopped with a note the operator can act on, tear down its managed console;
or, on resume, clear the status and restore `launch_mode` without starting anything.

THE SUBSTITUTION, declared rather than left to be noticed: the helper lives in
`service/api_core/agent_stop_resume.py`, because leaving it in the router would not have reduced it —
that was the point. The extract-method gate needs the caller and the helper in one tree, so the
sources are CONCATENATED for the proof.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
SESSION_OPS = REPO / "service" / "routers" / "agents" / "session_ops.py"
STOP_RESUME = REPO / "service" / "api_core" / "agent_stop_resume.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "control_agent_before_split.py"

SOURCE_FUNCTION = "control_agent"
EXTRACTIONS = ["_apply_agent_stop_or_resume"]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {"_apply_agent_stop_or_resume": STOP_RESUME}

MODULES = (SESSION_OPS, STOP_RESUME)


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


def _declared(path: Path) -> set[str]:
    return {
        n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _helper() -> ast.AST:
    return next(
        n for n in ast.parse(STOP_RESUME.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
    )


class ControlAgentSplitIsInertTests(unittest.TestCase):
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
                helper, _declared(SESSION_OPS),
                f"{helper} is back in session_ops.py; this proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for node in ast.walk(ast.parse(STOP_RESUME.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"agent_stop_resume.py imports upward from {node.module}",
                )

    def test_the_counter_is_RETURNED_not_mutated(self):
        """The one live-out. The caller reports it in the response body.

        Left as a bare `+=` it would be a helper local after the split and the caller would report
        zero cancellations for a stop that cancelled several — a wrong number rather than an error.
        """
        returned = _helper().body[-1]
        self.assertIsInstance(returned, ast.Return)
        self.assertEqual("cancelled_queued", returned.value.id)
        call = next(
            n for n in ast.walk(ast.parse(SESSION_OPS.read_text(encoding="utf-8")))
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Await)
            and getattr(n.value.value.func, "id", "") == EXTRACTIONS[0]
        )
        self.assertEqual("cancelled_queued", call.targets[0].id, "the caller must rebind the same name")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
