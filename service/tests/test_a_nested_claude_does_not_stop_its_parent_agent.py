"""A `claude` an agent runs from its own shell does not leave that agent stopped (sc-manager, 2026-09-26).

sc-manager ran `claude mcp list` and `claude -p` from its Bash tool. Each child inherited the agent's
identity (`AIFY_AGENT_ID`, `CLAUDE_SESSION_ID`), so its aify-comms bridge registered as sc-manager with
the same session handle and took over by the same-session relaunch rule. Seconds later the child exited,
its bridge reported resident-lost as the current owner, and the service set the agent `stopped` with
`launch_mode='none'`. The real bridge kept beating, was ignored as superseded, and 37 messages over
2.5 hours created no run until the operator had the agent re-register.

The proof that the real bridge lives is a beat AFTER the nested bridge is lost: that beat reclaims the
session and lifts the stop. A predecessor killed by a real relaunch never beats again, so a relaunched
session that closes, however soon, still stops the agent (0.7.4 review of d51472e3: a freshness window on
the predecessor's older beats handed exactly that case to a dead bridge). Driven through the real endpoints.
"""

from __future__ import annotations

import json
import sqlite3
import unittest

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

    def _assert_reclaimed(self):
        agent = self._agent()
        self.assertNotEqual(agent["status"], "stopped")
        self.assertNotEqual(agent["launch_mode"], "none")
        self.assertEqual(json.loads(agent["runtime_state"])["bridgeInstanceId"], "real-bridge")
        self.assertEqual(self._superseded_by("real-bridge"), "")

    def test_the_real_bridges_next_beat_reclaims_the_session(self):
        self._nested_takes_over()
        self._lost("nested-bridge")
        self.assertEqual(self._agent()["status"], "stopped", "until a beat proves the real bridge lives")
        # A heartbeat with no bridge rewrites `status` to its current value, as every live beat does; the
        # offer must survive it, which is why the write count leaves `status` out.
        self.assertEqual(self.client.post(f"/api/v1/agents/{AGENT}/heartbeat", json={}).status_code, 200)
        self.assertNotIn("ignored", self._beat("real-bridge"), "the reclaiming beat is a beat like any other")
        self._assert_reclaimed()

    def test_a_long_nested_run_is_reclaimed_after_it_ends_not_during_it(self):
        self._nested_takes_over()
        self.assertEqual(self._beat("real-bridge").get("reason"), "bridge_superseded", "the nested bridge still owns it")
        self.assertEqual(json.loads(self._agent()["runtime_state"])["bridgeInstanceId"], "nested-bridge")
        self._lost("nested-bridge")
        self._beat("real-bridge")
        self._assert_reclaimed()

    def test_CONTROL_a_quick_relaunch_whose_session_closes_at_once_stays_stopped(self):
        """The predecessor was killed by the relaunch: its last beat is seconds old but it never beats again."""
        self._nested_takes_over()
        self._lost("nested-bridge")
        self.assertEqual(self._agent()["status"], "stopped")
        self.assertEqual(self._superseded_by("real-bridge"), "nested-bridge")

    def test_CONTROL_a_takeover_by_a_different_session_is_not_reclaimed(self):
        self._register("real-bridge")
        self._register("other-session-bridge", handle="another-session", force=True)
        self.assertEqual(self._superseded_by("real-bridge"), "other-session-bridge", "precondition: the forced takeover ran")
        self._lost("other-session-bridge")
        self.assertEqual(self._beat("real-bridge").get("reason"), "bridge_superseded")
        self.assertEqual(self._agent()["status"], "stopped")

    def test_CONTROL_an_operator_stop_is_not_lifted_by_a_beat(self):
        self._nested_takes_over()
        self._lost("nested-bridge")
        stopped = self.client.post(f"/api/v1/agents/{AGENT}/control", json={"action": "stop", "from_agent": "dashboard"})
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertEqual(self._beat("real-bridge").get("reason"), "bridge_superseded")
        self.assertEqual(self._agent()["status"], "stopped")

    def test_CONTROL_a_registration_after_the_offer_keeps_the_session(self):
        """The operator relaunched before the old bridge beat: the new session owns it, the offer lapses."""
        self._nested_takes_over()
        self._lost("nested-bridge")
        self._register("relaunched-bridge")
        self.assertEqual(self._beat("real-bridge").get("reason"), "bridge_superseded")
        self.assertEqual(json.loads(self._agent()["runtime_state"])["bridgeInstanceId"], "relaunched-bridge")

    def test_CONTROL_a_session_stop_is_not_lifted_by_a_beat(self):
        """The dashboard's Session Stop is a second stop route (review of 486c8262): the offer lapses on the
        state it changed, not on which route changed it."""
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
        self.assertEqual(self._beat("real-bridge").get("reason"), "bridge_superseded")
        agent = self._fetchone("SELECT status, launch_mode, status_note FROM agents WHERE id = ?", (AGENT,))
        self.assertEqual((agent["status"], agent["launch_mode"]), ("stopped", "none"))
        self.assertIn("from dashboard", agent["status_note"])

    def test_CONTROL_ownership_moved_without_a_status_change_keeps_the_new_owner(self):
        """A writer that moves the session (runtime_state) and leaves status, launch mode and note alone is
        invisible to the state check; the owner check is what refuses the reclaim then."""
        self._nested_takes_over()
        self._lost("nested-bridge")
        state = json.loads(self._agent()["runtime_state"])
        self._execute("UPDATE agents SET runtime_state = ? WHERE id = ?", (json.dumps(dict(state, bridgeInstanceId="elsewhere")), AGENT))
        self.assertEqual(self._beat("real-bridge").get("reason"), "bridge_superseded")
        self.assertEqual(json.loads(self._agent()["runtime_state"])["bridgeInstanceId"], "elsewhere")

    def _operator_stop(self):
        stopped = self.client.post(f"/api/v1/agents/{AGENT}/control", json={"action": "stop", "from_agent": "dashboard"})
        self.assertEqual(stopped.status_code, 200, stopped.text)

    def test_CONTROL_a_stop_before_the_loss_is_not_offered_back(self):
        """The operator stopped the agent while the nested bridge owned it: the loss offers nothing, so no
        beat can lift that stop (review of 17fd7b85)."""
        self._nested_takes_over()
        self._operator_stop()
        self._lost("nested-bridge")
        self.assertIsNone(self._fetchone("SELECT handback_offer FROM bridge_instances WHERE id = 'real-bridge'")["handback_offer"])
        self.assertEqual(self._beat("real-bridge").get("reason"), "bridge_superseded")
        self.assertEqual((self._agent()["status"], self._agent()["launch_mode"]), ("stopped", "none"))

    def test_CONTROL_a_later_stop_that_writes_the_same_fields_still_ends_the_offer(self):
        """The loss's reason is the caller's, and it can equal the dashboard's stop note; the offer's token is
        the service's, so a later stop with otherwise identical fields still changes what the offer holds."""
        dashboard_note = "Resident session stop requested from dashboard; live bridge should terminate the CLI host."
        self._nested_takes_over()
        lost = self.client.post(
            f"/api/v1/agents/{AGENT}/resident-lost",
            json={"bridgeId": "nested-bridge", "machineId": "win32:test-host", "reason": dashboard_note},
        )
        self.assertEqual(lost.status_code, 200, lost.text)
        self._operator_stop()
        self.assertEqual(self._fetchone("SELECT status_note FROM agents WHERE id = ?", (AGENT,))["status_note"], dashboard_note)
        self.assertEqual(self._beat("real-bridge").get("reason"), "bridge_superseded")
        self.assertEqual((self._agent()["status"], self._agent()["launch_mode"]), ("stopped", "none"))

    def test_CONTROL_a_status_patch_repeating_the_stopped_state_still_ends_the_offer(self):
        """A status PATCH that copies the loss's exact note and status is still a later write (review of
        5dc23997): the offer is held against a write count, not against values anyone can reproduce."""
        self._nested_takes_over()
        self._lost("nested-bridge")
        note = self._fetchone("SELECT status_note FROM agents WHERE id = ?", (AGENT,))["status_note"]
        patched = self.client.patch(f"/api/v1/agents/{AGENT}", json={"status": "stopped", "note": note})
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual(self._fetchone("SELECT status_note FROM agents WHERE id = ?", (AGENT,))["status_note"], note)
        self.assertEqual(self._beat("real-bridge").get("reason"), "bridge_superseded")
        self.assertEqual(self._agent()["status"], "stopped")


class AnUpgradedDatabaseCountsNoteWritesTests(unittest.TestCase):
    """A database from before 0.7.4 gains the column and the trigger at startup, in that order."""

    def test_the_write_count_rises_on_a_same_value_write_after_upgrade(self):
        import asyncio
        import tempfile
        from pathlib import Path

        from service import db as service_db

        path = Path(tempfile.mkdtemp()) / "old.db"
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE agents (id TEXT PRIMARY KEY, role TEXT, name TEXT, status TEXT, status_note TEXT DEFAULT '',"
                    " registered_at TEXT NOT NULL, last_seen TEXT NOT NULL)")
        old.execute("INSERT INTO agents (id, role, name, status, status_note, registered_at, last_seen) VALUES ('a','r','a','stopped','n','t','t')")
        old.commit()
        old.close()
        asyncio.run(getattr(service_db, "_real_init_db", service_db.init_db)(path))
        db = sqlite3.connect(path)
        try:
            db.execute("UPDATE agents SET status_note = 'n' WHERE id = 'a'")
            db.execute("UPDATE agents SET status = 'stopped' WHERE id = 'a'")
            db.commit()
            self.assertEqual(db.execute("SELECT note_generation FROM agents WHERE id = 'a'").fetchone()[0], 1)
        finally:
            db.close()
