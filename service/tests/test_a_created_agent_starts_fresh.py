"""A new agent lifetime uses Reset's existing launch policy; an existing one resumes.

These are isolated service routes and queued terminal controls, not actual agent starts.
"""
import sqlite3

from service.tests._base import FastApiTestCase


class CreatedAgentStartsFresh(FastApiTestCase):
    def response(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def register(self):
        self.response(self.client.post("/api/v1/agents", json={
            "agentId": "fixture-target", "role": "coder", "runtime": "hermes",
            "sessionMode": "managed", "sessionHandle": "fixture_old_session",
        }))

    def spawn_launch(self, expected_policy):
        self.response(self.client.post("/api/v1/environments/heartbeat", json={
            "id": "fixture-env", "machineId": "fixture-machine", "bridgeId": "fixture-bridge",
            "label": "Fixture", "os": "linux", "kind": "linux", "cwdRoots": ["/fixture"],
            "runtimes": [{"runtime": "hermes", "modes": ["managed-warm"],
                          "capabilities": {"nativeResume": True}}],
            "metadata": {"terminal": True, "pty": True, "terminalRuntimes": ["hermes"]},
        }))
        created = self.response(self.client.post("/api/v1/spawn-requests", json={
            "createdBy": "dashboard", "environmentId": "fixture-env", "agentId": "fixture-target",
            "role": "coder", "runtime": "hermes", "workspace": "/fixture",
            "resumePolicy": "native_first",
        }))["spawnRequest"]
        self.assertEqual(created["resumePolicy"], expected_policy)
        claimed = self.response(self.client.post("/api/v1/spawn-requests/claim", json={
            "environmentId": "fixture-env", "bridgeId": "fixture-bridge", "machineId": "fixture-machine",
        }))["spawnRequest"]
        self.assertEqual(claimed["id"], created["id"])
        self.assertEqual(claimed["resumePolicy"], expected_policy)
        # This is the real host's report shape. Even a stale handle cannot defeat the fresh policy.
        self.response(self.client.patch(f"/api/v1/spawn-requests/{created['id']}", json={
            "status": "running", "bridgeId": "fixture-bridge", "sessionHandle": "fixture_old_session",
            "runtimeState": {"environmentId": "fixture-env", "spawnRequestId": created["id"],
                             "resumePolicy": claimed["resumePolicy"]},
        }))
        db = sqlite3.connect(self._db_path)
        try:
            terminal = db.execute("SELECT id FROM terminal_sessions WHERE agent_id = 'fixture-target'").fetchone()
        finally:
            db.close()
        self.assertIsNotNone(terminal, "the real running transition must queue the wrapper PTY")
        return self.response(self.client.get(f"/api/v1/terminals/{terminal[0]}/launch"))["launch"]["env"]

    def assert_fresh(self):
        env = self.spawn_launch("fresh_context")
        self.assertEqual(env["AIFY_HERMES_FRESH_CONTEXT"], "1")
        self.assertEqual(env["AIFY_SESSION_HANDLE"], "")
        self.assertEqual(env["HERMES_SESSION_ID"], "")

    def test_a_spawn_with_no_prior_agent_row_starts_fresh(self):
        self.assert_fresh()

    def test_a_spawn_after_removal_starts_fresh(self):
        self.register()
        self.response(self.client.delete("/api/v1/agents/fixture-target"))
        self.assert_fresh()

    def test_an_existing_agent_keeps_the_requested_resume_policy(self):
        self.register()
        env = self.spawn_launch("native_first")
        self.assertEqual(env["AIFY_HERMES_FRESH_CONTEXT"], "")
        self.assertEqual(env["AIFY_SESSION_HANDLE"], "fixture_old_session")
        self.assertEqual(env["HERMES_SESSION_ID"], "fixture_old_session")
