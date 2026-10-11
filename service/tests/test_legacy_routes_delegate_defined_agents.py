"""D9c: the existing start, stop, restart and delete routes hand a DEFINED agent to the lifecycle queue.

Each witness drives the real route, then reads the queued lifecycle request and the legacy tables the
route used to write, since queued is not done and a legacy side effect beside the request would be a
second, unfenced actor. An undefined agent is refused a start since D8
(test_a_spawn_defines_its_agent_first.py).
"""
from __future__ import annotations

import asyncio
import sqlite3

import service.api_core.dispatch_start as dispatch_start
from service.db import get_db
from service.tests._base import FastApiTestCase
from service.tests.published_state import publish
from service.tests.test_agent_definition_push import A, snapshot_digest, valid

RUNTIMES = [{"runtime": runtime, "available": True} for runtime in ("claude-code", "codex", "hermes")]


class LegacyRoutesDelegateDefinedAgents(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": A["env"], "machineId": A["machine"], "os": "win32", "kind": "win32", "bridgeId": A["bridge"],
            "cwdRoots": ["/work"], "runtimes": RUNTIMES, "metadata": {}}).raise_for_status()
        entries = [valid("coder")]
        self.client.put(f"/api/v1/environments/{A['env']}/agent-definitions", json={
            "bridgeId": A["bridge"], "machineId": A["machine"], "storeId": "s1", "revision": 1,
            "snapshotDigest": snapshot_digest(entries), "entries": entries}).raise_for_status()
        self.sql("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, mode, status, started_at, last_seen) "
                 "VALUES ('sess-1', 'coder', ?, 'claude-code', 'managed-warm', 'running', 'now', 'now')", (A["env"],))

    def sql(self, query, params=()):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in conn.execute(query, params)]
            conn.commit()
            return rows
        finally:
            conn.close()

    def legacy_effects(self):
        return {table: self.sql(f"SELECT * FROM {table}")
                for table in ("spawn_requests", "terminal_controls", "definition_requests")}

    def queued(self, response, action, lifetime, *, fresh=False):
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["queued"], body["action"]), (True, action), "queued is not done")
        [row] = self.sql("SELECT action, expected_lifetime, fresh_context, machine_id, status FROM agent_lifecycle_requests")
        self.assertEqual(row, {"action": action, "expected_lifetime": lifetime, "fresh_context": int(fresh),
                               "machine_id": A["machine"], "status": "pending"})
        self.assertEqual(self.legacy_effects(), {"spawn_requests": [], "terminal_controls": [], "definition_requests": []})

    def test_each_route_queues_its_action_with_the_lifetime_the_host_published(self):
        routes = [
            ("start", None, lambda: self.client.post("/api/v1/agents/coder/control", json={"action": "start", "from_agent": "dashboard"})),
            ("stop", "L1", lambda: self.client.post("/api/v1/agents/coder/control", json={"action": "stop", "from_agent": "dashboard"})),
            ("kill", "L1", lambda: self.client.post("/api/v1/agents/coder/stop-worker", json={})),
            ("restart", "L1", lambda: self.client.post("/api/v1/sessions/sess-1/control", json={"action": "restart", "from_agent": "dashboard"})),
            ("delete", "L1", lambda: self.client.delete("/api/v1/agents/coder")),
        ]
        for action, lifetime, call in routes:
            with self.subTest(action=action):
                self.sql("DELETE FROM agent_lifecycle_requests")
                publish(self, A["machine"], {"coder": (lifetime, "idle" if lifetime else "available")})
                self.queued(call(), action, lifetime)
        self.assertEqual(len(self.sql("SELECT id FROM agents WHERE id = 'coder'")), 1, "its host removes it")

    def test_an_agent_without_the_operator_key_restarts_and_stops_but_does_not_kill(self):
        """Steven, 2026-10-11: the operator key locks what cannot be undone. A manager's comms_restart and stop of
        a defined agent, which send only the API key, still queue; its kill is refused and queues nothing."""
        self._app.state.config.operator_key = "fixture-key"
        publish(self, A["machine"], {"coder": ("L1", "idle")})
        self.queued(self.client.post("/api/v1/sessions/sess-1/control", json={"action": "restart", "from_agent": "manager"}),
                    "restart", "L1")
        self.sql("DELETE FROM agent_lifecycle_requests")
        self.queued(self.client.post("/api/v1/agents/coder/control", json={"action": "stop", "from_agent": "manager"}),
                    "stop", "L1")
        self.sql("DELETE FROM agent_lifecycle_requests")
        killed = self.client.post("/api/v1/agents/coder/stop-worker", json={})
        self.assertEqual(killed.status_code, 403, killed.text)
        self.assertEqual(self.sql("SELECT id FROM agent_lifecycle_requests"), [])

    def test_recreate_is_a_restart_into_a_fresh_conversation(self):
        publish(self, A["machine"], {"coder": ("L1", "idle")})
        self.queued(self.client.post("/api/v1/sessions/sess-1/control", json={"action": "recreate", "from_agent": "dashboard"}),
                    "restart", "L1", fresh=True)

    def test_without_fresh_state_from_its_host_nothing_is_queued_or_done(self):
        for applied_at in (None, "2000-01-01T00:00:00Z"):
            with self.subTest(applied_at=applied_at):
                if applied_at:
                    publish(self, A["machine"], {"coder": ("L1", "idle")}, applied_at=applied_at)
                refused = self.client.post("/api/v1/agents/coder/control", json={"action": "stop", "from_agent": "dashboard"})
                self.assertEqual(refused.status_code, 409, refused.text)
                self.assertEqual(refused.json()["detail"], f"no fresh agent state from {A['machine']}"
                                 ", so the running lifetime cannot be named; try again once its aify-env publishes")
                self.assertEqual(self.sql("SELECT id FROM agent_lifecycle_requests"), [])
                self.assertEqual(self.legacy_effects(), {"spawn_requests": [], "terminal_controls": [], "definition_requests": []})

    def test_two_published_lifetimes_name_no_lifetime(self):
        publish(self, A["machine"], {"coder": ("L1", "idle")})
        publish(self, A["machine"], {"coder": ("L2", "idle")}, instance="scoped")
        refused = self.client.post("/api/v1/agents/coder/control", json={"action": "stop", "from_agent": "dashboard"})
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["detail"], "its host publishes more than one running lifetime for this agent")
        self.assertEqual(self.sql("SELECT id FROM agent_lifecycle_requests"), [])

    def test_another_machines_state_names_nothing(self):
        publish(self, "win32:host-elsewhere", {"coder": ("L9", "idle")})
        refused = self.client.post("/api/v1/agents/coder/control", json={"action": "stop", "from_agent": "dashboard"})
        self.assertEqual(refused.status_code, 409, refused.text)

    def test_an_unusable_definition_is_refused_at_once(self):
        publish(self, A["machine"], {"coder": (None, "available")})
        entries = [valid("coder", revision=2, mode="resident")]
        self.client.put(f"/api/v1/environments/{A['env']}/agent-definitions", json={
            "bridgeId": A["bridge"], "machineId": A["machine"], "storeId": "s1", "revision": 2,
            "snapshotDigest": snapshot_digest(entries), "entries": entries}).raise_for_status()
        refused = self.client.post("/api/v1/agents/coder/control", json={"action": "start", "from_agent": "dashboard"})
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertIn("as resident", refused.json()["detail"])
        self.assertEqual(self.sql("SELECT id FROM agent_lifecycle_requests"), [])

    def cold_start(self, reasons):
        async def run():
            db = await get_db()
            try:
                started = await dispatch_start._coldstart_spawn_request_for_dispatch(
                    db, "coder", runtime="claude-code", settings={}, requested_by="test", warnings=reasons)
                await db.commit()
                return started
            finally:
                await db.close()
        return asyncio.run(run())

    def test_a_message_never_starts_an_agent_its_host_publishes_stopped(self):
        self.sql("UPDATE agent_sessions SET status = 'stopped'")
        publish(self, A["machine"], {"coder": (None, "stopped")})
        reasons: list[str] = []
        self.assertFalse(self.cold_start(reasons))
        self.assertEqual(reasons, [f"{dispatch_start.COLDSTART_REFUSED_PREFIX}stopped by the operator; start it explicitly to deliver"])
        self.assertEqual(self.sql("SELECT id FROM spawn_requests"), [])
        publish(self, A["machine"], {"coder": (None, "available")})
        self.assertTrue(self.cold_start([]), "control: started again, a message starts it")
