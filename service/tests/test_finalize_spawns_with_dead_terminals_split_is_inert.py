"""The `_finalize_spawns_with_dead_terminals` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: the sweep's two questions. One finds the spawns whose session has a dead terminal
and no live sibling; the other counts the ones the live-sibling guard held back. They move together
because they must stay in step — they share `end_statuses` and the same notion of dead-and-live, and
if one learned a new end status and the other did not, the counter would report a number about a
different question than the sweep was asking.

DECLARED SUBSTITUTION, and it is a real one rather than a formality. `_terminal_end_statuses_ordered`
travelled with its only two callers, and its DOCSTRING was CORRECTED rather than moved verbatim. It
claimed the constant was owned by "the router" and that a module-level import "would be a cycle";
both were true when written and neither is now, since `_TERMINAL_END_STATUSES_ORDERED` lives in
`service/api_core/terminal_status.py`, which imports nothing. The function body is unchanged — the
round trip below covers the two QUERIES, not the accessor, so the correction is safe and is named
here rather than left for a reader to notice.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
# `_finalize_spawns_with_dead_terminals` moved to `reconcilers/spawn_terminal_settlement.py` in
# v0.5.4 with the superseded-spawn reaper: both end a RUNNING spawn the world has moved past,
# while the two left behind repair spawn REQUESTS. A round-trip proof names the module holding
# the CALLER, so a relocation must touch it — see the one-line pin below.
CALLER = REPO / "service" / "reconcilers" / "spawn_terminal_settlement.py"
QUERY = REPO / "service" / "api_core" / "dead_terminal_spawn_query.py"
STATUS_OWNER = REPO / "service" / "api_core" / "terminal_status.py"
FIXTURE = (Path(__file__).resolve().parent / "data"
           / "finalize_spawns_with_dead_terminals_before_split.py")

SOURCE_FUNCTION = "_finalize_spawns_with_dead_terminals"

#: Edits made to the sweep SINCE the split, declared so the round trip stays exact without the
#: fixture pretending they never happened. The fixture is the pre-split original and is history; an
#: edit that quietly rewrote it would prove the wrong thing while staying green.
#: The block as it stands today. A LITERAL, not a read of the source: reading it would make the
#: declaration agree with whatever is there and prove nothing. Joined with chr(10) because the
#: split source is read with universal newlines, so the comparison is against LF.
_CURRENT_DETAIL_BLOCK = chr(10).join([
    '        # THREE OUTCOMES, not two. "no output was recorded" was said whenever no cause could be',
    '        # picked, including for a terminal that recorded plenty -- a drawing TUI whose screen holds',
    '        # conversation and progress meters but no epitaph. Telling an operator nothing was recorded',
    '        # sends them looking for a logging fault; telling them the console held no cause sends them',
    '        # to the console, which is where the answer actually is.',
    '        # MEANINGFUL content, not raw length. A console that recorded only harness scaffolding',
    '        # ("[terminal attached]", "[terminal exited]") recorded nothing an operator can use, and',
    '        # saying its console "recorded no cause" would send them to read two lines of nothing.',
    '        recorded = bool(_meaningful_lines(str(row["terminal_output"] or "")))',
    '        if cause:',
    '            detail = f": {cause}"',
    '        elif recorded:',
    '            detail = " (its console recorded no cause; read the console for what it was doing)"',
    '        else:',
    '            detail = " (no output was recorded)"',
])

EDITED_SINCE = [
    (
        # A dead TUI records a screen, not an epitaph. Saying "no output was recorded" for a
        # console holding conversation and progress meters sends an operator hunting a logging
        # fault; three outcomes tell them where the answer actually is.
        # (NOW, WAS) -- the helper replaces the current text with the original before comparing,
        # so the block that exists today comes first.
        _CURRENT_DETAIL_BLOCK,
        '        detail = f": {cause}" if cause else " (no output was recorded)"',
    ),
]

EXTRACTIONS = [
    "_select_spawns_with_dead_terminals",
    "_count_spawns_masked_by_live_sibling",
]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {
    "_select_spawns_with_dead_terminals": QUERY,
    "_count_spawns_masked_by_live_sibling": QUERY,
}

MODULES = (CALLER, QUERY)

#: Travelled WITH the two queries: they were its only callers.
RELOCATED_ACCESSOR = "_terminal_end_statuses_ordered"


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


def _declared(path: Path) -> set[str]:
    return {
        n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class FinalizeSpawnsWithDeadTerminalsSplitIsInertTests(unittest.TestCase):
    def test_the_extraction_inlines_back_to_the_original(self):
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), _combined_split_source(), EXTRACTIONS,
            edited_since=EDITED_SINCE)

    def test_the_source_function_is_still_where_this_proof_looks(self):
        """`CALLER` is a location pin, and a relocation is what breaks it.

        Added when the finaliser moved out of `spawn_lifecycle.py` in v0.5.4. The round trip already
        fails then — it cannot find the caller to inline into — but it fails as a gate-internal
        error about a missing definition. This says the true thing in one line.
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
        self.assertIn(SOURCE_FUNCTION, _declared(FIXTURE))

    def test_the_fixture_was_not_captured_with_a_mangled_decode(self):
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash."""
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helpers_are_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        for helper in EXTRACTIONS:
            self.assertNotIn(
                helper, _declared(CALLER),
                f"{helper} is back in spawn_terminal_settlement.py; this proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for node in ast.walk(ast.parse(QUERY.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"dead_terminal_spawn_query.py imports upward from {node.module}",
                )

    def test_the_accessor_TRAVELLED_and_still_borrows_one_owner(self):
        """The relocation, and the reason the accessor exists at all.

        Forking a second copy of the end-status set is what produced finding N7 — two managed-worker
        sweeps disagreeing about `degraded`. The accessor must therefore still READ the owner rather
        than declare anything.
        """
        self.assertIn(RELOCATED_ACCESSOR, _declared(QUERY))
        self.assertNotIn(RELOCATED_ACCESSOR, _declared(CALLER))
        source = QUERY.read_text(encoding="utf-8")
        self.assertIn("from service.api_core.terminal_status import _TERMINAL_END_STATUSES_ORDERED",
                      source)
        declared_constants = {
            t.id for n in ast.parse(source).body if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)
        }
        self.assertEqual(set(), declared_constants, "the leaf must declare no constants of its own")

    def test_the_CORRECTED_docstring_no_longer_claims_a_cycle_that_is_gone(self):
        """The substitution, asserted so it cannot silently revert to the false version.

        `service/api_core/terminal_status.py` imports nothing, so a module-level import of the
        constant is not a cycle and has not been since it left the router.
        """
        owner_imports = [
            node for node in ast.walk(ast.parse(STATUS_OWNER.read_text(encoding="utf-8")))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and getattr(node, "module", None) != "__future__"
        ]
        self.assertEqual([], owner_imports, "the status owner must stay dependency-free")
        accessor_doc = ast.get_docstring(next(
            n for n in ast.parse(QUERY.read_text(encoding="utf-8")).body
            if isinstance(n, ast.FunctionDef) and n.name == RELOCATED_ACCESSOR)) or ""
        self.assertNotIn("the router still owns", accessor_doc)
        self.assertIn("no longer buys anything", accessor_doc,
                      "the docstring must say the laziness is now vestigial")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
