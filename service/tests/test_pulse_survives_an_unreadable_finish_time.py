"""`/analytics/pulse` answers when a run's `finished_at` cannot be parsed.

v0.7.1 review (S11). `_iso_to_epoch(..., default=None)` returns None for an unreadable value and the
loop compared it with `<=`, so one bad row made the whole board a 500. The service writes `finished_at`
with `clock.now()`, so this needs a row something else wrote; a board that one row can take down is
still the wrong shape.
"""

import asyncio

from service.db import get_db
from service.tests._base import FastApiTestCase

NOW = "2026-09-26T00:00:00Z"


class PulseSurvivesAnUnreadableFinishTimeTests(FastApiTestCase):
    DB_NAME = "aify-pulse-finish.db"

    def _seed(self, finished_at):
        async def seed():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO agents (id, name, role, runtime, session_mode, status, registered_at, last_seen) "
                    "VALUES ('worker','w','coder','claude-code','managed','idle',?,?)", (NOW, NOW))
                await db.execute(
                    "INSERT INTO dispatch_runs (id, from_agent, target_agent, status, require_reply, runtime, "
                    "requested_at, claimed_at, started_at, finished_at) "
                    "VALUES ('run-1','sender','worker','completed',0,'claude-code',?,?,?,?)",
                    (NOW, NOW, NOW, finished_at))
                await db.commit()
            finally:
                await db.close()
        asyncio.run(seed())

    def test_an_unreadable_finish_time_does_not_take_the_board_down(self):
        # A value SQLite's julianday() reads and Python's fromisoformat() does not: the SQL filter keeps
        # the row, and the loop is handed None. A value neither can read ("not a date") is dropped by the
        # SQL and never reaches the loop, which is why the first draft of this test passed unfixed.
        self._seed("now")
        response = self.client.get("/api/v1/analytics/pulse")
        self.assertEqual(response.status_code, 200, response.text[:300])

    def test_control_a_readable_finish_time_answers(self):
        self._seed(NOW)
        self.assertEqual(self.client.get("/api/v1/analytics/pulse").status_code, 200)
