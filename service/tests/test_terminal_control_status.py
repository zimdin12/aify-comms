"""What a completed control implies about the terminal, tested by calling the deriver.

`_apply_terminal_status_from_control` was inline in `update_terminal_control` until v0.5.4, so
exercising it meant driving `PATCH /terminals/controls/{id}`. It is now a leaf and these tests run it
against a real sqlite database.

THE BRIDGE REPORTS ON THE CONTROL; THIS DECIDES WHAT THAT MEANS FOR THE TERMINAL. Three sources in
priority order — an explicit `terminalStatus` wins, a failed control implies `failed`, a completed
stop implies `stopped` — and anything else leaves the terminal alone, which is why a resize or an
input control must not touch its status.

AN END STATUS IS FIVE WRITES, and each is asserted separately because dropping any one leaves a
different lie behind: runs that will never get a reply stay open, the terminal row keeps a live
status, the session row disagrees with the terminal it points at, the console binding still names a
dead terminal, and the live-status cache keeps reporting the agent online.
"""

from __future__ import annotations

import unittest

import aiosqlite

import service.api_core.terminal_control_status as module
from service.api_core.terminal_control_status import _apply_terminal_status_from_control

SCHEMA = """
CREATE TABLE terminal_sessions (
    id TEXT PRIMARY KEY, session_id TEXT, agent_id TEXT, status TEXT,
    updated_at TEXT, stopped_at TEXT, error TEXT
);
CREATE TABLE agent_sessions (
    id TEXT PRIMARY KEY, agent_id TEXT, terminal_status TEXT, owner_mode TEXT, last_seen TEXT
);
"""

BEFORE = "2026-08-01T10:00:00Z"
NOW = "2026-08-15T12:00:00Z"


class _Req:
    def __init__(self, terminal_status=None, error=None):
        self.terminalStatus = terminal_status
        self.error = error


class TerminalControlStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)
        await self.db.execute(
            "INSERT INTO terminal_sessions VALUES ('t1','s1','a1','running',?,NULL,'')", (BEFORE,))
        await self.db.execute(
            "INSERT INTO agent_sessions VALUES ('s1','a1','attached','resident',?)", (BEFORE,))
        #: The two collaborators are recorded rather than executed: this module's job is to DECIDE
        #: and to call them, and a real terminal-run closer needs half the schema to say so.
        self.closed_runs: list[tuple] = []
        self.cleared_bindings: list[tuple] = []
        self.invalidated: list[str] = []
        self._closer = module._close_active_terminal_runs_for_terminal
        self._clear = module._clear_console_terminal_binding
        self._invalidate = module._invalidate_agent_live_state

        async def _close(db, terminal, status, *, now, reason):
            self.closed_runs.append((terminal["id"], status, now, reason))

        async def _clear_binding(db, agent_id, terminal_id, *, now):
            self.cleared_bindings.append((agent_id, terminal_id, now))

        async def _invalidate_cache(db, agent_id):
            self.invalidated.append(agent_id)

        module._close_active_terminal_runs_for_terminal = _close
        module._clear_console_terminal_binding = _clear_binding
        module._invalidate_agent_live_state = _invalidate_cache

    async def asyncTearDown(self):
        module._close_active_terminal_runs_for_terminal = self._closer
        module._clear_console_terminal_binding = self._clear
        module._invalidate_agent_live_state = self._invalidate
        await self.db.close()

    async def _apply(self, *, terminal_status=None, error=None, action="stop", status="completed"):
        terminal = await (await self.db.execute(
            "SELECT * FROM terminal_sessions WHERE id = 't1'")).fetchone()
        return await _apply_terminal_status_from_control(
            self.db, _Req(terminal_status, error), {"action": action}, terminal, status, NOW)

    async def _terminal(self):
        return await (await self.db.execute(
            "SELECT * FROM terminal_sessions WHERE id = 't1'")).fetchone()

    async def _session(self):
        return await (await self.db.execute(
            "SELECT * FROM agent_sessions WHERE id = 's1'")).fetchone()

    # ---- which status is derived -------------------------------------------

    async def test_an_explicit_terminal_status_wins(self):
        self.assertEqual("lost", await self._apply(terminal_status="lost", status="failed"))

    async def test_a_FAILED_control_implies_failed(self):
        self.assertEqual("failed", await self._apply(status="failed", action="resize"))

    async def test_a_completed_STOP_implies_stopped(self):
        self.assertEqual("stopped", await self._apply(action="stop", status="completed"))

    async def test_a_completed_NON_stop_control_implies_nothing(self):
        """A resize or an input control says nothing about whether the terminal is alive."""
        for action in ("resize", "input", "signal"):
            with self.subTest(action=action):
                self.assertEqual("", await self._apply(action=action, status="completed"))
                self.assertEqual("running", (await self._terminal())["status"],
                                 "the terminal row must be untouched")

    async def test_a_failed_RESIZE_still_implies_failed(self):
        """The control failing is about the control — but a bridge that cannot resize is a bridge
        whose terminal is in trouble, and that inference predates this split."""
        self.assertEqual("failed", await self._apply(action="resize", status="failed"))

    # ---- the five writes an end status owes --------------------------------

    async def test_an_end_status_closes_the_runs_that_will_never_be_answered(self):
        await self._apply(action="stop", status="completed")
        self.assertEqual(1, len(self.closed_runs))
        terminal_id, status, now, reason = self.closed_runs[0]
        self.assertEqual(("t1", "stopped", NOW), (terminal_id, status, now))
        self.assertIn("before an explicit reply", reason)

    async def test_an_end_status_stamps_the_terminal_row(self):
        await self._apply(action="stop", status="completed")
        row = await self._terminal()
        self.assertEqual("stopped", row["status"])
        self.assertEqual(NOW, row["updated_at"])
        self.assertEqual(NOW, row["stopped_at"])

    async def test_only_stopped_and_failed_stamp_stopped_at(self):
        """`ended` is a terminal end status but not a stop; a stop time would be a fabrication."""
        await self._apply(terminal_status="ended")
        self.assertIsNone((await self._terminal())["stopped_at"])

    async def test_the_error_text_is_recorded_only_on_a_FAILED_control(self):
        await self._apply(terminal_status="failed", error="node-pty died", status="failed")
        self.assertEqual("node-pty died", (await self._terminal())["error"])

    async def test_a_non_failed_end_status_leaves_the_existing_error_alone(self):
        await self.db.execute("UPDATE terminal_sessions SET error = 'earlier failure' WHERE id='t1'")
        await self._apply(action="stop", status="completed")
        self.assertEqual("earlier failure", (await self._terminal())["error"])

    async def test_an_end_status_mirrors_onto_the_session_and_returns_it_to_managed(self):
        """A session still marked resident after its terminal died is one nothing will respawn."""
        await self._apply(action="stop", status="completed")
        row = await self._session()
        self.assertEqual("stopped", row["terminal_status"])
        self.assertEqual("managed", row["owner_mode"])
        self.assertEqual(NOW, row["last_seen"])

    async def test_a_NON_stop_end_status_does_not_change_session_ownership(self):
        await self._apply(terminal_status="ended")
        self.assertEqual("resident", (await self._session())["owner_mode"])

    async def test_an_end_status_clears_the_console_binding(self):
        await self._apply(action="stop", status="completed")
        self.assertEqual([("a1", "t1", NOW)], self.cleared_bindings)

    async def test_an_end_status_invalidates_the_live_status_cache(self):
        """Without it the agent keeps reading online with a dead terminal until the 60s sweep."""
        await self._apply(action="stop", status="completed")
        self.assertEqual(["a1"], self.invalidated)

    async def test_a_NON_end_status_does_none_of_the_five(self):
        await self._apply(terminal_status="attached")
        self.assertEqual([], self.closed_runs)
        self.assertEqual([], self.cleared_bindings)
        self.assertEqual([], self.invalidated)
        self.assertEqual("attached", (await self._terminal())["status"],
                         "a non-end status still updates the row, just without the teardown")


if __name__ == "__main__":
    unittest.main()
