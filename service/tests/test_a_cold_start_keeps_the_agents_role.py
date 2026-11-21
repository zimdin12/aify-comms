"""Starting a stopped agent, from the dashboard or by messaging it, must not reset who it is.

The 0.8 plan's measured problem (docs/superpowers/plans/2026-09-30-aify-env-owns-the-agents.md): a
cold-start writes its spawn request with `role='coder'` and `name=<agent id>`
(`dispatch_start.py`), and the running transition copies the request's role and name into `agents`
(`running_spawn.py`). So a reviewer woken by a message comes back a coder. P3 is held to this test,
which is written against today's code first and must go red there.

The first start is a legacy spawn request queued before the upgrade, brought up through the real claim and
`running` routes, as `test_a_start_says_whether_it_replaces_a_live_instance.py` does; those routes stay until phase
3. The wake is a phase 2 cold start: the send path's helper queues an automatic lifecycle start, and a host claims
it, prepares its launch and attaches its worker through the lifecycle routes. Since D8 a defined agent's role is
its definition's, so this goes red if either the lifecycle launch or its attachment gives the identity away.
"""

from __future__ import annotations

import asyncio

from service.tests._base import FastApiTestCase
from service.tests.defined_agents import spawn_defined


class AColdStartKeepsTheAgentsRole(FastApiTestCase):
    DB_NAME = "aify-test-coldstart-role.db"
    ENV = "linux:role-host:default"
    MACHINE = "linux:role-host"
    BRIDGE = "bridge-role"

    def setUp(self):
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV, "machineId": self.MACHINE, "os": "linux", "kind": "linux",
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

    def _bring_up_legacy(self, spawn_id):
        claim = self.client.post("/api/v1/spawn-requests/claim", json={
            "environmentId": self.ENV, "bridgeId": self.BRIDGE, "machineId": self.MACHINE})
        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertEqual((claim.json().get("spawnRequest") or {}).get("id"), spawn_id, claim.text)
        running = self.client.patch(f"/api/v1/spawn-requests/{spawn_id}", json={"status": "running", "bridgeId": self.BRIDGE})
        self.assertEqual(running.status_code, 200, running.text)

    def _bring_up_lifecycle(self, request_id):
        """Claim, prepare and attach the automatic start `request_id` as its host does. Returns the launch."""
        host = {"bridgeId": self.BRIDGE, "machineId": self.MACHINE}
        base = f"/api/v1/environments/{self.ENV}/lifecycle-requests"
        claim = self.client.post(f"{base}/claim", json={**host, "capabilities": ["automatic-start-v1"]})
        self.assertEqual([r["id"] for r in claim.json()["requests"]], [request_id], claim.text)
        launch = self.client.post(f"{base}/{request_id}/launch", json=host)
        self.assertEqual(launch.status_code, 200, launch.text)
        launch = launch.json()["launch"]
        attached = self.client.post(f"{base}/{request_id}/attachment", json={
            **host, "terminalId": launch["terminalId"], "handle": "native-woken", "processId": 4242,
            "lifetime": "lifetime-woken"})
        self.assertEqual(attached.status_code, 200, attached.text)
        return launch

    def _identity(self, agent_id):
        return self._rows("SELECT role, name FROM agents WHERE id = ?", (agent_id,))

    def test_a_COLD_START_by_message_keeps_the_role_and_name_the_agent_was_started_with(self):
        from service.api_core.dispatch_start import _coldstart_spawn_request_for_dispatch
        from service.api_core.settings import _load_settings
        from service.db import get_db

        agent_id = "the-fixturereader"
        # Since D8 an agent starts only from its host's definition, which carries its role and name.
        created = spawn_defined(self, agent_id, environment_id=self.ENV, machine_id=self.MACHINE,
                                bridge_id=self.BRIDGE, workspace="/work", role="reviewer", name="The Reviewer")
        self._bring_up_legacy(created["id"])
        # CONTROL: the first start did set the identity this test then expects to survive.
        self.assertEqual(self._identity(agent_id), [{"role": "reviewer", "name": "The Reviewer"}])

        # Its worker is gone; a message wakes it through the cold-start helper the send path uses.
        self._rows("UPDATE spawn_requests SET status = 'running', finished_at = '2026-01-01T00:00:00Z' WHERE agent_id = ?", (agent_id,))
        # Gone means its console too: a live one refuses an automatic start (test_a_send_starts_no_second_worker_beside_a_live_one.py).
        self._rows("UPDATE terminal_sessions SET status = 'stopped' WHERE agent_id = ?", (agent_id,))
        legacy_before = self._rows("SELECT id FROM spawn_requests WHERE agent_id = ?", (agent_id,))

        async def coldstart():
            db = await get_db()
            try:
                started = await _coldstart_spawn_request_for_dispatch(
                    db, agent_id, runtime="claude-code", settings=await _load_settings(db), requested_by="sc-fixtureowner")
                await db.commit()
                return started
            finally:
                await db.close()

        self.assertTrue(asyncio.run(coldstart()), "control: the helper queued a start")
        [woken] = self.client.get(f"/api/v1/agents/{agent_id}/lifecycle-requests").json()["requests"]
        self.assertEqual((woken["action"], woken.get("startIntent"), woken["status"]), ("start", "automatic", "pending"))
        self.assertEqual(self._rows("SELECT id FROM spawn_requests WHERE agent_id = ?", (agent_id,)), legacy_before,
                         "the wake writes no legacy spawn request")
        launch = self._bring_up_lifecycle(woken["id"])

        self.assertEqual(launch["env"]["AIFY_AGENT_ROLE"], "reviewer", "the woken worker is launched as a reviewer")
        self.assertEqual(self._identity(agent_id), [{"role": "reviewer", "name": "The Reviewer"}],
                         "a cold start must not reset the agent to a coder named after its id")
