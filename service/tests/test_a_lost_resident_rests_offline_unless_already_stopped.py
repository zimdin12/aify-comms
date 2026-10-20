"""A resident whose runtime is lost rests `offline`, unless something had already stopped it (B2, path 1).

A resident closing its own CLI is not an operator stop. aify-env publishes such a resident `offline`, and comms wrote
`stopped` with `launch_mode='none'`, the operator's stop markers, so the two disagreed for every resident that ever
exited (sc-manager and mc-manager, 2026-10-17/18). A stop already on the row predates the loss and is someone else's:
it stays.

THE STORED FIELDS ARE ASSERTED, NOT ONLY THE SERVED STATUS. Served status reads `stopped` when either marker is set
(`status_inputs.py`), so a test that watched only the served word could not tell a kept `status` from a kept
`launch_mode`. The status-only stop below is the case where losing the prior-stop guard shows.

NO SEND CHANGES. A message to a resident whose bridge is gone is stored without a run whether the row says `stopped`
or `offline`; the disposition is asserted apart from status.
"""

from __future__ import annotations

import sqlite3

from service.routers.api_v2 import router  # noqa: F401 -- the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT = "lost-resident"
MACHINE = "win32:test-host"
REASON = "Resident *-aify session closed cleanly"


class ALostResidentRestsOfflineUnlessAlreadyStopped(FastApiTestCase):
    DB_NAME = "aify-b2-resident-loss.db"

    def _register(self, bridge_id: str = "resident-bridge", *, mode: str = "resident") -> None:
        response = self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "manager", "runtime": "claude-code", "sessionMode": mode,
            "launchMode": "detached", "sessionHandle": "handle-1", "machineId": MACHINE, "bridgeId": bridge_id,
            "capabilities": ["resident-run", "resume", "interrupt"]})
        self.assertEqual(response.status_code, 200, response.text)

    def _lost(self, bridge_id: str = "resident-bridge") -> dict:
        response = self.client.post(f"/api/v1/agents/{AGENT}/resident-lost",
                                    json={"bridgeId": bridge_id, "machineId": MACHINE, "reason": REASON})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _execute(self, sql: str, params=()) -> None:
        db = sqlite3.connect(self._db_path)
        try:
            db.execute(sql, params)
            db.commit()
        finally:
            db.close()

    def _stored(self) -> tuple:
        db = sqlite3.connect(self._db_path)
        try:
            return db.execute("SELECT status, launch_mode, status_note FROM agents WHERE id = ?", (AGENT,)).fetchone()
        finally:
            db.close()

    def _served(self) -> str:
        response = self.client.get(f"/api/v1/agents/{AGENT}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["agent"]["status"]

    def _send(self) -> dict:
        response = self.client.post("/api/v1/messages/send", json={
            "from_agent": "dashboard", "to": AGENT, "type": "request", "subject": "ping", "body": "ping",
            "trigger": True})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_a_loss_rests_the_resident_offline_with_its_launch_mode_kept(self):
        self._register()
        lost = self._lost()
        self.assertEqual(lost["transition"], "resident_to_offline")
        status, launch_mode, note = self._stored()
        self.assertEqual((status, launch_mode), ("offline", "detached"))
        self.assertIn(REASON, note)
        self.assertEqual(self._served(), "offline")

    def test_a_send_to_the_lost_resident_is_stored_without_a_run_as_before(self):
        """The disposition, asserted apart from status: stored, no run, the wake reported as not started."""
        self._register()
        self._lost()
        sent = self._send()
        self.assertTrue(sent["stored"])
        self.assertEqual(sent["dispatchRuns"], [])
        self.assertEqual([hint.get("targetAgentId") for hint in sent["notStarted"]], [AGENT])
        db = sqlite3.connect(self._db_path)
        try:
            runs = db.execute("SELECT COUNT(*) FROM dispatch_runs WHERE target_agent = ?", (AGENT,)).fetchone()[0]
            inbox = db.execute("SELECT COUNT(*) FROM messages WHERE to_agent = ?", (AGENT,)).fetchone()[0]
        finally:
            db.close()
        self.assertEqual((runs, inbox), (0, 1))

    def test_CONTROL_a_status_only_stop_before_the_loss_stays_stopped(self):
        """The prior-stop guard's own witness: nothing but `status` says this agent was stopped."""
        self._register()
        self._execute("UPDATE agents SET status = 'stopped', launch_mode = 'detached' WHERE id = ?", (AGENT,))
        self.assertEqual(self._lost()["transition"], "resident_to_stopped")
        self.assertEqual(self._stored()[0], "stopped")
        self.assertEqual(self._served(), "stopped")

    def test_CONTROL_a_launch_mode_only_stop_before_the_loss_keeps_todays_write(self):
        self._register()
        self._execute("UPDATE agents SET status = 'idle', launch_mode = 'none' WHERE id = ?", (AGENT,))
        self.assertEqual(self._lost()["transition"], "resident_to_stopped")
        status, launch_mode, note = self._stored()
        self.assertEqual((status, launch_mode), ("stopped", "none"))
        self.assertIn(REASON, note)

    def test_CONTROL_a_loss_from_a_bridge_that_is_not_current_changes_nothing(self):
        self._register()
        before = self._stored()
        self.assertEqual(self._lost("some-other-bridge").get("reason"), "bridge_not_current")
        self.assertEqual(self._stored(), before)

    def test_CONTROL_a_managed_agent_rests_cold_startable(self):
        self._register(mode="managed")
        self.assertEqual(self._lost()["transition"], "managed_worker_lost_available")
        self.assertEqual(self._stored()[:2], ("active", "detached"))
