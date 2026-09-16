"""`POST /agents` refuses a session id a DIFFERENT live agent holds, as the session-handle route does.

Before this, registering agent `b` with live agent `a`'s id stored it on both rows. The bridge sends
its own session id on every `comms_register`, so a second name registered from inside a live session
was enough. The controls matter as much as the refusal: a dead owner, the agent's own id, and no id
at all must all register exactly as before.
"""

import sqlite3

from service.tests._base import FastApiTestCase

H = "11111111-2222-4333-8444-555555555555"


class RegistrationRespectsALiveSessionOwner(FastApiTestCase):
    def _register(self, agent_id, handle="", mode="resident"):
        res = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": "claude-code", "sessionMode": mode,
            "machineId": "linux:test-host", "bridgeId": f"bridge-{agent_id}", "sessionHandle": handle,
        })
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def _row(self, agent_id):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(
                "SELECT session_handle, pending_session_id, status_note FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
        finally:
            conn.close()

    def _make_dead(self, agent_id):
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("UPDATE agents SET last_seen = '2026-01-01T00:00:00Z' WHERE id = ?", (agent_id,))
            conn.commit()
        finally:
            conn.close()

    def test_a_live_owner_keeps_the_id_and_the_newcomer_registers_without_it(self):
        self._register("a", H)
        body = self._register("b", H)
        self.assertEqual(self._row("a")["session_handle"], H)
        self.assertEqual(self._row("b")["session_handle"], "", "two live agents now share one conversation")
        self.assertEqual(self._row("b")["pending_session_id"], H, "the refused id must wait for a Confirm")
        self.assertIn("'a'", self._row("b")["status_note"])
        self.assertEqual(body["sessionCollision"]["collisionWith"], "a")

    def test_the_takeover_path_is_refused_too(self):
        # A resident registering over its own MANAGED row returns early through the takeover path.
        self._register("a", H)
        self._register("b", mode="managed")
        body = self._register("b", H)
        self.assertNotEqual(self._row("b")["session_handle"], H)
        self.assertEqual(self._row("b")["pending_session_id"], H)
        self.assertEqual(body.get("sessionCollision", {}).get("collisionWith"), "a", body)

    def test_control_a_dead_owner_is_not_a_collision(self):
        self._register("a", H)
        self._make_dead("a")
        body = self._register("b", H)
        self.assertEqual(self._row("b")["session_handle"], H)
        self.assertNotIn("sessionCollision", body)

    def test_control_an_agent_re_registering_its_own_id_keeps_it(self):
        self._register("a", H)
        body = self._register("a", H)
        self.assertEqual(self._row("a")["session_handle"], H)
        self.assertNotIn("sessionCollision", body)

    def test_control_no_id_registers_as_before(self):
        self._register("a", H)
        body = self._register("b")
        self.assertEqual(self._row("b")["session_handle"], "")
        self.assertEqual(self._row("b")["pending_session_id"] or "", "")
        self.assertNotIn("sessionCollision", body)
