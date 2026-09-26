"""A `claude` an agent runs from its own shell does not leave that agent stopped (sc-manager, 2026-09-26).

sc-manager ran `claude mcp list` and `claude -p` from its Bash tool. Each child inherited the agent's
identity (`AIFY_AGENT_ID`, `CLAUDE_SESSION_ID`), so its aify-comms bridge registered as sc-manager with
the same session handle and took over by the same-session relaunch rule. Seconds later the child exited,
its bridge reported resident-lost as the current owner, and the service set the agent `stopped` with
`launch_mode='none'`. The real bridge kept beating, was ignored as superseded, and 37 messages over
2.5 hours created no run until the operator had the agent re-register.

Now the loss of a bridge that took over a same-handle session writes `offline`, not a stop, and a beat
from the session's own bridge takes ownership back without touching status. A predecessor killed by a
real relaunch never beats, so nothing is handed back. Every explicit stop stands, whatever route wrote
it, because the reclaim never writes status and heartbeats leave a stopped agent stopped. Driven
through the real endpoints.
"""

from __future__ import annotations

import json
import sqlite3

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT = "nested-parent"
HANDLE = "502989ba-6e2f-4358-95e0-1a4b340c2579"
DASHBOARD_STOP_NOTE = "Resident session stop requested from dashboard; live bridge should terminate the CLI host."


class ANestedClaudeDoesNotStopItsParentAgentTests(FastApiTestCase):
    DB_NAME = "aify-nested-claude.db"

    def _register(self, bridge_id: str, *, handle: str = HANDLE, force: bool = False):
        body = {
            "agentId": AGENT, "role": "manager", "runtime": "claude-code", "sessionMode": "resident",
            "launchMode": "detached", "sessionHandle": handle, "machineId": "win32:test-host",
            "bridgeId": bridge_id, "capabilities": ["resident-run", "resume", "interrupt"],
        }
        if force:
            body["force"] = True
        response = self.client.post("/api/v1/agents", json=body)
        self.assertEqual(response.status_code, 200, response.text)

    def _beat(self, bridge_id: str) -> dict:
        response = self.client.post(f"/api/v1/agents/{AGENT}/heartbeat", json={"bridgeId": bridge_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _lost(self, bridge_id: str, reason: str = "Resident *-aify session closed cleanly") -> dict:
        response = self.client.post(
            f"/api/v1/agents/{AGENT}/resident-lost",
            json={"bridgeId": bridge_id, "machineId": "win32:test-host", "reason": reason},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _fetchone(self, sql: str, params=()):
        db = sqlite3.connect(self._db_path)
        try:
            db.row_factory = sqlite3.Row
            return db.execute(sql, params).fetchone()
        finally:
            db.close()

    def _execute(self, sql: str, params=()) -> None:
        db = sqlite3.connect(self._db_path)
        try:
            db.execute(sql, params)
            db.commit()
        finally:
            db.close()

    def _agent(self):
        return self._fetchone("SELECT status, launch_mode, status_note, runtime_state FROM agents WHERE id = ?", (AGENT,))

    def _owner(self) -> str:
        return json.loads(self._agent()["runtime_state"] or "{}").get("bridgeInstanceId", "")

    def _superseded_by(self, bridge_id: str) -> str:
        return self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id = ?", (bridge_id,))["superseded_by"]

    def _nested_takes_over(self):
        self._register("real-bridge")
        self._register("nested-bridge")
        self.assertEqual(self._superseded_by("real-bridge"), "nested-bridge", "precondition: the same-session takeover ran")

    def _operator_stop(self):
        stopped = self.client.post(f"/api/v1/agents/{AGENT}/control", json={"action": "stop", "from_agent": "dashboard"})
        self.assertEqual(stopped.status_code, 200, stopped.text)

    def _assert_stays_stopped(self):
        """The explicit stop stands after the real bridge beats, whoever owns the session now."""
        self._beat("real-bridge")
        self.assertEqual(self._agent()["status"], "stopped")

    # -- the incident ---------------------------------------------------------------------------------

    def test_the_loss_writes_no_stop_and_the_real_bridges_beat_takes_the_session_back(self):
        self._nested_takes_over()
        self.assertEqual(self._lost("nested-bridge")["transition"], "resident_lost_reclaimable")
        agent = self._agent()
        self.assertNotEqual(agent["status"], "stopped", "the nested bridge's exit must not stop the agent")
        self.assertNotEqual(agent["launch_mode"], "none")
        # A heartbeat with no bridge rewrites `status` to its current value, as every live beat does.
        self.assertEqual(self.client.post(f"/api/v1/agents/{AGENT}/heartbeat", json={}).status_code, 200)
        self.assertNotIn("ignored", self._beat("real-bridge"), "the reclaiming beat is a beat like any other")
        self.assertEqual(self._owner(), "real-bridge")
        self.assertEqual(self._superseded_by("real-bridge"), "")
        self.assertNotIn(self._agent()["status"], ("stopped", "offline"))

    def test_a_long_nested_run_is_reclaimed_after_it_ends_not_during_it(self):
        self._nested_takes_over()
        self.assertEqual(self._beat("real-bridge").get("reason"), "bridge_superseded", "the nested bridge still owns it")
        self.assertEqual(self._owner(), "nested-bridge")
        self._lost("nested-bridge")
        self._beat("real-bridge")
        self.assertEqual(self._owner(), "real-bridge")

    # -- nothing is handed back ----------------------------------------------------------------------

    def test_CONTROL_a_quick_relaunch_whose_session_closes_at_once_hands_nothing_back(self):
        """The predecessor was killed by the relaunch: its last beat is seconds old but it never beats
        again, so ownership stays with the closed session (review of d51472e3)."""
        self._nested_takes_over()
        self._lost("nested-bridge")
        self.assertEqual(self._superseded_by("real-bridge"), "nested-bridge")
        self.assertEqual(self._owner(), "nested-bridge")
        self.assertEqual(self._agent()["status"], "offline")

    def test_CONTROL_a_takeover_by_a_different_session_is_not_reclaimed(self):
        self._register("real-bridge")
        self._register("other-session-bridge", handle="another-session", force=True)
        self.assertEqual(self._superseded_by("real-bridge"), "other-session-bridge", "precondition: the forced takeover ran")
        self.assertEqual(self._lost("other-session-bridge")["transition"], "resident_to_stopped")
        self.assertEqual(self._beat("real-bridge").get("reason"), "bridge_superseded")
        self.assertEqual(self._agent()["status"], "stopped")

    def test_CONTROL_a_registration_after_the_offer_keeps_the_session(self):
        self._nested_takes_over()
        self._lost("nested-bridge")
        self._register("relaunched-bridge")
        self.assertEqual(self._beat("real-bridge").get("reason"), "bridge_superseded")
        self.assertEqual(self._owner(), "relaunched-bridge")

    def test_CONTROL_ownership_moved_without_a_registration_keeps_the_new_owner(self):
        self._nested_takes_over()
        self._lost("nested-bridge")
        state = json.loads(self._agent()["runtime_state"])
        self._execute("UPDATE agents SET runtime_state = ? WHERE id = ?", (json.dumps(dict(state, bridgeInstanceId="elsewhere")), AGENT))
        self.assertEqual(self._beat("real-bridge").get("reason"), "bridge_superseded")
        self.assertEqual(self._owner(), "elsewhere")

    # -- every explicit stop stands, whatever its route ----------------------------------------------

    def test_CONTROL_a_stop_before_the_loss_stays(self):
        """The operator stopped the agent while the nested bridge owned it (review of 17fd7b85)."""
        self._nested_takes_over()
        self._operator_stop()
        self.assertEqual(self._lost("nested-bridge")["transition"], "resident_to_stopped")
        self.assertIsNone(self._fetchone("SELECT handback_offer FROM bridge_instances WHERE id = 'real-bridge'")["handback_offer"])
        self._assert_stays_stopped()
        self.assertEqual(self._agent()["launch_mode"], "none")

    def test_CONTROL_an_agent_stop_after_the_loss_stays(self):
        self._nested_takes_over()
        self._lost("nested-bridge")
        self._operator_stop()
        self._assert_stays_stopped()
        self.assertEqual(self._agent()["launch_mode"], "none")

    def test_CONTROL_a_session_stop_after_the_loss_stays(self):
        """The dashboard's Session Stop is a second stop route (review of 486c8262)."""
        self._execute(
            "INSERT INTO environments(id, machine_id, bridge_id, registered_at, last_seen) VALUES (?,?,?,?,?)",
            ("env-test", "win32:test-host", "env-bridge", "2026-09-26T00:00:00Z", "2099-01-01T00:00:00Z"),
        )
        self._nested_takes_over()
        session = self._fetchone("SELECT id FROM agent_sessions WHERE agent_id = ?", (AGENT,))
        self.assertIsNotNone(session, "precondition: registration created a resident session")
        self._lost("nested-bridge")
        stopped = self.client.post(f"/api/v1/sessions/{session['id']}/control", json={"action": "stop", "from_agent": "dashboard"})
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self._assert_stays_stopped()
        self.assertIn("from dashboard", self._agent()["status_note"])

    def test_CONTROL_a_loss_reason_equal_to_the_dashboard_note_then_a_real_stop_stays(self):
        """The loss's reason is the caller's and can equal the dashboard's note (review of 17fd7b85)."""
        self._nested_takes_over()
        self._lost("nested-bridge", reason=DASHBOARD_STOP_NOTE)
        self._operator_stop()
        self._assert_stays_stopped()

    def test_CONTROL_a_status_patch_after_the_loss_stays(self):
        """A status PATCH writes status and note (review of 5dc23997)."""
        self._nested_takes_over()
        self._lost("nested-bridge")
        patched = self.client.patch(f"/api/v1/agents/{AGENT}", json={"status": "stopped", "note": "held by the operator"})
        self.assertEqual(patched.status_code, 200, patched.text)
        self._assert_stays_stopped()

    def test_CONTROL_a_run_patch_that_stops_the_agent_after_the_loss_stays(self):
        """A run PATCH writes `status` alone (review of 47e71270)."""
        self._nested_takes_over()
        self._execute(
            "INSERT INTO dispatch_runs(id, from_agent, target_agent, requested_at, status) VALUES (?,?,?,?,?)",
            ("run-before-loss", "operator", AGENT, "2026-09-26T00:00:00Z", "running"),
        )
        self._lost("nested-bridge")
        patched = self.client.patch("/api/v1/dispatch/runs/run-before-loss", json={"agentStatus": "stopped"})
        self.assertEqual(patched.status_code, 200, patched.text)
        self._assert_stays_stopped()
