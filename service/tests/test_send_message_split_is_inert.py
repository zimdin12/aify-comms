"""The `send_message` split, re-proved against the real code on every run.

Same shape as `test_register_agent_split_is_inert.py` and `test_analytics_split_is_inert.py`: proving the
split once at refactor time proves the commit, running the round trip in the suite proves it STAYS true.

WHY THIS ONE MATTERS MORE THAN THE OTHER TWO. `send_message` is the hottest user-facing path in the
product, and the block extracted from it is 278 lines that decide, per recipient, whether a worker exists
or must be cold-started. A silent change here does not raise — it strands a dispatch, which looks like an
idle agent.

DECLARED SUBSTITUTION: the helper lives in `service/api_core/dispatch_start.py`, not in `messages.py`,
because leaving it in place would not have reduced the file — that was the point. The extract-method gate
needs the caller and the helper in one tree, so the two sources are CONCATENATED for the proof.
Concatenation changes no body and the gate re-parses the result, but it is not the single-file comparison
the analytics precedent makes, so it is named here rather than left to be noticed.

WHAT THIS DOES NOT DO: it proves the extraction is inert. It does not prove the helper landed in a
sensible module, and it says nothing about the route's behaviour — the dispatch tests own that.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
MESSAGES = REPO / "service" / "routers" / "dispatch_messages" / "messages.py"
DISPATCH_START = REPO / "service" / "api_core" / "dispatch_start.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "send_message_before_split.py"

SOURCE_FUNCTION = "send_message"
EXTRACTIONS = ["_launch_recipients_for_dispatch"]


def _combined_split_source() -> str:
    return "\n\n".join(p.read_text(encoding="utf-8") for p in (MESSAGES, DISPATCH_START))


class SendMessageSplitIsInertTests(unittest.TestCase):
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

        That produced a round-trip failure pointing at an untouched block once already. The captured
        comments contain em dashes, so their absence means the fixture is corrupt rather than merely
        different — and a corrupt fixture makes the comparison above compare the wrong thing.
        """
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertGreater(text.count("—"), 5, "fixture looks locale-mangled, not utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        declared = {
            n.name for n in ast.parse(MESSAGES.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for helper in EXTRACTIONS:
            self.assertNotIn(helper, declared, f"{helper} is back in messages.py; this proof is vacuous")

    def test_exactly_one_module_declares_the_helper(self):
        owners = [
            path for path in (MESSAGES, DISPATCH_START)
            if any(
                isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in EXTRACTIONS
                for n in ast.parse(path.read_text(encoding="utf-8")).body
            )
        ]
        self.assertEqual([DISPATCH_START], owners)

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent.

        This one is worth asserting rather than assuming: the extracted loop needed THIRTEEN names, and
        `messages.py` reaches several of them through `dispatch_messages/shared.py`. Importing them from
        there would have been the convenient move and an upward one.
        """
        for node in ast.walk(ast.parse(DISPATCH_START.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"dispatch_start.py imports upward from {node.module}",
                )

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
