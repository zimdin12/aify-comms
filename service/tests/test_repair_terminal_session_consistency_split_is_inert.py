"""The `_repair_terminal_session_consistency` deduplication, re-proved on every run.

WHAT WAS EXTRACTED: the eight lines that CLOSE a repair — clear the console binding if the terminal
had an agent, append the `terminal_consistency_repaired` event — which appeared at the end of all
THREE repair branches, verbatim. Dead PTY, stale status, orphaned row: each writes its own UPDATE and
then repeated the same ending.

THIS IS A DEDUPLICATION, WHICH IS WHY IT NEEDED A GATE CHANGE. Until v0.5.4 the extract-method
verifier refused any helper called more than once ("inline-back is only defined for a single call
site"), so a block appearing three times had no provable fix. Inline-back now splices the body back
into EVERY site, which is what makes this round trip meaningful: if the three endings had NOT been
identical, putting one body back in all three places could not reconstruct the original and this test
would fail.

WHY THE DUPLICATION MATTERED. Three copies of one ending is how a branch quietly stops clearing the
console binding — a fix applied to the branch being debugged leaves the other two behind, and the
dashboard then auto-mounts an xterm over a dead PTY. That is not hypothetical; it is the incident
`_clear_console_terminal_binding` documents.

`repaired += 1` STAYED at the call sites. It reads and writes a counter the caller owns, so moving it
would turn a void helper into one whose return value must not be dropped.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
CONSISTENCY = REPO / "service" / "reconcilers" / "terminal_consistency.py"

MODULES = (CONSISTENCY,)
FIXTURE = (
    Path(__file__).resolve().parent / "data"
    / "repair_terminal_session_consistency_before_split.py"
)

SOURCE_FUNCTION = "_repair_terminal_session_consistency"
EXTRACTIONS = ["_record_consistency_repair"]
OWNERS = {"_record_consistency_repair": CONSISTENCY}

EXPECTED_CALL_SITES = 3


def _combined_split_source() -> str:
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


class RepairTerminalSessionConsistencySplitIsInertTests(unittest.TestCase):
    def test_the_extraction_inlines_back_to_the_original(self):
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
        self.assertNotIn(
            "�", FIXTURE.read_text(encoding="utf-8"), "fixture has U+FFFD replacement characters")

    def test_all_THREE_call_sites_still_exist(self):
        """The whole point is that one helper serves three places.

        If two were ever re-inlined the round trip would fail, but if two were DELETED it would not —
        a shorter original is a different function, and the fixture comparison is what catches that.
        This pins the count directly so the intent is legible: three branches, one ending.
        """
        calls = [
            node for node in ast.walk(ast.parse(CONSISTENCY.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == EXTRACTIONS[0]
        ]
        self.assertEqual(EXPECTED_CALL_SITES, len(calls))

    def test_the_call_sites_are_IDENTICAL(self):
        """The gate allows several call sites only when the calls are the same.

        Every other rule it applies — arguments, whether they are bound yet, what the statement does
        with the result — resolves ONE call site, so identical calls are what makes checking one of
        them sufficient. Asserted here rather than trusted, because a later edit that varied one call
        would still round-trip and would quietly leave the other two unexamined.
        """
        calls = [
            ast.dump(node, include_attributes=False)
            for node in ast.walk(ast.parse(CONSISTENCY.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == EXTRACTIONS[0]
        ]
        self.assertEqual(1, len(set(calls)), "the three calls must be identical")

    def test_the_counter_stayed_with_the_caller(self):
        """`repaired += 1` must NOT have travelled: it is the caller's own running total."""
        helper = next(
            n for n in ast.parse(CONSISTENCY.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
        )
        names = {
            t.target.id for t in ast.walk(helper)
            if isinstance(t, ast.AugAssign) and isinstance(t.target, ast.Name)
        }
        self.assertNotIn("repaired", names)

    def test_exactly_one_module_declares_the_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [
                path for path in MODULES
                if any(
                    isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == helper
                    for n in ast.parse(path.read_text(encoding="utf-8")).body
                )
            ]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
