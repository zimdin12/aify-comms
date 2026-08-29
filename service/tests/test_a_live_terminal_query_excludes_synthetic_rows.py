"""Every query asking "does agent ? have a LIVE terminal row" excludes the deprecated synthetic ones.

THE INCIDENT IS RECORDED IN `live_process_probes.py`: Plan 4 deprecated synthetic `vterm_` terminals,
pre-Plan-4 rows persist in operator DBs with `status='running'` and no cleanup path, and on 2026-05-26
they hid a dead worker so `sc-coder` and `sc-architect` kept reading `online`.

SIX SITES LEARNED THAT AND ONE DID NOT. Measured 2026-08-26 with an `ast` walk over 229 product
modules: nine queries ask that question, six carried `id NOT LIKE 'vterm_%'`, and
`managed_env.py`'s console-start read was the only one asking the same question without it. That is
the shape this repo keeps finding -- a hazard understood, written down, and repaired in one of its two
homes. A gate is what turns "we fixed the one we tripped over" into "there is not a seventh".

WHY A CENSUS AND NOT AN ANECDOTE. One site missing a clause proves nothing on its own; it may want the
synthetic rows, and two of them genuinely do. What makes it a defect is being the odd one out among
queries asking the SAME question, which is why this test defines that question narrowly and then
exempts, by name and with a reason, the sites that ask a different one.

FIRST RUN OF THIS SCAN REPORTED ZERO, and its own positive control said it could not see a file known
to contain a match. It was a hand-rolled window scan around `FROM terminal_sessions`, and it was
wrong. `ast` folds adjacent string literals and handles triple-quotes; the window did not. The control
is the only reason that zero was not published.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1]
LIVE_STATUSES = ("starting", "attached", "running", "active", "idle", "recovering")

#: THE CLAUSE, not the word. A first version of this gate looked for the substring `vterm_` anywhere
#: in the statement -- and the fix it was written to protect carries an SQL comment EXPLAINING the
#: clause, which contains that word. Deleting the clause left the comment, the substring was still
#: found, and the mutation survived: a gate that could not fail on its own defect.
CLAUSE = re.compile(r"NOT\s+LIKE\s+'vterm_%'", re.IGNORECASE)

#: Sites that ask a DIFFERENT question, each with the reason it does not need the exclusion.
#:
#: ADDING A NAME HERE IS A DECISION, not a repair. The whole finding is that one site omitted a clause
#: six siblings carry; appending to this list to make a red test green re-creates it.
EXEMPT = {
    # Looks a terminal up BY ITS OWN ID (`WHERE id = ? AND agent_id = ?`). Once the row is named, what
    # its id looks like cannot change the answer.
    "service/routers/agents/registration.py": "selects one terminal by id, so the id pattern is moot",
    # STOPS stale terminals rather than reading liveness from them. Excluding synthetic rows here
    # would leave exactly the deprecated rows the incident is about un-cleaned -- this is the one path
    # that collects them, which is what "no cleanup path" in the incident note means.
    "service/api_core/resident_takeover_writes.py": "stops stale rows; synthetic ones are the point",
}


def _sql_literals(tree: ast.AST):
    """Every string constant in a module, including adjacent-literal concatenations.

    `ast` rather than a regex, and that is not style. See this file's docstring: the window scan this
    replaces reported zero matches across the whole service.
    """
    # ONE OWNER for reading an f-string whole. This gate grew its own copy of that handling on
    # 2026-08-29 and so did `test_terminal_sql_compares_terminal_statuses`, the same afternoon, for
    # the same reason -- which is precisely the duplication these gates catch in product code.
    # `service/tests/sql_sources.py` owns it now and carries its own controls.
    #
    # The status FRAGMENTS are deliberately left unresolved here. This gate asks whether a query
    # carries `AND id NOT LIKE 'vterm_%'`, which is about the clause AROUND the status list, and
    # `_asks_the_question` below recognises such a query by the fragment NAME.
    from service.tests.sql_sources import literal_text

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            continue
        if isinstance(node, ast.Constant) and not isinstance(node.value, str):
            continue
        if isinstance(node, ast.BinOp) and not isinstance(node.op, ast.Add):
            continue
        rendered = literal_text(node)
        if rendered:
            yield node.lineno, rendered


def _without_sql_comments(sql: str) -> str:
    """The statement with `-- ...` comment text removed.

    A comment is prose, not a clause. This repo writes long explanations inside its SQL, and one of
    them explains the very clause this gate checks for -- so reading the comment as code lets a
    deleted clause keep passing.
    """
    return " ".join(
        line.split("--", 1)[0] for line in sql.replace("\r", "").split("\n")
    )


def _asks_the_question(sql: str) -> bool:
    """Does this statement ask whether ONE NAMED AGENT has a live terminal row?

    Narrow on purpose. `status NOT IN (...)` is the inverse question, a fleet-wide scan is a different
    one, and a query against another table that merely mentions these statuses is not this at all.
    """
    return (
        "FROM terminal_sessions" in sql
        and "status IN" in sql
        and "agent_id = ?" in sql
        and (
            any(f"'{status}'" in sql for status in LIVE_STATUSES)
            # THE STATUS LIST MOVED BEHIND A CONSTANT on 2026-08-29 and this scan went BLIND: it
            # matched on the literal member names, so once sixteen filters interpolated
            # `TERMINAL_LIVE_FILTER_SQL` / `TERMINAL_ACTIVE_STATUS_SQL` instead, it found ZERO
            # live-terminal queries and reported a clean sweep of nothing. Its own
            # `test_most_of_the_population_carries_the_clause` is what said so -- "only 0 of 0" --
            # which is exactly the job of a positive control.
            or "TERMINAL_LIVE_FILTER_SQL" in sql
            or "TERMINAL_ACTIVE_STATUS_SQL" in sql
            or "TERMINAL_STOPPABLE_STATUS_SQL" in sql
        )
    )


def _live_terminal_queries() -> list[tuple[str, int, bool, str]]:
    found: list[tuple[str, int, bool, str]] = []
    for path in sorted(SERVICE.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        rel = str(path.relative_to(SERVICE.parent)).replace("\\", "/")
        seen: set = set()
        for lineno, text in _sql_literals(tree):
            flat = " ".join(text.split())
            if not _asks_the_question(" ".join(_without_sql_comments(text).split())):
                continue
            key = (lineno, flat[:80])
            if key in seen:
                continue
            seen.add(key)
            code = " ".join(_without_sql_comments(text).split())
            found.append((rel, lineno, bool(CLAUSE.search(code)), flat[:160]))
    return found


class LiveTerminalQueriesExcludeSyntheticRowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.queries = _live_terminal_queries()

    def test_the_scan_reads_a_real_population(self):
        """Anti-vacuity. A parser that matched nothing would report a clean service."""
        self.assertGreaterEqual(
            len(self.queries), 6,
            f"only {len(self.queries)} live-terminal queries found; the scan is not reading the service",
        )
        self.assertGreater(
            len({rel for rel, *_ in self.queries}), 1,
            "every match came from one file, which is how the first version of this scan failed",
        )

    def test_the_scan_can_say_PRESENT_and_ABSENT(self):
        """Both controls, in the same run as the assertion they defend."""
        files = {rel for rel, *_ in self.queries}
        # PRESENT: the module that records the incident asks this question and must be seen.
        self.assertIn("service/api_core/live_process_probes.py", files)
        # ABSENT: the inverse question must NOT be collected. `terminal_controls.py` asks
        # `status NOT IN (...)`, which is the opposite of what this gate governs.
        self.assertNotIn("service/reconcilers/terminal_controls.py", files)

    def test_every_live_terminal_query_excludes_synthetic_rows(self):
        offenders = [
            f"{rel}:{line}\n      {sql}"
            for rel, line, excludes, sql in self.queries
            if not excludes and rel not in EXEMPT
        ]
        self.assertEqual(offenders, [], (
            "these ask whether an agent has a LIVE terminal row and would count a deprecated "
            "synthetic `vterm_` row as one:\n  " + "\n  ".join(offenders)
            + "\n\nSix sibling queries carry `AND id NOT LIKE 'vterm_%'`. Pre-Plan-4 synthetic rows "
            "persist with status='running' and hid a dead worker on 2026-05-26. Add the clause, or "
            "add the file to EXEMPT with the reason it asks a different question."
        ))

    def test_the_exemptions_still_ask_a_different_question(self):
        """An exemption that no longer matches is an unchecked name, not a decision."""
        files = {rel for rel, *_ in self.queries}
        stale = sorted(name for name in EXEMPT if name not in files)
        self.assertEqual(stale, [], (
            f"EXEMPT names files with no live-terminal query left: {stale}. Delete them, or the list "
            "rots into exemptions nobody granted."
        ))

    def test_the_exemption_list_stays_small(self):
        """Two is the size of the idea: one by-id lookup and one cleanup path. A growing list is the
        gate being negotiated away one file at a time."""
        self.assertLessEqual(len(EXEMPT), 3, (
            f"{len(EXEMPT)} files are exempt. Each was a decision; together they are a pattern."
        ))

    def test_most_of_the_population_carries_the_clause(self):
        """The gate rests on the clause being the NORM. If that ever stopped being true the finding
        would be inverted -- six sites wrong rather than one -- and this test should say so rather
        than keep enforcing a minority convention."""
        carrying = sum(1 for _, _, excludes, _ in self.queries if excludes)
        self.assertGreaterEqual(carrying, 5, (
            f"only {carrying} of {len(self.queries)} live-terminal queries exclude synthetic rows; "
            "the convention this gate enforces is no longer the majority one"
        ))


    def test_the_gate_reads_the_clause_and_not_the_prose(self):
        """The mutation that SURVIVED the first version of this file, pinned.

        The fix carries an SQL comment explaining the clause, and that comment contains the word
        `vterm_`. A substring check therefore passed with the clause deleted. Both halves are asserted
        here: a statement whose only mention is in a comment does NOT count, and one with the real
        clause does.
        """
        commented_only = (
            "SELECT created_at FROM terminal_sessions WHERE agent_id = ? "
            "AND status IN ('starting','running')\n-- vterm_ rows are deprecated, see the incident\n"
            "ORDER BY updated_at DESC LIMIT 1"
        )
        self.assertTrue(_asks_the_question(" ".join(_without_sql_comments(commented_only).split())))
        self.assertFalse(
            CLAUSE.search(" ".join(_without_sql_comments(commented_only).split())),
            "a clause mentioned only in prose was accepted as the clause",
        )
        real = commented_only.replace("ORDER BY", "AND id NOT LIKE 'vterm_%' ORDER BY")
        self.assertTrue(CLAUSE.search(" ".join(_without_sql_comments(real).split())))


if __name__ == "__main__":
    unittest.main()
