"""The `_launch_recipients_for_dispatch` split, re-proved against the real code on every run.

WHAT WAS EXTRACTED: the legacy via-console delivery path — 57 lines that give a managed-claude
recipient a console to receive on. It fires only when the operator turned `insert_messages_via_console`
on AND managed-terminal backing is enabled; the DEFAULT route is the channel branch beside it, which
leaves the run launchable for claude-channel.js to claim.

THE HELPER STAYED IN THIS MODULE. `dispatch_launch.py` is essentially this one function, so the
problem was never the file — it was a 302-line body whose branches could not be read at once.

CHOOSING THE BLOCK TOOK THREE TRIES, and the two rejected candidates are the useful record. The
largest return-free block is 94 lines, and it is NOT extractable: it ends in `continue`, whose loop
stays behind in the caller, so the escape would bind to a different loop — the shape the gate refuses.
The block above it is nearly the whole body, which would leave an empty shell rather than a decomposed
function. What remained is this one: no return, no loop escape, no live-outs.

THE GATE CAUGHT A MISSING PARAMETER I HAD NOT SEEN. `_execution_mode` is a caller local read inside
the block; omitting it produced a helper that would raise NameError on the first call, and no other
check here would have noticed — the round trip reconstructs the ORIGINAL, where the name is in scope.

WHAT THIS DOES NOT DO: it proves the extraction is inert. Whether the delivery routing is correct is
the dispatch tests' job.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
LAUNCH = REPO / "service" / "api_core" / "dispatch_launch.py"

MODULES = (LAUNCH,)
FIXTURE = Path(__file__).resolve().parent / "data" / "launch_recipients_for_dispatch_before_split.py"

SOURCE_FUNCTION = "_launch_recipients_for_dispatch"
EXTRACTIONS = ["_back_managed_claude_with_a_console"]
OWNERS = {name: LAUNCH for name in EXTRACTIONS}


def _combined_split_source() -> str:
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


class LaunchRecipientsSplitIsInertTests(unittest.TestCase):
    def test_the_extractions_inline_back_to_the_original(self):
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
        live = LAUNCH.read_text(encoding="utf-8")
        expected = ast.get_source_segment(live, next(
            n for n in ast.parse(live).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )) or ""
        if expected.count("—"):
            self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_caller_no_longer_contains_the_bodies(self):
        """A reverted split would round-trip by having nothing to inline.

        The helpers live in the SAME module as their caller, so "is it still declared here" cannot be
        the check — it is declared here on purpose. What must be true is that the CALLER's own body no
        longer holds them, which is what the call sites prove.
        """
        src = LAUNCH.read_text(encoding="utf-8")
        caller = next(
            n for n in ast.parse(src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        called = {
            node.func.id for node in ast.walk(caller)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for helper in EXTRACTIONS:
            self.assertIn(helper, called, f"{helper} is not called; the split was reverted")

    def test_each_helper_is_declared_exactly_once(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        declared = [
            n.name for n in ast.parse(LAUNCH.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for helper in EXTRACTIONS:
            self.assertEqual(1, declared.count(helper), f"{helper} must be declared exactly once")

    def test_the_module_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for node in ast.walk(ast.parse(LAUNCH.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"dispatch_launch.py imports upward from {node.module}",
                )

    def test_the_extracted_block_carries_no_loop_escape(self):
        """The property that made THIS block the one that could move, not the bigger one above it.

        The 94-line candidate ends in `continue`, belonging to a loop that stays in the caller — moved
        into a helper that escape binds to a different loop, or to none, and the gate refuses it. This
        block has none, which is why it was the one available. Pinned because adding a `continue` here
        later would be accepted by every other check in this file while making the helper wrong.
        """
        helper = next(
            n for n in ast.parse(LAUNCH.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
        )
        loops = [n for n in ast.walk(helper) if isinstance(n, (ast.For, ast.AsyncFor, ast.While))]
        escapes = [n for n in ast.walk(helper) if isinstance(n, (ast.Break, ast.Continue))]
        self.assertEqual(
            [], escapes if not loops else [],
            "the helper has a break/continue but no loop of its own to bind it",
        )

    def test_the_caller_local_the_gate_caught_is_still_passed(self):
        """`_execution_mode` is a caller local read inside the block, and it is easy to miss.

        Omitting it produced a helper that raises NameError on its first call, and nothing else here
        would have seen it: the round trip reconstructs the ORIGINAL, where the name is in scope. The
        parameter keeps its leading underscore because inline-back splices the body over the call
        WITHOUT substituting arguments — a renamed parameter is unverifiable.
        """
        src = LAUNCH.read_text(encoding="utf-8")
        helper = next(
            n for n in ast.parse(src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
        )
        self.assertIn("_execution_mode", {a.arg for a in helper.args.args})
        call = next(
            node for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == EXTRACTIONS[0]
        )
        self.assertIn("_execution_mode", {a.id for a in call.args if isinstance(a, ast.Name)})

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
