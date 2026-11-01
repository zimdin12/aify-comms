"""Remove of an agent that is not registered writes nothing (action audit #7, 2026-11-02).

The route used to tombstone any id it was asked to remove, so a typo or a case variant left a tombstone that refuses
that name's registration from then on (410), under an answer that said nothing was removed. Its runs were cancelled
too. A never-existed id, a case variant of a real one and a repeat removal now answer the same `ok: false` and write
no tombstone and cancel no work; a real removal is unchanged.
"""
from __future__ import annotations

import sqlite3

from service.tests._base import FastApiTestCase


class RemovingAnIdThatIsNotThereWritesNothing(FastApiTestCase):
    def sql(self, query, params=()):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in conn.execute(query, params)]
            conn.commit()
            return rows
        finally:
            conn.close()

    def register(self, agent_id):
        self.client.post("/api/v1/agents", json={"agentId": agent_id, "role": "coder", "runtime": "claude-code",
                                                 "sessionMode": "resident"}).raise_for_status()

    def queued_run_for(self, agent_id):
        self.sql("INSERT INTO dispatch_runs (id, from_agent, target_agent, status, requested_at) "
                 "VALUES (?, 'operator', ?, 'queued', '2026-11-01T00:00:00Z')", (f"run-{agent_id}", agent_id))

    def tombstones(self):
        """Whole rows, not ids: a rewrite of the same id (INSERT OR REPLACE) changes its time and metadata."""
        return self.sql("SELECT * FROM agent_tombstones ORDER BY agent_id")

    def mark_tombstone(self, agent_id):
        """Give an existing tombstone values no removal writes, so any rewrite of it shows."""
        self.sql("UPDATE agent_tombstones SET removed_at = '2000-01-01T00:00:00Z', removed_by = 'sentinel', "
                 "bridge_id = 'bridge-sentinel', reason = 'sentinel' WHERE agent_id = ?", (agent_id,))

    def assert_nothing_written(self, agent_id):
        before_runs = self.sql("SELECT id, status FROM dispatch_runs ORDER BY id")
        before_tombstones = self.tombstones()
        answer = self.client.delete(f"/api/v1/agents/{agent_id}")
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertFalse(answer.json()["ok"], "a removal that removed nothing must say so")
        self.assertEqual(self.tombstones(), before_tombstones, f"removing {agent_id!r} wrote a tombstone")
        self.assertEqual(self.sql("SELECT id, status FROM dispatch_runs ORDER BY id"), before_runs, "it cancelled work")

    def test_an_id_that_never_existed_is_not_tombstoned_and_can_still_register(self):
        self.queued_run_for("ghost")
        self.assert_nothing_written("ghost")
        registered = self.client.post("/api/v1/agents", json={"agentId": "ghost", "role": "coder", "runtime": "claude-code"})
        self.assertEqual(registered.status_code, 200, registered.text)

    def test_a_case_variant_of_a_real_agent_removes_and_blocks_nothing(self):
        self.register("coder")
        self.assert_nothing_written("Coder")
        self.assertEqual(self.sql("SELECT id FROM agents WHERE id = 'coder'"), [{"id": "coder"}], "the real agent went")

    def test_a_repeat_removal_writes_nothing_more(self):
        self.register("twice")
        self.assertTrue(self.client.delete("/api/v1/agents/twice").json()["ok"])
        self.assertEqual([row["agent_id"] for row in self.tombstones()], ["twice"])
        self.mark_tombstone("twice")
        self.assert_nothing_written("twice")

    def test_a_removal_that_loses_to_a_competing_one_before_its_lock_writes_nothing(self):
        """Review of 8b488416: the route saw the agent, then a competing removal deleted it and wrote its tombstone
        before this one took the removal's write lock. The loser must leave the winner's tombstone and the work as they
        are; the presence check that decides it runs in the transaction that would delete."""
        import service.routers.agents.identity as identity

        self.register("contested")
        self.queued_run_for("contested")
        real = identity.remove_agent

        async def competitor_wins_first(db, agent_id, **kwargs):
            self.sql("DELETE FROM agents WHERE id = ?", (agent_id,))
            self.sql("INSERT INTO agent_tombstones (agent_id, removed_at, removed_by, bridge_id, reason) "
                     "VALUES (?, '2000-01-01T00:00:00Z', 'winner', 'bridge-winner', 'winner')", (agent_id,))
            self.sql("UPDATE dispatch_runs SET status = 'cancelled' WHERE target_agent = ?", (agent_id,))
            return await real(db, agent_id, **kwargs)

        identity.remove_agent = competitor_wins_first
        self.addCleanup(setattr, identity, "remove_agent", real)
        answer = self.client.delete("/api/v1/agents/contested")
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertFalse(answer.json()["ok"], "the loser removed nothing and must say so")
        self.assertEqual(self.tombstones(), [{"agent_id": "contested", "removed_at": "2000-01-01T00:00:00Z",
                                              "removed_by": "winner", "bridge_id": "bridge-winner", "reason": "winner"}],
                         "the losing removal rewrote the winner's tombstone")

    def test_CONTROL_a_real_removal_still_tombstones_and_cancels_its_work(self):
        self.register("leaving")
        self.queued_run_for("leaving")
        answer = self.client.delete("/api/v1/agents/leaving")
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertTrue(answer.json()["ok"])
        self.assertEqual([row["agent_id"] for row in self.tombstones()], ["leaving"])
        self.assertNotEqual(self.sql("SELECT status FROM dispatch_runs WHERE id = 'run-leaving'"), [{"status": "queued"}])
