"""Only the operator queues a change to a DEFINED agent, whichever route asks (external review of 0.8.1, HIGH 1).

THE DEFECT. With `OPERATOR_KEY` set, `POST /agent-definitions/{id}/requests` refused an ordinary caller
(403), while `POST /agents/{id}/environment` and `PATCH /agents/{id}/session-mode` recorded whatever name
the caller gave and queued the same change for its host (200). A queued workspace becomes a folder
grant in aify-env, so `C:/` granted the whole drive.

THE FIX is at the one place every route queues: `definition_requests.admit` takes an `OperatorProof`,
which only `operator_authz` makes, and refuses without one. Each refusal here is paired with the
operator's own request on the same route, which must still queue, so a route refusing everything
cannot pass; and every refusal is checked to have queued nothing.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

from service.api_core.operator_authz import OPERATOR_KEY_HEADER
from service.tests._base import FastApiTestCase
from service.tests.test_agent_definition_push import A, snapshot_digest, valid

SECRET = "s3cret-operator-key"
RUNTIMES = [{"runtime": r, "modes": ["managed-warm"], "capabilities": {}} for r in ("claude-code", "codex")]


class OnlyTheOperatorChangesADefinedAgent(FastApiTestCase):
    DB_NAME = "aify-test-operator-defined-changes.db"

    def setUp(self):
        super().setUp()
        beat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": A["env"], "machineId": A["machine"], "os": "win32", "kind": "win32",
            "bridgeId": A["bridge"], "cwdRoots": ["/work"], "runtimes": RUNTIMES,
            "terminal": True, "pty": True, "terminalRuntimes": ["claude-code"], "metadata": {}})
        self.assertEqual(beat.status_code, 200, beat.text)
        entries = [valid("lead")]
        pushed = self.client.put(f"/api/v1/environments/{A['env']}/agent-definitions", json={
            "bridgeId": A["bridge"], "machineId": A["machine"], "storeId": "s1", "revision": 1,
            "snapshotDigest": snapshot_digest(entries), "entries": entries})
        self.assertEqual(pushed.status_code, 200, pushed.text)

    def queued(self):
        conn = sqlite3.connect(str(self._db_path))
        try:
            return [(r[0], json.loads(r[1])) for r in conn.execute(
                "SELECT requested_by, patch FROM definition_requests WHERE agent_id = 'lead'").fetchall()]
        finally:
            conn.close()

    def routes(self):
        """route -> send(actor, headers): each asks for a change to the defined agent `lead`."""
        def environment(actor, headers):
            return self.client.post("/api/v1/agents/lead/environment", headers=headers,
                                    json={"environmentId": A["env"], "workspace": "C:/", "requestedBy": actor})

        def session_mode(actor, headers):
            return self.client.patch("/api/v1/agents/lead/session-mode", headers=headers,
                                     json={"mode": "resident", "requestedBy": actor})

        def change_request(actor, headers):
            return self.client.post("/api/v1/agent-definitions/lead/requests", headers=headers,
                                    json={"patch": {"workspace": "C:/"}, "requestedBy": actor})

        return {"environment": environment, "session-mode": session_mode, "change-request": change_request}

    def test_an_ordinary_caller_is_refused_on_every_route_with_the_key_set_or_not(self):
        for key in (SECRET, ""):
            for name, send in self.routes().items():
                with self.subTest(route=name, key_set=bool(key)):
                    self.client.app.state.config.operator_key = key
                    refused = send("mallory", {})
                    self.assertEqual(refused.status_code, 403, refused.text)
                    self.assertEqual(self.queued(), [], "a refused change must queue nothing")

    def test_CONTROL_the_operator_queues_on_every_route(self):
        self.client.app.state.config.operator_key = SECRET
        for name, send in self.routes().items():
            with self.subTest(route=name):
                before = len(self.queued())
                done = send("dashboard", {OPERATOR_KEY_HEADER: SECRET})
                self.assertEqual(done.status_code, 200, done.text)
                self.assertEqual(len(self.queued()), before + 1, "the operator's change is queued")
                self.assertEqual(self.queued()[-1][0], "dashboard")
                # One open request per agent: settle this one so the next route can queue.
                conn = sqlite3.connect(str(self._db_path))
                try:
                    conn.execute("UPDATE definition_requests SET status = 'done'")
                    conn.commit()
                finally:
                    conn.close()

    def test_the_queue_itself_refuses_a_change_without_a_proof(self):
        """The guard is at the funnel, so a route that forgets to prove the operator still cannot queue."""
        from service.api_core.definition_requests import admit
        from service.db import get_db

        async def ask(operator):
            db = await get_db()
            try:
                await db.execute("BEGIN IMMEDIATE")
                return await admit(db, "lead", {"model": "x"}, operator, "2026-10-03T00:00:00Z")
            finally:
                await db.close()

        for operator in ("dashboard", None):
            with self.subTest(operator=operator):
                with self.assertRaises(Exception) as refused:
                    asyncio.run(ask(operator))
                self.assertEqual(getattr(refused.exception, "status_code", None), 403)
                self.assertEqual(self.queued(), [])
