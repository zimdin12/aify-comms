"""The `start_session_console` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: the refusal that fires when the chosen environment cannot host a Console. Both of
its branches raise 409, so a caller reading only the status code cannot tell them apart — and they
have different fixes. Whole-environment PTY capability being off is a HOST problem (node-pty is not
installed or built for that bridge, and the Console is dead there for every runtime); an
advertised-runtimes miss is a SELECTION problem (the host is fine, this runtime is not on its list).

THE SUBSTITUTION, declared rather than left to be noticed: the helper lives in
`service/api_core/console_capability_gate.py`, because leaving it in the router would not have
reduced it — that was the point. The extract-method gate needs the caller and the helper in one tree,
so the sources are CONCATENATED for the proof. Concatenation changes no body and the gate re-parses
the result, but it is not the single-file comparison the analytics precedent makes.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
SESSIONS = REPO / "service" / "routers" / "sessions.py"
GATE = REPO / "service" / "api_core" / "console_capability_gate.py"
#: The two terminal_sessions inserts moved together, into a module of their own rather than
#: beside the gate: they refuse nothing, and the point of the move was to put the TWINS side
#: by side where their duplication is visible.
ROWS = REPO / "service" / "api_core" / "console_terminal_rows.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "start_session_console_before_split.py"

SOURCE_FUNCTION = "start_session_console"
EXTRACTIONS = [
    "_refuse_console_without_terminal_capability",
    "_insert_virtual_console_terminal",
    "_insert_pty_console_terminal",
    # The virtual-RPC REUSE path — an early exit, unprovable until the call-site-shape rule.
    "_reuse_virtual_rpc_console_terminal",
]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {
    "_reuse_virtual_rpc_console_terminal": ROWS,
    "_refuse_console_without_terminal_capability": GATE,
    "_insert_virtual_console_terminal": ROWS,
    "_insert_pty_console_terminal": ROWS,
}

MODULES = (SESSIONS, GATE, ROWS)


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


def _declared(path: Path) -> set[str]:
    return {
        n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class StartSessionConsoleSplitIsInertTests(unittest.TestCase):
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
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash.

        Asked of the LIVE source rather than hardcoded: a sibling proof used a fixed threshold copied
        from a neighbour and failed on capture because its function simply had fewer em dashes.
        """
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        if GATE.read_text(encoding="utf-8").count("—"):
            self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        for helper in EXTRACTIONS:
            self.assertNotIn(
                helper, _declared(SESSIONS), f"{helper} is back in sessions.py; this proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaves_do_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent.

        Over EVERY leaf. Naming one module is how this check goes quietly blind the moment a second
        helper lands somewhere else, which is exactly what happened here.
        """
        for leaf in (GATE, ROWS):
            for node in ast.walk(ast.parse(leaf.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(
                        node.module.startswith("service.routers")
                        or node.module == "service.control_plane",
                        f"{leaf.name} imports upward from {node.module}",
                    )

    def test_BOTH_refusal_branches_survive_with_their_distinct_remedies(self):
        """The reason this block is worth extracting at all, asserted so it cannot quietly collapse.

        Both branches raise 409. If a later edit merged them into one "terminal not supported"
        message, every test here would still pass — the round trip would simply prove the new,
        collapsed block moves faithfully. So the DISTINCTION is asserted directly: one branch must
        name the host-side fix (install/build node-pty for that bridge), the other must name the
        selection-side fix (the runtimes that environment actually advertises).
        """
        source = GATE.read_text(encoding="utf-8")
        self.assertIn("node-pty", source, "the host-capability branch must name the host-side fix")
        self.assertIn(
            "terminalRuntimes", source,
            "the runtime-selection branch must read what the environment advertises")
        gate = next(
            n for n in ast.parse(source).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
        )
        assignments = [
            n for n in ast.walk(gate) if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "detail" for t in n.targets)
        ]
        self.assertEqual(
            2, len(assignments),
            "the two refusals have different remedies and must stay two distinct messages")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
