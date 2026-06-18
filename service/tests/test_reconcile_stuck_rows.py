"""_reconcile_stuck_terminal_and_session_rows (2026-06-18 fleet audit).

Two stuck-row patterns the other reapers miss:
  1. terminal_sessions wedged in the transitional 'stopping' state (bridge died before
     PATCHing 'stopped') — the managed-worker reaper only scans active states, not 'stopping'.
  2. agent_sessions status='ended' with ended_at STILL NULL — reads as a live session forever.
"""

import asyncio
import sqlite3

import aiosqlite

from service.routers.api_v2 import _reconcile_stuck_terminal_and_session_rows
from service.tests._base import FastApiTestCase


class ReconcileStuckRowsTests(FastApiTestCase):
    def _seed(self):
        con = sqlite3.connect(str(self._db_path))
        con.execute("PRAGMA foreign_keys=OFF")
        # terminal_sessions: one OLD stuck 'stopping', one RECENT 'stopping', one healthy 'attached'
        con.executemany(
            "INSERT INTO terminal_sessions "
            "(id, session_id, agent_id, environment_id, runtime, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [
                ("t-old-stopping", "s1", "a1", "e1", "claude-code", "stopping", "2020-01-01T00:00:00", "2020-01-01T00:00:00"),
                ("t-recent-stopping", "s1", "a1", "e1", "claude-code", "stopping", "2020-01-01T00:00:00", "2020-01-01T00:00:00"),
                ("t-attached", "s1", "a1", "e1", "claude-code", "attached", "2020-01-01T00:00:00", "2020-01-01T00:00:00"),
            ],
        )
        con.execute("UPDATE terminal_sessions SET updated_at = datetime('now') WHERE id = 't-recent-stopping'")
        # agent_sessions: one ended-but-not-closed ghost, one properly closed, one live
        con.executemany(
            "INSERT INTO agent_sessions "
            "(id, agent_id, environment_id, runtime, status, started_at, last_seen, ended_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [
                ("sess-ghost", "a1", "e1", "claude-code", "ended", "2020-01-01T00:00:00", "2020-01-02T00:00:00", None),
                ("sess-closed", "a1", "e1", "claude-code", "ended", "2020-01-01T00:00:00", "2020-01-02T00:00:00", "2020-01-02T00:00:01"),
                ("sess-live", "a1", "e1", "claude-code", "running", "2020-01-01T00:00:00", "2020-01-02T00:00:00", None),
            ],
        )
        con.commit()
        con.close()

    def _run(self):
        async def run():
            db = await aiosqlite.connect(str(self._db_path))
            db.row_factory = aiosqlite.Row
            try:
                counts = await _reconcile_stuck_terminal_and_session_rows(db)
                await db.commit()
                return counts
            finally:
                await db.close()
        return asyncio.run(run())

    def _rows(self, table):
        con = sqlite3.connect(str(self._db_path))
        con.row_factory = sqlite3.Row
        try:
            return {r["id"]: r for r in con.execute(f"SELECT * FROM {table}").fetchall()}
        finally:
            con.close()

    def test_closes_stuck_stopping_and_backfills_ended_at(self):
        self._seed()
        counts = self._run()
        terms = self._rows("terminal_sessions")
        sessions = self._rows("agent_sessions")

        self.assertEqual(counts["stuck_stopping_terminals_closed"], 1, "only the OLD stuck 'stopping' PTY is closed")
        self.assertEqual(terms["t-old-stopping"]["status"], "stopped", "wedged 'stopping' PTY forced to 'stopped'")
        self.assertIsNotNone(terms["t-old-stopping"]["stopped_at"], "stopped_at stamped")
        self.assertEqual(terms["t-recent-stopping"]["status"], "stopping", "a recently-stopping PTY is left alone (grace)")
        self.assertEqual(terms["t-attached"]["status"], "attached", "a healthy attached PTY is untouched")

        self.assertEqual(counts["ended_sessions_backfilled"], 1, "only the ended-but-open ghost is backfilled")
        self.assertEqual(sessions["sess-ghost"]["ended_at"], "2020-01-02T00:00:00", "ended_at backfilled from last_seen")
        self.assertEqual(sessions["sess-closed"]["ended_at"], "2020-01-02T00:00:01", "already-closed session untouched")
        self.assertIsNone(sessions["sess-live"]["ended_at"], "a live (non-ended) session is never backfilled")
