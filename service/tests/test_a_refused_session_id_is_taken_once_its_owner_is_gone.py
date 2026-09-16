"""A session id refused at registration is TAKEN when the bridge offers it again after its owner has gone.

External review, 2026-09-16: the bridge's heartbeat recorded a refused id as delivered and never offered it
again, so "wait out the owner's lease" -- which DECISIONS.md promised -- could not happen. The heartbeat now
re-offers an id the service answered `session-collision` to (mcp/stdio/session-handle-heartbeat.js). That fix
is only worth anything if the route then ADOPTS the id once the owner is gone, rather than parking it again,
and that is the half this proves: the same PATCH the heartbeat sends, before and after the owner's lease.
"""

import sqlite3

from service.tests._base import FastApiTestCase

H = "11111111-2222-4333-8444-555555555555"


class ARefusedSessionIdIsTakenOnceItsOwnerIsGone(FastApiTestCase):
    def _register(self, agent_id, handle=""):
        res = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": "claude-code", "sessionMode": "resident",
            "machineId": "linux:test-host", "bridgeId": f"bridge-{agent_id}", "sessionHandle": handle,
        })
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def _heartbeat(self, agent_id, handle):
        res = self.client.patch(f"/api/v1/agents/{agent_id}/session-handle",
                                json={"sessionHandle": handle, "requestedBy": "bridge-heartbeat"})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def _handle(self, agent_id):
        conn = sqlite3.connect(str(self._db_path))
        try:
            return conn.execute("SELECT session_handle FROM agents WHERE id = ?", (agent_id,)).fetchone()[0] or ""
        finally:
            conn.close()

    def _make_dead(self, agent_id):
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("UPDATE agents SET last_seen = '2026-01-01T00:00:00Z' WHERE id = ?", (agent_id,))
            conn.commit()
        finally:
            conn.close()

    def test_the_heartbeat_offer_is_refused_while_the_owner_lives_and_taken_once_it_is_gone(self):
        self._register("a", H)
        self.assertIn("sessionCollision", self._register("b", H), "control: the registration was refused")
        self.assertEqual(self._handle("b"), "")

        # The heartbeat's next offer, with the owner still live: refused the same way, which is what the
        # bridge now reads as "offer it again".
        self.assertEqual(self._heartbeat("b", H).get("state"), "session-collision")
        self.assertEqual(self._handle("b"), "", "a live owner's id was taken")

        # The owner's lease runs out. The same offer is now the id's to take.
        self._make_dead("a")
        answer = self._heartbeat("b", H)
        self.assertNotEqual(answer.get("state"), "session-collision", answer)
        self.assertEqual(self._handle("b"), H, f"the id was still not taken once its owner was gone: {answer}")
