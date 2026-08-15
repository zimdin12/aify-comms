"""Every column that names an agent must have an answer for what a RENAME does to it.

`PATCH /agents/{id}/rename` moves an agent's identity. The schema has twenty-odd columns holding an
agent id, and the rename touches thirteen of them. The other columns are not wrong to be untouched —
but which ones, and why, was knowable only by reading the rewrite and the schema side by side, and
NOTHING failed when a new one appeared.

THAT IS THE FAILURE THIS EXISTS TO CATCH. Add a table with an `agent_id` column — this repo has added
five since the rename was written — and rename silently strands its rows under an id that is
tombstoned in the same transaction. Nothing raises. The rows are simply never found again, and the
symptom arrives later as an agent that lost something it used to have.

So every agent-referencing column is required to be classified below, and the classification is
checked against the LIVE schema. A new column fails this test until someone decides which bucket it
belongs in, which is the decision that was previously being made by omission.

THE THREE BUCKETS:

    REPOINTED    the rewrite updates it to the new id
    CASCADES     its table declares ON DELETE CASCADE on `agents`, so deleting the old row removes
                 the stale rows rather than stranding them. Derived state that rebuilds itself
    LEFT_BEHIND  deliberately untouched, with the reason recorded here

`UNRESOLVED` is a fourth bucket and it is NOT a synonym for LEFT_BEHIND: it holds columns whose
treatment looks like an oversight rather than a decision, and it is reported to the operator rather
than blessed. v0.5.x is the refactor line, so this test RECORDS the gap; closing it is a behaviour
change and a separate decision. If someone fixes one, this test fails and they move the entry to
REPOINTED — which is the point.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
DB = REPO / "service" / "db.py"
RENAME_WRITES = REPO / "service" / "api_core" / "agent_rename_writes.py"

#: Column names that hold an agent id. `requested_by` and `removed_by` are audit trails of WHO asked
#: rather than references to the subject, so they are not in scope; they are recorded in LEFT_BEHIND
#: where they belong to a table that is otherwise in scope.
AGENT_COLUMNS = {"agent_id", "from_agent", "to_agent", "target_agent", "created_by", "managed_by"}

REPOINTED = {
    ("agents", "managed_by"),
    ("agent_sessions", "agent_id"),
    ("spawn_specs", "agent_id"),
    ("spawn_requests", "agent_id"),
    ("bridge_instances", "agent_id"),
    ("read_receipts", "agent_id"),
    ("channel_members", "agent_id"),
    ("messages", "from_agent"),
    ("messages", "to_agent"),
    ("shared_artifacts", "from_agent"),
    ("dispatch_runs", "from_agent"),
    ("dispatch_runs", "target_agent"),
    ("dispatch_controls", "from_agent"),
    ("channels", "created_by"),
}

CASCADES = {
    #: Derived per-agent state with `ON DELETE CASCADE` on `agents`. Deleting the old row clears it,
    #: and the engine recomputes under the new id on the next observation. Repointing it would carry
    #: a status snapshot across an identity change, which is worse than recomputing.
    ("agent_status_state", "agent_id"),
    ("agent_turn_state", "agent_id"),
    ("agent_console_signal", "agent_id"),
    ("claimer_leases", "agent_id"),
    #: Vestigial: retained for schema compatibility, read and written by nothing since the live-status
    #: cache became an in-memory dict. See CLAUDE.md.
    ("agent_live_state", "agent_id"),
}

LEFT_BEHIND = {
    #: MUST keep naming the OLD id. Recording that the old id is retired is the entire point of the
    #: row the rename writes.
    ("agent_tombstones", "agent_id"): "the tombstone is ABOUT the old id",
    #: Who asked for the spawn, not who it is for. `spawn_requests.agent_id` is the subject and IS
    #: repointed.
    ("spawn_requests", "created_by"): "requester audit, not the subject of the rename",
}

UNRESOLVED = {
    #: FOUND 2026-08-15 while extracting the rewrite. `terminal_sessions` is the ONLY table with an
    #: `agent_id` that is neither repointed nor cascaded — it has no foreign key to `agents`, so the
    #: `DELETE FROM agents` does not reach it. After a rename its rows still name an id that the same
    #: transaction tombstoned, so `_active_terminal_for_agent(new_id)` finds nothing and the renamed
    #: agent looks like it has no console, while the stale rows keep a `running` status under a dead
    #: id. Reported to the operator; NOT fixed here, because adding a repoint is a behaviour change
    #: and v0.5.x is the refactor line.
    ("terminal_sessions", "agent_id"): "neither repointed nor cascaded — reported, awaiting a ruling",
}


def _schema_tables() -> dict[str, set[str]]:
    """Every CREATE TABLE in `service/db.py`, as {table: {column, ...}}."""
    source = DB.read_text(encoding="utf-8")
    tables: dict[str, set[str]] = {}
    for match in re.finditer(
        r"CREATE TABLE(?: IF NOT EXISTS)?\s+(\w+)\s*\((.*?)\n\s*\)\s*", source, re.S
    ):
        name, body = match.group(1), match.group(2)
        columns = {
            line_match.group(1)
            for line in body.split("\n")
            if (line_match := re.match(r"\s*(\w+)\s+\w", line))
        }
        tables.setdefault(name, set()).update(columns)
    return tables


def _agent_references() -> set[tuple[str, str]]:
    return {
        (table, column)
        for table, columns in _schema_tables().items()
        for column in columns
        if column in AGENT_COLUMNS
    }


def _repointed_by_the_rewrite() -> set[tuple[str, str]]:
    """What the rewrite ACTUALLY updates, read out of its own source.

    Two forms: a literal `UPDATE <table> SET <column> = ?`, and the `for table, column in (...)`
    loop, whose pairs are read from the tuple rather than from the f-string it builds.
    """
    tree = ast.parse(RENAME_WRITES.read_text(encoding="utf-8"))
    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for table, column in re.findall(r"UPDATE\s+(\w+)\s+SET\s+(\w+)\s*=", node.value):
                found.add((table, column))
        if isinstance(node, ast.For) and isinstance(node.iter, (ast.Tuple, ast.List)):
            for element in node.iter.elts:
                if isinstance(element, ast.Tuple) and len(element.elts) == 2 and all(
                    isinstance(part, ast.Constant) and isinstance(part.value, str)
                    for part in element.elts
                ):
                    found.add((element.elts[0].value, element.elts[1].value))
    return found


class AgentRenameCoversEveryAgentReferenceTests(unittest.TestCase):
    def test_the_schema_scan_finds_the_tables_it_is_supposed_to(self):
        """Anti-vacuity: every check below is over what this finds, so finding little must fail."""
        tables = _schema_tables()
        self.assertGreaterEqual(len(tables), 20, f"only {len(tables)} tables parsed; the scan is blind")
        self.assertIn("agents", tables)
        self.assertIn("agent_id", tables.get("terminal_sessions", set()))

    def test_every_agent_reference_in_the_schema_is_classified(self):
        classified = REPOINTED | CASCADES | set(LEFT_BEHIND) | set(UNRESOLVED)
        unclassified = sorted(_agent_references() - classified)
        self.assertEqual(
            [], unclassified,
            "a column holding an agent id has no recorded answer for what a rename does to it. "
            "Decide, then add it to REPOINTED, CASCADES, LEFT_BEHIND or UNRESOLVED:\n  "
            + "\n  ".join(f"{table}.{column}" for table, column in unclassified),
        )

    def test_no_classification_names_a_column_that_no_longer_exists(self):
        """The other direction. A stale entry silently shrinks what this test covers."""
        classified = REPOINTED | CASCADES | set(LEFT_BEHIND) | set(UNRESOLVED)
        actual = _agent_references()
        stale = sorted(classified - actual)
        self.assertEqual(
            [], stale,
            "a classified column is not in the schema any more; delete the entry:\n  "
            + "\n  ".join(f"{table}.{column}" for table, column in stale),
        )

    def test_REPOINTED_matches_what_the_rewrite_actually_does(self):
        """The list here is a claim about the code; this is the part that checks it.

        Read from the rewrite's own source, including the `for table, column in (...)` loop — a
        regex over the f-string it builds would see `UPDATE {table} SET {column}` and learn nothing.
        """
        actual = _repointed_by_the_rewrite()
        # The rewrite also copies the row and tombstones the old id; those are not repoints.
        self.assertEqual(
            sorted(REPOINTED), sorted(actual),
            "REPOINTED disagrees with the rewrite. Either a repoint was added or removed without "
            "updating this classification, or the classification was always wrong.",
        )

    def test_every_CASCADES_entry_really_cascades(self):
        """Otherwise "it cascades" is an assumption, and the rows are stranded rather than cleared."""
        source = DB.read_text(encoding="utf-8")
        for table, column in sorted(CASCADES):
            match = re.search(
                r"CREATE TABLE(?: IF NOT EXISTS)?\s+" + table + r"\s*\((.*?)\n\s*\)\s*", source, re.S)
            self.assertIsNotNone(match, f"{table} is not in the schema")
            self.assertIn(
                "REFERENCES agents(id) ON DELETE CASCADE", " ".join(match.group(1).split()),
                f"{table}.{column} is classified as cascading but declares no cascade on agents",
            )

    def test_nothing_is_in_two_buckets(self):
        buckets = [REPOINTED, CASCADES, set(LEFT_BEHIND), set(UNRESOLVED)]
        for i, first in enumerate(buckets):
            for second in buckets[i + 1:]:
                self.assertEqual(
                    set(), first & second,
                    f"a column is classified twice: {sorted(first & second)}")

    def test_the_UNRESOLVED_gap_is_still_open_and_still_says_so(self):
        """Deliberately fails if someone fixes it, so the classification cannot go stale silently.

        If `terminal_sessions.agent_id` starts being repointed, this test fails and the entry moves
        to REPOINTED. That is the intended way for the gap to close — by a decision that updates the
        record, not by a quiet edit that leaves this file claiming a gap that no longer exists.
        """
        self.assertTrue(UNRESOLVED, "if the last gap closed, delete this test with the entry")
        for (table, column) in UNRESOLVED:
            self.assertNotIn(
                (table, column), _repointed_by_the_rewrite(),
                f"{table}.{column} is repointed now — move it from UNRESOLVED to REPOINTED",
            )


if __name__ == "__main__":
    unittest.main()
