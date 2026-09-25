"""An ended terminal's replay buffer is released after the TTL, whichever way it ended.

`_prune_terminal_history` clears `terminal_sessions.output` for terminals that have ended. It listed
four of the six ended statuses by hand ('stopped', 'failed', 'ended', 'cancelled'), so a terminal that
ended `lost` or `completed` kept its buffer for ever. It now reads the one ended set,
`_TERMINAL_END_STATUSES`. This proves every member is released and that a live terminal is not.
"""

import asyncio
import sqlite3

import aiosqlite

from service.api_core.terminal_status import _TERMINAL_ACTIVE_STATUSES, _TERMINAL_END_STATUSES
from service.reconcilers.terminal_history import _prune_terminal_history
from service.tests._base import FastApiTestCase

OLD = "2020-01-01T00:00:00"


class EndedTerminalOutputIsReleasedTests(FastApiTestCase):
    def _seed(self, statuses):
        con = sqlite3.connect(str(self._db_path))
        con.execute("PRAGMA foreign_keys=OFF")
        # One agent per row, so the keep-N-rows-per-agent cap never touches these.
        con.executemany(
            "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, runtime, output, status,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [(f"t-{s}", f"s-{s}", f"a-{s}", "e1", "codex", "buffered bytes", s, OLD, OLD) for s in statuses],
        )
        con.commit()
        con.close()

    def _prune(self):
        async def run():
            db = await aiosqlite.connect(str(self._db_path))
            db.row_factory = aiosqlite.Row
            try:
                return await _prune_terminal_history(db, ended_output_ttl_hours=24)
            finally:
                await db.close()
        return asyncio.run(run())

    def _outputs(self):
        con = sqlite3.connect(str(self._db_path))
        try:
            return dict(con.execute("SELECT status, output FROM terminal_sessions").fetchall())
        finally:
            con.close()

    def test_every_ended_status_releases_its_output_and_a_live_one_keeps_it(self):
        ended = sorted(_TERMINAL_END_STATUSES)
        self.assertIn("lost", ended)
        self.assertIn("completed", ended)
        self._seed([*ended, "running"])
        self.assertIn("running", _TERMINAL_ACTIVE_STATUSES)

        counts = self._prune()

        outputs = self._outputs()
        self.assertEqual(counts["ended_output_cleared"], len(ended))
        for status in ended:
            self.assertEqual(outputs[status], "", f"a terminal that ended {status!r} kept its buffer")
        self.assertEqual(outputs["running"], "buffered bytes", "a live terminal's output was released")
