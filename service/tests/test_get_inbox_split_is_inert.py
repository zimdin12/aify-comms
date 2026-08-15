"""The `get_inbox` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: everything a non-peek inbox read WRITES. `GET /inbox` looks like a read and is
not one — it stamps read receipts, completes dispatch runs stranded by a bridge that died mid-turn,
and refreshes the caller's own status. `peek=true` does none of it, which is why the parameter
exists: a dashboard poll has to be able to look at an inbox without marking it read or telling the
status engine the agent just started working.

THE SUBSTITUTION, declared rather than left to be noticed: the helper lives in
`service/api_core/inbox_read_receipts.py`, because leaving it in the router would not have reduced
it — that was the point. The extract-method gate needs the caller and the helper in one tree, so the
sources are CONCATENATED for the proof. Concatenation changes no body and the gate re-parses the
result, but it is not the single-file comparison the analytics precedent makes.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
MESSAGES = REPO / "service" / "routers" / "dispatch_messages" / "messages.py"
RECEIPTS = REPO / "service" / "api_core" / "inbox_read_receipts.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "get_inbox_before_split.py"

SOURCE_FUNCTION = "get_inbox"
EXTRACTIONS = ["_settle_inbox_read"]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {"_settle_inbox_read": RECEIPTS}

MODULES = (MESSAGES, RECEIPTS)


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


def _declared(path: Path) -> set[str]:
    return {
        n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _helper() -> ast.AsyncFunctionDef:
    return next(
        n for n in ast.parse(RECEIPTS.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
    )


class GetInboxSplitIsInertTests(unittest.TestCase):
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
        if RECEIPTS.read_text(encoding="utf-8").count("—"):
            self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        for helper in EXTRACTIONS:
            self.assertNotIn(
                helper, _declared(MESSAGES), f"{helper} is back in messages.py; this proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for node in ast.walk(ast.parse(RECEIPTS.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"inbox_read_receipts.py imports upward from {node.module}",
                )

    def test_PEEK_still_suppresses_every_write(self):
        """The parameter's entire purpose, asserted structurally.

        `peek=true` must reach none of the three writes. If a later edit hoisted one of them above
        the guard — a receipt, the stranded-run completion, or the status refresh — the round trip
        would still close (it proves the block moves faithfully, not that the block is right), and a
        dashboard poll would start marking inboxes read and reporting the agent as working.
        """
        helper = _helper()
        # Drop the docstring by shape rather than by index — an index would turn "someone deleted
        # the docstring" into a confusing failure about the guard.
        statements = [
            node for node in helper.body
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str))
        ]
        self.assertEqual(1, len(statements), "the guard must be the helper's only statement")
        guard = statements[0]
        self.assertIsInstance(guard, ast.If)
        self.assertIsInstance(guard.test, ast.UnaryOp)
        self.assertIsInstance(guard.test.op, ast.Not)
        self.assertEqual("peek", guard.test.operand.id)
        self.assertEqual([], guard.orelse, "a peek must do nothing at all, not something else")

    def test_QUEUED_runs_are_left_for_the_bridge_to_claim(self):
        """The subtle one, and the reason the status filter is not a detail.

        Only runs a bridge ALREADY took are completed here. A queued run is what the bridge claims in
        order to wake the agent as a turn; completing it from a read would silently delete the wake,
        and nothing would raise — the message would simply be marked read and the agent never woken.
        """
        statements = [
            node.value for node in ast.walk(_helper())
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and "UPDATE dispatch_runs" in node.value
        ]
        self.assertEqual(1, len(statements), "expected exactly one dispatch_runs update")
        sql = statements[0]
        self.assertIn("status IN ('claimed', 'running')", sql)
        self.assertNotIn("queued", sql, "a queued run must be left for the bridge to claim")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
