"""The `claim_dispatch_once` split, re-proved against the real code on every run.

Same shape as the other `*_split_is_inert` proofs here: proving the split once at refactor time proves
the commit, running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: the 49-line loop that decides WHICH queued run this bridge may take. It was the
only part of a 422-line function with a decision in it — everything around it is the claim
transaction. A VALUE-shaped extraction: `selected_run` is initialised inside the moved block and is
its single live-out, so the call is an assignment and the caller passes none of it in.

`continue` and `break` travel with the loop they target, so they are not escapes. The gate refuses a
`break` whose loop stays behind in the CALLER — the shape that silently changes control flow — and
accepts this one, which is the distinction the loop-bounded-escape rule exists to draw.

WHAT THIS DOES NOT DO: it proves the extraction is inert. It says nothing about whether the claim
logic is correct — the dispatch and claim-gating tests own that.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
CLAIM = REPO / "service" / "dispatch_claim.py"
SELECTION = REPO / "service" / "api_core" / "claim_run_selection.py"

#: ONE tuple, read by every check below. Naming modules inline per check has gone blind five times in
#: this directory: a helper landing somewhere an inline list does not mention makes the round trip
#: inline NOTHING while the test keeps passing.
MODULES = (CLAIM, SELECTION)
FIXTURE = Path(__file__).resolve().parent / "data" / "claim_dispatch_once_before_split.py"

SOURCE_FUNCTION = "_claim_dispatch_once"
EXTRACTIONS = ["_select_claimable_run"]

#: PER HELPER, not as a set — the set form asserted the owner list was exactly one module, which stops
#: meaning anything the moment a second extraction lands elsewhere.
OWNERS = {"_select_claimable_run": SELECTION}


def _combined_split_source() -> str:
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


class ClaimDispatchOnceSplitIsInertTests(unittest.TestCase):
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
        live = CLAIM.read_text(encoding="utf-8")
        expected = ast.get_source_segment(live, next(
            n for n in ast.parse(live).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )) or ""
        if expected.count("—"):
            self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        declared = {
            n.name for n in ast.parse(CLAIM.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for helper in EXTRACTIONS:
            self.assertNotIn(helper, declared, f"{helper} is back in dispatch_claim.py; this proof is vacuous")

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
        for node in ast.walk(ast.parse(SELECTION.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"claim_run_selection.py imports upward from {node.module}",
                )

    def test_the_call_is_an_ASSIGNMENT_and_the_escapes_stayed_with_their_loop(self):
        """Two properties this split depends on, neither visible to the round trip.

        `selected_run` is the block's single live-out AND is initialised inside it, so the call must
        be `selected_run = await ...`. Written as a bare statement the value would be discarded and
        the caller would read a stale name — a shape the call-site-shape rule now refuses, pinned here
        because this is the function that would suffer from it.

        The `continue`/`break` must remain INSIDE the helper with the loop they target. If the loop
        were ever left behind in the caller, those escapes would bind to a different loop and the
        round trip would still close — which is exactly why the gate refuses that shape and why it is
        worth asserting the loop travelled.
        """
        split = ast.parse(CLAIM.read_text(encoding="utf-8"))
        call = next(
            node for node in ast.walk(split)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Await)
            and isinstance(node.value.value, ast.Call)
            and isinstance(node.value.value.func, ast.Name)
            and node.value.value.func.id == "_select_claimable_run"
        )
        self.assertEqual(
            ["selected_run"], [t.id for t in call.targets if isinstance(t, ast.Name)],
            "the run selector's value must be bound, not discarded",
        )
        helper = next(
            n for n in ast.parse(SELECTION.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "_select_claimable_run"
        )
        loops = [n for n in ast.walk(helper) if isinstance(n, (ast.For, ast.While))]
        self.assertTrue(loops, "the loop must have travelled with its own break/continue")
        escapes = [n for n in ast.walk(helper) if isinstance(n, (ast.Break, ast.Continue))]
        self.assertTrue(escapes, "the skip/select escapes belong to the moved loop")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
