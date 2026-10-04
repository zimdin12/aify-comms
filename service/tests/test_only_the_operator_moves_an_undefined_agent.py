"""Only the operator rewrites an UNDEFINED agent's model, effort, workspace or mode (external review of 0.8.4).

THE DEFECT. `POST /agents/{id}/environment` and `PATCH /agents/{id}/session-mode` proved the operator only
for a defined agent. For any other agent they recorded whatever name the caller gave and rewrote its model,
effort, workspace and mode, which Steven's 2026-10-02 ruling makes operator-protected; PATCH /effort already
proved the operator for every agent. Each refusal here is checked to have changed nothing on the record, and
paired with the operator's own call on the same route, which must still succeed.
"""
from __future__ import annotations

import sqlite3

from service.api_core.operator_authz import OPERATOR_KEY_HEADER
from service.tests._base import FastApiTestCase
from service.tests.test_agent_definition_push import A

SECRET = "s3cret-operator-key"
RUNTIMES = [{"runtime": r, "modes": ["managed-warm"], "capabilities": {}} for r in ("claude-code", "codex")]


class OnlyTheOperatorMovesAnUndefinedAgent(FastApiTestCase):
    DB_NAME = "aify-test-operator-undefined-changes.db"

    def setUp(self):
        super().setUp()
        beat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": A["env"], "machineId": A["machine"], "os": "win32", "kind": "win32",
            "bridgeId": A["bridge"], "cwdRoots": ["/work"], "runtimes": RUNTIMES,
            "terminal": True, "pty": True, "terminalRuntimes": ["claude-code"], "metadata": {}})
        self.assertEqual(beat.status_code, 200, beat.text)
        self.client.post("/api/v1/agents", json={"agentId": "plain", "role": "coder", "runtime": "claude-code",
                                                 "sessionMode": "managed", "cwd": "/work/plain",
                                                 "model": "m1"}).raise_for_status()

    def record(self):
        conn = sqlite3.connect(str(self._db_path))
        try:
            return conn.execute("SELECT model, cwd, session_mode, runtime_config FROM agents WHERE id = 'plain'").fetchone()
        finally:
            conn.close()

    def routes(self):
        def environment(actor, headers):
            return self.client.post("/api/v1/agents/plain/environment", headers=headers, json={
                "environmentId": A["env"], "workspace": "/work/elsewhere", "model": "rogue",
                "runtimeConfig": {"effort": "low"}, "requestedBy": actor})

        def session_mode(actor, headers):
            return self.client.patch("/api/v1/agents/plain/session-mode", headers=headers,
                                     json={"mode": "resident", "requestedBy": actor})

        return {"environment": environment, "session-mode": session_mode}

    def test_an_ordinary_caller_is_refused_and_changes_nothing_with_the_key_set_or_not(self):
        before = self.record()
        for key in (SECRET, ""):
            for name, send in self.routes().items():
                with self.subTest(route=name, key_set=bool(key)):
                    self.client.app.state.config.operator_key = key
                    refused = send("mallory", {})
                    self.assertEqual(refused.status_code, 403, refused.text)
                    self.assertEqual(self.record(), before, "a refused change writes nothing")

    def test_an_unproven_operator_claim_is_refused_while_a_key_is_set(self):
        self.client.app.state.config.operator_key = SECRET
        before = self.record()
        for name, send in self.routes().items():
            with self.subTest(route=name):
                self.assertEqual(send("dashboard", {}).status_code, 403)
                self.assertEqual(self.record(), before)

    def test_CONTROL_the_operator_still_moves_it_on_both_routes(self):
        self.client.app.state.config.operator_key = SECRET
        moved = self.routes()["environment"]("dashboard", {OPERATOR_KEY_HEADER: SECRET})
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual(self.record()[0], "rogue", "the operator's assignment is written")
        switched = self.routes()["session-mode"]("dashboard", {OPERATOR_KEY_HEADER: SECRET})
        self.assertEqual(switched.status_code, 200, switched.text)
        self.assertEqual(self.record()[2], "resident")
