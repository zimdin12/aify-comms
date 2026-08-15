"""The `_settle_running_spawn` split, re-proved against the real code on every run.

WHAT WAS EXTRACTED: three of the four things that happen when a spawn request becomes a live worker —
migrating its bridge id onto the terminal actually serving the session, handing the waiting work to
dispatch, and giving it a managed PTY. Ninety lines out of a 311-line function.

THE HELPERS STAYED IN THIS MODULE, which is the unusual part and is deliberate. `running_spawn.py` is
already under the 400-line target, so the problem was never the file — it was one function long enough
that its four phases could not be seen at once. Moving the helpers to a new module would have split
one subject across two files to fix a size problem that did not exist, and the three phases are not a
shared subject: bridge migration, dispatch handoff and PTY creation have nothing in common except the
moment they run.

WHY IT WAS EXTRACTABLE AT ALL: `_settle_running_spawn` contains no `return` anywhere. Every other
large function reached in this release has been a guard chain — `_bridge_claim_block_reason` is 208
lines of nothing but early exits at four depths, and extract-method cannot judge those. A function
that only does things, rather than deciding whether to keep going, is the shape that splits cleanly.

WHAT THIS DOES NOT DO: it proves the extractions are inert. Whether the settlement is correct is
`test_running_spawn_reconciliation.py`'s job.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
SPAWN = REPO / "service" / "api_core" / "running_spawn.py"

MODULES = (SPAWN,)
FIXTURE = Path(__file__).resolve().parent / "data" / "settle_running_spawn_before_split.py"

SOURCE_FUNCTION = "_settle_running_spawn"
EXTRACTIONS = [
    "_migrate_bridge_id_onto_live_terminal",
    "_hand_settled_spawn_to_dispatch",
    "_ensure_pty_for_settled_spawn",
]
OWNERS = {name: SPAWN for name in EXTRACTIONS}


def _combined_split_source() -> str:
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


class SettleRunningSpawnSplitIsInertTests(unittest.TestCase):
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
        live = SPAWN.read_text(encoding="utf-8")
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
        src = SPAWN.read_text(encoding="utf-8")
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
            n.name for n in ast.parse(SPAWN.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for helper in EXTRACTIONS:
            self.assertEqual(1, declared.count(helper), f"{helper} must be declared exactly once")

    def test_the_module_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for node in ast.walk(ast.parse(SPAWN.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"running_spawn.py imports upward from {node.module}",
                )

    def test_the_settlement_still_has_no_early_exit(self):
        """The property that made this splittable, pinned so a later edit cannot quietly remove it.

        `_settle_running_spawn` does things; it does not decide whether to keep going. Add a `return`
        mid-body and the next extraction from it becomes unprovable — the gate would refuse it, and
        the reason would look like a gate problem rather than a change to this function's shape.
        """
        caller = next(
            n for n in ast.parse(SPAWN.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        # A SINGLE TRAILING return is not an early exit — this function ends `return session_id`, and
        # my first version of this assertion forgot that and failed on correct code. What must stay
        # absent is a return anywhere OTHER than the final statement.
        self.assertIsInstance(caller.body[-1], ast.Return, "the settlement should end by returning")
        early = [
            node for stmt in caller.body[:-1]
            for node in ast.walk(stmt)
            if isinstance(node, ast.Return)
        ]
        self.assertEqual([], early, "the settlement gained an early exit")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
