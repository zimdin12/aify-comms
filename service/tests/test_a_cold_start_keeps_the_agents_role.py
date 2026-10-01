"""Starting a stopped agent, from the dashboard or by messaging it, must not reset who it is.

The 0.8 plan's measured problem (docs/superpowers/plans/2026-09-30-aify-env-owns-the-agents.md): a
cold-start writes its spawn request with `role='coder'` and `name=<agent id>`
(`dispatch_start.py`), and the running transition copies the request's role and name into `agents`
(`running_spawn.py`). So a reviewer woken by a message comes back a coder. P3 is held to this test,
which is written against today's code first and must go red there.

It drives the real routes: the spawn route, the claim, and the `running` report that brings a request
up, as `test_a_start_says_whether_it_replaces_a_live_instance.py` does.
"""

from __future__ import annotations

import asyncio

from service.tests._base import FastApiTestCase


class AColdStartKeepsTheAgentsRole(FastApiTestCase):
    DB_NAME = "aify-test-coldstart-role.db"
    ENV = "linux:role-host:default"
    BRIDGE = "bridge-role"

    def setUp(self):
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV, "machineId": "linux:role-host", "os": "linux", "kind": "linux",
            "bridgeId": self.BRIDGE, "cwdRoots": ["/work"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "terminal": True, "pty": True, "terminalRuntimes": ["claude-code"],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)

    def _rows(self, sql, params=()):
        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                rows = [dict(r) for r in await (await db.execute(sql, params)).fetchall()]
                await db.commit()
                return rows
            finally:
                await db.close()

        return asyncio.run(go())

    def _bring_up(self, spawn_id):
        claim = self.client.post("/api/v1/spawn-requests/claim", json={
            "environmentId": self.ENV, "bridgeId": self.BRIDGE, "machineId": "linux:role-host"})
        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertEqual((claim.json().get("spawnRequest") or {}).get("id"), spawn_id, claim.text)
        running = self.client.patch(f"/api/v1/spawn-requests/{spawn_id}", json={"status": "running", "bridgeId": self.BRIDGE})
        self.assertEqual(running.status_code, 200, running.text)

    def _identity(self, agent_id):
        return self._rows("SELECT role, name FROM agents WHERE id = ?", (agent_id,))

    def test_a_COLD_START_by_message_keeps_the_role_and_name_the_agent_was_started_with(self):
        from service.api_core.dispatch_start import _coldstart_spawn_request_for_dispatch
        from service.api_core.settings import _load_settings
        from service.db import get_db

        agent_id = "the-reviewer"
        created = self.client.post("/api/v1/spawn-requests", json={
            "agentId": agent_id, "environmentId": self.ENV, "runtime": "claude-code",
            "role": "reviewer", "name": "The Reviewer", "workspace": "/work", "createdBy": "dashboard",
        })
        self.assertEqual(created.status_code, 200, created.text)
        self._bring_up(created.json()["spawnRequest"]["id"])
        # CONTROL: the first start did set the identity this test then expects to survive.
        self.assertEqual(self._identity(agent_id), [{"role": "reviewer", "name": "The Reviewer"}])

        # Its worker is gone; a message wakes it through the cold-start helper the send path uses.
        self._rows("UPDATE spawn_requests SET status = 'running', finished_at = '2026-01-01T00:00:00Z' WHERE agent_id = ?", (agent_id,))

        async def coldstart():
            db = await get_db()
            try:
                started = await _coldstart_spawn_request_for_dispatch(
                    db, agent_id, runtime="claude-code", settings=await _load_settings(db), requested_by="sc-manager")
                await db.commit()
                return started
            finally:
                await db.close()

        self.assertTrue(asyncio.run(coldstart()), "control: the helper created a request")
        woken = self._rows("SELECT id FROM spawn_requests WHERE agent_id = ? AND status = 'queued'", (agent_id,))
        self.assertEqual(len(woken), 1, woken)
        self._bring_up(woken[0]["id"])

        self.assertEqual(self._identity(agent_id), [{"role": "reviewer", "name": "The Reviewer"}],
                         "a cold start must not reset the agent to a coder named after its id")
