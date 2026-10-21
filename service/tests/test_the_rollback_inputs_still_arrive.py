"""The rollback of the 0.9.8 switch stays ready while the switched service runs (plan page 3, "The rollback").

What reverts is the SW5 commit alone, over the same database, with nobody relaunched. That is a rollback only if the
legacy readers it restores find their inputs current, so the legacy write routes must keep applying while the
switched readers ignore them. Page 3's test, step by step:

  1. a turn starts after the cutover, through the routes the launchers still post to;
  2. the legacy readers are asked over that same database: the hold and its release (`turn_busy_decision`), the
     word (`legacy_status`, the derivation every refresh still runs), and run ownership (the claim's rows);
  3. the switched service meanwhile serves aify-env's word and gates on aify-env's busy, never on those tables.

The revert itself is replayed by scripts/sw5_rollback_replay.py (SW5 review R6): the switched service writes a private
database across the cutover, and the REVERTED service, a separate interpreter importing the reverted checkout, holds,
queues, releases and claims over that same file, with the ownership the switched claim wrote. It needs a reverted
checkout, so it runs beside the suite rather than in it.
"""
from __future__ import annotations

import asyncio
import sqlite3

from service.api_core.claim_gating import turn_busy_decision
from service.db import get_db
from service.reconcilers.status_cache import _live_state_get
from service.tests import env_records
from service.tests._base import FastApiTestCase

AGENT = "rb-claude"
BRIDGE = f"bridge-{AGENT}"
MACHINE = env_records.TEST_HOST


class TheRollbackInputsStillArrive(FastApiTestCase):
    def setUp(self):
        super().setUp()
        answer = self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "claude-code", "sessionMode": "resident",
            "machineId": MACHINE, "bridgeId": BRIDGE, "launchMode": "detached",
            "capabilities": ["resident-run", "resume", "interrupt"], "runtimeConfig": {"channelEnabled": True}})
        self.assertEqual(answer.status_code, 200, answer.text)
        env_records.report(self, AGENT)  # aify-env: idle. The legacy routes below say otherwise, on purpose.

    def _legacy_holds(self) -> bool:
        async def ask():
            db = await get_db()
            try:
                return (await turn_busy_decision(db, AGENT)).holds
            finally:
                await db.close()
        return asyncio.run(ask())

    def _row(self, query: str):
        conn = sqlite3.connect(str(self._db_path))
        try:
            return conn.execute(query, (AGENT,)).fetchone()
        finally:
            conn.close()

    def _words(self):
        """(served, legacy): the switched word and the one a revert would serve, from one refresh."""
        served = self.client.get(f"/api/v1/agents/{AGENT}").json()["agent"]["status"]
        return served, _live_state_get(AGENT)["legacy_status"]

    def test_a_turn_after_the_cutover_reaches_the_legacy_readers_and_not_the_switched_ones(self):
        self.assertEqual((self._legacy_holds(), self._words()), (False, ("online", "online")), "CONTROL: no turn yet")

        started = self.client.post(f"/api/v1/agents/{AGENT}/turn-start", json={})
        self.assertEqual(started.status_code, 200, started.text)
        self.assertEqual(self._row("SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?")[0], 1)
        self.assertTrue(self._legacy_holds(), "the legacy hold a revert restores must see the turn")
        served, legacy = self._words()
        self.assertEqual(legacy, "working", "the legacy word a revert restores must see the turn")
        self.assertEqual(served, "online", "the switched word is aify-env's, which still says idle")

        self.client.post("/api/v1/agents/" + AGENT + "/turn-end", json={}).raise_for_status()
        self.assertEqual(self._row("SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?")[0], 0)
        self.assertFalse(self._legacy_holds(), "the legacy release a revert restores must see the turn end")
        self.assertEqual(self._words(), ("online", "online"))

    def test_the_switched_gate_ignores_a_legacy_turn_and_the_claim_writes_the_ownership_rows_legacy_reads(self):
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "INSERT INTO dispatch_runs (id, from_agent, target_agent, dispatch_mode, execution_mode, message_type, "
                "subject, body, priority, status, requested_at) VALUES ('rb-run', 'dashboard', ?, 'start_if_possible', "
                "'resident', 'info', 's', 'b', 'normal', 'queued', datetime('now'))", (AGENT,))
            conn.commit()
        finally:
            conn.close()
        self.client.post(f"/api/v1/agents/{AGENT}/turn-start", json={}).raise_for_status()
        self.assertTrue(self._legacy_holds(), "CONTROL: a revert would hold this claim")
        claim = self.client.post("/api/v1/dispatch/claim", json={
            "agentId": AGENT, "bridgeId": f"channel-{MACHINE}-{AGENT}", "bridgeKind": "channel-sidecar",
            "machineId": MACHINE, "executionModes": ["channel", "resident"]})
        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertEqual((claim.json().get("run") or {}).get("id"), "rb-run", claim.text)
        status, bridge = self._row("SELECT status, claim_bridge_id FROM dispatch_runs WHERE target_agent = ?")
        self.assertEqual((status, bridge), ("claimed", f"channel-{MACHINE}-{AGENT}"),
                         "run ownership is written where the legacy readers look for it")
