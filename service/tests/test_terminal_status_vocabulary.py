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

CLOSED 2026-08-16, by tracing rather than by preference. `test_terminal_status_transition.py` ruled
pass-through deliberate: "rejecting an unrecognised target would silently drop writes from a newer
writer." The bridge IS host-side and routinely a different build, so the concern was real — but it
assumes the dropped write carries information this service could use, and it cannot: a service that
does not recognise a status has no code that acts on it.

WHAT DECIDED IT was reading the reapers. Every one selects `WHERE status IN (...)` —
`managed_workers`, `terminals` (both its active and its end query), `terminal_consistency` — and not
one keys on age. So the row an unknown status strands is invisible to all of them, while
`agent_terminal_ops.py` still reads it as "still running". Refusing loses a string nothing could have
used; keeping it loses the row. Both concrete futures agree: a bridge that INVENTS a status, and one
that RENAMES `stopped` to `exited`, are each better served by keeping the last known status.

THE ENUMERATION IS WHY THE CHANGE IS SAFE, and it stays. Both writers are counted here — the bridge's
four literals and the routers' six — so if either ever needs a new status, this fails by file and
value BEFORE the refusal could drop it silently.

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
    # `attached` only since 2026-08-26: the exit body moved to `terminal-exit-report.js` when the
    # exit code and signal were added to it, so the two end statuses now live one file over. The
    # scan follows the spread rather than being told, which is why that file appears below on its
    # own rather than as an exception here.
    "mcp/stdio/terminal-manager.mjs": {"attached"},
    "mcp/stdio/terminal-exit-report.js": {"failed", "stopped"},
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
        # A THIRD SHAPE: the body is SPREAD from a helper. `terminal-manager.mjs` posts
        # `{bridgeId, ...exitReport(detail)}`, and the two exit statuses live in the helper's module
        # rather than at the call site -- so the two scans above see `attached` there and nothing
        # else, and would have reported the exit vocabulary as GONE rather than moved.
        #
        # This is the same blind spot `realtime-dispositions.test.mjs` had on 2026-08-26: a scan that
        # reads one shape of producer reports honestly about that shape and silently about the rest.
        # Following the spread keeps the census DERIVED. Attribution goes to the module the literal
        # lives in, because "which file can introduce a status" is what this census is for.
        for match in re.finditer(r"/output`,\s*\{([\s\S]*?)\n\s*\}\)", src):
            for helper in re.findall(r"\.\.\.\s*([A-Za-z_$][\w$]*)\s*\(", match.group(1)):
                imported = re.search(
                    rf'import\s*\{{[^}}]*\b{re.escape(helper)}\b[^}}]*\}}\s*from\s*"\./([^"]+)"', src
                )
                if not imported:
                    continue
                helper_path = path.parent / imported.group(1)
                if not helper_path.exists():
                    continue
                helper_key = helper_path.resolve().relative_to(REPO.resolve()).as_posix()
                helper_src = helper_path.read_text(encoding="utf-8")
                for field in re.findall(r"^\s*status:\s*([^\n]*)", helper_src, re.M):
                    for value in re.findall(r'"([a-z-]+)"', field):
                        found.setdefault(helper_key, set()).add(value)
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
            # AN F-STRING IS A WRITER TOO, and reading only `ast.Constant` was silent about them.
            # `terminal_runs.py:356` writes `status = 'stopped'` inside an f-string -- it has to be
            # one, because the WHERE clause interpolates a placeholder list -- so the scan below could
            # not see it, and the guarantee this file claims ("if either writer ever needs a new
            # status, this fails by file and value BEFORE the refusal could drop it silently") did
            # not hold for that shape. No live defect: `stopped` is already a member. But the shape is
            # in use, so a NEW literal added the same way would have passed unnoticed and stranded the
            # row exactly as the `lost` incident in this file's docstring describes.
            #
            # Only the CONSTANT parts are joined. An interpolated value is not a literal and cannot be
            # judged here; `terminal_output.py` builds its whole SET clause that way and correctly
            # contributes nothing.
            sql = node.args[0]
            if isinstance(sql, ast.Constant) and isinstance(sql.value, str):
                text = sql.value
            elif isinstance(sql, ast.JoinedStr):
                text = "".join(
                    part.value for part in sql.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                )
            else:
                continue
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
        # The ternary MOVED on 2026-08-26 and this control moved with it. It is the same property --
        # a two-literal ternary must be read whole -- checked where the ternary now lives. Leaving it
        # pointed at the old file would have made it a control over nothing, passing on a scan that
        # had stopped reading ternaries entirely.
        exit_report = literals.get("mcp/stdio/terminal-exit-report.js", set())
        self.assertIn(
            "stopped", exit_report,
            "the ternary `status: error ? \"failed\" : \"stopped\"` carries TWO literals in one "
            "field; a scan that reads only the first sees half the senders",
        )
        self.assertIn(
            "failed", exit_report,
            "the exit body is SPREAD into the POST from a helper module. A scan that reads only the "
            "call site sees `attached` and concludes the exit statuses are gone rather than moved",
        )
        self.assertIn(
            "running", literals.get("mcp/stdio/codex-session.js", set()),
            "`_pushTerminalFrame(text, \"running\")` reaches the same column through the virtual "
            "terminal sink — a scan that only reads direct POSTs misses four files",
        )
        # THE PYTHON SIDE HAS THE SAME KIND OF SECOND SHAPE, and it was unread until 2026-08-26.
        # `terminal_runs.py` writes `status = 'stopped'` inside an F-STRING -- it has to be one,
        # because the WHERE clause interpolates a placeholder list -- and a scan that accepts only
        # `ast.Constant` SQL cannot see it. That is the third instance in one day of one class: a
        # scan reads one shape of producer and is silent about the rest, which reads exactly like
        # having checked.
        writes = _router_terminal_status_writes()
        self.assertGreaterEqual(len(writes), 15, "the router scan found almost no writers")
        self.assertIn(
            "stopped", writes.get("service/reconcilers/terminal_runs.py", set()),
            "an f-string UPDATE is invisible to this scan again. A NEW literal added that way would "
            "pass unnoticed and strand the row exactly as the `lost` incident above describes",
        )

    def test_an_unrecognised_status_is_refused_and_the_reason_is_what_it_would_have_cost(self):
        """RESOLVED 2026-08-16 — the open question this file used to pin is closed.

        It pinned the OPPOSITE, because `test_terminal_status_transition.py` ruled pass-through
        deliberate. Tracing the reapers decided it: every one selects `WHERE status IN (...)` and not
        one keys on age, so a row holding an undeclared status matches none of them. The assertions
        below are what makes that concrete rather than a claim — the value really is outside all
        three sets, so "invisible to every sweep" is a fact about this code, not a worry.
        """
        for unknown in ["stoppped", "paused", "resumed", "crashed", "exited"]:
            with self.subTest(unknown=unknown):
                self.assertEqual(
                    _terminal_status_transition("running", unknown), "",
                    "an undeclared status must not reach the column",
                )
                self.assertNotIn(unknown, _TERMINAL_ACTIVE_STATUSES, "…the status engine sees no live terminal")
                self.assertNotIn(unknown, _TERMINAL_END_STATUSES, "…no close-out path ever fires")
                self.assertNotIn(unknown, _TERMINAL_MONOTONIC_STATUSES, "…the resurrection guard is off")
        # The rescue this buys: refusing leaves the LAST KNOWN status in place, and that status is
        # one the reapers still act on. Keeping the unknown one is what stranded the row.
        self.assertEqual(_terminal_status_transition("stopped", "paused"), "")
        self.assertEqual(_terminal_status_transition("running", "stopped"), "stopped")

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
