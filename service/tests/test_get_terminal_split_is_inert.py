"""The `get_terminal` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: choosing a width to draw the terminal at, and rendering the snapshot into the
serialised row. Three sources in priority order — a live screen from the running emulator, then the
PTY's recorded size, then a heuristic inferred from the drawn cells — and the rule that the render is
never NARROWER than the source.

THE SUBSTITUTION, declared rather than left to be noticed: the helper lives in
`service/api_core/terminal_snapshot_view.py`, because leaving it in the router would not have reduced
it — that was the point. The extract-method gate needs the caller and the helper in one tree, so the
sources are CONCATENATED for the proof.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
TERMINALS = REPO / "service" / "routers" / "terminals.py"
VIEW = REPO / "service" / "api_core" / "terminal_snapshot_view.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "get_terminal_before_split.py"

SOURCE_FUNCTION = "get_terminal"

#: Edits made SINCE the split, as (NOW, WAS): the helper rewrites today's text back to the original
#: before comparing, so the current block comes first. Declared rather than folded into the fixture,
#: which is history -- editing that would prove the wrong thing while staying green.
_EVENTS_QUERY_NOW = chr(10).join([
    "        # THE LAST 200, not the first. `ORDER BY id ASC LIMIT 200` returned a terminal's OLDEST",
    '        # events, so for any console busier than 200 rows everything recent -- including whatever',
    '        # it was doing when it died -- was unreachable through the one endpoint that exists to',
    '        # explain a terminal. Measured on a live console: the cap was hit exactly, which is what',
    '        # being truncated looks like from outside.',
    '        #',
    '        # Selected DESC and reversed so the response stays in chronological order: the shape does',
    '        # not change, only which 200 rows it carries.',
    '        events = list(reversed(await (await db.execute(',
    '            "SELECT * FROM terminal_events WHERE terminal_id = ? ORDER BY id DESC LIMIT 200",',
    '            (terminal_id,),',
    '        )).fetchall()))',
])

_EVENTS_QUERY_WAS = chr(10).join([
    '        events = await (await db.execute(',
    '            "SELECT * FROM terminal_events WHERE terminal_id = ? ORDER BY id ASC LIMIT 200",',
    '            (terminal_id,),',
    '        )).fetchall()',
])

#: The grid clamp, after the bounds moved from literals to the constants that already owned them.
#: `TERMINAL_MAX_COLS = 500` and its three siblings were declared at the BOTTOM of
#: `terminal_snapshot.py`, below the functions that clamp with the same numbers -- so those functions,
#: and this call site, wrote the values out. Same numbers, four homes. Behaviour is unchanged, which
#: is precisely why it has to be declared here rather than left to look like a divergence.
_GRID_CLAMP_NOW = chr(10).join([
    '                # THE BOUNDS COME FROM THE RENDERER, which is the binding constraint -- a pyte',
    '                # screen is allocated cols*rows cells. Typed here as literals they were a fourth copy',
    '                # of numbers the snapshot module already declares.',
    '                eff_cols = max(TERMINAL_MIN_COLS, min(max(int(cols), int(src_w or 0)), TERMINAL_MAX_COLS))',
    '                eff_rows = max(TERMINAL_MIN_ROWS, min(int(rows), TERMINAL_MAX_ROWS))',
])

_GRID_CLAMP_WAS = chr(10).join([
    '                eff_cols = max(20, min(max(int(cols), int(src_w or 0)), 500))',
    '                eff_rows = max(5, min(int(rows), 200))',
])

EDITED_SINCE = [
    (_EVENTS_QUERY_NOW, _EVENTS_QUERY_WAS),
    (_GRID_CLAMP_NOW, _GRID_CLAMP_WAS),
]

EXTRACTIONS = ["_attach_terminal_snapshot"]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {"_attach_terminal_snapshot": VIEW}

MODULES = (TERMINALS, VIEW)


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


def _declared(path: Path) -> set[str]:
    return {
        n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class GetTerminalSplitIsInertTests(unittest.TestCase):
    def test_the_extraction_inlines_back_to_the_original(self):
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), _combined_split_source(), EXTRACTIONS,
            edited_since=EDITED_SINCE)

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
                helper, _declared(TERMINALS), f"{helper} is back in terminals.py; this proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for node in ast.walk(ast.parse(VIEW.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"terminal_snapshot_view.py imports upward from {node.module}",
                )

    def test_the_render_helpers_are_reached_from_their_PURE_owner(self):
        """`service/terminal_snapshot.py` is the tested, dependency-free owner of all three.

        The router reached them through its own aliased imports. Importing them from anywhere else —
        a router's shared module, say — would have worked and would have re-created the layering
        problem that blocked the turn-busy extraction for a release.
        """
        modules = {
            node.module for node in ast.walk(ast.parse(VIEW.read_text(encoding="utf-8")))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertEqual({"service.terminal_snapshot"}, modules - {"__future__"})

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
