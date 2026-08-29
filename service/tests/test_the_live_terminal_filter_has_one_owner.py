r"""Sixteen hand-typed live-terminal filters became two named fragments. Nothing they match changed.

WHAT WAS FIXED. Sixteen `WHERE status IN (...)` filters wrote a live-terminal status set out by hand,
in five spellings -- including one whose members had wrapped onto the next source line and one with a
different member order. They now interpolate a fragment rendered from one set.

THEY WERE NOT ALL THE SAME SET, and that is the reason there are TWO fragments rather than one.
Twelve carried the active set plus `recovering`; four carried the active set exactly. Merging them
would have changed what four filters consider a live terminal.

I NEARLY MADE THAT MERGE, and the way it was stopped is the part worth keeping. `recovering` is not
in `TERMINAL_SESSION_STATUSES`, no writer produces it, and the column has held three values across
the 103 rows in its whole history (`stopped` 97, `failed` 3, `attached` 3) -- so it reads exactly like
a status that leaked in from `agent_sessions`, and I removed it. Three things said otherwise, in
increasing order of authority: `test_agent_status_inputs.py` asserts "live status 'recovering' must
be in the terminal filter"; `test_terminal_sql_compares_terminal_statuses.py` carries it in a
`FOREIGN_LITERALS` ledger with the reason it is tolerated; and that ledger cites an OPERATOR RULING,
`44299eb6` -- "`recovering` is live but not active, on purpose -- do not unify them".

So the member stays, this file pins that it stays, and the evidence that it is inert is recorded
beside it rather than acted on. The lesson is not about `recovering`: a set-membership argument, however
one-sided the data looks, is not a ruling, and the ruling already existed two directories away.
"""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

from service.api_core.terminal_status import (
    TERMINAL_ACTIVE_STATUS_SQL,
    TERMINAL_LIVE_FILTER_SQL,
    TERMINAL_LIVE_FILTER_STATUSES,
    TERMINAL_SESSION_STATUSES,
    TERMINAL_STOPPABLE_STATUS_SQL,
    _TERMINAL_ACTIVE_STATUSES,
)

REPO = Path(__file__).resolve().parents[2]
SKIP_DIRS = {"node_modules", "tests", "fixtures", "__pycache__", ".git", ".pytest_cache",
             ".venv", "venv", "data", "new_dashboard"}

#: A filter on a status column. The alias forms are what the joined queries use, and leaving them out
#: is how a scan of this shape reports a clean sweep of half the population.
STATUS_FILTER = re.compile(
    r"\b(?:t\.|s\.|terminal\.)?status\s+(?:NOT\s+)?IN\s*\(\s*((?:'[^']*')(?:\s*,\s*'[^']*')*)\s*\)",
    re.IGNORECASE,
)

#: WHICH FILTERS ARE TERMINAL FILTERS, decided by their MEMBERS rather than by a table named
#: somewhere else in the file. The first version of this scan keyed on the file mentioning
#: `terminal_sessions` and judged every `status IN (...)` in it against the terminal vocabulary --
#: which named FOURTEEN innocent lines, because those same files filter `dispatch_runs.status`
#: (`claimed`, `queued`), `terminal_controls.status` (`pending`), `environments.status` (`online`,
#: `degraded`) and `agent_sessions.mode` (`managed-warm`). A scan that accuses innocent lines is one
#: nobody reads, and it would have shipped if the first run had not been red.
#:
#: A filter carrying ALL FIVE terminal-active statuses is unambiguously about a terminal: no other
#: status column in this schema has `attached`, `active` and `idle` together.
TERMINAL_SIGNATURE = frozenset(_TERMINAL_ACTIVE_STATUSES)


def hand_typed_terminal_filters(root: Path) -> list[str]:
    """`path:line` for every literal filter still carrying the whole terminal-active set."""
    found = []
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = [d for d in subdirs if d not in SKIP_DIRS]
        for filename in files:
            if not filename.endswith(".py") or filename.startswith("test_"):
                continue
            path = Path(directory) / filename
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in STATUS_FILTER.finditer(text):
                members = {piece.strip().strip("'") for piece in match.group(1).split(",")}
                if TERMINAL_SIGNATURE <= members:
                    line = text[:match.start()].count("\n") + 1
                    found.append(f"{path.relative_to(root).as_posix()}:{line}")
    return sorted(found)


class TheLiveTerminalFilterHasOneOwner(unittest.TestCase):
    def test_NO_QUERY_SPELLS_THE_LIVE_TERMINAL_SET_BY_HAND(self):
        offenders = hand_typed_terminal_filters(REPO)
        self.assertEqual(offenders, [], (
            "these queries write a live-terminal set out instead of interpolating one of the "
            "fragments in terminal_status.py:\n  " + "\n  ".join(offenders)
            + "\nSixteen copies in five spellings is what this replaced; a seventeenth restarts it."
        ))

    def test_THE_SCAN_CAN_SAY_NO(self):
        """NEGATIVE CONTROL, on text written to fail: the exact shape the twelve had."""
        planted = ("WHERE t.status IN ('starting', 'attached', 'running', 'active', 'idle', "
                   "'recovering')")
        match = STATUS_FILTER.search(planted)
        self.assertIsNotNone(match, "the pattern no longer matches an aliased filter")
        members = {piece.strip().strip("'") for piece in match.group(1).split(",")}
        self.assertTrue(TERMINAL_SIGNATURE <= members, "the signature no longer recognises it")

    def test_the_scan_ignores_a_filter_on_a_DIFFERENT_status_column(self):
        """The other half of the same control, and the mistake that produced fourteen false
        accusations in the first draft of this file."""
        for innocent in ("WHERE status IN ('queued', 'claimed')",
                         "AND s.status IN ('starting', 'running', 'recovering', 'restarting')",
                         "WHERE status IN ('online', 'degraded')"):
            match = STATUS_FILTER.search(innocent)
            self.assertIsNotNone(match, innocent)
            members = {piece.strip().strip("'") for piece in match.group(1).split(",")}
            self.assertFalse(TERMINAL_SIGNATURE <= members,
                             f"{innocent} was taken for a terminal filter")

    # ---- the two sets stay two ------------------------------------------------------------------

    def test_THE_LIVE_FILTER_KEEPS_THE_RULED_MEMBER(self):
        """`44299eb6`: "recovering is live but not active, on purpose -- do not unify them". Twelve
        filters and `test_agent_status_inputs.py` depend on it being here."""
        self.assertIn("recovering", TERMINAL_LIVE_FILTER_STATUSES)
        self.assertIn("'recovering'", TERMINAL_LIVE_FILTER_SQL)
        self.assertIn("'recovering'", TERMINAL_STOPPABLE_STATUS_SQL)

    def test_THE_ACTIVE_FRAGMENT_STAYS_FREE_OF_IT(self):
        """The other side of the same ruling. The four filters that use the active set exactly must
        not acquire the member by being pointed at the wrong fragment -- that is the unification the
        ruling forbids, arrived at by accident."""
        self.assertNotIn("'recovering'", TERMINAL_ACTIVE_STATUS_SQL)
        self.assertEqual(
            sorted(re.findall(r"'([^']*)'", TERMINAL_ACTIVE_STATUS_SQL)),
            sorted(_TERMINAL_ACTIVE_STATUSES),
        )
        self.assertNotIn("recovering", TERMINAL_SESSION_STATUSES)

    def test_the_two_live_fragments_differ_by_exactly_stopping(self):
        """`agent_terminal_ops` wants "not yet gone" rather than "live". Derived, so the ruled member
        reaches both at once and they cannot drift the way the sixteen drifted."""
        live = set(re.findall(r"'([^']*)'", TERMINAL_LIVE_FILTER_SQL))
        stoppable = set(re.findall(r"'([^']*)'", TERMINAL_STOPPABLE_STATUS_SQL))
        self.assertEqual(stoppable - live, {"stopping"})
        self.assertEqual(live - stoppable, set())

    def test_the_rendered_fragment_is_valid_sql_against_a_real_table(self):
        """Executed, because a fragment with a stray comma is a valid Python string and only SQLite
        objects to it. It also shows what the ruling costs: the disputed member matches nothing,
        which is what `FOREIGN_LITERALS` calls inert."""
        import sqlite3

        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE TABLE terminal_sessions (status TEXT)")
            connection.executemany(
                "INSERT INTO terminal_sessions (status) VALUES (?)",
                [(s,) for s in sorted(TERMINAL_SESSION_STATUSES)],
            )
            live = connection.execute(
                f"SELECT status FROM terminal_sessions WHERE status IN {TERMINAL_LIVE_FILTER_SQL}"
            ).fetchall()
            self.assertEqual(sorted(row[0] for row in live), sorted(_TERMINAL_ACTIVE_STATUSES))
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
