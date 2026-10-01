"""`PATCH /agents/{id}/effort` changes the effort an agent starts with, through whatever its start reads (P0 C12).

Both ends: the write, and the reader. For an undefined managed agent the reader is the launch route's
`managed_launch_env`, fed the agent exactly as the API serves it. For a defined agent the change is a
request for its host (C5), never a write here. An undefined resident is refused: its launcher reads only
a definition, so a write here would be accepted and ignored.
"""

from __future__ import annotations

import json
import sqlite3

from service.api_core.launch_env import managed_launch_env
from service.tests._base import FastApiTestCase
from service.tests.test_agent_definition_push import A, snapshot_digest, valid


class AnAgentsEffortChanges(FastApiTestCase):
    DB_NAME = "aify-test-agent-effort.db"

    def register(self, agent_id, **over):
        body = {"agentId": agent_id, "role": "coder", "runtime": "codex", **over}
        self.assertEqual(self.client.post("/api/v1/agents", json=body).status_code, 200)

    def row(self, agent_id):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return dict(conn.execute("SELECT runtime_config, definition_state FROM agents WHERE id = ?", (agent_id,)).fetchone())
        finally:
            conn.close()

    def test_an_undefined_managed_agent_starts_with_the_new_effort(self):
        self.register("worker", sessionMode="managed", runtimeConfig={"effort": "low", "thinking": "minimal", "maxTurns": 5})
        changed = self.client.patch("/api/v1/agents/worker/effort", json={"effort": "XHigh"})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["appliesAt"], "next start")
        self.assertEqual(json.loads(self.row("worker")["runtime_config"]), {"effort": "xhigh", "maxTurns": 5},
                         "thinking goes: the launch reads effort then thinking, and one value must decide")
        agent = self.client.get("/api/v1/agents/worker").json()["agent"]
        self.assertEqual(agent["runsWith"]["effort"], {"value": "xhigh", "from": "agent"})
        env = managed_launch_env(terminal={"agentId": "worker", "runtime": "codex"}, agent=agent)
        self.assertEqual(env.get("AIFY_MANAGED_EFFORT"), "xhigh", "the reader: what the worker is launched with")

    def test_empty_is_the_runtimes_own(self):
        self.register("worker", sessionMode="managed", runtimeConfig={"effort": "low"})
        self.assertEqual(self.client.patch("/api/v1/agents/worker/effort", json={"effort": ""}).status_code, 200)
        agent = self.client.get("/api/v1/agents/worker").json()["agent"]
        self.assertEqual(agent["runsWith"]["effort"], {"value": "", "from": "runtime"})
        self.assertNotIn("AIFY_MANAGED_EFFORT", managed_launch_env(terminal={"agentId": "worker", "runtime": "codex"}, agent=agent))

    def test_a_defined_agents_change_is_a_request_for_its_host_and_writes_nothing_here(self):
        beat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": A["env"], "machineId": A["machine"], "os": "win32", "kind": "win32", "bridgeId": A["bridge"],
            "cwdRoots": ["/work"], "runtimes": [], "metadata": {}})
        self.assertEqual(beat.status_code, 200, beat.text)
        entries = [valid("coder", effort="low")]
        pushed = self.client.put(f"/api/v1/environments/{A['env']}/agent-definitions", json={
            "bridgeId": A["bridge"], "machineId": A["machine"], "storeId": "s1", "revision": 1,
            "snapshotDigest": snapshot_digest(entries), "entries": entries})
        self.assertEqual(pushed.status_code, 200, pushed.text)
        before = self.row("coder")
        queued = self.client.patch("/api/v1/agents/coder/effort", json={"effort": "high"})
        self.assertEqual(queued.status_code, 200, queued.text)
        body = queued.json()
        self.assertEqual((body["request"]["patch"], body["appliesAt"]), ({"effort": "high"}, "next start"))
        self.assertEqual(self.row("coder"), before, "the host applies it; nothing is written here")

    def test_an_undefined_resident_is_refused_with_the_way_to_define_it(self):
        self.register("operator-run", sessionMode="resident", runtimeConfig={"effort": "low"})
        refused = self.client.patch("/api/v1/agents/operator-run/effort", json={"effort": "high"})
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertIn('"operator-run" is a resident agent that no host defines: its launcher reads only a definition',
                      refused.json()["detail"])
        self.assertIn("aify-env agents import", refused.json()["detail"])
        self.assertEqual(json.loads(self.row("operator-run")["runtime_config"]), {"effort": "low"})

    def test_an_effort_is_one_word_and_an_unknown_agent_is_404(self):
        self.register("worker", sessionMode="managed")
        for bad in ("high; rm -rf /", "a b", "x" * 17, "hi\ngh", "high-2"):
            self.assertEqual(self.client.patch("/api/v1/agents/worker/effort", json={"effort": bad}).status_code, 422, bad)
        self.assertEqual(self.client.patch("/api/v1/agents/nobody/effort", json={"effort": "high"}).status_code, 404)
