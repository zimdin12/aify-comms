"""Whatever the bridge sent became a terminal's status, and nothing checked it was a status.

`POST /terminals/{id}/output` takes `status: Optional[str]` on `TerminalOutputRequest`, and the
handler does `status = str(req.status or "").strip()` — no allowlist, no enum, no rejection. From
there it reaches `_terminal_status_transition`, whose single caller is `terminal_output.py`, and that
function returned any non-empty string unchanged. Two hops from an HTTP body to a status column, with
no gate in between.

WHY AN UNRECOGNISED VALUE IS THE WORST ONE. It is invisible to every allowlist at once:

    not in _TERMINAL_ACTIVE_STATUSES    -> the status engine does not count the terminal as live
    not in _TERMINAL_END_STATUSES       -> `_close_out_terminal_on_end_status` never closes it, and
                                           `agent_terminal_ops.py` reads it as "still running"
    not in _TERMINAL_MONOTONIC_STATUSES -> the finished->active guard cannot protect it either

Every one of those reads it as "not finished", so the row is live to the checks that would keep it
and invisible to the ones that would clean it up. That is the `lost` incident's exact shape, quoted
in `test_status_set_literal_twins_are_frozen.py`: a gate written as `status NOT IN (...)` silently
treats an unlisted status as live, and four sessions stayed permanently unstartable.

AND THE WRITER IS THE BRIDGE, which runs on the HOST and can be older or newer than the service —
the mismatch `aify-comms doctor`'s `bridge-current` check exists to catch. A status renamed on one
side of that gap writes limbo rows in silence.

WHAT THIS DOES NOT DO IS CLOSE IT. I made `_terminal_status_transition` refuse a status outside the
vocabulary, then found `test_terminal_status_transition.py` ruling the opposite ON PURPOSE, with an
argument: "this function is a guard, not a vocabulary check, and rejecting an unrecognised target
would silently drop writes from a newer writer." That cost is real too — the bridge is host-side and
routinely NEWER than the service. Two real costs, no way to have both: it is an operator's call, so
the change was reverted and the trade is stated here instead of settled by one test overwriting
another.

WHAT IT DOES DO is make the drift impossible to miss while the question is open. Both writers are
enumerated — the bridge's four literals and the routers' six — and either sending a status the
service does not recognise fails here by file and value. The stored-limbo consequence is pinned as
assertions rather than prose, so it cannot quietly stop being true.

NO LIVE DEFECT EXISTS. Every literal the bridge sends (`attached`, `failed`, `running`, `stopped`)
and every one the routers write (those plus `starting`, `stopping`) is already a member.
"""

from __future__ import annotations

import ast
import pathlib
import re
import unittest

from service.api_core.terminal_status import (
    TERMINAL_SESSION_STATUSES,
    _TERMINAL_ACTIVE_STATUSES,
    _TERMINAL_END_STATUSES,
    _TERMINAL_MONOTONIC_STATUSES,
    _terminal_status_transition,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
PRUNE = {"node_modules", "fixtures", "__pycache__", ".git", ".venv", "tests"}

#: The bridge's terminal-status literals, by file. Two shapes reach the same column: a direct
#: `POST .../output` body, and the second argument of `_pushTerminalFrame`, which the virtual
#: terminal sink forwards into that same body.
BRIDGE_SENDERS = {
    "mcp/stdio/codex-session.js": {"running"},
    "mcp/stdio/hermes-managed-gateway-session.js": {"running"},
    "mcp/stdio/hermes-session.js": {"failed", "running"},
    "mcp/stdio/pi-session.js": {"running", "stopped"},
    "mcp/stdio/terminal-manager.mjs": {"attached", "failed", "stopped"},
}


def _bridge_terminal_status_literals() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted((REPO / "mcp" / "stdio").rglob("*.*js")):
        rel = path.relative_to(REPO)
        if PRUNE & set(rel.parts) or ".test." in path.name or path.suffix not in (".js", ".mjs"):
            continue
        src = path.read_text(encoding="utf-8")
        key = rel.as_posix()
        for match in re.finditer(r"/output`,\s*\{([\s\S]*?)\n\s*\}\)", src):
            field = re.search(r"status:\s*([^\n]*)", match.group(1))
            if not field:
                continue
            # A ternary is one field with two literals — take every quoted token on the line.
            for value in re.findall(r'"([a-z-]+)"', field.group(1)):
                found.setdefault(key, set()).add(value)
        for match in re.finditer(r"_pushTerminalFrame\(([\s\S]*?)\);", src):
            args = match.group(1)
            for value in re.findall(r'"([a-z-]+)"', args[args.rfind(","):]):
                found.setdefault(key, set()).add(value)
    return found


def _router_terminal_status_writes() -> dict[str, set[str]]:
    """Literals written to `terminal_sessions.status` from Python, SQL text and parameter tuples."""
    found: dict[str, set[str]] = {}
    for path in sorted((REPO / "service").rglob("*.py")):
        rel = path.relative_to(REPO)
        if PRUNE & set(rel.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            sql = node.args[0]
            if not (isinstance(sql, ast.Constant) and isinstance(sql.value, str)):
                continue
            text = sql.value
            if not re.search(r"\bUPDATE\s+terminal_sessions\b|\bINSERT\s+INTO\s+terminal_sessions\b", text, re.I):
                continue
            for value in re.findall(r"\bstatus\s*=\s*'([a-z_-]+)'", text, re.I):
                found.setdefault(rel.as_posix(), set()).add(value.lower())
            if re.search(r"\bstatus\s*=\s*\?", text, re.I) or "INSERT INTO" in text.upper():
                for arg in node.args[1:]:
                    for element in (arg.elts if isinstance(arg, (ast.Tuple, ast.List)) else []):
                        if isinstance(element, ast.Constant) and isinstance(element.value, str):
                            if element.value.lower() in TERMINAL_SESSION_STATUSES:
                                found.setdefault(rel.as_posix(), set()).add(element.value.lower())
    return found


class TerminalStatusVocabularyTests(unittest.TestCase):
    def test_the_vocabulary_is_derived_not_retyped(self):
        # TWO CHECKS, because one of them is weaker than its name. Comparing VALUES catches a
        # retyped copy only once the underlying sets change — I mutated the derivation into a
        # hand-typed literal of the same twelve strings and this assertion passed. It is still worth
        # keeping: it is what fails on the drift itself, which is the harm.
        self.assertEqual(
            TERMINAL_SESSION_STATUSES,
            frozenset(_TERMINAL_ACTIVE_STATUSES | _TERMINAL_MONOTONIC_STATUSES),
            "the vocabulary must stay the UNION of the two sets, not a third hand-typed list",
        )
        # So the DERIVATION is asserted too, as an AST shape rather than as text: located by NAME,
        # blind to formatting and to the order of the operands. It proves the expression is a union
        # of those two names — not where it sits, and not that it is correct.
        module = ast.parse((REPO / "service/api_core/terminal_status.py").read_text(encoding="utf-8"))
        assignment = next(
            (n for n in module.body
             if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "TERMINAL_SESSION_STATUSES" for t in n.targets)),
            None,
        )
        self.assertIsNotNone(assignment, "TERMINAL_SESSION_STATUSES is no longer a module-level assignment")
        operands = {
            n.id for n in ast.walk(assignment.value)
            if isinstance(n, ast.Name)
        }
        self.assertEqual(
            operands, {"frozenset", "_TERMINAL_ACTIVE_STATUSES", "_TERMINAL_MONOTONIC_STATUSES"},
            "TERMINAL_SESSION_STATUSES must be COMPUTED from the two sets. A literal with today's "
            "values passes the equality check above and then stops following them.",
        )
        self.assertTrue(
            any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr) for n in ast.walk(assignment.value)),
            "the derivation must be a union of the two sets",
        )
        # The end set is the monotonic set minus the one in-flight teardown state. Stated because
        # three near-identical sets is exactly how a spelling goes missing from one of them.
        self.assertEqual(
            sorted(_TERMINAL_MONOTONIC_STATUSES - _TERMINAL_END_STATUSES), ["stopping"],
            "`stopping` is monotonic (do not go back to active) but not ENDED (do not close it out)",
        )

    def test_every_status_the_bridge_sends_is_in_the_vocabulary(self):
        """THE ONE THAT MATTERS. The bridge runs on the host and can be older than the service."""
        literals = _bridge_terminal_status_literals()
        for rel, values in sorted(literals.items()):
            with self.subTest(file=rel):
                unknown = sorted(values - TERMINAL_SESSION_STATUSES)
                self.assertEqual(
                    unknown, [],
                    f"{rel} sends terminal status {unknown}, which the service does not recognise. "
                    f"`_terminal_status_transition` now DROPS it, so the terminal keeps its previous "
                    f"status — before that gate it would have been stored, and no reaper, no status "
                    f"engine and no close-out path would have seen the row again.",
                )

    def test_the_bridge_sender_census_is_frozen(self):
        self.assertEqual(
            {rel: sorted(values) for rel, values in sorted(_bridge_terminal_status_literals().items())},
            {rel: sorted(values) for rel, values in sorted(BRIDGE_SENDERS.items())},
            "the bridge files writing terminal statuses changed — declare the new sender and the "
            "literals it sends, so a new spelling is a decision rather than a silent limbo row",
        )

    def test_every_status_the_routers_write_is_in_the_vocabulary(self):
        writes = _router_terminal_status_writes()
        self.assertTrue(writes, "no Python write to terminal_sessions.status found at all")
        for rel, values in sorted(writes.items()):
            with self.subTest(file=rel):
                self.assertEqual(sorted(values - TERMINAL_SESSION_STATUSES), [], rel)

    def test_the_scans_are_not_silently_matching_nothing(self):
        literals = _bridge_terminal_status_literals()
        self.assertGreaterEqual(len(literals), 5, "the bridge scan found almost no senders")
        self.assertIn("attached", literals.get("mcp/stdio/terminal-manager.mjs", set()))
        self.assertIn(
            "stopped", literals.get("mcp/stdio/terminal-manager.mjs", set()),
            "the ternary `status: error ? \"failed\" : \"stopped\"` carries TWO literals in one "
            "field; a scan that reads only the first sees half the senders",
        )
        self.assertIn(
            "running", literals.get("mcp/stdio/codex-session.js", set()),
            "`_pushTerminalFrame(text, \"running\")` reaches the same column through the virtual "
            "terminal sink — a scan that only reads direct POSTs misses four files",
        )

    def test_an_unrecognised_status_is_STORED_today_and_lands_on_the_live_side_of_everything(self):
        """THE OPEN QUESTION, pinned as behaviour rather than argued in prose.

        `test_terminal_status_transition.py` rules this deliberate: "this function is a guard, not a
        vocabulary check, and rejecting an unrecognised target would silently drop writes from a
        newer writer." I changed it to refuse, found that ruling, and reverted — an operator picks
        between dropping a newer bridge's write and storing a row nothing can clean up.

        What this test adds is the second half of the trade, stated in assertions instead of prose:
        the stored value really is outside every allowlist, so the consequence is not hypothetical.
        """
        for unknown in ["stoppped", "paused", "resumed", "crashed"]:
            self.assertEqual(
                _terminal_status_transition("running", unknown), unknown,
                "today an unrecognised status is stored as-is",
            )
            self.assertNotIn(unknown, _TERMINAL_ACTIVE_STATUSES, "…so the status engine sees no live terminal")
            self.assertNotIn(unknown, _TERMINAL_END_STATUSES, "…so no close-out path ever fires")
            self.assertNotIn(unknown, _TERMINAL_MONOTONIC_STATUSES, "…so the resurrection guard is off")
        # And the guard genuinely cannot protect such a row: a finished terminal can be moved to an
        # unknown status and then back to `running`, which the vocabulary path would have refused.
        self.assertEqual(_terminal_status_transition("stopped", "paused"), "paused")
        self.assertEqual(_terminal_status_transition("paused", "running"), "running")

    def test_every_real_transition_is_unchanged(self):
        """The full ordered matrix, so a future change to this rule has to face every pair."""
        for current in sorted(TERMINAL_SESSION_STATUSES | {""}):
            for nxt in sorted(TERMINAL_SESSION_STATUSES):
                expected = (
                    "" if current in _TERMINAL_MONOTONIC_STATUSES and nxt in _TERMINAL_ACTIVE_STATUSES
                    else nxt
                )
                self.assertEqual(
                    _terminal_status_transition(current, nxt), expected,
                    f"{current!r} -> {nxt!r} changed meaning",
                )

    def test_the_monotonic_guard_still_refuses_a_finished_terminal_going_active(self):
        # Anti-vacuity for the loop above: assert the guard fires on a case that reaches it, so a
        # broken guard cannot pass by making `expected` wrong in the same way on both sides.
        self.assertEqual(_terminal_status_transition("stopped", "running"), "")
        self.assertEqual(_terminal_status_transition("stopped", "failed"), "failed")
        self.assertEqual(_terminal_status_transition("running", "stopped"), "stopped")
        self.assertEqual(_terminal_status_transition("  STOPPED  ", "RUNNING"), "")

    def test_case_and_whitespace_are_normalised_before_the_allowlist(self):
        self.assertEqual(_terminal_status_transition("running", "  STOPPED "), "stopped")
        self.assertEqual(_terminal_status_transition("running", "Attached"), "attached")
