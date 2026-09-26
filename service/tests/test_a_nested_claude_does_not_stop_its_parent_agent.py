"""A `claude` an agent runs from its own shell does not leave that agent stopped (sc-manager, 2026-09-26).

sc-manager ran `claude mcp list` and `claude -p` from its Bash tool. Each child inherited the agent's
identity (`AIFY_AGENT_ID`, `CLAUDE_SESSION_ID`), so its aify-comms bridge registered as sc-manager with
the same session handle and took over by the same-session relaunch rule. Seconds later the child exited,
its bridge reported resident-lost as the current owner, and the service set the agent `stopped` with
`launch_mode='none'`. The real bridge kept beating, was ignored as superseded, and 37 messages over
2.5 hours created no run until the operator had the agent re-register.

A bridge that took over a same-handle bridge which is still alive is not the session's last process:
its loss hands ownership back instead of stopping the agent. Driven through the real endpoints.
"""

from __future__ import annotations

import json
import sqlite3

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT = "nested-parent"
HANDLE = "502989ba-6e2f-4358-95e0-1a4b340c2579"


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

    def _lost(self, bridge_id: str) -> dict:
        response = self.client.post(
            f"/api/v1/agents/{AGENT}/resident-lost",
            json={"bridgeId": bridge_id, "machineId": "win32:test-host", "reason": "Resident *-aify session closed cleanly"},
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
        return self._fetchone("SELECT status, launch_mode, runtime_state FROM agents WHERE id = ?", (AGENT,))

    def _superseded_by(self, bridge_id: str) -> str:
        return self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id = ?", (bridge_id,))["superseded_by"]

    def _nested_takes_over(self):
        self._register("real-bridge")
        self._register("nested-bridge")
        self.assertEqual(self._superseded_by("real-bridge"), "nested-bridge", "precondition: the same-session takeover ran")

    def _age_the_real_bridge(self):
        self._execute("UPDATE bridge_instances SET last_seen = '2026-01-01T00:00:00Z' WHERE id = 'real-bridge'")

    def test_the_nested_bridges_exit_hands_the_agent_back_to_the_live_one(self):
        self._nested_takes_over()
        self._lost("nested-bridge")
        agent = self._agent()
        self.assertNotEqual(agent["status"], "stopped")
        self.assertNotEqual(agent["launch_mode"], "none")
        self.assertEqual(json.loads(agent["runtime_state"])["bridgeInstanceId"], "real-bridge")
        self.assertEqual(self._superseded_by("real-bridge"), "")
        self.assertNotIn("ignored", self._beat("real-bridge"), "the live bridge's beats must count again")

    def test_a_long_nested_run_is_handed_back_because_the_real_bridge_kept_beating(self):
        """A `claude -p` can outlive the lease; the beats the real bridge sent while superseded prove it lives."""
        self._nested_takes_over()
        self._age_the_real_bridge()
        self.assertEqual(self._beat("real-bridge").get("reason"), "bridge_superseded", "still ignored while superseded")
        self._lost("nested-bridge")
        self.assertNotEqual(self._agent()["status"], "stopped")
        self.assertEqual(json.loads(self._agent()["runtime_state"])["bridgeInstanceId"], "real-bridge")

    def test_CONTROL_a_relaunched_session_that_closes_still_stops_the_agent(self):
        """A real relaunch: the prior bridge was killed and never beats again, so the new one is the last."""
        self._nested_takes_over()
        self._age_the_real_bridge()
        self._lost("nested-bridge")
        self.assertEqual(self._agent()["status"], "stopped")
        self.assertEqual(self._superseded_by("real-bridge"), "nested-bridge")

    def test_CONTROL_a_takeover_of_a_different_session_is_not_handed_back(self):
        """The handback is for one session held by two processes; a forced takeover by another session is not that."""
        self._register("real-bridge")
        self._register("other-session-bridge", handle="another-session", force=True)
        self.assertEqual(self._superseded_by("real-bridge"), "other-session-bridge", "precondition: the forced takeover ran")
        self._lost("other-session-bridge")
        self.assertEqual(self._agent()["status"], "stopped")
