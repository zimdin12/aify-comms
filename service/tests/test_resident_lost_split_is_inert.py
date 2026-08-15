"""The `resident_lost` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: the fallback for an agent whose runtime bridge died and could NOT be returned to
managed. Two very different agents reach it — a resident with no managed backing, which is correctly
stopped, and a managed worker whose backing died, which must rest cold-startable — and the whole
point of the block is that they do not rest in the same state.

THE MULTI-OUTPUT SHAPE is declared because it is the risky one. The call unpacks two values,
`returned, transition`, and a transposed return would have the same names and types and silently swap
them. The gate treats a transposition as NOT self-assigning, so inline-back emits `a, b = b, a`, which
fails to reconstruct the original — that is the intended outcome and is what makes this proof worth
running on a two-output extraction.

THE SUBSTITUTION, declared rather than left to be noticed: the helper lives in
`service/api_core/resident_loss.py`, because leaving it in the router would not have reduced it —
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
RESIDENT_LOSS = REPO / "service" / "api_core" / "resident_loss.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "resident_lost_before_split.py"

SOURCE_FUNCTION = "resident_lost"
EXTRACTIONS = ["_settle_lost_resident_when_no_transition"]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {"_settle_lost_resident_when_no_transition": RESIDENT_LOSS}

MODULES = (SESSION_OPS, RESIDENT_LOSS)


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
        n for n in ast.parse(RESIDENT_LOSS.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
    )


class ResidentLostSplitIsInertTests(unittest.TestCase):
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
        for node in ast.walk(ast.parse(RESIDENT_LOSS.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"resident_loss.py imports upward from {node.module}",
                )

    def test_the_two_outputs_are_returned_in_the_order_the_caller_unpacks(self):
        """The multi-output dialect's one real risk, restated where a reader will look for it.

        A transposed return has the same names, arity and types and silently swaps values — the
        agent's row would be reported under the other transition's label. The round trip above does
        catch it, and this says so in one assertion rather than leaving it to be inferred from a
        failure message about AST inequality.
        """
        returned = _helper().body[-1]
        self.assertIsInstance(returned, ast.Return)
        self.assertEqual(["returned", "transition"], [e.id for e in returned.value.elts])
        call = next(
            n for n in ast.walk(ast.parse(SESSION_OPS.read_text(encoding="utf-8")))
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Await)
            and getattr(n.value.value.func, "id", "") == EXTRACTIONS[0]
        )
        self.assertEqual(
            ["returned", "transition"], [e.id for e in call.targets[0].elts],
            "the caller must unpack in the same order the helper returns")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
