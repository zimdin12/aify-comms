r"""The shared SQL reader sees an f-string, and the gates that scan queries use it.

WHAT THIS IS FOR. Three gates in this suite read the SQL a module issues. Two of them were keyed on
`ast.Constant` and went blind on 2026-08-29, the moment sixteen filters moved their status list
behind a constant: one reported "0 of 0 live-terminal queries" and the other reported that a literal
it tracks was no longer used anywhere, which was false. Both were fixed with their own copy of the
same f-string handling, on the same afternoon -- which is the duplication this repo spends its rounds
removing from product code, arriving in the tests instead.

So the handling has one owner, `service/tests/sql_sources.py`, and this file proves it does the job
that the two ad-hoc copies were written for. The controls are the point: a reader that silently
returned "" for every f-string would satisfy any "no offenders" assertion downstream.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.sql_sources import (
    literal_text,
    looks_like_sql,
    sql_literals,
    status_fragment_resolutions,
)

REPO = Path(__file__).resolve().parents[2]
SERVICE = REPO / "service"


def _first(source: str) -> ast.AST:
    return ast.parse(source).body[0].value


class TheSqlReaderSeesFStrings(unittest.TestCase):
    def test_an_f_string_is_read_whole_rather_than_in_pieces(self):
        node = _first('f"SELECT id FROM t WHERE status IN {FRAGMENT} AND id NOT LIKE \'v_%\'"')
        text = literal_text(node)
        self.assertIn("SELECT id FROM t", text)
        self.assertIn("{FRAGMENT}", text, "the interpolation vanished; the statement reads as two")
        self.assertIn("id NOT LIKE 'v_%'", text, (
            "the tail after the interpolation was dropped -- exactly what an ast.Constant scan does, "
            "and why a gate looking for that clause found none"
        ))

    def test_a_named_fragment_can_be_resolved_to_its_members(self):
        node = _first('f"SELECT 1 FROM t WHERE status IN {LIVE_SQL}"')
        text = literal_text(node, {"LIVE_SQL": "('a', 'b')"})
        self.assertIn("status IN ('a', 'b')", text)

    def test_a_plain_literal_and_a_concatenation_still_read(self):
        self.assertEqual(literal_text(_first('"SELECT 1"')), "SELECT 1")
        self.assertEqual(
            literal_text(_first('"SELECT 1 " + "FROM t " + "WHERE id = ?"')),
            "SELECT 1 FROM t WHERE id = ?",
        )

    def test_PROSE_IS_NOT_SQL(self):
        """The first version of this predicate asked whether the text CONTAINED a keyword, so
        `f"Buffered update from {sender}: {subject}"` was classified as a statement and its two
        interpolations reported as values spliced into SQL. Every alarming name in that census came
        from prose, and the census was the thing being used to decide whether there was a defect."""
        self.assertFalse(looks_like_sql("Buffered update from alice: rebuild the index"))
        self.assertFalse(looks_like_sql("late bridge update was ignored"))
        self.assertFalse(looks_like_sql("select a runtime to continue"))
        # A BARE PROJECTION IS OUT OF SCOPE and this pins that as a decision: "SELECT 1" and
        # "select a runtime to continue" are indistinguishable to a predicate this cheap, nothing in
        # the tree issues the former, and letting it in readmits the prose. PRAGMA is exempt from the
        # clause rule for the opposite reason: it has no clause and is unmistakable.
        self.assertFalse(looks_like_sql("SELECT 1"))
        self.assertTrue(looks_like_sql("SELECT 1 FROM t"))
        self.assertTrue(looks_like_sql("PRAGMA table_info(agents)"))
        self.assertTrue(looks_like_sql("\n            UPDATE agents SET x = ?\n"))
        self.assertTrue(looks_like_sql("   insert into t values (?)"))

    def test_THE_READER_FINDS_BOTH_KINDS_IN_THE_REAL_TREE(self):
        """POSITIVE CONTROL, with the measurement that motivated the module. Every downstream
        assertion is about what this FOUND; a reader returning nothing passes them all."""
        found = list(sql_literals(SERVICE))
        self.assertGreater(len(found), 800, f"only {len(found)} SQL literals found; the reader is broken")
        interpolated = [text for _, _, text in found if "{" in text]
        self.assertGreater(len(interpolated), 100, (
            f"only {len(interpolated)} of {len(found)} SQL literals carry an interpolation. Measured "
            "2026-08-29: 123 of 854. A reader that lost them would report a clean sweep of the "
            "eight-ninths it can still see."
        ))

    def test_the_status_fragments_resolve_from_their_owner(self):
        """Read from `terminal_status.py` rather than re-listed, so a renamed or added fragment
        cannot leave a scanner quietly resolving nothing."""
        fragments = status_fragment_resolutions()
        self.assertIn("TERMINAL_LIVE_FILTER_SQL", fragments)
        self.assertIn("TERMINAL_ACTIVE_STATUS_SQL", fragments)
        for name, rendered in fragments.items():
            self.assertTrue(rendered.startswith("(") and rendered.endswith(")"), name)

    def test_a_resolved_scan_can_still_see_the_ruled_member(self):
        """The concrete failure this closes. `recovering` lives in twelve terminal filters, none of
        which spell it out any more. A scan that resolves the fragments still finds it; the one that
        did not declared the ledger entry stale and told the next reader to delete a ruling."""
        resolutions = status_fragment_resolutions()
        texts = [text for _, _, text in sql_literals(SERVICE, resolutions)]
        carrying = [t for t in texts if "'recovering'" in t and "terminal_sessions" in t]
        self.assertGreaterEqual(len(carrying), 8, (
            f"only {len(carrying)} terminal queries resolve to naming 'recovering'; the ledger in "
            "test_terminal_sql_compares_terminal_statuses.py depends on them being visible"
        ))


if __name__ == "__main__":
    unittest.main()
