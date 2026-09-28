"""A managed delivery loop restarting does not fail the work it was carrying.

External review of 0.7.6 (F4). The two reapers that fail work nothing can deliver or answer --
`_fail_stranded_delivered_reply_runs` and `_reap_undeliverable_queued_runs` -- both wait
`queued_run_backstop_seconds` first, "so a worker between restarts is not read as gone". But the wait
was measured from the RUN's `requested_at`, and a restart happens at any point in a run's life. A
hermes delivery loop releases its claimer lease on teardown and acquires it again after its first
claim, and a released lease reads "no live claimer" immediately. So a sweep landing in that gap failed
any reply-owed run older than the grace, while the hermes gateway, which the loop does not own, was
still there to answer it.

What a restart changes is the lease: it is released, then acquired. The grace now runs from the
release, so a claimer that left less than the grace ago is between restarts, and one gone longer is
gone.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

from service.db import get_db
from service.reconcilers.dispatch_lifecycle import _fail_stranded_delivered_reply_runs
from service.reconcilers.undeliverable_queued_runs import _reap_undeliverable_queued_runs
from service.tests._base import FastApiTestCase

AGENT = "hermes-restarting"
GRACE = 180  # queued_run_backstop_seconds, the default


def _iso(seconds_ago: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds_ago))


class ADeliveryLoopRestartDoesNotFailReplyOwedWorkTests(FastApiTestCase):
    DB_NAME = "aify-loop-restart-grace.db"

    def setUp(self):
        super().setUp()
        response = self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "hermes", "sessionMode": "managed",
            "machineId": "linux:test-host", "bridgeId": "bridge-current",
            "capabilities": ["native-managed-run", "managed-run", "resume", "interrupt"],
            "runtimeConfig": {"gatewayUrl": "ws://127.0.0.1:9119/api/ws?token=t"},
        })
        self.assertEqual(response.status_code, 200, response.text)

    def _seed(self, *, lease_state: str, lease_changed_seconds_ago: int, run_status: str):
        with sqlite3.connect(self._db_path) as db:
            db.execute("INSERT INTO claimer_leases (agent_id, bridge_id, state, updated_at) VALUES (?,?,?,?)",
                       (AGENT, f"hermes-channel-{AGENT}", lease_state, _iso(lease_changed_seconds_ago)))
            # Ten minutes old, well past the grace: the age that made every such run a candidate.
            db.execute("INSERT INTO dispatch_runs (id, from_agent, target_agent, status, require_reply, runtime, "
                       "requested_at) VALUES ('run-1', 'sender', ?, ?, 1, 'hermes', ?)",
                       (AGENT, run_status, _iso(600)))
        db.close()

    def _status(self) -> str:
        db = sqlite3.connect(self._db_path)
        try:
            return db.execute("SELECT status FROM dispatch_runs WHERE id = 'run-1'").fetchone()[0]
        finally:
            db.close()

    def _sweep(self, reaper):
        async def run():
            db = await get_db()
            try:
                result = await reaper(db, limit=200)
                await db.commit()
                return result
            finally:
                await db.close()
        return asyncio.run(run())

    # ── the reply the gateway still owes ─────────────────────────────────────────────────────

    def test_a_reply_owed_run_survives_a_sweep_that_lands_while_the_loop_restarts(self):
        self._seed(lease_state="released", lease_changed_seconds_ago=5, run_status="delivered")
        self.assertEqual(self._sweep(_fail_stranded_delivered_reply_runs), [])
        self.assertEqual(self._status(), "delivered", "a restart failed the reply the agent still owed")

    def test_CONTROL_a_loop_gone_for_longer_than_the_grace_still_fails_it(self):
        self._seed(lease_state="released", lease_changed_seconds_ago=GRACE + 60, run_status="delivered")
        self.assertEqual([r["runId"] for r in self._sweep(_fail_stranded_delivered_reply_runs)], ["run-1"])
        self.assertEqual(self._status(), "failed")

    def test_CONTROL_a_live_loop_leaves_it_alone(self):
        self._seed(lease_state="acquired", lease_changed_seconds_ago=5, run_status="delivered")
        self.assertEqual(self._sweep(_fail_stranded_delivered_reply_runs), [])
        self.assertEqual(self._status(), "delivered")

    # ── the queued run the restarted loop will claim ─────────────────────────────────────────

    def test_a_queued_run_survives_a_sweep_that_lands_while_the_loop_restarts(self):
        self._seed(lease_state="released", lease_changed_seconds_ago=5, run_status="queued")
        self.assertEqual(self._sweep(_reap_undeliverable_queued_runs), [])
        self.assertEqual(self._status(), "queued", "a restart failed a run the next loop would have claimed")

    def test_CONTROL_a_queued_run_for_a_loop_gone_longer_than_the_grace_is_reaped(self):
        self._seed(lease_state="released", lease_changed_seconds_ago=GRACE + 60, run_status="queued")
        self._sweep(_reap_undeliverable_queued_runs)
        self.assertEqual(self._status(), "failed")
