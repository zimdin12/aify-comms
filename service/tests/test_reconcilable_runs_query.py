"""Which lingering `delivered` runs are reconcilable, asked of the query itself.

`_select_reconcilable_delivered_runs` was inline in `_close_reconcilable_delivered_runs` until
v0.5.4, so exercising it meant running the reconciler. It is now a leaf and these tests run it
against a real sqlite database, which is the only way to test SQL: the conditions below are
`datetime()` comparisons and correlated NOT EXISTS clauses, and a mock would just agree with
whatever I believed.

THE DEFECT THIS QUERY EXISTS AROUND (2026-08-04). Class 1 — the reply landed but nothing closed the
run — used to sit behind an outer `finished_at = ''` guard. The path that links a reply sets
`result_message_id` AND `finished_at` together, so every row the class was written for was filtered
out before the clause was reached. Seven runs were found permanently stuck at `delivered`, the oldest
from 2026-05-30, never once eligible for the reconciler meant to repair them.

That is why `test_class_1_is_selected_even_though_it_is_FINISHED` is the most important test here:
it is the exact shape that was excluded, and nothing else in the suite asserts it.
"""

from __future__ import annotations

import unittest

import aiosqlite

from service.api_core.reconcilable_runs_query import _select_reconcilable_delivered_runs

SCHEMA = """
CREATE TABLE dispatch_runs (
    id TEXT PRIMARY KEY, target_agent TEXT, status TEXT, result_message_id TEXT,
    require_reply INTEGER DEFAULT 0, requested_at TEXT, finished_at TEXT
);
CREATE TABLE agent_sessions (
    id TEXT PRIMARY KEY, agent_id TEXT, status TEXT
);
"""

#: Comfortably outside any stale window used here, and in the format `datetime()` parses.
OLD = "2026-01-01T00:00:00Z"
#: Comfortably inside it.
RECENT = "2099-01-01T00:00:00Z"


class ReconcilableRunsQueryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)

    async def asyncTearDown(self):
        await self.db.close()

    async def _run(self, rid, *, agent="a1", status="delivered", result="",
                   require_reply=0, requested_at=OLD, finished_at=""):
        await self.db.execute(
            "INSERT INTO dispatch_runs VALUES (?,?,?,?,?,?,?)",
            (rid, agent, status, result, require_reply, requested_at, finished_at))

    async def _session(self, sid, *, agent="a1", status="running"):
        await self.db.execute("INSERT INTO agent_sessions VALUES (?,?,?)", (sid, agent, status))

    async def _select(self, *, limit=500, stale_hours=24):
        rows = await _select_reconcilable_delivered_runs(self.db, limit, stale_hours)
        return [r["id"] for r in rows]

    # ---- class 1: the reply landed --------------------------------------------

    async def test_class_1_is_selected_even_though_it_is_FINISHED(self):
        """THE 2026-08-04 DEFECT, as a test.

        The path that links a reply stamps `result_message_id` and `finished_at` together, so an
        outer `finished_at = ''` guard excluded exactly the rows this class targets. Seven runs sat
        stuck for months. A row that is `delivered` WITH a finish stamp is inconsistent by
        definition — that is the repair, not a reason to skip it.
        """
        await self._run("r1", result="msg-9", finished_at="2026-08-04T00:00:00Z")
        self.assertEqual(["r1"], await self._select())

    async def test_class_1_ignores_the_stale_window_entirely(self):
        """A landed reply is closable now; waiting a day serves nobody."""
        await self._run("r1", result="msg-9", requested_at=RECENT)
        self.assertEqual(["r1"], await self._select())

    async def test_an_EMPTY_result_id_is_not_a_landed_reply(self):
        await self._run("r1", result="", requested_at=RECENT)
        self.assertEqual([], await self._select())

    # ---- class 2: no reply was ever owed ---------------------------------------

    async def test_class_2_selects_a_stale_info_only_run(self):
        await self._run("r1", require_reply=0, requested_at=OLD)
        self.assertEqual(["r1"], await self._select())

    async def test_class_2_leaves_a_RECENT_info_only_run_alone(self):
        await self._run("r1", require_reply=0, requested_at=RECENT)
        self.assertEqual([], await self._select())

    async def test_class_2_requires_an_unfinished_run(self):
        """Unlike class 1: an unfinished run is one nobody closed, which is what makes it stale."""
        await self._run("r1", require_reply=0, requested_at=OLD, finished_at="2026-02-01T00:00:00Z")
        self.assertEqual([], await self._select())

    # ---- class 3: a reply was owed and nobody is left to give it ---------------

    async def test_class_3_selects_an_orphaned_stale_run(self):
        await self._run("r1", require_reply=1, requested_at=OLD)
        self.assertEqual(["r1"], await self._select())

    async def test_class_3_spares_a_run_whose_agent_has_OTHER_work_in_flight(self):
        """Someone is still working; the reply may yet arrive."""
        for in_flight in ("queued", "claimed", "running"):
            with self.subTest(in_flight=in_flight):
                await self.db.execute("DELETE FROM dispatch_runs")
                await self._run("r1", require_reply=1, requested_at=OLD)
                await self._run("r2", status=in_flight, requested_at=OLD)
                self.assertEqual([], await self._select())

    async def test_class_3_spares_a_run_whose_agent_still_has_a_LIVE_session(self):
        for alive in ("starting", "running", "recovering", "restarting", "cli-takeover"):
            with self.subTest(alive=alive):
                await self.db.execute("DELETE FROM agent_sessions")
                await self._session("s1", status=alive)
                await self.db.execute("DELETE FROM dispatch_runs")
                await self._run("r1", require_reply=1, requested_at=OLD)
                self.assertEqual([], await self._select())

    async def test_class_3_is_not_spared_by_ANOTHER_agents_live_work(self):
        """Both NOT EXISTS clauses are correlated on target_agent; a shared table is not a shared
        owner."""
        await self._run("r1", agent="a1", require_reply=1, requested_at=OLD)
        await self._run("r2", agent="someone-else", status="running", requested_at=OLD)
        await self._session("s1", agent="someone-else", status="running")
        self.assertEqual(["r1"], await self._select())

    async def test_class_3_ignores_a_DEAD_session_of_its_own_agent(self):
        """"Has a session row" is not "has an owner" — an ended session produces no reply."""
        await self._session("s1", status="ended")
        await self._run("r1", require_reply=1, requested_at=OLD)
        self.assertEqual(["r1"], await self._select())

    async def test_a_runs_OWN_row_does_not_count_as_its_agents_in_flight_work(self):
        """`r2.id != dispatch_runs.id` — without it nothing in class 3 would ever be selected."""
        await self._run("r1", require_reply=1, requested_at=OLD)
        self.assertEqual(["r1"], await self._select())

    # ---- scope and shape --------------------------------------------------------

    async def test_only_DELIVERED_runs_are_considered(self):
        for status in ("queued", "claimed", "running", "completed", "failed", "cancelled"):
            with self.subTest(status=status):
                await self.db.execute("DELETE FROM dispatch_runs")
                await self._run("r1", status=status, result="msg-9")
                self.assertEqual([], await self._select())

    async def test_the_oldest_runs_come_first(self):
        await self._run("r-new", result="m", requested_at="2026-06-01T00:00:00Z")
        await self._run("r-old", result="m", requested_at="2026-01-01T00:00:00Z")
        self.assertEqual(["r-old", "r-new"], await self._select())

    async def test_the_limit_is_honoured(self):
        for i in range(5):
            await self._run(f"r{i}", result="m", requested_at=f"2026-0{i + 1}-01T00:00:00Z")
        self.assertEqual(["r0", "r1"], await self._select(limit=2))

    async def test_a_degenerate_stale_hours_falls_back_to_one_hour_not_zero(self):
        """`max(1, int(stale_hours or 24))` — a 0 would make `datetime('now', '-0 hours')` select
        every unfinished run the instant it was created."""
        await self._run("r1", require_reply=0, requested_at=RECENT)
        for degenerate in (0, -5, None):
            with self.subTest(stale_hours=degenerate):
                self.assertEqual([], await self._select(stale_hours=degenerate))


if __name__ == "__main__":
    unittest.main()
