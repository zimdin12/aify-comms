"""Which runs the reaper may fail, asked of the query itself.

`_select_orphaned_managed_runs` was inline in `_close_orphaned_managed_runs` until v0.5.4, so
exercising it meant running the reaper. It is now a leaf and these tests run it against a real sqlite
database, which is the only way to test SQL: this is three OR-ed branches of correlated NOT EXISTS
clauses over `datetime()` comparisons, and a mock would agree with whatever I believed.

THIS IS THE DANGEROUS KIND OF SWEEP. It FAILS runs that a bridge may still be holding, so a query one
clause too wide kills live work and the failure reads to an operator as an agent that gave up. Every
branch here was added after something went wrong without it, and the tests are written per branch and
per exclusion rather than per happy path.

THE CUTOFFS ARE PARAMETERS, which is what makes this testable without a clock — a test hands it two
timestamps rather than arranging for wall time to pass. `'-0 hours'` therefore means "everything is
past the cutoff" and `'-100 years'` means "nothing is", and the tests use those two poles.
"""

from __future__ import annotations

import unittest

import aiosqlite

from service.api_core.orphaned_runs_query import _select_orphaned_managed_runs

SCHEMA = """
CREATE TABLE dispatch_runs (
    id TEXT PRIMARY KEY, target_agent TEXT, subject TEXT, status TEXT,
    started_at TEXT, claimed_at TEXT, requested_at TEXT,
    execution_mode TEXT, dispatch_mode TEXT, claim_bridge_id TEXT
);
CREATE TABLE bridge_instances (id TEXT PRIMARY KEY, last_seen TEXT);
CREATE TABLE dispatch_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, event_type TEXT, created_at TEXT
);
"""

OLD = "2020-01-01T00:00:00Z"
NOW_ISH = "2099-01-01T00:00:00Z"

#: Everything is past this cutoff.
EVERYTHING_STALE = "-0 hours"
#: Nothing is past this one.
NOTHING_STALE = "-100 years"


class OrphanedRunsQueryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)

    async def asyncTearDown(self):
        await self.db.close()

    async def _run(self, rid, *, status="running", started_at=OLD, claimed_at=OLD,
                   requested_at=OLD, bridge=""):
        await self.db.execute(
            "INSERT INTO dispatch_runs VALUES (?,?,'s',?,?,?,?,'managed','start',?)",
            (rid, "a1", status, started_at, claimed_at, requested_at, bridge))

    async def _bridge(self, bid, *, last_seen=NOW_ISH):
        await self.db.execute("INSERT INTO bridge_instances VALUES (?,?)", (bid, last_seen))

    async def _event(self, rid, *, event_type="progress", created_at=NOW_ISH):
        await self.db.execute(
            "INSERT INTO dispatch_events (run_id, event_type, created_at) VALUES (?,?,?)",
            (rid, event_type, created_at))

    async def _select(self, *, cutoff=EVERYTHING_STALE, ceiling=NOTHING_STALE, limit=500):
        rows = await _select_orphaned_managed_runs(self.db, cutoff, ceiling, limit)
        return [r["id"] for r in rows]

    # ---- scope --------------------------------------------------------------

    async def test_only_claimed_and_running_runs_are_candidates(self):
        for status in ("queued", "delivered", "completed", "failed", "cancelled"):
            with self.subTest(status=status):
                await self.db.execute("DELETE FROM dispatch_runs")
                await self._run("r1", status=status)
                self.assertEqual([], await self._select())

    async def test_a_claimed_run_and_a_running_run_both_qualify(self):
        for status in ("claimed", "running"):
            with self.subTest(status=status):
                await self.db.execute("DELETE FROM dispatch_runs")
                await self._run("r1", status=status)
                self.assertEqual(["r1"], await self._select())

    # ---- branch 1: no live owning bridge, no progress ------------------------

    async def test_a_run_with_no_claiming_bridge_is_orphaned(self):
        await self._run("r1", bridge="")
        self.assertEqual(["r1"], await self._select())

    async def test_a_run_whose_bridge_is_STALE_is_orphaned(self):
        """The 2026-05-23 operator report: sc-coder's run sat 'running' 50+ minutes because
        claim_bridge_id pointed at a bridge that had since gone stale, and the original
        `claim_bridge_id = ''` check could not see it."""
        await self._bridge("b-dead", last_seen=OLD)
        await self._run("r1", bridge="b-dead")
        self.assertEqual(["r1"], await self._select())

    async def test_a_run_whose_bridge_is_LIVE_is_spared_by_branch_1(self):
        await self._bridge("b-live", last_seen=NOW_ISH)
        await self._run("r1", bridge="b-live", started_at=NOW_ISH, claimed_at=NOW_ISH)
        self.assertEqual([], await self._select())

    async def test_a_run_that_has_PROGRESSED_since_the_cutoff_is_spared(self):
        await self._run("r1")
        await self._event("r1", event_type="output", created_at=NOW_ISH)
        self.assertEqual([], await self._select())

    async def test_reply_reminder_skipped_does_NOT_count_as_progress(self):
        """The same operator report: it fired every minute and kept resetting the window long after
        the controller had died. It is a service-side METADATA event about the run, not progress
        from the runtime."""
        await self._run("r1")
        await self._event("r1", event_type="reply_reminder_skipped", created_at=NOW_ISH)
        self.assertEqual(["r1"], await self._select())

    async def test_another_runs_progress_does_not_spare_this_one(self):
        await self._run("r1")
        await self._run("r2")
        await self._event("r2", event_type="output", created_at=NOW_ISH)
        self.assertEqual(["r1"], await self._select())

    # ---- branch 2: the absolute wall-clock ceiling ---------------------------

    async def test_the_CEILING_ages_out_a_run_whose_bridge_is_still_alive(self):
        """FIX 5. The inner controller died without PATCHing the run terminal, so the bridge keeps
        heartbeating while nothing is actually working."""
        await self._bridge("b-live", last_seen=NOW_ISH)
        await self._run("r1", bridge="b-live", started_at=OLD)
        self.assertEqual([], await self._select(cutoff=NOTHING_STALE, ceiling=NOTHING_STALE))
        self.assertEqual(["r1"], await self._select(cutoff=NOTHING_STALE, ceiling=EVERYTHING_STALE))

    async def test_the_ceiling_still_respects_real_progress(self):
        await self._bridge("b-live", last_seen=NOW_ISH)
        await self._run("r1", bridge="b-live", started_at=OLD)
        await self._event("r1", event_type="output", created_at=NOW_ISH)
        self.assertEqual([], await self._select(cutoff=NOTHING_STALE, ceiling=EVERYTHING_STALE))

    # ---- branch 3: claimed but never started --------------------------------

    async def test_a_run_CLAIMED_but_never_STARTED_is_orphaned_regardless_of_bridge(self):
        """2026-06-18: a managed/hermes claim whose prompt.submit silently failed to start a turn
        sits 'claimed' — so the target reads falsely busy and reply reminders skip — until the wall
        ceiling. Once claimed, a turn starts within seconds."""
        await self._bridge("b-live", last_seen=NOW_ISH)
        await self._run("r1", status="claimed", started_at=None, claimed_at=OLD, bridge="b-live")
        self.assertEqual(["r1"], await self._select(ceiling=NOTHING_STALE))

    async def test_a_never_started_run_inside_the_stale_window_is_spared(self):
        await self._bridge("b-live", last_seen=NOW_ISH)
        await self._run("r1", status="claimed", started_at=None, claimed_at=NOW_ISH,
                        requested_at=NOW_ISH, bridge="b-live")
        self.assertEqual([], await self._select(cutoff=NOTHING_STALE, ceiling=NOTHING_STALE))

    async def test_branch_3_ignores_progress_events(self):
        """Deliberate asymmetry, worth pinning: a run that never started is orphaned even if events
        exist, because events about a turn that never began do not mean it began."""
        await self._bridge("b-live", last_seen=NOW_ISH)
        await self._run("r1", status="claimed", started_at=None, claimed_at=OLD, bridge="b-live")
        await self._event("r1", event_type="output", created_at=NOW_ISH)
        self.assertEqual(["r1"], await self._select(ceiling=NOTHING_STALE))

    # ---- shape --------------------------------------------------------------

    async def test_the_oldest_request_comes_first(self):
        await self._run("r-new", requested_at="2026-06-01T00:00:00Z")
        await self._run("r-old", requested_at="2026-01-01T00:00:00Z")
        self.assertEqual(["r-old", "r-new"], await self._select())

    async def test_the_limit_is_honoured(self):
        for i in range(4):
            await self._run(f"r{i}", requested_at=f"2026-0{i + 1}-01T00:00:00Z")
        self.assertEqual(["r0", "r1"], await self._select(limit=2))

    async def test_the_row_carries_what_the_reaper_needs_to_report(self):
        """The caller builds its failure summary from these columns; a narrowed SELECT would raise
        on a key that is not there, but only for the runs that reach that path."""
        await self._run("r1")
        rows = await _select_orphaned_managed_runs(self.db, EVERYTHING_STALE, NOTHING_STALE, 500)
        for column in ("id", "target_agent", "subject", "started_at", "requested_at",
                       "execution_mode", "dispatch_mode", "claim_bridge_id"):
            self.assertIn(column, rows[0].keys())


if __name__ == "__main__":
    unittest.main()
