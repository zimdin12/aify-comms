"""The queries an idle service repeats do not walk the whole messages, runs or controls tables.

MEASURED, 2026-09-16. An idle host spent about 2% of a core. By the service's own /proc CPU counter, most of
it came in two bursts a minute: a dashboard's GET /stats (0.18 CPU-s a call) and the 60s reconcile sweep.
On a copy of the live database both were dominated by full scans of `messages` and `dispatch_runs`, and
the indexes in service/schema.py took /stats from 406ms to 112ms and the sweep's control settlement from
51ms to 0.

An index only helps a query that spells its expression and predicate the way the index does, so this runs
the REAL statements -- captured from the real route and reconciler, not copied here -- through
`EXPLAIN QUERY PLAN`. Each measured query must use ITS index, and no watched table may be read by a plain
scan: without the first, a query that falls back to a weaker index still reads SEARCH.

One scan is declared rather than fixed: the unread aggregate walks every message lacking a receipt,
which is what it counts.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import aiosqlite.core as core

import service.db as db_module
from service.db import init_db
from service.reconcilers.stuck_controls import _close_controls_for_ended_runs
from service.routers.stats import get_stats

WATCHED_TABLES = ("messages", "dispatch_runs", "dispatch_controls")

#: The one scan that is the query's job rather than a missing index, named by a fragment of its SQL.
DECLARED_SCANS = ("SUM(CASE WHEN a.id IS NOT NULL AND m.source = 'direct'",)

#: Each measured query and the index that made it cheap. "No full scan" is NOT enough: removing one of these
#: leaves the planner on a weaker index -- `idx_messages_source` walks 98% of messages -- and the plan still
#: reads SEARCH. So each query must use ITS index, and must still be issued (a renamed query fails here).
REQUIRED_INDEXES = {
    "stats": (
        ("FROM messages WHERE source = 'direct' AND timestamp >= ?", "idx_messages_source_ts"),
        ("FROM messages WHERE source = 'direct' GROUP BY type", "idx_messages_source_type"),
        ("status = 'completed' AND COALESCE(finished_at, requested_at) >= ?", "idx_dispatch_runs_status_finished"),
        ("require_reply = 1 AND status IN ('completed', 'failed', 'cancelled')", "idx_dispatch_runs_reply_open"),
    ),
    "sweep": (
        ("FROM dispatch_controls c JOIN dispatch_runs r ON r.id = c.run_id", "idx_dispatch_controls_status_requested"),
    ),
}


def _normal(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


class _Request:
    app = None


class TheIdlePathsDoNotScanWholeTables(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "plans.db"
        asyncio.run(init_db(self.path))
        self._original_path = db_module._db_path
        db_module._db_path = self.path

    def tearDown(self):
        db_module._db_path = self._original_path
        self._tmp.cleanup()

    def _captured(self, run) -> list[tuple[str, tuple]]:
        seen: list[tuple[str, tuple]] = []
        original = core.Connection._execute

        async def recording(conn, fn, *args, **kwargs):
            if args and isinstance(args[0], str) and _normal(args[0]).upper().startswith(("SELECT", "WITH")):
                params = tuple(args[1]) if len(args) > 1 and args[1] else ()
                seen.append((args[0], params))
            return await original(conn, fn, *args, **kwargs)

        with mock.patch.object(core.Connection, "_execute", recording):
            asyncio.run(run())
        self.assertTrue(seen, "control: nothing was captured, so no plan was checked")
        return seen

    def _full_scans(self, statements) -> list[str]:
        found = []
        with closing(sqlite3.connect(self.path)) as conn:
            for sql, params in statements:
                flat = _normal(sql)
                if not any(re.search(rf"\b{table}\b", flat) for table in WATCHED_TABLES):
                    continue
                if any(fragment in flat for fragment in DECLARED_SCANS):
                    continue
                for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params):
                    detail = row[3]
                    # A plain SCAN reads every row. A covering-index scan or a scan of a subquery's result
                    # does not read the table itself.
                    if detail.startswith("SCAN ") and "COVERING INDEX" not in detail and "(subquery" not in detail:
                        found.append(f"{detail}  <-  {flat[:120]}")
        return found

    def _stats_statements(self):
        return self._captured(lambda: get_stats(_Request()))

    def _sweep_statements(self):
        async def run():
            async with core.connect(self.path) as conn:
                conn.row_factory = sqlite3.Row
                await _close_controls_for_ended_runs(conn)
        return self._captured(run)

    def _missing_indexes(self, statements, required) -> list[str]:
        problems = []
        with closing(sqlite3.connect(self.path)) as conn:
            for fragment, index in required:
                matching = [(sql, p) for sql, p in statements if fragment in _normal(sql)]
                if not matching:
                    problems.append(f"no statement containing {fragment!r} was issued")
                    continue
                for sql, params in matching:
                    plan = " | ".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params))
                    if index not in plan:
                        problems.append(f"{fragment!r} does not use {index}: {plan}")
        return problems

    def test_each_measured_stats_query_uses_its_own_index(self):
        self.assertEqual(self._missing_indexes(self._stats_statements(), REQUIRED_INDEXES["stats"]), [])

    def test_the_sweeps_control_settlement_uses_its_own_index(self):
        self.assertEqual(self._missing_indexes(self._sweep_statements(), REQUIRED_INDEXES["sweep"]), [])

    def test_GET_stats_reads_messages_and_runs_through_indexes(self):
        self.assertEqual(self._full_scans(self._stats_statements()), [])

    def test_the_sweep_settles_controls_without_walking_every_ended_run(self):
        self.assertEqual(self._full_scans(self._sweep_statements()), [])

    def test_CONTROL_without_its_index_a_stats_query_is_reported_as_a_scan(self):
        # Proves the check can say no: remove one index the stats queries rely on and it must see the scan.
        statements = self._stats_statements()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("DROP INDEX idx_dispatch_runs_status_finished")
            conn.execute("DROP INDEX idx_dispatch_runs_status_requested")
            conn.commit()
        self.assertTrue(self._full_scans(statements), "dropping the indexes produced no scan: the check is blind")



class AnOldDatabaseStillStarts(unittest.TestCase):
    """REVIEW FINDING, 2026-09-16: the partial index on `require_reply` was first put in the schema script, which
    runs BEFORE the migrations. A database whose `dispatch_runs` predates that column (2026-04-23) failed
    startup with `no such column: require_reply`. Built here from the real schema, minus the migrated columns.
    """

    def test_a_runs_table_without_the_migrated_columns_starts_and_gets_the_index(self):
        from service.db import DISPATCH_RUN_MIGRATIONS
        from service.schema import SCHEMA

        table = re.search(r"CREATE TABLE IF NOT EXISTS dispatch_runs \((.*?)\n\);", SCHEMA, re.S)
        self.assertIsNotNone(table, "control: the runs table was not found in the schema")
        kept = [line for line in table.group(1).split("\n")
                if not any(re.match(rf"\s*{column}\b", line) for column in DISPATCH_RUN_MIGRATIONS)]
        legacy = "CREATE TABLE dispatch_runs (" + "\n".join(kept).rstrip().rstrip(",") + "\n)"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute(legacy)
                columns = {row[1] for row in conn.execute("PRAGMA table_info(dispatch_runs)")}
                self.assertNotIn("require_reply", columns, "control: the legacy table already has the column")
            asyncio.run(init_db(path))
            with closing(sqlite3.connect(path)) as conn:
                names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
            self.assertIn("idx_dispatch_runs_reply_open", names)


if __name__ == "__main__":
    unittest.main()
