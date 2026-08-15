"""What Stop and Resume actually do, tested by calling them.

`_apply_agent_stop_or_resume` was inline in `control_agent` until v0.5.4, so exercising it meant
driving `POST /agents/{id}/control`. It is now a leaf and these tests run it against a real sqlite
database.

STOP IS FOUR THINGS, NOT ONE. Cancel the runs queued for this agent — each with an event, so a
cancelled run stays distinguishable from a failed one — mark the agent stopped with a note the
operator can act on, and tear down its managed console. Each of those is asserted separately, because
a Stop that does three of the four looks like it worked.

THE MANAGED-ONLY TEARDOWN IS THE ONE MOST WORTH PINNING (operator-reported 2026-05-31). aify-comms is
the lifecycle driver for a MANAGED session, so a Stop that left the TUI running abandoned a live
process nobody owned. A RESIDENT window is the operator's own process and must NOT be killed; its
bridge terminates the CLI host, which is what the resident stop note says.

RESUME is deliberately narrower than an inverse of stop, and that asymmetry is asserted too: it
starts nothing.
"""

from __future__ import annotations

import unittest

import aiosqlite

from service.api_core.agent_stop_resume import _apply_agent_stop_or_resume

SCHEMA = """
CREATE TABLE agents (
    id TEXT PRIMARY KEY, status TEXT, status_note TEXT, launch_mode TEXT,
    session_mode TEXT, last_seen TEXT
);
CREATE TABLE dispatch_runs (
    id TEXT PRIMARY KEY, target_agent TEXT, status TEXT, summary TEXT, finished_at TEXT
);
CREATE TABLE dispatch_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, event_type TEXT, body TEXT, created_at TEXT
);
"""
#: Column names copied from the real schema rather than guessed. The first version of this fixture
#: called the event column `payload`; the write failed loudly here, but a fixture that merely LOOKS
#: right is how a test ends up describing a system that does not exist.

BEFORE = "2026-08-01T10:00:00Z"
NOW = "2026-08-15T12:00:00Z"


class _Req:
    def __init__(self, from_agent=None, body=None):
        self.from_agent = from_agent
        self.body = body


class AgentStopResumeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)
        #: Recorded by the fake teardown below so "the console was torn down" is an observation
        #: rather than an assumption about a function this test does not own.
        self.torn_down: list[tuple] = []

    async def asyncTearDown(self):
        await self.db.close()

    async def _agent(self, agent_id="a1", *, session_mode="managed", status="idle",
                     launch_mode="detached"):
        await self.db.execute(
            "INSERT INTO agents VALUES (?,?,?,?,?,?)",
            (agent_id, status, "", launch_mode, session_mode, BEFORE))
        return await (await self.db.execute(
            "SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()

    async def _run(self, rid, *, agent="a1", status="queued"):
        await self.db.execute(
            "INSERT INTO dispatch_runs VALUES (?,?,?,'',NULL)", (rid, agent, status))

    async def _apply(self, action, *, agent_id="a1", session_mode="managed", agent=None,
                     from_agent=None):
        if agent is None:
            agent = await self._agent(agent_id, session_mode=session_mode)
        import service.api_core.agent_stop_resume as module

        real = module._request_stop_agent_terminals

        async def _spy(db, aid, *, requested_by, now):
            self.torn_down.append((aid, requested_by, now))

        module._request_stop_agent_terminals = _spy
        try:
            return await _apply_agent_stop_or_resume(
                self.db, agent_id, agent, _Req(from_agent), action, NOW, 0)
        finally:
            module._request_stop_agent_terminals = real

    async def _agent_row(self, agent_id="a1"):
        return await (await self.db.execute(
            "SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()

    async def _runs(self):
        return await (await self.db.execute(
            "SELECT * FROM dispatch_runs ORDER BY id")).fetchall()

    # ---- stop ---------------------------------------------------------------

    async def test_stop_cancels_the_queued_runs_and_counts_them(self):
        await self._run("r1")
        await self._run("r2")
        cancelled = await self._apply("stop")
        self.assertEqual(2, cancelled)
        self.assertEqual({"cancelled"}, {r["status"] for r in await self._runs()})

    async def test_a_cancelled_run_says_WHY_it_was_cancelled(self):
        """"cancelled" with no cause is indistinguishable from a failure to whoever reads it next."""
        await self._run("r1")
        await self._apply("stop")
        row = (await self._runs())[0]
        self.assertIn("stopped from the dashboard", row["summary"])
        self.assertEqual(NOW, row["finished_at"])

    async def test_every_cancelled_run_gets_its_own_event(self):
        await self._run("r1")
        await self._run("r2")
        await self._apply("stop")
        events = await (await self.db.execute(
            "SELECT * FROM dispatch_events WHERE event_type = 'agent_stopped'")).fetchall()
        self.assertEqual(2, len(events))
        self.assertEqual({"r1", "r2"}, {e["run_id"] for e in events})

    async def test_a_RUNNING_run_is_not_cancelled_by_a_stop(self):
        """Only queued work is cancelled here; a run in flight is torn down by other means."""
        await self._run("r1", status="running")
        cancelled = await self._apply("stop")
        self.assertEqual(0, cancelled)
        self.assertEqual("running", (await self._runs())[0]["status"])

    async def test_another_agents_queued_run_is_never_cancelled(self):
        await self._run("r1", agent="someone-else")
        cancelled = await self._apply("stop", agent_id="a1")
        self.assertEqual(0, cancelled)
        self.assertEqual("queued", (await self._runs())[0]["status"])

    async def test_stop_marks_the_agent_stopped_and_clears_launch_mode(self):
        await self._apply("stop")
        row = await self._agent_row()
        self.assertEqual("stopped", row["status"])
        self.assertEqual("none", row["launch_mode"])
        self.assertEqual(NOW, row["last_seen"])

    async def test_a_MANAGED_stop_tears_down_the_console(self):
        """The operator-reported defect: a Stop that left the TUI running abandoned a live process."""
        await self._apply("stop", session_mode="managed", from_agent="steven")
        self.assertEqual([("a1", "steven", NOW)], self.torn_down)

    async def test_a_RESIDENT_stop_does_NOT_tear_down_anything(self):
        """A resident window is the operator's own process; its bridge terminates the CLI host."""
        await self._apply("stop", session_mode="resident")
        self.assertEqual([], self.torn_down)

    async def test_the_stop_note_differs_by_session_mode(self):
        """The note is the only thing telling the operator what to expect next."""
        await self._apply("stop", session_mode="managed")
        self.assertIn("Resume to allow wake", (await self._agent_row())["status_note"])
        await self.db.execute("DELETE FROM agents")
        await self._apply("stop", session_mode="resident")
        self.assertIn("live bridge should terminate", (await self._agent_row())["status_note"])

    async def test_an_absent_requester_falls_back_to_dashboard(self):
        await self._apply("stop", from_agent=None)
        self.assertEqual("dashboard", self.torn_down[0][1])

    # ---- resume -------------------------------------------------------------

    async def test_resume_clears_the_status_and_the_note(self):
        agent = await self._agent(status="stopped", launch_mode="none")
        await self._apply("resume", agent=agent)
        row = await self._agent_row()
        self.assertEqual("idle", row["status"])
        self.assertEqual("", row["status_note"])

    async def test_resume_restores_launch_mode_ONLY_when_stop_had_cleared_it(self):
        agent = await self._agent(status="stopped", launch_mode="none")
        await self._apply("resume", agent=agent)
        self.assertEqual("detached", (await self._agent_row())["launch_mode"])

    async def test_resume_leaves_an_existing_launch_mode_alone(self):
        agent = await self._agent(status="stopped", launch_mode="managed")
        await self._apply("resume", agent=agent)
        self.assertEqual("managed", (await self._agent_row())["launch_mode"])

    async def test_resume_starts_NOTHING(self):
        """Deliberately narrower than an inverse of stop: the next send cold-starts a worker."""
        await self._run("r1", status="cancelled")
        agent = await self._agent(status="stopped", launch_mode="none")
        await self._apply("resume", agent=agent)
        self.assertEqual([], self.torn_down)
        self.assertEqual("cancelled", (await self._runs())[0]["status"], "resume must not requeue")

    async def test_an_unknown_action_touches_nothing(self):
        await self._run("r1")
        agent = await self._agent()
        cancelled = await self._apply("interrupt", agent=agent)
        self.assertEqual(0, cancelled)
        self.assertEqual("idle", (await self._agent_row())["status"])
        self.assertEqual("queued", (await self._runs())[0]["status"])


if __name__ == "__main__":
    unittest.main()
