"""A query that forgets `turn_started_at` silently un-fixes the in_turn ceiling.

THE SHAPE, and it is the reason this file exists rather than a comment. `_turn_anchor` falls back to
`last_event_at` when the anchor is absent — a fallback written for rows created before the column
existed, which the boot backfill repairs. But a SELECT that simply does not ASK for the column
produces a row with the anchor missing, hits that same fallback, and silently restores the exact
behaviour the anchor was added to end. Nothing raises. Every targeted test passes.

That is not hypothetical: `status_signal_prefetch.py` had TWO such queries, and they feed the clamp
on the SERVED path. The fix was inert there while its own test file was green, and it took a review
to find. The fallback cannot distinguish "old row" from "query forgot to ask", so the guard has to
live out here where the queries are.

THE RULE, derived rather than listed: any read of `agent_status_state` that selects `last_event_at`
is feeding a turn-liveness decision, and must also select `turn_started_at`. A read that wants
neither — `turn_boundaries.py` asks only for `in_turn` — is not making that decision and is not
required to.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1]

#: Files that legitimately do not participate: the migration that CREATES the column, and the
#: schema that declares it. Derived from what they are, not from a list of names to maintain.
NOT_A_READER = ("db.py", "schema.py")


def _statements(text: str) -> list[str]:
    """Every SELECT ... FROM agent_status_state in a file, as whole statements.

    Whole statements, because these are built from adjacent string literals across several lines --
    a line-based scan reads `"FROM agent_status_state WHERE agent_id=?"` on its own and concludes
    the query selects nothing at all. That is how a scanner reports a clean zero while the thing it
    is looking for sits one line above.
    """
    flat = re.sub(r'"\s*\n\s*"', "", text)          # join adjacent literals
    flat = re.sub(r"'\s*\n\s*'", "", flat)
    return re.findall(r"SELECT\s+(.*?)\s+FROM\s+agent_status_state", flat, re.S | re.I)


class EveryStatusStateReadAsksForTheAnchorTests(unittest.TestCase):
    def _readers(self):
        found = []
        for path in sorted(SERVICE.rglob("*.py")):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            if path.name in NOT_A_READER:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for columns in _statements(text):
                found.append((str(path.relative_to(SERVICE)).replace("\\", "/"), columns))
        return found

    def test_the_scanner_finds_the_reads_that_exist(self):
        """ANTI-VACUITY, and it is the whole risk here: a scanner that matches nothing passes every
        assertion below and reports the codebase clean. The two known readers are named, so a regex
        that stops working fails instead of going quiet."""
        files = {path for path, _ in self._readers()}
        self.assertIn("api_core/status_inputs.py", files)
        self.assertIn("api_core/status_signal_prefetch.py", files)
        self.assertGreaterEqual(len(self._readers()), 4, self._readers())

    def test_the_scanner_reads_a_multi_line_query_as_one_statement(self):
        """The failure mode that hid the defect. Concatenated literals are ONE query, and a scan
        that treats each line separately sees a FROM with no column list."""
        split_across_lines = (
            'x = await db.execute(\n'
            '    "SELECT in_turn, awaiting_input, last_event_at, turn_started_at "\n'
            '    "FROM agent_status_state WHERE agent_id=?", (a,))\n'
        )
        found = _statements(split_across_lines)
        self.assertEqual(len(found), 1, found)
        self.assertIn("turn_started_at", found[0])
        # NEGATIVE CONTROL: the same scanner reports the anchor ABSENT when it is absent, so a pass
        # above is a real presence rather than a matcher that always finds it.
        missing = _statements(
            'x = await db.execute(\n'
            '    "SELECT in_turn, awaiting_input, last_event_at "\n'
            '    "FROM agent_status_state WHERE agent_id=?", (a,))\n'
        )
        self.assertEqual(len(missing), 1, missing)
        self.assertNotIn("turn_started_at", missing[0])

    def test_any_read_that_takes_last_event_at_also_takes_the_anchor(self):
        """THE RULE. `last_event_at` is the column the clamp used to age against, so a query asking
        for it is feeding a turn-liveness decision — and one that asks for it WITHOUT the anchor
        gets the pre-fix behaviour through a fallback meant for something else."""
        for path, columns in self._readers():
            with self.subTest(path=path, columns=columns.strip()):
                if "last_event_at" not in columns:
                    continue
                self.assertIn(
                    "turn_started_at", columns,
                    f"{path} selects last_event_at without turn_started_at. `_turn_anchor` will fall "
                    f"back to the moving column and the in_turn ceiling becomes unreachable on this "
                    f"path — silently, because the fallback exists for old ROWS and cannot tell them "
                    f"from a query that did not ask.",
                )
