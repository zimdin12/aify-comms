"""The `append_terminal_output` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHY THIS ROUTE IS WORTH THE PROOF. It is the hottest write path in the service — a live PTY posts
every 1-4 seconds — and a silent change to it does not raise. It shows up as a console that stops
updating, or output from two processes interleaved into one screen, which reads to an operator as the
agent misbehaving rather than as a bug here.

THE SUBSTITUTION, declared rather than left to be noticed: both helpers live in
`service/api_core/terminal_output_settlement.py`, because leaving them in the router would not have
reduced it — that was the point. The extract-method gate needs the caller and the helpers in one tree,
so the sources are CONCATENATED for the proof. Concatenation changes no body and the gate re-parses
the result, but it is not the single-file comparison the analytics precedent makes.

ONE `MODULES` TUPLE, READ BY EVERY CHECK — written that way from the start. The alternative has gone
blind five times in this directory, twice in the same file.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
TERMINALS = REPO / "service" / "routers" / "terminals.py"
SETTLEMENT = REPO / "service" / "api_core" / "terminal_output_settlement.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "append_terminal_output_before_split.py"

SOURCE_FUNCTION = "append_terminal_output"

#: Edits made SINCE the split, as (NOW, WAS): the helper rewrites today's text back to the original
#: before comparing, so the current block comes first. Declared rather than folded into the fixture,
#: which is history -- editing that would prove the wrong thing while staying green.
#:
#: THE EDIT. A terminal now records HOW its process ended. node-pty gives the bridge the exit code and
#: signal and both were being dropped one hop short of this table, so a death recorded `stopped`, an
#: empty error, and nothing else. Written straight to the row rather than through the coalescing
#: output queue, which exists to batch a high-frequency stream an exit is not part of.
_EXIT_RECORD_NOW = chr(10).join([
    '        status = str(req.status or "").strip()',
    '        # HOW IT ENDED, written straight to the row rather than through the output queue.',
    '        #',
    '        # The queue exists to COALESCE a high-frequency stream: many chunks collapse into one write.',
    '        # An exit is reported once and its two values have nothing to do with that batching, so',
    '        # threading them through the pending state would complicate the hot path to carry a field it',
    '        # would forward unchanged. Writing here also means the exit survives a later output chunk --',
    "        # bytes can still arrive after the exit POST on a busy terminal, and the queue's UPDATE names",
    '        # only output, seq and status, so it cannot clobber these columns.',
    '        #',
    '        # `is not None` rather than truthiness: 0 is a clean exit and the most common value, and',
    '        # `if req.exitCode:` would drop exactly the case this exists to record.',
    '        if req.exitCode is not None or str(req.exitSignal or "").strip():',
    '            await _record_terminal_exit(db, terminal_id, req.exitCode, req.exitSignal)',
]) + chr(10)

_EXIT_RECORD_WAS = chr(10).join([
    '        status = str(req.status or "").strip()',
]) + chr(10)

#: THE SECOND EDIT, 2026-08-26. The sentence a requesting agent reads when a terminal ended under
#: its run said `Terminal stopped ...` for every ending that is not a spawn failure -- a clean
#: exit, a crash and a kill alike -- while the code and signal that say which sat in the same row,
#: written moments earlier in the same request. The block reads them back and builds the sentence
#: from what happened. Read here rather than threaded down because the caller's values are
#: expressions and this gate refuses a call whose argument name differs from its parameter.
_END_SUMMARY_NOW = chr(10).join([
    '            # HOW IT ENDED, read back rather than assumed. `_record_terminal_exit` wrote and committed',
    '            # the exit code and signal on this same connection a few lines earlier in the request, so',
    '            # this SELECT sees them; the `terminal` row in hand was read BEFORE that write and does',
    '            # not carry them.',
    '            #',
    '            # ONE EXTRA QUERY, ON THE ENDING PATH ONLY. This branch runs when a terminal-ending status',
    '            # arrives -- once per terminal, not per output chunk -- so it does not touch the hot',
    "            # ingest path this module's high-frequency half lives on.",
    '            #',
    "            # Read here instead of threaded down from the caller because the caller's values are",
    '            # expressions (`req.exitCode`), and the extract-method gate that proves this helper still',
    '            # inlines back into `append_terminal_output` refuses a call whose argument name differs',
    '            # from the parameter it fills. Reading the row keeps the signature, and with it the proof.',
    '            exit_row = await (await db.execute(',
    '                "SELECT exit_code, exit_signal FROM terminal_sessions WHERE id = ?", (terminal_id,),',
    '            )).fetchone()',
    '            summary = terminal_end_summary(',
    '                status,',
    '                exit_row["exit_code"] if exit_row is not None else None,',
    '                str((exit_row["exit_signal"] if exit_row is not None else "") or ""),',
    '            )',
]) + chr(10)

_END_SUMMARY_WAS = chr(10).join([
    '            summary = f"Terminal {status} before an explicit reply was recorded."',
]) + chr(10)

# ANSWERING A PARKED CONSOLE, added 2026-09-03. The answering itself lives DOWN in the write path,
# where the live screen is current -- checked here in the route it never fired once, because the
# write is coalesced and a worker PARKED at a dialog sends no later chunk to trigger a recheck.
# What is left in this function is naming the chunk once, and dropping a terminal's prompt record
# when it ends. Declared as deletions because they are ADDITIONS: the reconstruction removes them.
_PROMPT_EDITS = [
    ("        chunk_text = req.output or \"\"\n        next_seq = await TERMINAL_OUTPUT_WRITES.enqueue(\n            terminal_id,\n            chunk_text,", "        next_seq = await TERMINAL_OUTPUT_WRITES.enqueue(\n            terminal_id,\n            req.output or \"\","),
    ("        if status in _TERMINAL_END_STATUSES:\n            _forget_answered_prompts(terminal_id)\n", ""),
]
EDITED_SINCE = [(_EXIT_RECORD_NOW, _EXIT_RECORD_WAS), (_END_SUMMARY_NOW, _END_SUMMARY_WAS)] + _PROMPT_EDITS
EXTRACTIONS = ["_settle_bridge_takeover_for_output", "_close_out_terminal_on_end_status"]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {
    "_settle_bridge_takeover_for_output": SETTLEMENT,
    "_close_out_terminal_on_end_status": SETTLEMENT,
}

MODULES = (TERMINALS, SETTLEMENT)


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


class AppendTerminalOutputSplitIsInertTests(unittest.TestCase):
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
        names = {
            n.name for n in ast.parse(FIXTURE.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(SOURCE_FUNCTION, names)

    def test_the_fixture_was_not_captured_with_a_mangled_decode(self):
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash.

        Asked of the LIVE source rather than hardcoded: a sibling proof used a fixed threshold copied
        from a neighbour and failed on capture because its function simply had fewer em dashes.
        """
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        live = TERMINALS.read_text(encoding="utf-8")
        expected = ast.get_source_segment(live, next(
            n for n in ast.parse(live).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )) or ""
        if expected.count("—"):
            self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helpers_are_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        declared = {
            n.name for n in ast.parse(TERMINALS.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for helper in EXTRACTIONS:
            self.assertNotIn(
                helper, declared, f"{helper} is back in terminals.py; this proof is vacuous")

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
        for node in ast.walk(ast.parse(SETTLEMENT.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"terminal_output_settlement.py imports upward from {node.module}",
                )

    def test_the_END_STATUSES_SET_IS_NOT_FORKED(self):
        """The set is PASSED IN, not re-declared in the leaf.

        A second copy of which statuses end a terminal is the forked-constant class this whole series
        has been removing, and it is the kind that fails quietly: the two copies agree until someone
        adds a status to one of them, and then a terminal ends without its runs being closed.
        """
        leaf = ast.parse(SETTLEMENT.read_text(encoding="utf-8"))
        declared = {
            t.id for n in leaf.body if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)
        }
        self.assertNotIn(
            "_TERMINAL_END_STATUSES", declared,
            "the leaf declares its own end-status set; it must receive the router's",
        )
        params = {
            a.arg
            for n in ast.walk(leaf) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            for a in n.args.args
        }
        self.assertIn(
            "_TERMINAL_END_STATUSES", params,
            "the set must arrive as a parameter. It is named in screaming-case deliberately: the "
            "extract-method gate splices a helper body over its call without substituting arguments, "
            "so it refuses any call whose argument name differs from the parameter it fills",
        )

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
