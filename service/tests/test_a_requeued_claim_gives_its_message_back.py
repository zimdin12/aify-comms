"""A requeued claim gives back the read receipt its claim wrote, so the message is unread until delivered.

v0.7.1 review (comms-senior-dev, W02). A claim writes a read receipt for each source message with
`read_at = claimed_at`. Both requeue paths cleared `claimed_at` and left the receipt, so:

    claim at t1 (receipt t1) -> requeue (claimed_at cleared, receipt t1 kept)
    -> re-claim at t2 (INSERT OR IGNORE keeps the t1 receipt) -> the run fails before starting
    -> the release sweep matches read_at = t2 and deletes nothing

and the message the target never received stayed read for good. `claim_receipts.py` named this as
residue it could not recover. The requeue is the last moment the claim time is known, so the receipt
is given back there, in the same transaction as the requeue.

A receipt the agent EARNED (a read at any other time) is never touched: the claim's INSERT OR IGNORE
left it with its own `read_at`, which cannot equal the claim time being released.
"""

import asyncio

from service.api_core.recovery_writes import _requeue_instead_of_failing_undelivered_claim
from service.db import get_db
from service.reconcilers.dispatch_queue import _requeue_orphaned_claimed_runs
from service.tests._base import FastApiTestCase

CLAIMED_AT = "2020-01-01T00:00:00Z"
EARNED_AT = "2019-12-31T00:00:00Z"


class ARequeuedClaimGivesItsMessageBackTests(FastApiTestCase):
    DB_NAME = "aify-requeued-claim-receipts.db"

    def _run(self, work):
        async def scenario():
            db = await get_db()
            try:
                result = await work(db)
                await db.commit()
                return result
            finally:
                await db.close()
        return asyncio.run(scenario())

    def _seed(self, run_id="run-1"):
        async def seed(db):
            await db.execute(
                "INSERT INTO agents (id, name, role, runtime, session_mode, status, registered_at, last_seen) "
                "VALUES ('target','t','coder','claude-code','managed','idle',?,?)", (CLAIMED_AT, CLAIMED_AT))
            for message_id in ("m-claimed", "m-earned"):
                await db.execute(
                    "INSERT INTO messages (id, from_agent, to_agent, subject, body, timestamp) "
                    "VALUES (?, 'sender', 'target', 's', 'b', 1)", (message_id,))
            await db.execute(
                "INSERT INTO dispatch_runs (id, from_agent, target_agent, status, require_reply, runtime, "
                "message_id, claim_bridge_id, claim_machine_id, claimed_at, requested_at) "
                "VALUES (?, 'sender', 'target', 'claimed', 1, 'claude-code', 'm-claimed', "
                "'dead-bridge', 'win32:test', ?, ?)", (run_id, CLAIMED_AT, CLAIMED_AT))
            await db.execute("INSERT INTO read_receipts (message_id, agent_id, read_at) "
                             "VALUES ('m-claimed', 'target', ?)", (CLAIMED_AT,))
            await db.execute("INSERT INTO read_receipts (message_id, agent_id, read_at) "
                             "VALUES ('m-earned', 'target', ?)", (EARNED_AT,))
        self._run(seed)

    def _unread(self):
        async def read(db):
            cursor = await db.execute(
                "SELECT m.id FROM messages m LEFT JOIN read_receipts r ON r.message_id = m.id AND r.agent_id = ? "
                "WHERE m.to_agent = ? AND r.message_id IS NULL ORDER BY m.id", ("target", "target"))
            return [row["id"] for row in await cursor.fetchall()]
        return self._run(read)

    def test_control_before_any_requeue_both_messages_read(self):
        self._seed()
        self.assertEqual(self._unread(), [])

    def test_the_failing_path_that_requeues_instead_gives_the_message_back(self):
        self._seed()
        requeued = self._run(lambda db: _requeue_instead_of_failing_undelivered_claim(db, "run-1", reason="stale"))
        self.assertTrue(requeued, "control: the run was requeued")
        self.assertEqual(self._unread(), ["m-claimed"], "the claim's receipt outlived the requeue")

    def test_the_orphan_sweep_gives_the_message_back(self):
        self._seed()
        requeued = self._run(lambda db: _requeue_orphaned_claimed_runs(db, grace_seconds=0))
        self.assertEqual([r["runId"] for r in requeued], ["run-1"], "control: the orphan sweep requeued it")
        self.assertEqual(self._unread(), ["m-claimed"], "the claim's receipt outlived the requeue")
