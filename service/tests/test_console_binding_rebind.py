"""A live console orphaned by warm rotation must be rebound onto the current session.

REPORTED by another team's tech-lead 2026-08-11, with the rows and the root cause. A managed agent
read `working`, `GET /agents/{id}/console` said `live: true`, and the dashboard offered
**"Start console"** — because `console-chooser.js` reads the CURRENT session row's binding and warm
rotation had left the live terminal bound to the previous, now-ended row. Clicking would have spawned
a second console beside the live one.

The regression shape is the reporter's own: an attached terminal on an ended session plus a running
session with no terminal → after reconcile the running session owns it.

These tests are heavier on the REFUSAL cases than the repair case, deliberately. A wrong rebind points
an operator's console at another process, so every guard gets its own test.
"""

from __future__ import annotations

import asyncio
import unittest

import aiosqlite

from service.reconcilers.console_binding import rebind_orphaned_live_consoles

SCHEMA = """
CREATE TABLE agent_sessions (
    id TEXT, agent_id TEXT, status TEXT, terminal_id TEXT, terminal_status TEXT,
    started_at TEXT, last_seen TEXT
);
CREATE TABLE terminal_sessions (
    id TEXT, agent_id TEXT, session_id TEXT, status TEXT, updated_at TEXT
);
"""


class RebindOrphanedConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)

    async def asyncTearDown(self):
        await self.db.close()

    async def _session(self, sid, *, agent="a1", status="running", terminal_id="", started="2026-08-11T10:00:00Z"):
        await self.db.execute(
            "INSERT INTO agent_sessions VALUES (?,?,?,?,?,?,?)",
            (sid, agent, status, terminal_id, "attached" if terminal_id else "", started, started),
        )

    async def _terminal(self, tid, *, agent="a1", session_id="", status="attached"):
        await self.db.execute(
            "INSERT INTO terminal_sessions VALUES (?,?,?,?,?)",
            (tid, agent, session_id, status, "2026-08-11T10:00:00Z"),
        )

    async def _run(self):
        n = await rebind_orphaned_live_consoles(self.db)
        await self.db.commit()
        return n

    async def _binding(self, sid):
        row = await (await self.db.execute(
            "SELECT terminal_id, terminal_status FROM agent_sessions WHERE id = ?", (sid,)
        )).fetchone()
        return (row["terminal_id"], row["terminal_status"])

    # ── the reported case ────────────────────────────────────────────────────────────
    async def test_the_reported_case_is_repaired(self):
        await self._session("old", status="ended", terminal_id="term1", started="2026-08-10T10:00:00Z")
        await self._session("new", status="running", terminal_id="", started="2026-08-11T10:00:00Z")
        await self._terminal("term1", session_id="old", status="attached")

        self.assertEqual(await self._run(), 1)
        self.assertEqual(await self._binding("new"), ("term1", "attached"),
                         "the running session must own the live terminal")
        self.assertEqual(await self._binding("old"), ("", ""),
                         "and the ended session must let go of it — two owners is its own bug")
        term = await (await self.db.execute("SELECT session_id FROM terminal_sessions WHERE id='term1'")).fetchone()
        self.assertEqual(term["session_id"], "new", "the terminal row must point back at the new session")

    async def test_it_is_idempotent(self):
        await self._session("old", status="ended", terminal_id="term1")
        await self._session("new", status="running", terminal_id="")
        await self._terminal("term1", session_id="old")
        self.assertEqual(await self._run(), 1)
        self.assertEqual(await self._run(), 0, "a second pass must find nothing to do")

    # ── the refusals, one test each ──────────────────────────────────────────────────
    async def test_a_DEAD_terminal_is_never_rebound(self):
        """The whole point of the report: the existing healer covers dead terminals. Rebinding one
        here would hand the operator a console onto a process that is gone."""
        for dead in ("stopped", "failed", "", "unknown"):
            with self.subTest(dead):
                await self.db.execute("DELETE FROM agent_sessions")
                await self.db.execute("DELETE FROM terminal_sessions")
                await self._session("old", status="ended", terminal_id="t")
                await self._session("new", status="running", terminal_id="")
                await self._terminal("t", session_id="old", status=dead)
                self.assertEqual(await self._run(), 0)
                self.assertEqual(await self._binding("new"), ("", ""))

    async def test_a_LIVE_owning_session_is_never_robbed(self):
        """If the terminal's owner is still live this is not an orphan, and moving the binding would
        break a working console."""
        for alive in ("running", "starting", "recovering", "active", "idle"):
            with self.subTest(alive):
                await self.db.execute("DELETE FROM agent_sessions")
                await self.db.execute("DELETE FROM terminal_sessions")
                await self._session("owner", status=alive, terminal_id="t")
                await self._session("new", status="running", terminal_id="")
                await self._terminal("t", session_id="owner")
                self.assertEqual(await self._run(), 0)

    async def test_a_current_session_that_ALREADY_has_a_terminal_is_untouched(self):
        await self._session("old", status="ended", terminal_id="term1")
        await self._session("new", status="running", terminal_id="term2")
        await self._terminal("term1", session_id="old")
        await self._terminal("term2", session_id="new")
        self.assertEqual(await self._run(), 0)
        self.assertEqual((await self._binding("new"))[0], "term2")

    async def test_never_crosses_agents(self):
        """The worst possible failure: pointing one operator's console at another agent's process."""
        await self._session("old", agent="a2", status="ended", terminal_id="term1")
        await self._session("new", agent="a1", status="running", terminal_id="")
        await self._terminal("term1", agent="a2", session_id="old")
        self.assertEqual(await self._run(), 0)
        self.assertEqual(await self._binding("new"), ("", ""))

    async def test_a_second_live_session_holding_a_terminal_blocks_the_repair(self):
        """Two live owners is a DUPLICATE-SESSION problem. Rebinding here would hide it behind a
        console that happens to work, which is how a real bug becomes invisible."""
        await self._session("old", status="ended", terminal_id="term1")
        await self._session("other-live", status="running", terminal_id="term2")
        await self._session("new", status="running", terminal_id="")
        await self._terminal("term1", session_id="old")
        await self._terminal("term2", session_id="other-live")
        self.assertEqual(await self._run(), 0)

    async def test_no_sessions_at_all(self):
        self.assertEqual(await self._run(), 0)


if __name__ == "__main__":
    unittest.main()
