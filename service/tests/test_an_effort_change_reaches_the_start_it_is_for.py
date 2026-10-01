"""An agent's model and effort, from the edit to the launch that uses them (P0 C12, the 0.8 review).

Each case is a route chain the review of 3ada3809 ran and found broken, kept as its witness:

- C1: an effort change answered "next start", and the next start undid it. A restart starts from the
  agent's stored spawn spec, and a start going running copies that spec's runtimeConfig over the agent.
- C2: the effort route read the whole runtime config, then wrote it back, so a usage-source edit
  committed between the two was lost behind two 200s.
- C3: `runsWith` read the record one way and the launch another, for inputs registration accepts.
- C4: one damaged stored config took the whole agent list down with a 500.
- G2: apply-defaults with hermes' empty effort left `thinking` behind, so the launch still said high.

The reader every expectation is checked against is the real launch route, not a copy of its rule.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from unittest.mock import patch

from service.routers.agents import attributes
from service.tests._base import PRE_PLAN4_SETTINGS, FastApiTestCase

ENV = "linux:test-host:default"
CLAIM = {"environmentId": ENV, "bridgeId": "bridge-current", "machineId": "linux:test-host"}


class AnEffortChangeReachesItsStart(FastApiTestCase):
    DB_NAME = "aify-test-effort-reaches-its-start.db"
    LEGACY_SETTINGS = PRE_PLAN4_SETTINGS

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def sql(self, query, params=()):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in conn.execute(query, params)]
            conn.commit()
            return rows
        finally:
            conn.close()

    def config(self, agent_id="worker"):
        return json.loads(self.sql("SELECT runtime_config FROM agents WHERE id = ?", (agent_id,))[0]["runtime_config"])

    def heartbeat(self, env=ENV, runtime="codex"):
        self.ok(self.client.post("/api/v1/environments/heartbeat", json={
            "id": env, "label": env, "machineId": "linux:test-host", "os": "linux", "kind": "linux",
            "bridgeId": "bridge-current", "cwdRoots": ["/workspace"], "metadata": {},
            "runtimes": [{"runtime": runtime, "modes": ["managed-warm"],
                          "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True}}]}))

    def launch_for(self, agent_id, session_id, runtime="codex", terminal="term-1"):
        """What the real launch route hands the host for this session. The row only makes it reachable."""
        self.sql("INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, runtime, workspace, command, "
                 "status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (terminal, session_id, agent_id, ENV, runtime, "/workspace/project", "fake", "starting",
                  "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"))
        return self.ok(self.client.get(f"/api/v1/terminals/{terminal}/launch"))["launch"]

    def running(self, request_id, process):
        self.ok(self.client.post("/api/v1/spawn-requests/claim", json=CLAIM))
        return self.ok(self.client.patch(f"/api/v1/spawn-requests/{request_id}", json={
            "status": "running", "bridgeId": "bridge-current", "processId": process, "machineId": "linux:test-host"}))["spawnRequest"]

    def restarted_launch(self, effort):
        """Spawn at low, change the effort, restart, and return the replacement's real launch."""
        self.heartbeat()
        first = self.ok(self.client.post("/api/v1/spawn-requests", json={
            "createdBy": "dashboard", "environmentId": ENV, "agentId": "worker", "runtime": "codex", "role": "coder",
            "workspace": "/workspace/project", "runtimeConfig": {"effort": "low", "thinking": "minimal", "maxTurns": 5}}))["spawnRequest"]
        session = self.running(first["id"], "fake-1")["sessionId"]
        self.ok(self.client.patch("/api/v1/agents/worker/effort", json={"effort": effort}))
        restart = self.ok(self.client.post(f"/api/v1/sessions/{session}/control", json={
            "action": "restart", "from_agent": "dashboard", "subject": "restart"}))["spawnRequest"]
        self.assertEqual(restart["spawnSpecId"], first["spawnSpecId"], "the restart starts from the stored spec")
        replacement = self.running(restart["id"], "fake-2")
        return self.launch_for("worker", replacement["sessionId"])

    def test_c1_a_restart_launches_with_the_changed_effort(self):
        launch = self.restarted_launch("high")
        self.assertEqual(launch["env"].get("AIFY_MANAGED_EFFORT"), "high")
        self.assertEqual(self.config()["effort"], "high", "settlement copied the spec over the agent")
        self.assertNotIn("thinking", self.config())
        self.assertEqual(self.config()["maxTurns"], 5, "only the effort changes")

    def test_c1_a_cleared_effort_is_the_runtimes_own_even_against_the_hosts_environment(self):
        launch = self.restarted_launch("")
        self.assertNotIn("AIFY_MANAGED_EFFORT", launch["env"])
        self.assertIn("AIFY_MANAGED_EFFORT", launch["unsetEnv"],
                      "absent from the overlay is inherited from the host, not cleared")
        self.assertIn("AIFY_MANAGED_MODEL", launch["unsetEnv"])

    def test_c2_a_usage_source_edit_made_while_the_effort_route_reads_is_kept(self):
        self.ok(self.client.post("/api/v1/agents", json={
            "agentId": "worker", "role": "coder", "runtime": "codex", "sessionMode": "managed",
            "runtimeConfig": {"effort": "low", "maxTurns": 5}}))
        effort_read, release = threading.Event(), threading.Event()
        original = attributes.get_db

        class Paused:
            """The effort route's connection, held still right after its runtime_config read."""
            def __init__(self, db):
                self.db = db

            def __getattr__(self, name):
                return getattr(self.db, name)

            async def execute(self, sql, *args):
                cursor = await self.db.execute(sql, *args)
                if not sql.startswith("SELECT id, session_mode, runtime_config FROM agents"):
                    return cursor
                row = await cursor.fetchone()
                effort_read.set()
                await asyncio.to_thread(release.wait, 15)

                class Done:
                    async def fetchone(self):
                        return row
                return Done()

        async def paused_db(*args, **kwargs):
            return Paused(await original(*args, **kwargs))

        answers = {}
        with patch.object(attributes, "get_db", paused_db):
            effort = threading.Thread(target=lambda: answers.__setitem__(
                "effort", self.client.patch("/api/v1/agents/worker/effort", json={"effort": "high"})))
            effort.start()
            self.assertTrue(effort_read.wait(15), "the effort route never read")
            usage = threading.Thread(target=lambda: answers.__setitem__(
                "usage", self.client.patch("/api/v1/agents/worker/usage-source", json={"usageSource": "anthropic"})))
            usage.start()
            # Unfenced, the usage edit commits inside this window and the resumed effort write erases it.
            # Fenced, it waits on the effort route's write lock (busy_timeout 5 s) and lands after it.
            usage.join(1.5)
            release.set()
            effort.join(15)
            usage.join(15)
        self.assertEqual((answers["effort"].status_code, answers["usage"].status_code), (200, 200),
                         f"{answers['effort'].text} / {answers['usage'].text}")
        self.assertEqual(self.config(), {"effort": "high", "maxTurns": 5, "usageSource": "anthropic"})

    def test_c3_runs_with_says_what_the_launch_route_launches_for_every_accepted_input(self):
        self.heartbeat()
        cases = {
            "control": {"effort": "high"},
            "model-in-config": {"model": "config-model", "effort": " ", "thinking": "high"},
            "numeric-effort": {"effort": 0, "thinking": "high"},
        }
        for index, (name, config) in enumerate(cases.items()):
            agent_id = f"projection-{index}"
            self.ok(self.client.post("/api/v1/agents", json={
                "agentId": agent_id, "role": "coder", "runtime": "codex", "sessionMode": "managed", "model": "",
                "runtimeConfig": config}))
            shown = self.ok(self.client.get(f"/api/v1/agents/{agent_id}"))["agent"]["runsWith"]
            session = f"sess-{index}"
            self.sql("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, started_at, last_seen, "
                     "spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,NULL,NULL)",
                     (session, agent_id, ENV, "codex", "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"))
            env = self.launch_for(agent_id, session, terminal=f"term-{index}")["env"]
            with self.subTest(name):
                self.assertEqual(shown["model"]["value"], env.get("AIFY_MANAGED_MODEL", ""))
                self.assertEqual(shown["effort"]["value"], env.get("AIFY_MANAGED_EFFORT", ""))
        # The three cases differ, so agreement is not three empty answers.
        self.assertEqual(self.ok(self.client.get("/api/v1/agents/projection-1"))["agent"]["runsWith"],
                         {"model": {"value": "config-model", "from": "agent"}, "effort": {"value": "high", "from": "agent"}})

    def test_c4_a_damaged_stored_config_leaves_the_list_and_the_agent_readable(self):
        self.ok(self.client.post("/api/v1/agents", json={
            "agentId": "worker", "role": "coder", "runtime": "codex", "sessionMode": "managed"}))
        self.ok(self.client.post("/api/v1/agents", json={"agentId": "neighbour", "role": "coder", "runtime": "codex"}))
        self.sql("UPDATE agents SET runtime_config = ? WHERE id = ?", ("{not json", "worker"))
        listed = self.ok(self.client.get("/api/v1/agents"))["agents"]
        self.assertIn("neighbour", listed, "one damaged row must not hide every other agent")
        self.assertEqual(listed["worker"]["runsWith"]["effort"], {"value": "", "from": "runtime"})
        self.ok(self.client.get("/api/v1/agents/worker"))

    def test_g2_an_empty_default_applied_to_hermes_clears_the_old_thinking(self):
        self.ok(self.client.post("/api/v1/agents", json={
            "agentId": "worker", "role": "coder", "runtime": "hermes", "sessionMode": "managed",
            "runtimeConfig": {"thinking": "high", "model": "old-model", "keep": 5}}))
        self.ok(self.client.put("/api/v1/settings", json={"managed_hermes_effort": "", "managed_hermes_model": ""}))
        self.ok(self.client.post("/api/v1/settings/apply-managed-defaults"))
        self.assertEqual(self.config(), {"effort": "", "keep": 5})
        shown = self.ok(self.client.get("/api/v1/agents/worker"))["agent"]["runsWith"]
        self.assertEqual(shown, {"model": {"value": "", "from": "runtime"}, "effort": {"value": "", "from": "runtime"}})
