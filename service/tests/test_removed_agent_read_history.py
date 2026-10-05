"""Removing an identity forgets only its read history, not its messages.

Receipts are keyed by agent ID but have no agent FK. They are not claim gates.
The claim and stop-window controls below keep that distinction observable.
"""
import asyncio
import sqlite3
from unittest.mock import patch

from fastapi import Request

from service.api_core.agent_remove import remove_agent
from service.db import get_db
from service.dispatch_claim import _claim_dispatch_once
from service.models import DispatchClaimRequest
from service.tests._base import FastApiTestCase


class RemovedAgentReadHistoryTests(FastApiTestCase):
    def register(self, agent_id, **extra):
        response = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": "hermes",
            "sessionMode": "managed", "runtimeConfig": {"channelEnabled": True},
            **extra,
        })
        self.assertEqual(response.status_code, 200, response.text)

    def sql(self, statement, parameters=()):
        db = sqlite3.connect(self._db_path)
        try:
            rows = db.execute(statement, parameters).fetchall()
            db.commit()
            return rows
        finally:
            db.close()

    def seed_history(self):
        for agent_id in ("target", "target-sibling", "sender"):
            self.register(agent_id)
        self.sql("INSERT INTO messages (id, from_agent, type, subject, body, timestamp) "
                 "VALUES ('old-mail', 'sender', 'info', 'old', 'retained message', 1)")
        for agent_id in ("target", "target-sibling"):
            self.sql("INSERT INTO read_receipts VALUES ('old-mail', ?, '2026-10-05T00:00:00Z')", (agent_id,))
        self.assertEqual(len(self.sql("SELECT * FROM read_receipts")), 2)

    def test_removal_forgets_only_the_removed_ids_read_history(self):
        self.seed_history()
        messages = self.sql("SELECT * FROM messages")
        sibling = self.sql("SELECT * FROM read_receipts WHERE agent_id = 'target-sibling'")
        response = self.client.delete("/api/v1/agents/target")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.sql("SELECT * FROM agents WHERE id = 'target'"), [])
        self.assertEqual(len(self.sql("SELECT * FROM agent_tombstones WHERE agent_id = 'target'")), 1)
        self.assertEqual(self.sql("SELECT * FROM read_receipts WHERE agent_id = 'target'"), [])
        self.assertEqual(self.sql("SELECT * FROM read_receipts WHERE agent_id = 'target-sibling'"), sibling)
        self.assertEqual(self.sql("SELECT * FROM messages"), messages)

    def test_second_fence_refusal_preserves_the_ids_read_history(self):
        self.seed_history()
        before = self.sql("SELECT * FROM read_receipts ORDER BY agent_id")
        answers = iter(("", "custody moved"))

        async def fence(_db):
            return next(answers)

        async def attempt():
            db = await get_db()
            try:
                return await remove_agent(db, "target", actor="fixture", reason="fixture", refusal=fence)
            finally:
                await db.close()

        self.assertEqual(asyncio.run(attempt()), (0, "custody moved"))
        self.assertEqual(self.sql("SELECT * FROM read_receipts ORDER BY agent_id"), before)
        self.assertEqual(self.sql("SELECT status FROM agents WHERE id = 'target'"), [("stopped",)])
        self.assertEqual(self.sql("SELECT * FROM agent_tombstones WHERE agent_id = 'target'"), [])

    def test_a_recreated_id_can_claim_a_new_run_despite_its_source_receipt(self):
        self.register("sender")
        self.register("target")
        removed = self.client.delete("/api/v1/agents/target")
        self.assertEqual(removed.status_code, 200, removed.text)
        self.register("target", restoreDeleted=True, autoRegister=False)
        sent = self.client.post("/api/v1/messages/send", json={
            "from_agent": "sender", "to": "target", "type": "request", "subject": "new work",
            "body": "fixture only", "trigger": False,
        })
        self.assertEqual(sent.status_code, 200, sent.text)
        message_id = sent.json()["messageId"]
        run_id = "fixture-new-run"
        self.sql("INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, execution_mode, requested_at) "
                 "VALUES (?, ?, 'sender', 'target', 'channel', '2026-10-05T00:00:00Z')", (run_id, message_id))
        self.sql("INSERT INTO read_receipts VALUES (?, 'target', '2026-10-05T00:00:00Z')", (message_id,))
        self.assertEqual(self.sql("SELECT status FROM dispatch_runs WHERE id = ?", (run_id,)), [("queued",)])
        claimed = self.client.post("/api/v1/dispatch/claim", json={
            "agentId": "target", "bridgeId": "fixture-sidecar", "bridgeKind": "channel-sidecar",
            "executionModes": ["channel", "resident"],
        })
        self.assertEqual(claimed.status_code, 200, claimed.text)
        self.assertEqual(claimed.json()["run"]["id"], run_id, claimed.text)
        self.assertEqual(self.sql("SELECT status FROM dispatch_runs WHERE id = ?", (run_id,)), [("claimed",)])

    def test_removal_exposes_stopped_before_the_permanent_tombstone(self):
        self.register("target")
        env = self.client.post("/api/v1/environments/heartbeat", json={
            "id": "fixture-env", "machineId": "fixture-machine", "bridgeId": "fixture-bridge",
            "label": "Fixture", "os": "windows", "kind": "windows", "cwdRoots": ["C:/fixture"],
            "runtimes": [{"runtime": "hermes", "modes": ["managed-warm"]}], "metadata": {},
        })
        self.assertEqual(env.status_code, 200, env.text)
        timestamp = "2026-10-05T00:00:00Z"
        self.sql("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, started_at, last_seen, "
                 "spawn_spec_id, spawn_request_id) VALUES ('fixture-session', 'target', 'fixture-env', 'hermes', "
                 "'running', ?, ?, NULL, NULL)", (timestamp, timestamp))
        self.sql("INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id, runtime, status, "
                 "created_at, updated_at) VALUES ('fixture-terminal', 'fixture-session', 'target', 'fixture-env', "
                 "'fixture-bridge', 'hermes', 'attached', ?, ?)", (timestamp, timestamp))
        observed = []

        async def observe_stop(_db, agent_id):
            self.assertEqual(agent_id, "target")
            controls = self.sql("SELECT action, body FROM terminal_controls")
            self.assertEqual(len(controls), 1)
            self.assertEqual(controls[0][0], "stop")
            self.assertTrue(controls[0][1].startswith("__aify_reap_triad__ "))
            req = DispatchClaimRequest(agentId=agent_id, bridgeId="fixture-sidecar", bridgeKind="channel-sidecar",
                                       executionModes=["channel", "resident"])
            observed.append(await _claim_dispatch_once(req, Request({"type": "http", "app": self._app})))

        with patch("service.api_core.agent_remove._await_stop_claims", side_effect=observe_stop):
            removed = self.client.delete("/api/v1/agents/target")
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(observed, [{"ok": True, "run": None, "stopped": True}])
        self.assertEqual(self.sql("SELECT * FROM terminal_controls"), [])
        final_claim = self.client.post("/api/v1/dispatch/claim", json={"agentId": "target"})
        self.assertEqual(final_claim.status_code, 410, final_claim.text)
