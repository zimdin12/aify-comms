"""Which managed workers are idle enough to close, asked of the query itself.

`_select_idle_virtual_rpc_workers` was inline in `_close_idle_virtual_rpc_workers` until v0.5.4, so
exercising it meant running the reconciler. It is now a leaf and these tests run it against a real
sqlite database.

CLOSING A WORKER IS DESTRUCTIVE, which is why almost every test here asserts a row is NOT selected.
Every clause in the query is a reason not to close: the terminal must be live, running a recognised
worker command, belong to a MANAGED session, be untouched for the idle window, and owe no work.
Relax any one and the sweep closes a console an operator is looking at, or a worker that is about to
be handed a run.

THE OWED-WORK CLAUSE IS THE SUBTLE ONE and gets four tests. It spares queued, claimed and running
runs — and ALSO `delivered` runs that still require a reply, because a delivered run whose answer has
not come back is work the agent still owes even though nothing is executing. Dropping that half would
close the worker that was about to produce the reply. A delivered run that owes NO reply is finished
and must not spare anything, or no idle worker would ever be closed.
"""

from __future__ import annotations

import unittest

import aiosqlite

from service.api_core.idle_worker_query import _select_idle_virtual_rpc_workers
from service.api_core.virtual_rpc import VIRTUAL_RPC_COMMAND_SET

SCHEMA = """
CREATE TABLE terminal_sessions (
    id TEXT PRIMARY KEY, session_id TEXT, agent_id TEXT, command TEXT, status TEXT,
    environment_id TEXT, bridge_id TEXT, updated_at TEXT
);
CREATE TABLE agent_sessions (id TEXT PRIMARY KEY, owner_mode TEXT, mode TEXT);
CREATE TABLE agents (id TEXT PRIMARY KEY, session_mode TEXT);
CREATE TABLE dispatch_runs (
    id TEXT PRIMARY KEY, target_agent TEXT, status TEXT, require_reply INTEGER DEFAULT 0
);
"""

OLD = "2020-01-01T00:00:00Z"
RECENT = "2099-01-01T00:00:00Z"

#: A real worker command, taken from the owner rather than typed — a literal here would pass while
#: the set it is supposed to represent had moved on.
WORKER_COMMAND = sorted(VIRTUAL_RPC_COMMAND_SET)[0]


class IdleWorkerQueryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)
        await self.db.execute("INSERT INTO agents VALUES ('a1','managed')")
        await self.db.execute("INSERT INTO agent_sessions VALUES ('s1','managed','managed-warm')")

    async def asyncTearDown(self):
        await self.db.close()

    async def _terminal(self, tid="t1", *, agent="a1", session="s1", command=None,
                        status="running", updated_at=OLD):
        await self.db.execute(
            "INSERT INTO terminal_sessions VALUES (?,?,?,?,?,'env','b',?)",
            (tid, session, agent, WORKER_COMMAND if command is None else command, status, updated_at))

    async def _run(self, rid, *, agent="a1", status="queued", require_reply=0):
        await self.db.execute(
            "INSERT INTO dispatch_runs VALUES (?,?,?,?)", (rid, agent, status, require_reply))

    async def _select(self, *, minutes=30, limit=200):
        rows = await _select_idle_virtual_rpc_workers(self.db, minutes, limit)
        return [r["id"] for r in rows]

    async def test_an_idle_managed_worker_is_selected(self):
        await self._terminal()
        self.assertEqual(["t1"], await self._select())

    # ---- reasons NOT to close -----------------------------------------------

    async def test_a_terminal_that_is_not_LIVE_is_left_alone(self):
        for status in ("stopped", "failed", "lost", "ended", "completed", "cancelled"):
            with self.subTest(status=status):
                await self.db.execute("DELETE FROM terminal_sessions")
                await self._terminal(status=status)
                self.assertEqual([], await self._select())

    async def test_a_RECENTLY_updated_terminal_is_left_alone(self):
        await self._terminal(updated_at=RECENT)
        self.assertEqual([], await self._select())

    async def test_an_unrecognised_command_is_left_alone(self):
        """Not our worker to close — it could be anything the operator started."""
        await self._terminal(command="bash -l")
        self.assertEqual([], await self._select())

    async def test_the_wrapper_and_opencode_command_shapes_are_recognised(self):
        for command in ("claude-aify --resume", "opencode run"):
            with self.subTest(command=command):
                await self.db.execute("DELETE FROM terminal_sessions")
                await self._terminal(command=command)
                self.assertEqual(["t1"], await self._select())

    async def test_a_RESIDENT_session_is_never_closed(self):
        """The operator's own process. Managed-ness is asked three ways and none of them says yes."""
        await self.db.execute("UPDATE agents SET session_mode = 'resident'")
        await self.db.execute("UPDATE agent_sessions SET owner_mode = 'resident', mode = 'resident'")
        await self._terminal()
        self.assertEqual([], await self._select())

    async def test_ANY_of_the_three_managed_signals_is_enough(self):
        """They disagree in practice during mode switches and adoptions; the permissive read is the
        safe one HERE, because a managed worker left running is a leak while a resident one closed is
        an operator's session killed."""
        for column, table in (("session_mode", "agents"), ("owner_mode", "agent_sessions")):
            with self.subTest(signal=column):
                await self.db.execute("UPDATE agents SET session_mode = 'resident'")
                await self.db.execute(
                    "UPDATE agent_sessions SET owner_mode = 'resident', mode = 'resident'")
                await self.db.execute(f"UPDATE {table} SET {column} = 'managed'")
                await self.db.execute("DELETE FROM terminal_sessions")
                await self._terminal()
                self.assertEqual(["t1"], await self._select())

    # ---- owed work -----------------------------------------------------------

    async def test_a_worker_with_work_IN_FLIGHT_is_spared(self):
        for status in ("queued", "claimed", "running"):
            with self.subTest(status=status):
                await self.db.execute("DELETE FROM dispatch_runs")
                await self.db.execute("DELETE FROM terminal_sessions")
                await self._terminal()
                await self._run("r1", status=status)
                self.assertEqual([], await self._select())

    async def test_a_DELIVERED_run_still_owing_a_reply_spares_the_worker(self):
        """Nothing is executing, but the answer has not come back — closing now kills the replier."""
        await self._terminal()
        await self._run("r1", status="delivered", require_reply=1)
        self.assertEqual([], await self._select())

    async def test_a_DELIVERED_run_owing_NO_reply_does_not_spare_it(self):
        """Otherwise every finished info-only delivery would keep a worker alive forever."""
        await self._terminal()
        await self._run("r1", status="delivered", require_reply=0)
        self.assertEqual(["t1"], await self._select())

    async def test_ANOTHER_agents_work_does_not_spare_this_worker(self):
        await self._terminal()
        await self._run("r1", agent="someone-else", status="running")
        self.assertEqual(["t1"], await self._select())

    # ---- shape ---------------------------------------------------------------

    async def test_the_least_recently_updated_comes_first(self):
        await self._terminal("t-new", updated_at="2026-06-01T00:00:00Z")
        await self._terminal("t-old", updated_at="2026-01-01T00:00:00Z")
        self.assertEqual(["t-old", "t-new"], await self._select())

    async def test_the_limit_is_honoured(self):
        for i in range(4):
            await self._terminal(f"t{i}", updated_at=f"2026-0{i + 1}-01T00:00:00Z")
        self.assertEqual(["t0", "t1"], await self._select(limit=2))

    async def test_the_row_carries_what_the_close_needs(self):
        await self._terminal()
        rows = await _select_idle_virtual_rpc_workers(self.db, 30, 200)
        for column in ("id", "agent_id", "command", "environment_id", "bridge_id",
                       "agent_session_id"):
            self.assertIn(column, rows[0].keys())


if __name__ == "__main__":
    unittest.main()
