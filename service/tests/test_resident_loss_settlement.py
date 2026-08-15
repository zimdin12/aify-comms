"""A dead runtime must leave the agent somewhere it can come back FROM, tested by calling it.

`_settle_lost_resident_when_no_transition` was inline in `resident_lost` until v0.5.4, so the only
way to exercise it was to drive `POST /agents/{id}/resident-lost`. It is now a leaf and these tests
run it directly against a real sqlite database.

WHY IT IS WORTH THIS MUCH TEST. The block decides where an agent RESTS when its runtime bridge died
and could not be handed back to managed, and the two agents that reach it need opposite answers:

    resident, no managed backing   -> stopped. Nothing is left to wake.
    managed worker, backing died   -> cold-startable. The server can re-spawn it on the next message.

Getting that wrong is not a crash. Operator-reported 2026-07-06/07: the old code stopped BOTH, the
send-gate rejects `status='stopped'` outright, and a whole hermes team sat unreachable — every send
bounced with `dispatchRuns:[]` and the only recovery was a manual `hermes-aify` restart. The tests
below assert the two states directly, because "it stopped the agent" and "it made the agent
unreachable forever" look identical from inside this function.
"""

from __future__ import annotations

import unittest

import aiosqlite

from service.api_core.resident_loss import _settle_lost_resident_when_no_transition

SCHEMA = """
CREATE TABLE agents (
    id TEXT PRIMARY KEY, session_mode TEXT, status TEXT, status_note TEXT,
    launch_mode TEXT, last_seen TEXT
);
"""

NOW = "2026-08-15T12:00:00Z"


class _Req:
    """The request body's one field this block reads."""

    def __init__(self, reason=None):
        self.reason = reason


class ResidentLossSettlementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)

    async def asyncTearDown(self):
        await self.db.close()

    async def _agent(self, agent_id="a1", *, session_mode="resident"):
        await self.db.execute(
            "INSERT INTO agents VALUES (?,?,?,?,?,?)",
            (agent_id, session_mode, "active", "", "detached", NOW),
        )
        return await (await self.db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()

    async def _settle(self, row, *, reason=None, returned=None, transition=None, agent_id="a1"):
        return await _settle_lost_resident_when_no_transition(
            self.db, agent_id, row, _Req(reason), NOW, returned, transition)

    async def test_a_managed_worker_rests_COLD_STARTABLE_not_stopped(self):
        """The operator-reported defect, asserted as the state itself rather than as a label.

        `status='active'` derives `available` with no live worker, and `launch_mode='detached'` is
        what lets the next send cold-start a fresh session. A `stopped` here is the failure that
        stranded a hermes team: the send-gate refuses it outright and nothing ever wakes the agent.
        """
        row = await self._agent(session_mode="managed")
        returned, transition = await self._settle(row)
        self.assertEqual("managed_worker_lost_available", transition)
        self.assertEqual("active", returned["status"])
        self.assertEqual("detached", returned["launch_mode"])
        self.assertNotEqual("stopped", returned["status"])

    async def test_a_resident_with_no_managed_backing_IS_stopped(self):
        """The other half. A resident that lost its runtime has nothing left to wake."""
        row = await self._agent(session_mode="resident")
        returned, transition = await self._settle(row)
        self.assertEqual("resident_to_stopped", transition)
        self.assertEqual("stopped", returned["status"])
        self.assertEqual("none", returned["launch_mode"])

    async def test_an_agent_with_no_session_mode_at_all_is_treated_as_resident(self):
        """Failing toward `stopped` is the safe direction here.

        Only a literal `managed` earns the cold-startable state. Treating an unknown mode as managed
        would leave an agent advertising availability that nothing can actually start.
        """
        row = await self._agent(session_mode=None)
        _, transition = await self._settle(row)
        self.assertEqual("resident_to_stopped", transition)

    async def test_the_session_mode_comparison_ignores_case_and_padding(self):
        for mode in ("MANAGED", " managed ", "Managed"):
            with self.subTest(mode=mode):
                await self.db.execute("DELETE FROM agents")
                row = await self._agent(session_mode=mode)
                _, transition = await self._settle(row)
                self.assertEqual("managed_worker_lost_available", transition)

    async def test_an_EXISTING_transition_is_left_completely_alone(self):
        """The guard. If the auto-return already moved the agent, this block must not touch it."""
        row = await self._agent(session_mode="managed")
        before = await (await self.db.execute("SELECT * FROM agents WHERE id = 'a1'")).fetchone()
        returned, transition = await self._settle(
            row, returned="untouched", transition="resident_to_managed")
        self.assertEqual("resident_to_managed", transition)
        self.assertEqual("untouched", returned, "the caller's row must be handed straight back")
        after = await (await self.db.execute("SELECT * FROM agents WHERE id = 'a1'")).fetchone()
        self.assertEqual(before["status"], after["status"])
        self.assertEqual(before["status_note"], after["status_note"])

    async def test_the_operators_reason_reaches_the_status_note(self):
        row = await self._agent(session_mode="resident")
        returned, _ = await self._settle(row, reason="wrapper crashed on host b")
        self.assertIn("wrapper crashed on host b", returned["status_note"])

    async def test_a_managed_note_says_it_will_come_back(self):
        """Advisory text an operator acts on: the difference between "dead" and "will restart"."""
        row = await self._agent(session_mode="managed")
        returned, _ = await self._settle(row, reason="gateway port dead")
        self.assertIn("gateway port dead", returned["status_note"])
        self.assertIn("cold-start", returned["status_note"])

    async def test_each_mode_has_its_own_default_reason(self):
        for mode, expected in (("managed", "runtime/gateway lost"), ("resident", "no managed backing")):
            with self.subTest(mode=mode):
                await self.db.execute("DELETE FROM agents")
                row = await self._agent(session_mode=mode)
                returned, _ = await self._settle(row, reason=None)
                self.assertIn(expected, returned["status_note"])

    async def test_an_enormous_reason_cannot_overflow_the_note(self):
        """`status_note` is rendered in the dashboard and read by operators; both paths clamp it."""
        for mode in ("managed", "resident"):
            with self.subTest(mode=mode):
                await self.db.execute("DELETE FROM agents")
                row = await self._agent(session_mode=mode)
                returned, _ = await self._settle(row, reason="x" * 5000)
                self.assertLessEqual(len(returned["status_note"]), 500)

    async def test_the_row_handed_back_is_RE_READ_not_the_stale_one(self):
        """Every caller of this reports the agent from `returned`.

        Handing back the row that was passed in would report the state from BEFORE the update, so
        the response would describe an agent that no longer exists. Nothing raises; the dashboard
        just shows the old status.
        """
        row = await self._agent(session_mode="resident")
        self.assertEqual("active", row["status"])
        returned, _ = await self._settle(row)
        self.assertEqual("stopped", returned["status"])


if __name__ == "__main__":
    unittest.main()
