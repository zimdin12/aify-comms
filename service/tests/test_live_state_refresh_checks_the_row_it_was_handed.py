"""A caller-supplied agent row has to be the row for the agent being refreshed.

`_refresh_agent_live_state(db, agent_id, agent_row=...)` takes a row the caller already holds so a
loop over the fleet does not re-select each row it is already iterating. That optimisation carries a
hazard the query never had: the function writes the derived live-state cache under `agent_id`, so a
row belonging to a DIFFERENT agent would file one agent's status under another's key. No exception,
no log, just a wrong status served to the dashboard and to every claim-deliverability check that
funnels through the same derivation.

Every caller today is correctly keyed -- the batch refresh uses `rows_by_id[aid]`, and the two
analytics loops pass the row they are iterating -- so this guards the next caller rather than a
present bug. That is exactly when the guard is cheap to add and impossible to add later without first
paying for the incident.

The guard falls back to the QUERY rather than raising. Re-reading is always correct, and a status
derivation reached by claim gates and write endpoints is a poor place to turn a caller's mistake into
a 500.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase


class LiveStateRefreshChecksTheRowTests(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    def setUp(self) -> None:
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": "linux:test-host:default", "machineId": "linux:test-host", "os": "linux",
            "kind": "linux", "bridgeId": "bridge-a", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        # Two agents that derive DIFFERENTLY. A managed agent resolves through the environment and
        # worker checks; a message-only resident does not. If the two derived the same state, handing
        # one row in place of the other would be undetectable and this file would prove nothing.
        managed = self.client.post("/api/v1/agents", json={
            "agentId": "row-guard-managed", "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "machineId": "linux:test-host", "bridgeId": "bridge-a",
            "capabilities": ["managed-run"],
        })
        self.assertEqual(managed.status_code, 200, managed.text)
        resident = self.client.post("/api/v1/agents", json={
            "agentId": "row-guard-resident", "role": "tester", "runtime": "codex",
            "sessionMode": "resident", "machineId": "linux:other-host",
        })
        self.assertEqual(resident.status_code, 200, resident.text)

    #: THE CLOCK IS FROZEN, because every assertion in this file compares two derived states for
    #: equality and the derived state carries `updated_at`, stamped from `service.clock.now()` at
    #: ONE-SECOND resolution. Two calls that straddle a second boundary differ in that field and
    #: nothing else -- so the comparison was of the clock, not of the row that was handed over. It
    #: failed exactly that way inside the full suite on 2026-08-27 while passing alone, because the
    #: gap between the two calls widens under load.
    #:
    #: It also repaired the file's own control. `test_the_two_agents_derive_differently` asserts the
    #: two agents derive DIFFERENTLY, and a differing timestamp satisfied that on its own -- so the
    #: one check standing between this file and vacuity could not fail. Frozen, it compares statuses.
    FROZEN_NOW = "2026-08-27T00:00:00Z"

    @classmethod
    def _refresh(cls, agent_id: str, *, agent_row=None):
        from service.api_core.status_refresh import _refresh_agent_live_state
        from service.db import get_db
        from service.reconcilers import status_cache

        async def go():
            status_cache._LIVE_STATE_CACHE.clear()
            db = await get_db()
            try:
                row = None
                if agent_row is not None:
                    cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_row,))
                    row = await cursor.fetchone()
                return await _refresh_agent_live_state(
                    db, agent_id, agent_row=row, now=cls.FROZEN_NOW,
                )
            finally:
                await db.close()

        return asyncio.run(go())

    def test_the_two_agents_derive_differently(self) -> None:
        """The control. If both agents produced the same live state, every assertion below would hold
        no matter which row the refresh used, and this file would be decoration."""
        managed = self._refresh("row-guard-managed")
        resident = self._refresh("row-guard-resident")
        self.assertIsNotNone(managed, "the managed agent produced no live state at all")
        self.assertIsNotNone(resident, "the resident agent produced no live state at all")
        self.assertNotEqual(
            managed, resident,
            "both agents derived an identical live state, so handing one row in place of the other "
            "cannot be detected and this test proves nothing",
        )

    def test_a_row_for_another_agent_is_refused_and_the_real_row_used(self) -> None:
        with_wrong_row = self._refresh("row-guard-managed", agent_row="row-guard-resident")
        correct = self._refresh("row-guard-managed")
        self.assertEqual(
            with_wrong_row, correct,
            "passing another agent's row changed the live state computed for this one, so the "
            "cache would be filed under the wrong agent",
        )

    def test_the_right_row_is_still_used_when_it_matches(self) -> None:
        """The guard must not throw the optimisation away: a correctly-keyed row still short-circuits
        the re-read, which is the whole reason the parameter exists."""
        handed = self._refresh("row-guard-managed", agent_row="row-guard-managed")
        queried = self._refresh("row-guard-managed")
        self.assertEqual(handed, queried)
        self.assertIsNotNone(handed)


if __name__ == "__main__":
    import unittest

    unittest.main()
