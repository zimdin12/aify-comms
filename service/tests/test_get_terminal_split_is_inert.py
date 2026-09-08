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

#: DECLARED EDIT, 2026-09-08. The endpoint offers a named PROJECTION: the console's callers repaint
#: from the snapshot and a handful of size fields, and were being sent 147,250 bytes to do it --
#: 93,430 bytes of raw tail the snapshot replaces and a 46,516-byte event page neither reads, sized
#: with the server's own encoder and checked against the wire. The default response
#: is untouched, which is what makes the addition safe without first proving no other consumer
#: anywhere reads that page. Behaviour for every existing caller is unchanged, which is exactly why
#: it has to be declared here rather than left to look like a divergence.
_CONSOLE_VIEW_SIGNATURE_NOW = chr(10).join([
    "async def get_terminal(",
    "    terminal_id: str,",
    "    cols: Optional[int] = None,",
    "    rows: Optional[int] = None,",
    "    view: Optional[str] = None,",
    "):",
])

_CONSOLE_VIEW_SIGNATURE_WAS = (
    "async def get_terminal(terminal_id: str, cols: Optional[int] = None, rows: Optional[int] = None):"
)

_CONSOLE_VIEW_BRANCH_NOW = chr(10).join([
    '        if view == "console":',
    "            # THE PROJECTION A CONSOLE ACTUALLY REPAINTS FROM, and nothing else. One response",
    "            # measured on the live fleet 2026-09-08, decomposed with the server's own encoder and",
    "            # checked by re-encoding it against its wire size: 147,250 bytes, of which the raw",
    "            # output tail is 93,430 and the event page 46,516, while the console writes the",
    "            # 6,442-byte snapshot and reads a handful of size fields. Every sequence gap costs one",
    "            # of these -- that is the standing suspect for the operator's intermittent lag -- and so",
    "            # does every console mount.",
    "            #",
    "            # THE TAIL IS DROPPED ONLY WHEN THERE IS A SNAPSHOT TO REPLACE IT. Both console callers",
    "            # write `snapshot || output`: the fallback is real and is taken whenever pyte could not",
    "            # render (it is optional, and a dead terminal has its buffer forgotten). Dropping the",
    "            # tail unconditionally would blank exactly the screen an operator opens a dead console to",
    "            # read. So the server answers the question the caller is actually asking -- give me what",
    "            # I will paint -- rather than being handed a list of fields to omit.",
    "            #",
    "            # A NAMED PROJECTION RATHER THAN A BAG OF TOGGLES, and the default is untouched: a caller",
    "            # that does not ask for it gets exactly today's response, so nothing else on this",
    "            # endpoint had to be proven uninterested in the event page first.",
    '            if term_dict.get("snapshot"):',
    '                term_dict.pop("output", None)',
    '            return {"ok": True, "terminal": term_dict, "view": "console"}',
    "        return {",
])

_CONSOLE_VIEW_BRANCH_WAS = "        return {"

#: DECLARED EDIT, 2026-08-29. `GET /terminals/{id}` reads one row wider than the page and says
#: whether the event list is truncated, and the cap moved to `TERMINAL_EVENTS_KEPT_PER_TERMINAL`
#: -- it was written as a literal here AND in the pruner. Undone rather than re-captured, so the
#: pre-split baseline survives.
#: DECLARED EDIT, 2026-09-08. The snapshot bytes and the sequence describing them are bound to ONE
#: generation. `_terminal_session_to_dict` reads the sequence, the caller then AWAITS the agent's
#: role lookup, and the live screen is rendered after it -- so output appended during that await was
#: in the picture and not in the number, and a browser seeded with the pair would be sent bytes it
#: already had. Read with no await between it and the render, which is what makes it a pair.
_LIVE_SEQ_NOW = chr(10).join([
    '            term_dict["renderedRows"] = live_rows',
    "            # THE BYTES AND THE SEQUENCE COME FROM ONE GENERATION, and until 2026-09-08 they did",
    "            # not. The whole-diff review constructed the tear: `_terminal_session_to_dict` reads",
    "            # `outputSeq` from the live buffer, the caller then AWAITS the agent's role lookup, and",
    "            # this render happens after it. Output appended during that await is IN the screen and",
    "            # NOT in the number, so the response seeds a browser with a picture already containing",
    "            # bytes it is about to be sent again -- and for a TUI a second write of the same bytes",
    "            # is a cursor movement nobody asked for, which is the corruption class the sequence",
    "            # exists to prevent. The next quiescent GET then returns the higher sequence with the",
    "            # identical snapshot, which is how the tear hides.",
    "            #",
    "            # RE-READ HERE, NOT MOVED EARLIER. Moving the await only narrows the window. This is a",
    "            # synchronous read taken with no await between it and the render above, and",
    "            # `_append_terminal_output` feeds the screen and records the sequence with no await",
    "            # between those either -- so on one event loop the pair cannot be split.",
    '            term_dict["outputSeq"] = current_seq(str(term_dict["id"]), term_dict.get("outputSeq"))',
])

_LIVE_SEQ_WAS = '            term_dict["renderedRows"] = live_rows'

#: The import the read above needs. `terminal_tail_buffer` is a dependency-free leaf and is the
#: OWNER of the sequence, which is why the layering gate below admits it by name.
_LIVE_SEQ_IMPORT_NOW = chr(10).join([
    "from service.api_core.terminal_tail_buffer import current_seq",
    "from service.terminal_snapshot import (",
])

_LIVE_SEQ_IMPORT_WAS = "from service.terminal_snapshot import ("

EDITED_SINCE = [
    (_LIVE_SEQ_NOW, _LIVE_SEQ_WAS),
    (_LIVE_SEQ_IMPORT_NOW, _LIVE_SEQ_IMPORT_WAS),
    (
        '\nfrom service.api_core.tuning import TERMINAL_EVENTS_KEPT_PER_TERMINAL\nfrom service.api_core.events import _append_terminal_control, _append_terminal_event',
        '\nfrom service.api_core.events import _append_terminal_control, _append_terminal_event',
    ),
    (
        '        # not change, only which 200 rows it carries.\n        # ONE ROW WIDER THAN THE PAGE, so the response can say whether this is the whole history --\n        # the same shape as /sessions, /dispatch/runs, /contracts and /messages/recent. The number\n        # comes from `TERMINAL_EVENTS_KEPT_PER_TERMINAL` rather than being written here a second\n        # time: the pruner keeps exactly that many, and two hardcoded 200s in different modules\n        # agreed by coincidence.\n        event_rows = await (await db.execute(\n            "SELECT * FROM terminal_events WHERE terminal_id = ? ORDER BY id DESC LIMIT ?",\n            (terminal_id, TERMINAL_EVENTS_KEPT_PER_TERMINAL + 1),\n        )).fetchall()\n        events_truncated = len(event_rows) > TERMINAL_EVENTS_KEPT_PER_TERMINAL\n        events = list(reversed(event_rows[:TERMINAL_EVENTS_KEPT_PER_TERMINAL]))\n        term_dict = _terminal_session_to_dict(terminal)',
        '        # not change, only which 200 rows it carries.\n        events = list(reversed(await (await db.execute(\n            "SELECT * FROM terminal_events WHERE terminal_id = ? ORDER BY id DESC LIMIT 200",\n            (terminal_id,),\n        )).fetchall()))\n        term_dict = _terminal_session_to_dict(terminal)',
    ),
    (
        '            "events": [_terminal_event_to_dict(row) for row in events],\n            # WHAT THE CALLER IS LOOKING AT. Measured 2026-08-29: 21 of 26 terminals held 200 or\n            # more events, so for most of them this list was already a page and said nothing.\n            "eventsShowing": len(events),\n            "eventsTruncated": events_truncated,\n        }',
        '            "events": [_terminal_event_to_dict(row) for row in events],\n        }',
    ),
    (_EVENTS_QUERY_NOW, _EVENTS_QUERY_WAS),
    (_GRID_CLAMP_NOW, _GRID_CLAMP_WAS),
    (_CONSOLE_VIEW_SIGNATURE_NOW, _CONSOLE_VIEW_SIGNATURE_WAS),
    (_CONSOLE_VIEW_BRANCH_NOW, _CONSOLE_VIEW_BRANCH_WAS),
]

#: What `terminal_snapshot_view.py` may import, each entry with the reason it qualifies. The verdict
#: on every entry is DERIVED in `test_every_module_on_that_list_is_ITSELF_a_leaf`, so a name approved
#: once cannot carry a cycle in later.
ALLOWED_IMPORTS = {
    "service.terminal_snapshot": "the tested, dependency-free owner of the render helpers",
    "service.api_core.terminal_tail_buffer":
        "the owner of the sequence the rendered screen has to be paired with, in one generation",
}

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
        """Exactly the modules on the list, and nothing else.

        The router reached the render helpers through its own aliased imports. Importing them from
        anywhere else — a router's shared module, say — would have worked and would have re-created
        the layering problem that blocked the turn-busy extraction for a release.

        THE SECOND ENTRY ARRIVED 2026-09-08 and it is a decision, not a widening to make a red test
        green. The snapshot and the sequence describing it have to be read with no await between
        them or they are two generations, which review constructed; the sequence's owner is
        `terminal_tail_buffer`, so the read has to happen where the render happens. The list stays a
        LIST because each entry needs a reason a person wrote down — and the verdict on each entry
        is derived below rather than taken on trust.
        """
        modules = {
            node.module for node in ast.walk(ast.parse(VIEW.read_text(encoding="utf-8")))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertEqual(set(ALLOWED_IMPORTS), modules - {"__future__"})

    def test_every_module_on_that_list_is_ITSELF_a_leaf(self):
        """The list is the population; this is the verdict, and it is derived.

        An allowed module that reached into a router would carry the cycle in behind a name somebody
        approved once — which is exactly how a whitelist rots. Each entry is re-checked against the
        same rule `test_the_leaf_does_not_import_upward` applies to the view itself.
        """
        for name, why in ALLOWED_IMPORTS.items():
            path = REPO / (name.replace(".", "/") + ".py")
            self.assertTrue(path.exists(), f"{name} is allowed ({why}) and names no file")
            reached = {
                node.module for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(node, ast.ImportFrom) and node.module
            }
            upward = {
                module for module in reached
                if module.startswith("service.routers") or module == "service.control_plane"
            }
            self.assertEqual(set(), upward, f"{name} is on the allowed list and reaches up into {upward}")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
