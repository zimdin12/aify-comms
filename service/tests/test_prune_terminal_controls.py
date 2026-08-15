"""_prune_terminal_history retention for terminal_controls (2026-06-07).

terminal_controls is the runtime command QUEUE. Once a control is HANDLED it is pure
delivered-keystroke audit history and was never pruned — it grew unbounded (13k+ rows /
4 days). The sweep must delete ONLY handled controls past the TTL and NEVER a still-PENDING
control (a queued keystroke/resize/stop a bridge hasn't executed yet).
"""

import asyncio
import sqlite3

import aiosqlite


from service.tests._base import FastApiTestCase
from service.reconcilers.terminal_history import _prune_terminal_history


class PruneTerminalControlsRetentionTests(FastApiTestCase):
    def _seed(self):
        con = sqlite3.connect(str(self._db_path))
        con.execute("PRAGMA foreign_keys=OFF")
        con.executemany(
            "INSERT INTO terminal_controls "
            "(id, terminal_id, environment_id, action, status, requested_at, handled_at) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                # handled + OLD → prune
                ("c-old-handled", "t1", "e1", "input", "handled", "2020-01-01T00:00:00", "2020-01-01T00:00:01"),
                # handled but RECENT → keep
                ("c-recent-handled", "t1", "e1", "input", "handled", "2020-01-01T00:00:00", None),
                # PENDING + old requested_at, never handled → KEEP (queued command)
                ("c-old-pending", "t1", "e1", "input", "pending", "2020-01-01T00:00:00", None),
            ],
        )
        con.execute("UPDATE terminal_controls SET handled_at = datetime('now') WHERE id = 'c-recent-handled'")
        con.commit()
        con.close()

    def _run_prune(self):
        async def run():
            db = await aiosqlite.connect(str(self._db_path))
            db.row_factory = aiosqlite.Row
            try:
                return await _prune_terminal_history(db, terminal_control_ttl_hours=24)
            finally:
                await db.close()
        return asyncio.run(run())

    def _remaining(self):
        con = sqlite3.connect(str(self._db_path))
        try:
            return {r[0] for r in con.execute("SELECT id FROM terminal_controls").fetchall()}
        finally:
            con.close()

    def test_prunes_handled_old_keeps_pending_and_recent(self):
        self._seed()
        counts = self._run_prune()
        remaining = self._remaining()
        self.assertEqual(counts["terminal_controls"], 1, "exactly the handled+old control is pruned")
        self.assertNotIn("c-old-handled", remaining, "handled control past the TTL must be pruned")
        self.assertIn("c-recent-handled", remaining, "recently-handled control must be kept")
        self.assertIn(
            "c-old-pending",
            remaining,
            "a PENDING (unhandled) control must NEVER be pruned — it is a queued command",
        )
