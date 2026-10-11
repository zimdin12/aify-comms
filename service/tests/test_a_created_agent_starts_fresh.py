"""A new agent lifetime uses Reset's existing launch policy; an existing one resumes.

These are isolated service routes and queued terminal controls, not actual agent starts.

D8 (0.9): a new id is no longer spawned by POST /spawn-requests. The host is asked to define it, and the
LIFECYCLE spawn queued once that definition is published is what launches it, so the new-lifetime cases read
that launch. An existing agent cannot be spawned by that route at all any more (defined or not, it is
refused), so the requested `resumePolicy` it carried has no 0.9 input; an existing agent resumes on the one
legacy start left, a defined agent's cold start, which is what the existing-agent case now drives.
"""
import sqlite3

from service.tests._base import FastApiTestCase
from service.tests.defined_agents import define, publish, spawn_defined

ENV = {"environment_id": "fixture-env", "machine_id": "fixture-machine", "bridge_id": "fixture-bridge"}
HOST = {"bridgeId": "fixture-bridge", "machineId": "fixture-machine"}


class CreatedAgentStartsFresh(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.response(self.client.post("/api/v1/environments/heartbeat", json={
            "id": "fixture-env", "machineId": "fixture-machine", "bridgeId": "fixture-bridge",
            "label": "Fixture", "os": "linux", "kind": "linux", "cwdRoots": ["/fixture"],
            "runtimes": [{"runtime": "hermes", "modes": ["managed-warm"],
                          "capabilities": {"nativeResume": True}}],
            "metadata": {"terminal": True, "pty": True, "terminalRuntimes": ["hermes"]},
        }))
        # D8: a spawn asks this machine's host to define the agent, so the host has published its store.
        publish(self, **ENV)

    def response(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def register(self):
        self.response(self.client.post("/api/v1/agents", json={
            "agentId": "fixture-target", "role": "coder", "runtime": "hermes",
            "sessionMode": "managed", "sessionHandle": "fixture_old_session",
        }))

    def created_launch(self):
        """Spawn a new `fixture-target` the D8 way, to the env its lifecycle spawn launches it with."""
        creation = self.response(self.client.post("/api/v1/spawn-requests", json={
            "createdBy": "dashboard", "environmentId": "fixture-env", "agentId": "fixture-target",
            "role": "coder", "runtime": "hermes", "workspace": "/fixture",
            "resumePolicy": "native_first",
        }))["definitionRequest"]
        base = "/api/v1/environments/fixture-env"
        self.response(self.client.post(f"{base}/definition-requests/claim", json=HOST))
        self.response(self.client.post(f"{base}/definition-requests/{creation['id']}/result", json={
            **HOST, "status": "done", "resultIncarnation": 1, "resultRevision": 1}))
        define(self, "fixture-target", runtime="hermes", workspace="/fixture", **ENV)
        claimed = self.response(self.client.post(f"{base}/lifecycle-requests/claim", json=HOST))
        self.assertEqual([r["action"] for r in claimed["requests"]], ["spawn"], claimed)
        launch = self.response(self.client.post(
            f"{base}/lifecycle-requests/{claimed['requests'][0]['id']}/launch", json=HOST))["launch"]
        return launch["env"]

    def cold_start_launch(self, expected_policy):
        created = spawn_defined(self, "fixture-target", runtime="hermes", workspace="/fixture", **ENV)
        self.assertEqual(created["resumePolicy"], expected_policy)
        claimed = self.response(self.client.post("/api/v1/spawn-requests/claim", json={
            "environmentId": "fixture-env", "bridgeId": "fixture-bridge", "machineId": "fixture-machine",
        }))["spawnRequest"]
        self.assertEqual(claimed["id"], created["id"])
        self.assertEqual(claimed["resumePolicy"], expected_policy)
        # This is the real host's report shape.
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
        env = self.created_launch()
        self.assertEqual(env["AIFY_HERMES_FRESH_CONTEXT"], "1")
        self.assertEqual(env["AIFY_SESSION_HANDLE"], "")
        self.assertEqual(env["HERMES_SESSION_ID"], "")

    def test_a_spawn_with_no_prior_agent_row_starts_fresh(self):
        self.assert_fresh()

    def test_a_spawn_after_removal_starts_fresh(self):
        self.register()
        self.response(self.client.delete("/api/v1/agents/fixture-target"))
        self.assert_fresh()

    def test_an_existing_agent_resumes_its_native_session(self):
        self.register()
        env = self.cold_start_launch("native_first")
        self.assertEqual(env["AIFY_HERMES_FRESH_CONTEXT"], "")
        self.assertEqual(env["AIFY_SESSION_HANDLE"], "fixture_old_session")
        self.assertEqual(env["HERMES_SESSION_ID"], "fixture_old_session")
