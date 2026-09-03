"""The `update_terminal_control` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: what a completed control implies about the TERMINAL, as opposed to about the
control. The bridge reports on the control; this decides whether that means the terminal stopped,
failed, or is unaffected — and applies the five writes an end status owes.

THE SUBSTITUTION, declared rather than left to be noticed: the helper lives in
`service/api_core/terminal_control_status.py`, because leaving it in the router would not have
reduced it. The extract-method gate needs the caller and the helper in one tree, so the sources are
CONCATENATED for the proof.

SECOND EXTRACTION OUT OF THIS ROUTER, and it gets its own fixture rather than joining
`test_get_terminal_split_is_inert.py`: that proof is anchored on a different FUNCTION. One fixture
per source function, however many blocks come out of it.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
# `update_terminal_control` moved to `terminal_controls.py` in v0.5.4. See the note in
# `test_stop_terminal_split_is_inert.py`: the caller module is a location pin by construction.
CALLER = REPO / "service" / "routers" / "terminal_controls.py"
CONTROL_STATUS = REPO / "service" / "api_core" / "terminal_control_status.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "update_terminal_control_before_split.py"

SOURCE_FUNCTION = "update_terminal_control"

#: Edits made SINCE the split, as (NOW, WAS): the helper rewrites today's text back to the original
#: before comparing, so the current block comes first. Declared rather than folded into the fixture,
#: which is history -- editing that would prove the wrong thing while staying green.
#:
#: THE EDIT, in two places. The terminal status bound into both UPDATEs is now the NORMALISED twin
#: that was already being computed two lines above and used only for the end-status membership check.
#: Both statements compare the same parameter against lowercase literals, so a mixed-case value was
#: stored verbatim, failed those CASE expressions, and then matched no reaper. See
#: test_a_control_writes_the_terminal_status_its_own_sql_compares.py.

_STATUS_PARAMS_NOW_A = chr(10).join([
    '                # NORMALISED, because the statement itself compares against lowercase literals and so',
    '                # does every reader. `terminal_status` is stripped but not lowered; the normalised',
    '                # twin two lines up was built for the end-status membership check and then not used',
    '                # for the writes. A `terminalStatus` of "Stopped" would be stored verbatim, fail the',
    "                # `? IN ('stopped','failed')` CASE so `stopped_at` is never stamped, and then match",
    '                # no reaper -- every one selects on the lowercase members. Same defect as the',
    '                # dispatch-run status, one path over, and here with four consequences instead of one.',
    '                (terminal_status_norm, now, terminal_status_norm, now, status, req.error or "", terminal["id"]),',
]) + chr(10)

_STATUS_PARAMS_WAS_A = chr(10).join([
    '                (terminal_status, now, terminal_status, now, status, req.error or "", terminal["id"]),',
]) + chr(10)

_STATUS_PARAMS_NOW_B = chr(10).join([
    '                # Same normalisation, and the second binding is why it matters here: `owner_mode`',
    "                # only returns to 'managed' when this CASE matches, so a mixed-case stop left the",
    '                # session owned by a console that has gone.',
    '                (terminal_status_norm, terminal_status_norm, now, terminal["session_id"]),',
]) + chr(10)

_STATUS_PARAMS_WAS_B = chr(10).join([
    '                (terminal_status, terminal_status, now, terminal["session_id"]),',
]) + chr(10)

#:
#: THE SECOND EDIT, 2026-09-03: an ADDITION rather than a substitution, so its `WAS` is just the
#: line the new block sits above. The host now REPORTS the size its pty actually opened at, and
#: this records it -- which is the only way a terminal that nobody has resized ever gets a width.
#: Without one the console snapshot has to guess the width from drawn cells, and a screen rendered
#: at a width it was not drawn at re-wraps every line. See
#: test_the_host_reports_the_size_its_pty_actually_has.py.

_SIZE_REPORT_NOW = chr(10).join([
    '        # AND THE SIZE THE HOST SAYS IT ACTUALLY HAS, which is a different fact from the one above.',
    '        # That branch records what the service ASKED for on a resize; this records what the host',
    '        # REPORTS, so it also covers the start control -- where the request is always 0 and the pty',
    '        # is nonetheless opened at some real width.',
    '        #',
    '        # PLACED AFTER the resize branch deliberately: when a control both requests and reports a',
    '        # size, the report wins. The host is the only party that knows what its pty actually took;',
    '        # the request is a wish, and a clamped or refused resize would otherwise be recorded as',
    '        # though it had applied.',
    '        reported_cols = int(req.cols or 0)',
    '        reported_rows = int(req.rows or 0)',
    '        if status == "completed" and reported_cols > 0 and reported_rows > 0:',
    '            await db.execute(',
    '                "UPDATE terminal_sessions SET cols = ?, rows = ? WHERE id = ?",',
    '                (reported_cols, reported_rows, terminal["id"]),',
    '            )',
    '            _resize_live_terminal_screen(terminal["id"], reported_cols, reported_rows)',
    '        if req.output:',
]) + chr(10)

_SIZE_REPORT_WAS = chr(10).join([
    '        if req.output:',
]) + chr(10)

EDITED_SINCE = [
    (_STATUS_PARAMS_NOW_A, _STATUS_PARAMS_WAS_A),
    (_STATUS_PARAMS_NOW_B, _STATUS_PARAMS_WAS_B),
    (_SIZE_REPORT_NOW, _SIZE_REPORT_WAS),
]
EXTRACTIONS = ["_apply_terminal_status_from_control"]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {"_apply_terminal_status_from_control": CONTROL_STATUS}

MODULES = (CALLER, CONTROL_STATUS)


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
        n for n in ast.parse(CONTROL_STATUS.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
    )


class UpdateTerminalControlSplitIsInertTests(unittest.TestCase):
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

        Added after `update_terminal_control` moved out of `terminals.py` in v0.5.4, in the same slice. The round trip already fails in that case — it cannot find the
        caller to inline into — but it fails as a gate-internal error about a missing definition,
        alongside two or three unrelated-looking failures in the same file. That reads like the
        SPLIT broke. This says the true thing in one line instead.
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

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        for helper in EXTRACTIONS:
            self.assertNotIn(
                helper, _declared(CALLER), f"{helper} is back in terminals.py; proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for node in ast.walk(ast.parse(CONTROL_STATUS.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"terminal_control_status.py imports upward from {node.module}",
                )

    def test_the_END_STATUS_SET_IS_NOT_FORKED(self):
        """Imported from its single owner, never re-declared.

        A second copy of which statuses end a terminal is the forked-constant class this series
        exists to remove, and it fails quietly: the copies agree until someone adds a status to one
        of them, and then a terminal ends without its runs being closed.
        """
        leaf = ast.parse(CONTROL_STATUS.read_text(encoding="utf-8"))
        declared = {
            t.id for n in leaf.body if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)
        }
        self.assertNotIn("_TERMINAL_END_STATUSES", declared, "the leaf declares its own copy")
        sources = {
            node.module for node in ast.walk(leaf)
            if isinstance(node, ast.ImportFrom)
            and any(a.name == "_TERMINAL_END_STATUSES" for a in node.names)
        }
        self.assertEqual({"service.api_core.terminal_status"}, sources)

    def test_the_derived_status_is_RETURNED_not_mutated(self):
        """The one live-out: the caller goes on to use it for the resize and response paths."""
        returned = _helper().body[-1]
        self.assertIsInstance(returned, ast.Return)
        self.assertEqual("terminal_status", returned.value.id)
        call = next(
            n for n in ast.walk(ast.parse(CALLER.read_text(encoding="utf-8")))
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Await)
            and getattr(n.value.value.func, "id", "") == EXTRACTIONS[0]
        )
        self.assertEqual("terminal_status", call.targets[0].id, "the caller must rebind the same name")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
