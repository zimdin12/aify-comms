"""The dead-terminal spawn sweep's two questions, asked of the queries themselves.

Both were inline in `_finalize_spawns_with_dead_terminals` until v0.5.4, so exercising them meant
running the reconciler. They are leaves now and these tests run them against a real sqlite database.

THE LIVE-SIBLING GUARD IS WHAT MOST OF THIS FILE IS ABOUT. A spawn is finalizable when its session
has a DEAD terminal and NO live one — because a session mid-rebind briefly shows both, and failing
the spawn then kills a healthy worker. The guard is correct and it is SILENT, which is why the second
query exists: without a count of what it held back, "0 finalized" cannot be told apart from "the
sweep never ran".

THE TWO MUST AGREE, and that is the one thing neither can check about itself. So the last test here
drives both against the same rows and asserts they partition: a spawn is either finalizable or
masked, never both, never neither.
"""

from __future__ import annotations

import unittest

import aiosqlite

from service.api_core.dead_terminal_spawn_query import (
    _count_spawns_masked_by_live_sibling,
    _select_spawns_with_dead_terminals,
    _terminal_end_statuses_ordered,
)

SCHEMA = """
CREATE TABLE spawn_requests (
    id TEXT PRIMARY KEY, agent_id TEXT, session_id TEXT, status TEXT,
    finished_at TEXT, created_at TEXT
);
CREATE TABLE terminal_sessions (
    id TEXT PRIMARY KEY, session_id TEXT, status TEXT, output TEXT, error TEXT,
    stopped_at TEXT, updated_at TEXT
);
"""

WHEN = "2026-08-15T12:00:00Z"

#: Taken from the owner rather than typed. A literal would keep passing while the set it stands for
#: moved on, which is the forked-constant failure the accessor exists to prevent.
DEAD = _terminal_end_statuses_ordered()[0]
PLACEHOLDERS = ",".join("?" for _ in _terminal_end_statuses_ordered())


class DeadTerminalSpawnQueryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)

    async def asyncTearDown(self):
        await self.db.close()

    async def _spawn(self, sid="sp1", *, session="s1", status="running", finished_at="",
                     created_at=WHEN):
        await self.db.execute(
            "INSERT INTO spawn_requests VALUES (?,'a1',?,?,?,?)",
            (sid, session, status, finished_at, created_at))

    async def _terminal(self, tid, *, session="s1", status=DEAD, stopped_at=WHEN):
        await self.db.execute(
            "INSERT INTO terminal_sessions VALUES (?,?,?,'out','err',?,?)",
            (tid, session, status, stopped_at, WHEN))

    async def _finalizable(self, limit=200):
        rows = await _select_spawns_with_dead_terminals(self.db, PLACEHOLDERS, limit)
        return [r["spawn_id"] for r in rows]

    async def _masked(self):
        row = await _count_spawns_masked_by_live_sibling(self.db, PLACEHOLDERS)
        return int((row["n"] if row is not None else 0) or 0)

    # ---- the sweep's candidates ---------------------------------------------

    async def test_a_spawn_whose_only_terminal_is_dead_is_finalizable(self):
        await self._spawn()
        await self._terminal("t1", status=DEAD)
        self.assertEqual(["sp1"], await self._finalizable())

    async def test_a_LIVE_SIBLING_spares_the_spawn(self):
        """The rebind race: a session mid-rebind shows both, and failing then kills a healthy
        worker."""
        await self._spawn()
        await self._terminal("t-dead", status=DEAD)
        await self._terminal("t-live", status="running")
        self.assertEqual([], await self._finalizable())

    async def test_every_end_status_counts_as_dead(self):
        for status in _terminal_end_statuses_ordered():
            with self.subTest(status=status):
                await self.db.execute("DELETE FROM terminal_sessions")
                await self.db.execute("DELETE FROM spawn_requests")
                await self._spawn()
                await self._terminal("t1", status=status)
                self.assertEqual(["sp1"], await self._finalizable())

    async def test_the_status_match_is_case_insensitive(self):
        """`LOWER(COALESCE(t.status, ''))` — a bridge reporting 'Stopped' is still stopped."""
        await self._spawn()
        await self._terminal("t1", status=DEAD.upper())
        self.assertEqual(["sp1"], await self._finalizable())

    async def test_only_starting_and_running_spawns_are_candidates(self):
        for status in ("queued", "claimed", "failed", "cancelled"):
            with self.subTest(status=status):
                await self.db.execute("DELETE FROM spawn_requests")
                await self._spawn(status=status)
                self.assertEqual([], await self._finalizable())

    async def test_an_already_FINISHED_spawn_is_left_alone(self):
        await self._spawn(finished_at=WHEN)
        await self._terminal("t1")
        self.assertEqual([], await self._finalizable())

    async def test_a_spawn_with_no_session_is_left_alone(self):
        """Nothing to join against; the terminal that killed it cannot be identified."""
        await self._spawn(session="")
        await self._terminal("t1", session="")
        self.assertEqual([], await self._finalizable())

    async def test_another_sessions_dead_terminal_does_not_finalize_this_spawn(self):
        await self._spawn(session="s1")
        await self._terminal("t1", session="s2")
        self.assertEqual([], await self._finalizable())

    async def test_the_oldest_spawn_comes_first_and_the_limit_holds(self):
        for i in range(3):
            await self._spawn(f"sp{i}", session=f"s{i}", created_at=f"2026-0{i + 1}-01T00:00:00Z")
            await self._terminal(f"t{i}", session=f"s{i}")
        self.assertEqual(["sp0", "sp1"], await self._finalizable(limit=2))

    async def test_the_row_carries_the_cause_the_refusal_will_name(self):
        """WS-1: the spawn error records the terminal's own recorded cause, so an agent reading the
        refusal later learns what actually happened."""
        await self._spawn()
        await self._terminal("t1")
        rows = await _select_spawns_with_dead_terminals(self.db, PLACEHOLDERS, 200)
        for column in ("spawn_id", "agent_id", "terminal_id", "terminal_status",
                       "terminal_output", "terminal_error", "died_at"):
            self.assertIn(column, rows[0].keys())

    # ---- what the guard held back --------------------------------------------

    async def test_the_masked_count_sees_exactly_what_the_guard_spared(self):
        await self._spawn()
        await self._terminal("t-dead", status=DEAD)
        await self._terminal("t-live", status="running")
        self.assertEqual(1, await self._masked())

    async def test_a_spawn_with_NO_dead_terminal_is_not_masked(self):
        """It was never a candidate, so counting it would inflate the "held back" number."""
        await self._spawn()
        await self._terminal("t-live", status="running")
        self.assertEqual(0, await self._masked())

    async def test_a_finalizable_spawn_is_not_ALSO_counted_as_masked(self):
        await self._spawn()
        await self._terminal("t1", status=DEAD)
        self.assertEqual(0, await self._masked())

    async def test_the_two_queries_PARTITION_the_candidates(self):
        """The one thing neither query can check about itself.

        Three sessions: one purely dead, one mid-rebind, one healthy. Exactly one must be
        finalizable and exactly one masked — if the two ever disagreed about what "dead" or "live"
        means, this is where it shows.
        """
        await self._spawn("sp-dead", session="s-dead")
        await self._terminal("t1", session="s-dead", status=DEAD)
        await self._spawn("sp-rebind", session="s-rebind")
        await self._terminal("t2", session="s-rebind", status=DEAD)
        await self._terminal("t3", session="s-rebind", status="running")
        await self._spawn("sp-healthy", session="s-healthy")
        await self._terminal("t4", session="s-healthy", status="running")

        self.assertEqual(["sp-dead"], await self._finalizable())
        self.assertEqual(1, await self._masked())


if __name__ == "__main__":
    unittest.main()
