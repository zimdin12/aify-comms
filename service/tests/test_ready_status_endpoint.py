"""PATCH /api/v1/agents/{id}/ready answers a running bridge's handshake report and stores nothing.

The readiness bit lived in `agent_turn_state`, which nothing read after the 0.9.8 switch; 0.9.9 S3c dropped the
table. The route stays so a bridge that still sends it gets a 200, and an unknown agent still gets a 404.
"""

import unittest

from service.tests._base import FastApiTestCase


class ReadyStatusEndpointTests(FastApiTestCase):
    DB_NAME = "aify-test-ready.db"

    def test_patch_ready_endpoint_returns_200_for_known_agent(self):
        registered = self.client.post("/api/v1/agents", json={"agentId": "ready-known", "role": "tester",
                                                               "runtime": "codex", "sessionMode": "managed"})
        self.assertEqual(registered.status_code, 200, registered.text)
        resp = self.client.patch("/api/v1/agents/ready-known/ready", json={"ready": True, "requestedBy": "test"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), {"ok": True, "agentId": "ready-known", "ready": True})

    def test_patch_ready_404_for_unknown_agent(self):
        resp = self.client.patch("/api/v1/agents/nonexistent-agent/ready", json={"ready": True})
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
