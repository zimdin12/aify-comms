r"""The ended-agent-session statuses are written once, and no SQL string spells them by hand.

`ENDED_AGENT_SESSION_STATUSES` in `service/api_core/agent_sessions.py` owns the six values that mean
an agent session is over. MEASURED 2026-08-29: NINE sites across five modules wrote those six out by
hand inside SQL -- three as `NOT IN` (the still-live filter) and six as `IN` (promote a dead-state
denorm when a live backing rebinds) -- while exactly ONE read the constant.

THIS IS NOT A COINCIDENCE OF VOCABULARY, which is the distinction the sibling gate insists on. Every
one of the nine filters `agent_sessions.status`, and every one means "this session has ended". Three
of them sit in the module that declares the constant.

WHY NO EXISTING GATE SAW IT. `test_inline_literal_set_duplication_is_frozen.py` reads Python literals
through `ast`, and `test_no_unruled_constant_coincidences.py` reads `ast.Assign`. A vocabulary
embedded in a SQL string is invisible to both -- it is not a set, a tuple, or an assignment. That is
the hole this closes, and the reason the ceiling here is ZERO rather than a frozen population: unlike
the five sets that gate holds, this one has an owner and no ruling is needed to say so.

WHY A LITERAL FRAGMENT RATHER THAN PLACEHOLDERS. The module already exposes a `?`-placeholder pair,
and it is the right tool where a parameter tuple is being assembled anyway. Six of these nine sites
are `CASE WHEN status IN (...)` buried mid-UPDATE, where threading six more parameters into an
existing tuple is how an argument lands in the wrong slot. The fragment renders the members once,
from the same constant, and a test below pins that every member is a bare lowercase word -- so the
interpolation cannot become an injection route even if somebody later adds a member from elsewhere.
"""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

from service.api_core.agent_sessions import (
    ENDED_AGENT_SESSION_STATUS_SQL,
    ENDED_AGENT_SESSION_STATUSES,
)

REPO = Path(__file__).resolve().parents[2]
SKIP_DIRS = {"node_modules", "tests", "fixtures", "__pycache__", ".git", ".pytest_cache",
             ".venv", "venv", "data"}
IN_CLAUSE = re.compile(r"IN\s*\(([^)]*)\)", re.IGNORECASE)


def hand_typed_spellings(root: Path, members: set[str]) -> list[str]:
    """Every `IN (...)` in product source whose members are exactly `members`, as `path:line`."""
    found = []
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = [d for d in subdirs if d not in SKIP_DIRS]
        for filename in files:
            if not filename.endswith((".py", ".js", ".mjs")) or ".test." in filename:
                continue
            path = Path(directory) / filename
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:  # pragma: no cover - unreadable file
                continue
            for match in IN_CLAUSE.finditer(text):
                spelled = {piece.strip().strip("'\"") for piece in match.group(1).split(",")}
                if spelled == members:
                    line = text[:match.start()].count("\n") + 1
                    found.append(f"{path.relative_to(root).as_posix()}:{line}")
    return sorted(found)


class TheEndedSessionSetHasOneOwner(unittest.TestCase):
    def test_NO_SQL_STRING_SPELLS_THE_SET_BY_HAND(self):
        spellings = hand_typed_spellings(REPO, set(ENDED_AGENT_SESSION_STATUSES))
        self.assertEqual(spellings, [], (
            "these SQL strings write out the ended-session statuses instead of interpolating "
            f"ENDED_AGENT_SESSION_STATUS_SQL:\n  " + "\n  ".join(spellings)
            + "\nA copy stays correct until the set changes, and then it is a filter that silently "
              "disagrees with every other one."
        ))

    def test_THE_SCAN_CAN_SAY_NO(self):
        """NEGATIVE CONTROL. The assertion above is a list-is-empty check, which an inert scanner
        passes perfectly. This drives the same function against a set it MUST find -- and against a
        near-miss it must not, since a scan that matched five-of-six would report a filter that is
        deliberately different as a duplicate."""
        live = {"active", "attached", "idle", "running", "starting"}
        self.assertNotEqual(hand_typed_spellings(REPO, live), [], (
            "the scanner found no SQL spelling of the live-terminal set, which this repo definitely "
            "contains -- so its empty verdict above proves nothing"
        ))
        impossible = {"nonesuch-alpha", "nonesuch-beta"}
        self.assertEqual(hand_typed_spellings(REPO, impossible), [])

    def test_the_fragment_is_DERIVED_from_the_constant(self):
        """A fragment typed out beside the constant would be the tenth copy, in the file whose whole
        job is to prevent one."""
        for status in ENDED_AGENT_SESSION_STATUSES:
            self.assertIn(f"'{status}'", ENDED_AGENT_SESSION_STATUS_SQL)
        self.assertEqual(
            ENDED_AGENT_SESSION_STATUS_SQL.count(","), len(ENDED_AGENT_SESSION_STATUSES) - 1,
            "the fragment carries a different number of members than the set it is built from",
        )
        self.assertTrue(ENDED_AGENT_SESSION_STATUS_SQL.startswith("("))
        self.assertTrue(ENDED_AGENT_SESSION_STATUS_SQL.endswith(")"))

    def test_every_member_is_a_bare_word_so_interpolation_stays_safe(self):
        """The fragment is interpolated into SQL rather than parameterised. That is safe because the
        values are this module's own frozen vocabulary -- and it stays safe only while they are plain
        identifiers. A member arriving with a quote in it would end the string early."""
        for status in ENDED_AGENT_SESSION_STATUSES:
            self.assertRegex(status, r"^[a-z][a-z_]*$", (
                f"{status!r} is not a bare lowercase word; the fragment interpolates members "
                "directly, so a member carrying a quote or a semicolon would change the statement"
            ))

    def test_the_rendered_fragment_is_valid_sql_against_a_real_database(self):
        """Executed, not eyeballed. A fragment with a stray comma or an unbalanced quote compiles as
        a Python string perfectly well and only fails when SQLite is asked to parse it."""
        import sqlite3

        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE TABLE probe (status TEXT)")
            connection.executemany("INSERT INTO probe (status) VALUES (?)",
                                   [(s,) for s in sorted(ENDED_AGENT_SESSION_STATUSES)] + [("running",)])
            matched = connection.execute(
                f"SELECT COUNT(*) FROM probe WHERE status IN {ENDED_AGENT_SESSION_STATUS_SQL}"
            ).fetchone()[0]
            self.assertEqual(matched, len(ENDED_AGENT_SESSION_STATUSES))
            survivors = connection.execute(
                f"SELECT status FROM probe WHERE status NOT IN {ENDED_AGENT_SESSION_STATUS_SQL}"
            ).fetchall()
            self.assertEqual([row[0] for row in survivors], ["running"])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
