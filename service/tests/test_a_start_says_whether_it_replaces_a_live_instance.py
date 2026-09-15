"""A launch tells the host whether it may replace a live instance of its agent (`AIFY_START_INTENT`).

aify-wrapper's launchers hold one lease per agent per host: REPLACE stops a live instance, START is
refused by one. The operator's rule (2026-09-14) is that only an explicit start replaces -- the
dashboard, or a restart/recreate -- and everything automatic is a start. `api_core/start_intent.py` has
the history.

These drive the real routes: the spawn route stores the intent, the claim and the `running` report bring
up the request's terminal the way production does, and the launch route hands the intent to the host.
"""

from __future__ import annotations

import asyncio

from service.api_core.launch_env import ALWAYS_SET, managed_launch_env
from service.api_core.start_intent import REPLACE, START, normalize_start_intent, start_intent_for_requester
from service.tests._base import FastApiTestCase


class AStartSaysWhetherItReplacesALiveInstance(FastApiTestCase):
    DB_NAME = "aify-test-start-intent.db"
    ENV = "linux:intent-host:default"
    BRIDGE = "bridge-intent"

    def setUp(self):
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV, "machineId": "linux:intent-host", "os": "linux", "kind": "linux",
            "bridgeId": self.BRIDGE, "cwdRoots": ["/work"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            # A host that can open a terminal, as aify-env advertises one: without these no PTY comes up.
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

    def _spawn(self, agent_id, **extra):
        body = {"agentId": agent_id, "environmentId": self.ENV, "runtime": "claude-code", "role": "coder", "workspace": "/work"}
        body.update(extra)
        created = self.client.post("/api/v1/spawn-requests", json=body)
        self.assertEqual(created.status_code, 200, created.text)
        return created.json()["spawnRequest"]["id"]

    def _bring_up(self, spawn_id):
        """Claim the request and report it running, which is what creates its terminal in production."""
        claim = self.client.post("/api/v1/spawn-requests/claim", json={
            "environmentId": self.ENV, "bridgeId": self.BRIDGE, "machineId": "linux:intent-host"})
        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertEqual((claim.json().get("spawnRequest") or {}).get("id"), spawn_id, claim.text)
        running = self.client.patch(f"/api/v1/spawn-requests/{spawn_id}", json={"status": "running", "bridgeId": self.BRIDGE})
        self.assertEqual(running.status_code, 200, running.text)

    def _terminals(self, agent_id):
        return self._rows("SELECT id, start_intent, requested_by FROM terminal_sessions WHERE agent_id = ? ORDER BY rowid", (agent_id,))

    def _launch_intent(self, terminal_id):
        response = self.client.get(f"/api/v1/terminals/{terminal_id}/launch")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["launch"]["env"]["AIFY_START_INTENT"]

    def test_the_REQUESTER_decides_what_is_stored(self):
        cases = {"dashboard": REPLACE, None: REPLACE, "sc-manager": START}
        for created_by, want in cases.items():
            with self.subTest(created_by):
                extra = {} if created_by is None else {"createdBy": created_by}
                spawn_id = self._spawn(f"stored-{created_by or 'omitted'}", **extra)
                self.assertEqual(self._rows("SELECT start_intent FROM spawn_requests WHERE id = ?", (spawn_id,)), [{"start_intent": want}])

    def test_THE_REQUESTS_OWN_TERMINAL_carries_its_intent_into_the_launch(self):
        for created_by, want in (("dashboard", REPLACE), ("sc-manager", START)):
            with self.subTest(created_by):
                agent_id = f"up-{created_by}"
                self._bring_up(self._spawn(agent_id, createdBy=created_by))
                terminals = self._terminals(agent_id)
                self.assertEqual(len(terminals), 1, f"control: the running report brought up one terminal, got {terminals}")
                self.assertEqual(terminals[0]["requested_by"], "spawn-request")
                self.assertEqual(terminals[0]["start_intent"], want)
                self.assertEqual(self._launch_intent(terminals[0]["id"]), want)

    def test_ANY_OTHER_terminal_launches_as_a_start(self):
        """A PTY recovered for a dispatch, or anything relaunched later, was not asked for by anybody: an
        old REPLACE must never reach it, or a message could kill a live instance."""
        agent_id = "other-terminal"
        self._bring_up(self._spawn(agent_id, createdBy="dashboard"))
        first = self._terminals(agent_id)[0]
        self.assertEqual(first["start_intent"], REPLACE, "control: the request's own terminal replaces")
        from service.api_core.managed_pty_for_dispatch import _ensure_managed_pty_for_dispatch
        from service.api_core.settings import _load_settings
        from service.db import get_db

        async def recover():
            db = await get_db()
            try:
                await db.execute("UPDATE terminal_sessions SET status = 'stopped' WHERE id = ?", (first["id"],))
                await db.execute("UPDATE agent_sessions SET terminal_id = '', terminal_status = '' WHERE agent_id = ?", (agent_id,))
                terminal = await _ensure_managed_pty_for_dispatch(
                    db, agent_id, runtime="claude-code", settings=await _load_settings(db), requested_by="dispatch")
                await db.commit()
                return terminal
            finally:
                await db.close()

        recovered = asyncio.run(recover())
        self.assertIsNotNone(recovered, "control: the dispatch path brought up a terminal")
        second = [t for t in self._terminals(agent_id) if t["id"] != first["id"]]
        self.assertEqual([t["start_intent"] for t in second], [START])
        self.assertEqual(self._launch_intent(second[0]["id"]), START)

    def test_a_RESTART_replaces_even_when_an_agent_asks_for_it(self):
        agent_id = "restarted"
        self._bring_up(self._spawn(agent_id, createdBy="dashboard"))
        session_id = self._rows("SELECT id FROM agent_sessions WHERE agent_id = ?", (agent_id,))[0]["id"]
        for action in ("restart", "recreate"):
            with self.subTest(action):
                self._rows("UPDATE spawn_requests SET status = 'running' WHERE agent_id = ?", (agent_id,))
                control = self.client.post(f"/api/v1/sessions/{session_id}/control", json={"action": action, "from_agent": "sc-manager"})
                self.assertEqual(control.status_code, 200, control.text)
                newest = self._rows("SELECT start_intent, status FROM spawn_requests WHERE agent_id = ? ORDER BY rowid DESC LIMIT 1", (agent_id,))
                self.assertEqual(newest[0]["start_intent"], REPLACE, newest)
                self.assertEqual(newest[0]["status"], "queued", "control: the restart queued a new request")

    def test_the_START_BUTTON_replaces_and_an_agent_starting_one_does_not(self):
        for from_agent, want in (("dashboard", REPLACE), ("sc-manager", START)):
            with self.subTest(from_agent):
                agent_id = f"started-by-{from_agent}"
                registered = self.client.post("/api/v1/agents", json={
                    "agentId": agent_id, "role": "coder", "runtime": "claude-code", "sessionMode": "managed",
                    "machineId": "linux:intent-host", "bridgeId": self.BRIDGE})
                self.assertEqual(registered.status_code, 200, registered.text)
                self._rows("UPDATE agents SET session_mode = 'managed' WHERE id = ?", (agent_id,))
                started = self.client.post(f"/api/v1/agents/{agent_id}/control", json={"action": "start", "from_agent": from_agent})
                self.assertEqual(started.status_code, 200, started.text)
                self.assertTrue(started.json().get("spawnRequested"), f"control: the button queued a spawn: {started.text}")
                rows = self._rows("SELECT start_intent FROM spawn_requests WHERE agent_id = ?", (agent_id,))
                self.assertEqual(rows, [{"start_intent": want}])


class StartIntentValues(FastApiTestCase):
    DB_NAME = "aify-test-start-intent-values.db"

    def test_anything_unrecognised_reads_as_a_start(self):
        for value in (None, "", "bogus", "REPLACE ", 1):
            self.assertIn(normalize_start_intent(value), (START, REPLACE))
        self.assertEqual(normalize_start_intent("bogus"), START)
        self.assertEqual(normalize_start_intent(" Replace "), REPLACE)
        self.assertEqual(start_intent_for_requester("  "), REPLACE)

    def test_the_launch_always_writes_it(self):
        self.assertIn("AIFY_START_INTENT", ALWAYS_SET)
        self.assertEqual(managed_launch_env(terminal={"agentId": "a"})["AIFY_START_INTENT"], START)
        self.assertEqual(managed_launch_env(terminal={"agentId": "a"}, start_intent="nonsense")["AIFY_START_INTENT"], START)
        # A spawn cannot set it: the whole AIFY_ prefix is the launch's.
        env = managed_launch_env(terminal={"agentId": "a"}, start_intent=REPLACE, spawn_env={"aify_start_intent": "start"})
        self.assertEqual([k for k in env if k.upper() == "AIFY_START_INTENT"], ["AIFY_START_INTENT"])
        self.assertEqual(env["AIFY_START_INTENT"], REPLACE)
