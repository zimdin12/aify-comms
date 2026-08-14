"""Four modules insert a terminal_sessions row. They must agree on what a row IS.

A terminal is created on four separate paths — a dispatch cold-start, an agent console start, and
the two branches of `start_session_console` (a virtual RPC console and a real PTY). Each writes its
own `INSERT INTO terminal_sessions`, and the four statements were written by copying one another.

WHY THIS IS PINNED RATHER THAN MERGED. The four differ in exactly the ways they should: a virtual RPC
terminal is born `running` because the RPC session already exists, while a real PTY is born
`starting` because a bridge still has to boot it; the command differs per path; the requester differs.
Collapsing them would mean threading those through as parameters, which is a behaviour-shaped change,
and v0.5.x is the refactor line. See `test_console_input_queueing_twins_agree.py`, which records the
same judgement for the console-input loops.

WHAT ACTUALLY FAILS WITHOUT THIS. Add a column to `terminal_sessions` and wire it into the path you
happened to be working in. Nothing raises. Terminals created by the other three paths simply carry
the default for that column forever, and the symptom surfaces much later and somewhere else — a
console that renders wrong, or a reaper that skips a row because the field it keys on was never
written. The column list is the shared contract, so the column list is what is asserted.

THE SITES ARE DISCOVERED, NOT LISTED. An earlier generation of tests in this repo named files inline
and went blind when code moved; this one scans the tree and fails if the scan finds fewer sites than
it did when it was written.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SERVICE = REPO / "service"

#: The four known writers, at the time this was written. Discovery below is what the checks USE; this
#: is the anti-vacuity floor, so a scan that silently stops finding sites fails instead of passing.
EXPECTED_SITE_COUNT = 4

TABLE = "INSERT INTO terminal_sessions"

SESSIONS = SERVICE / "routers" / "sessions.py"
CONSOLE_START = "start_session_console"

#: (line as it appears in the VIRTUAL RPC insert, line as it appears in the REAL PTY insert).
#: Compared stripped, so indentation between the two branches does not register as divergence.
SUBSTITUTIONS = [
    ("virtual_command,", "command,"),
    ('"running",', '"starting",'),
]


def _product_sources() -> list[Path]:
    return [
        p for p in SERVICE.rglob("*.py")
        if "tests" not in p.parts and p.name != "__init__.py"
    ]


def _insert_statements() -> list[tuple[Path, str]]:
    """Every string literal in the service that inserts a terminal_sessions row."""
    found = []
    for path in _product_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file is another test's failure
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and TABLE in node.value:
                found.append((path, node.value))
    return found


def _column_list(sql: str) -> list[str]:
    """The column names, in order, from an INSERT ... (cols) VALUES (...) statement."""
    match = re.search(r"terminal_sessions\s*\((.*?)\)\s*VALUES", sql, re.S)
    assert match, f"could not read a column list from: {sql[:80]!r}"
    return [c.strip() for c in match.group(1).replace("\n", " ").split(",") if c.strip()]


def _console_insert_bodies() -> tuple[list[str], list[str]]:
    """The two `db.execute(INSERT ...)` calls inside `start_session_console`, stripped.

    Returned in source order, which is the virtual RPC branch first and the real PTY second.
    """
    source = SESSIONS.read_text(encoding="utf-8").replace("\r\n", "\n")
    lines = source.split("\n")
    handler = next(
        n for n in ast.parse(source).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == CONSOLE_START
    )
    bodies = []
    for node in ast.walk(handler):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute" and node.args):
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and TABLE in first.value:
            bodies.append((node.lineno, [
                line.strip() for line in lines[node.lineno - 1:node.end_lineno] if line.strip()
            ]))
    return tuple(body for _, body in sorted(bodies))  # type: ignore[return-value]


class TerminalSessionInsertsAgreeTests(unittest.TestCase):
    def test_the_scan_still_finds_every_known_writer(self):
        """Anti-vacuity. Every check below is over whatever this finds; finding nothing must fail."""
        sites = _insert_statements()
        self.assertGreaterEqual(
            len(sites), EXPECTED_SITE_COUNT,
            f"expected at least {EXPECTED_SITE_COUNT} terminal_sessions inserts, found {len(sites)}: "
            f"{[str(p.relative_to(REPO)) for p, _ in sites]}. Either a writer was removed (update "
            f"EXPECTED_SITE_COUNT deliberately) or this scan has gone blind",
        )

    def test_every_writer_uses_the_SAME_columns_in_the_SAME_order(self):
        sites = _insert_statements()
        # Keyed by INDEX as well as path. Keying by path alone silently dropped one of the two
        # sessions.py inserts — the later one overwrote the earlier in the dict and was never
        # compared, which is the failure mode this whole file exists to catch.
        columns = {
            f"{path.relative_to(REPO)}#{i}": _column_list(sql) for i, (path, sql) in enumerate(sites)
        }
        reference = _column_list(sites[0][1])
        disagreeing = {name: cols for name, cols in columns.items() if cols != reference}
        self.assertEqual(
            {}, disagreeing,
            "a terminal_sessions insert writes a different column set than its siblings. Adding a "
            "column on one path only is silent: rows created by the other paths keep the default "
            "forever, and the symptom surfaces later and elsewhere.\n"
            f"  reference ({str(sites[0][0].relative_to(REPO))}): {reference}\n"
            + "\n".join(f"  {name}: {cols}" for name, cols in disagreeing.items()),
        )

    def test_every_writer_binds_one_placeholder_per_column(self):
        """A column added without its `?` raises; a `?` added without its column binds the wrong value."""
        for path, sql in _insert_statements():
            columns = _column_list(sql)
            placeholders = re.search(r"VALUES\s*\(([^)]*)\)", sql, re.S)
            self.assertIsNotNone(placeholders, f"{path.name}: no VALUES clause")
            self.assertEqual(
                len(columns), placeholders.group(1).count("?"),
                f"{path.relative_to(REPO)} binds a different number of values than it names columns",
            )

    def test_the_two_console_branches_are_the_same_length(self):
        """A line added to one branch and not the other is the cheapest way for these to drift."""
        virtual, real = _console_insert_bodies()
        self.assertEqual(
            len(virtual), len(real),
            f"the virtual RPC insert has {len(virtual)} lines and the real PTY insert has {len(real)}; "
            "one of them was edited and the other was not",
        )

    def test_the_two_console_branches_differ_ONLY_by_the_declared_substitutions(self):
        virtual, real = _console_insert_bodies()
        allowed = {(a, b) for a, b in SUBSTITUTIONS}
        undeclared = [
            (i, a, b) for i, (a, b) in enumerate(zip(virtual, real))
            if a != b and (a, b) not in allowed
        ]
        self.assertEqual(
            undeclared, [],
            "the two console-start inserts have diverged beyond their declared substitutions. A fix "
            "applied to one and not the other is silent — the un-updated branch keeps creating rows "
            "the rest of the system reads as complete:\n  "
            + "\n  ".join(f"line {i}:\n    virtual: {a}\n    real:    {b}" for i, a, b in undeclared),
        )

    def test_every_declared_substitution_is_STILL_USED(self):
        """A stale entry would silently widen what counts as agreement."""
        virtual, real = _console_insert_bodies()
        actual = {(a, b) for a, b in zip(virtual, real) if a != b}
        for pair in SUBSTITUTIONS:
            self.assertIn(
                pair, actual,
                f"declared substitution {pair} no longer occurs; delete it or fix the comparison",
            )

    def test_the_status_difference_is_the_REASON_they_are_not_merged(self):
        """Asserted so the distinction is defended rather than assumed.

        A virtual RPC console is born `running` because the RPC session it fronts already exists.
        A real PTY is born `starting` because a bridge still has to spawn the process, and a terminal
        that claimed `running` before its process existed would make the agent look alive to every
        status consumer. If these two ever agree, the merge becomes a decision worth making — and
        this test should be deleted along with one of the copies.
        """
        virtual, real = _console_insert_bodies()
        self.assertIn('"running",', virtual)
        self.assertIn('"starting",', real)
        self.assertNotEqual(virtual, real, "the two inserts are now identical — merge them and delete this")


if __name__ == "__main__":
    unittest.main()
