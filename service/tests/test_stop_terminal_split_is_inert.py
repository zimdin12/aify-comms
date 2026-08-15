"""The `stop_terminal` split, re-proved against the real code on every run.

Same shape as the other `*_split_is_inert` proofs here: proving the split once at refactor time proves
the commit, running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: the 58-line branch that settles a stop the BRIDGE CANNOT EXECUTE. It runs when
the terminal is already `stopped`/`failed`, or when no live bridge can claim the control — there is
nobody to hand the stop to, so the row is reconciled in the control plane and the handler answers
from there. It is an EARLY EXIT, which `service/tests/extract_method.py` refused outright until the
call-site-shape rule landed in v0.5.4; that is why a 122-line handler carried it with no way to prove
moving it was inert, and why this proof did not exist before.

IT IS ALSO NESTED. The block calls `_clear_console_terminal_binding`, which already lives in the
destination module, so proving it needed the dependency-ordered inlining added in the same release —
a helper extracted AROUND an existing one could not be verified at all before that.

WHERE IT LANDED: `api_core/terminal_controls_io.py`, whose subject is exactly this — control shaping,
claiming, and console-binding teardown. The block is the teardown path, so it joins the helper it
calls rather than sitting one import away from it.

WHAT THIS DOES NOT DO: it proves the extraction is inert. It says nothing about whether the handler
is correct — the terminal tests own that.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
# `stop_terminal` moved to `terminal_lifecycle.py` in v0.5.4. A round-trip proof names the module
# holding the CALLER, so a relocation must touch it — that is what the location pin costs, and it
# is why `test_the_source_function_is_still_where_this_proof_looks` states it in one line below.
CALLER = REPO / "service" / "routers" / "terminal_lifecycle.py"
CONTROLS = REPO / "service" / "api_core" / "terminal_controls_io.py"

#: ONE tuple, read by every check below. Naming modules inline per check has gone blind five times in
#: this directory: a helper landing somewhere an inline list does not mention makes the round trip
#: inline NOTHING while the test keeps passing.
MODULES = (CALLER, CONTROLS)
FIXTURE = Path(__file__).resolve().parent / "data" / "stop_terminal_before_split.py"

SOURCE_FUNCTION = "stop_terminal"
EXTRACTIONS = ["_reconcile_stop_for_unclaimable_terminal"]

#: PER HELPER, not as a set — the set form asserted the owner list was exactly one module, which stops
#: meaning anything the moment a second extraction lands elsewhere.
OWNERS = {"_reconcile_stop_for_unclaimable_terminal": CONTROLS}


def _combined_split_source() -> str:
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


class StopTerminalSplitIsInertTests(unittest.TestCase):
    def test_the_extraction_inlines_back_to_the_original(self):
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), _combined_split_source(), EXTRACTIONS)

    def test_the_source_function_is_still_where_this_proof_looks(self):
        """`CALLER` is a location pin, and a relocation is what breaks it.

        Added after `stop_terminal` moved out of `terminals.py` in v0.5.4 and this file went red four ways at once. The round trip already fails in that case — it cannot find the
        caller to inline into — but it fails as a gate-internal error about a missing definition,
        alongside two or three unrelated-looking failures in the same file. That reads like the
        SPLIT broke. This says the true thing in one line instead.
        """
        declared = {
            n.name for n in ast.parse(CALLER.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(
            SOURCE_FUNCTION, declared,
            f"{SOURCE_FUNCTION} is not declared in {CALLER.name}. If it was relocated, repoint "
            "CALLER at its new module — this proof names the file holding the caller, so a move "
            "must touch it.",
        )

    def test_the_fixture_is_the_function_it_claims_to_be(self):
        """A fixture that stopped containing the function would make the test above vacuous."""
        names = {
            n.name for n in ast.parse(FIXTURE.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(SOURCE_FUNCTION, names)

    def test_the_fixture_was_not_captured_with_a_mangled_decode(self):
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash.

        That produced a round-trip failure pointing at an untouched block once already. The count is
        asked of the LIVE source rather than hardcoded — a sibling proof copied a `> 5` threshold from
        a neighbour and failed on capture because its function simply had fewer em dashes.
        """
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        live = CALLER.read_text(encoding="utf-8")
        expected = ast.get_source_segment(live, next(
            n for n in ast.parse(live).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )) or ""
        if expected.count("—"):
            self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        declared = {
            n.name for n in ast.parse(CALLER.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for helper in EXTRACTIONS:
            self.assertNotIn(helper, declared, f"{helper} is back in terminals.py; this proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [
                path for path in MODULES
                if any(
                    isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == helper
                    for n in ast.parse(path.read_text(encoding="utf-8")).body
                )
            ]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for node in ast.walk(ast.parse(CONTROLS.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"terminal_controls_io.py imports upward from {node.module}",
                )

    def test_the_helper_binds_its_own_locals_rather_than_taking_them(self):
        """`reason`, `updated` and `ws` are written before they are read, so they are NOT parameters.

        The sibling proof pins the opposite case, and the distinction is the one a free-name scan gets
        wrong in both directions: a name that appears on both sides of the block is a live-in only if
        some path READS it first. Here all three are assigned at the top of their path, so passing
        them in would be noise — and worse, it would let the caller's value leak in silently if the
        assignment were ever moved under a branch.
        """
        split = ast.parse(CALLER.read_text(encoding="utf-8"))
        call = next(
            node for node in ast.walk(split)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "_reconcile_stop_for_unclaimable_terminal"
        )
        passed = {a.id for a in call.args if isinstance(a, ast.Name)}
        for name in ("reason", "updated", "ws"):
            self.assertNotIn(
                name, passed,
                f"{name} is assigned before it is read inside the helper; passing it in would hide a "
                "later change that made the assignment conditional",
            )
        self.assertIn("terminal", passed, "the row itself IS read first and must be passed")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
