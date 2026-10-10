"""The routes that edit an agent queue a request for a DEFINED one (P0 C5's table), and still edit an
undefined one directly.

Each witness pairs the two, because a route that queued for every agent would pass every
defined-agent assertion here. The request's patch is read back from `definition_requests`, and the
agent row is read to show the edit did not land directly.
"""
from __future__ import annotations

import json
import sqlite3

from service.tests._base import FastApiTestCase
from service.tests.published_state import publish
from service.tests.test_agent_definition_push import A, B, snapshot_digest, valid

RUNTIMES = [{"runtime": r, "modes": ["managed-warm"], "capabilities": {}} for r in ("claude-code", "codex", "pi")]


class EditingADefinedAgentBecomesARequest(FastApiTestCase):
    DB_NAME = "aify-test-definition-edits.db"

    def setUp(self):
        super().setUp()
        for host in (A, B):
            beat = self.client.post("/api/v1/environments/heartbeat", json={
                "id": host["env"], "machineId": host["machine"], "os": "win32", "kind": "win32",
                "bridgeId": host["bridge"], "cwdRoots": ["/work"], "runtimes": RUNTIMES,
                "terminal": True, "pty": True, "terminalRuntimes": ["claude-code", "codex"], "metadata": {}})
            self.assertEqual(beat.status_code, 200, beat.text)
        entries = [valid("lead")]
        pushed = self.client.put(f"/api/v1/environments/{A['env']}/agent-definitions", json={
            "bridgeId": A["bridge"], "machineId": A["machine"], "storeId": "s1", "revision": 1,
            "snapshotDigest": snapshot_digest(entries), "entries": entries})
        self.assertEqual(pushed.status_code, 200, pushed.text)
        self.client.post("/api/v1/agents", json={"agentId": "plain", "role": "coder", "runtime": "claude-code",
                                                  "sessionMode": "managed", "cwd": "/work"}).raise_for_status()

    def rows(self, sql, params=()):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def patches(self, agent_id):
        return [json.loads(r["patch"]) for r in self.rows(
            "SELECT patch FROM definition_requests WHERE agent_id = ? ORDER BY created_at, id", (agent_id,))]

    def test_herdr_space(self):
        queued = self.client.patch("/api/v1/agents/lead/herdr-space", json={"show": False})
        self.assertEqual(queued.status_code, 200, queued.text)
        self.assertEqual(queued.json()["request"]["patch"], {"herdrSpace": False})
        self.assertEqual(self.rows("SELECT herdr_space FROM agents WHERE id = 'lead'"), [{"herdr_space": 1}],
                         "the definition's value stands until its host applies the change")
        direct = self.client.patch("/api/v1/agents/plain/herdr-space", json={"show": False})
        self.assertEqual((direct.status_code, direct.json().get("request")), (200, None))
        self.assertEqual(self.rows("SELECT herdr_space FROM agents WHERE id = 'plain'"), [{"herdr_space": 0}],
                         "control: an undefined agent's setting changes at once")

    def test_a_request_for_an_agent_no_longer_defined_is_refused(self):
        """The queue's own check, inside its write transaction: a release that lands first leaves
        nothing to queue for, and says so."""
        import asyncio
        from service.api_core.definition_requests import queued_for_its_host
        from service.api_core.operator_authz import OperatorProof
        from service.db import get_db

        async def ask():
            db = await get_db()
            try:
                return await queued_for_its_host(db, "plain", {"role": "x"}, OperatorProof("dashboard"), "2026-10-01T00:00:00Z")
            finally:
                await db.close()

        with self.assertRaises(Exception) as refused:
            asyncio.run(ask())
        self.assertEqual((refused.exception.status_code, refused.exception.detail),
                         (409, '"plain" stopped being defined while this was asked; ask again'))

    def test_session_mode(self):
        queued = self.client.patch("/api/v1/agents/lead/session-mode", json={"mode": "resident", "requestedBy": "dashboard"})
        self.assertEqual(queued.status_code, 200, queued.text)
        self.assertEqual(self.patches("lead"), [{"mode": "resident"}])
        self.assertEqual(self.rows("SELECT session_mode FROM agents WHERE id = 'lead'"), [{"session_mode": "managed"}])
        direct = self.client.patch("/api/v1/agents/plain/session-mode", json={"mode": "resident", "requestedBy": "dashboard"})
        self.assertEqual(direct.status_code, 200, direct.text)
        self.assertEqual(self.rows("SELECT session_mode FROM agents WHERE id = 'plain'"), [{"session_mode": "resident"}],
                         "control: an undefined agent switches at once")
        self.assertEqual(self.patches("plain"), [])

    def test_environment_assignment(self):
        queued = self.client.post("/api/v1/agents/lead/environment", json={
            "environmentId": A["env"], "runtime": "codex", "model": "gpt-5", "workspace": "/work/lead",
            "runtimeConfig": {"effort": "high"}, "requestedBy": "dashboard"})
        self.assertEqual(queued.status_code, 200, queued.text)
        self.assertEqual(self.patches("lead"), [{"harness": "codex", "model": "gpt-5", "workspace": "/work/lead", "effort": "high"}])
        self.assertEqual(self.rows("SELECT runtime, cwd FROM agents WHERE id = 'lead'"), [{"runtime": "claude-code", "cwd": "/work"}])
        elsewhere = self.client.post("/api/v1/agents/lead/environment", json={"environmentId": B["env"], "requestedBy": "dashboard"})
        self.assertEqual((elsewhere.status_code, elsewhere.json()["detail"]),
                         (409, '"lead" is defined on win32:host-a; assign it an environment on that machine, not on win32:host-b'))
        nothing = self.client.post("/api/v1/agents/lead/environment", json={"environmentId": A["env"], "requestedBy": "dashboard"})
        self.assertEqual((nothing.status_code, nothing.json()["request"]), (200, None), "nothing to change, nothing queued")
        direct = self.client.post("/api/v1/agents/plain/environment", json={
            "environmentId": A["env"], "runtime": "codex", "workspace": "/work/plain", "requestedBy": "dashboard"})
        self.assertEqual(direct.status_code, 200, direct.text)
        self.assertEqual(self.rows("SELECT runtime, cwd FROM agents WHERE id = 'plain'"), [{"runtime": "codex", "cwd": "/work/plain"}],
                         "control: an undefined agent is assigned at once")

    def test_an_assignment_after_custody_moved_is_judged_against_the_new_owner(self):
        """Review of c8029614, N3: custody that moves first is what the assignment is judged against."""
        self.client.post("/api/v1/agent-definitions/lead/release",
                         json={"requestedBy": "dashboard", "machineId": A["machine"]}).raise_for_status()
        entries = [valid("lead")]
        self.client.put(f"/api/v1/environments/{B['env']}/agent-definitions", json={
            "bridgeId": B["bridge"], "machineId": B["machine"], "storeId": "t1", "revision": 1,
            "snapshotDigest": snapshot_digest(entries), "entries": entries}).raise_for_status()
        refused = self.client.post("/api/v1/agents/lead/environment", json={
            "environmentId": A["env"], "runtime": "codex", "requestedBy": "dashboard"})
        self.assertEqual((refused.status_code, refused.json()["detail"]),
                         (409, '"lead" is defined on win32:host-b; assign it an environment on that machine, not on win32:host-a'))
        self.assertEqual(self.patches("lead"), [])

    def test_custody_cannot_move_between_the_owner_read_and_the_queue(self):
        """Review of c8029614, N3: the owner is read inside the assignment's write transaction, so a
        release or acquisition trying to commit right after that read is held off until the request is
        queued against the owner it was judged by."""
        import service.api_core.definition_requests as requests_module

        def another_writer_is_blocked():
            conn = sqlite3.connect(str(self._db_path), timeout=0.2, isolation_level=None)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("ROLLBACK")
                return False
            except sqlite3.OperationalError as locked:
                return "locked" in str(locked)
            finally:
                conn.close()

        self.assertFalse(another_writer_is_blocked(), "control: the probe can say a writer is not blocked")
        observed, real = [], requests_module.defined_on

        async def owner_then_probe(db, agent_id):
            owner = await real(db, agent_id)
            observed.append(another_writer_is_blocked())
            return owner

        requests_module.defined_on = owner_then_probe
        self.addCleanup(setattr, requests_module, "defined_on", real)
        queued = self.client.post("/api/v1/agents/lead/environment", json={
            "environmentId": A["env"], "runtime": "codex", "requestedBy": "dashboard"})
        self.assertEqual(queued.status_code, 200, queued.text)
        self.assertEqual(observed, [True], "a competing writer was held off after the owner was read")
        self.assertEqual(self.rows("SELECT machine_id, store_id FROM definition_requests WHERE agent_id = 'lead'"),
                         [{"machine_id": A["machine"], "store_id": "s1"}])

    def test_an_assignment_to_a_runtime_no_definition_can_name_is_refused(self):
        refused = self.client.post("/api/v1/agents/lead/environment", json={
            "environmentId": A["env"], "runtime": "pi", "requestedBy": "dashboard"})
        self.assertEqual((refused.status_code, refused.json()["detail"]),
                         (422, 'runtime "pi" is not a harness a definition can name (claude-code, codex, hermes)'))

    def test_removal(self):
        # Since D9c a defined agent's removal is the lifecycle delete: its host ends the worker, then removes it.
        publish(self, A["machine"], {"lead": (None, "available")})
        queued = self.client.delete("/api/v1/agents/lead")
        self.assertEqual(queued.status_code, 200, queued.text)
        self.assertEqual((queued.json()["request"]["action"], queued.json()["queued"]), ("delete", True))
        self.assertEqual(self.patches("lead"), [], "no definition removal under a running worker")
        self.assertEqual(len(self.rows("SELECT id FROM agents WHERE id = 'lead'")), 1, "its host removes it, not this route")
        direct = self.client.delete("/api/v1/agents/plain")
        self.assertEqual((direct.status_code, direct.json()["ok"]), (200, True))
        self.assertEqual(self.rows("SELECT id FROM agents WHERE id = 'plain'"), [], "control: an undefined agent is removed")

    def test_with_an_operator_key_only_the_operator_queues_a_change(self):
        self.client.app.state.config.operator_key = "s3cret"
        self.addCleanup(setattr, self.client.app.state.config, "operator_key", "")
        refused = self.client.delete("/api/v1/agents/lead")
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertEqual(self.patches("lead"), [])
        self.assertEqual(self.client.patch("/api/v1/agents/lead/herdr-space", json={"show": False}).status_code, 403)
        self.assertEqual(self.patches("lead"), [], "an unproven caller queues nothing")
