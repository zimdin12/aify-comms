"""The `update_terminal_control` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: what a completed control implies about the TERMINAL, as opposed to about the
control. The bridge reports on the control; this decides whether that means the terminal stopped,
failed, or is unaffected — and applies the five writes an end status owes.

THE SUBSTITUTION, declared rather than left to be noticed: the helper lives in
`service/api_core/terminal_control_status.py`, because leaving it in the router would not have
reduced it. The extract-method gate needs the caller and the helper in one tree, so the sources are
CONCATENATED for the proof.

SECOND EXTRACTION OUT OF THIS ROUTER, and it gets its own fixture rather than joining
`test_get_terminal_split_is_inert.py`: that proof is anchored on a different FUNCTION. One fixture
per source function, however many blocks come out of it.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
TERMINALS = REPO / "service" / "routers" / "terminals.py"
CONTROL_STATUS = REPO / "service" / "api_core" / "terminal_control_status.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "update_terminal_control_before_split.py"

SOURCE_FUNCTION = "update_terminal_control"
EXTRACTIONS = ["_apply_terminal_status_from_control"]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {"_apply_terminal_status_from_control": CONTROL_STATUS}

MODULES = (TERMINALS, CONTROL_STATUS)


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
        n for n in ast.parse(CONTROL_STATUS.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
    )


class UpdateTerminalControlSplitIsInertTests(unittest.TestCase):
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
                helper, _declared(TERMINALS), f"{helper} is back in terminals.py; proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for node in ast.walk(ast.parse(CONTROL_STATUS.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"terminal_control_status.py imports upward from {node.module}",
                )

    def test_the_END_STATUS_SET_IS_NOT_FORKED(self):
        """Imported from its single owner, never re-declared.

        A second copy of which statuses end a terminal is the forked-constant class this series
        exists to remove, and it fails quietly: the copies agree until someone adds a status to one
        of them, and then a terminal ends without its runs being closed.
        """
        leaf = ast.parse(CONTROL_STATUS.read_text(encoding="utf-8"))
        declared = {
            t.id for n in leaf.body if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)
        }
        self.assertNotIn("_TERMINAL_END_STATUSES", declared, "the leaf declares its own copy")
        sources = {
            node.module for node in ast.walk(leaf)
            if isinstance(node, ast.ImportFrom)
            and any(a.name == "_TERMINAL_END_STATUSES" for a in node.names)
        }
        self.assertEqual({"service.api_core.terminal_status"}, sources)

    def test_the_derived_status_is_RETURNED_not_mutated(self):
        """The one live-out: the caller goes on to use it for the resize and response paths."""
        returned = _helper().body[-1]
        self.assertIsInstance(returned, ast.Return)
        self.assertEqual("terminal_status", returned.value.id)
        call = next(
            n for n in ast.walk(ast.parse(TERMINALS.read_text(encoding="utf-8")))
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Await)
            and getattr(n.value.value.func, "id", "") == EXTRACTIONS[0]
        )
        self.assertEqual("terminal_status", call.targets[0].id, "the caller must rebind the same name")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
