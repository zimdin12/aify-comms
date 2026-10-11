"""A launch tells the host whether it may replace a live instance of its agent (`AIFY_START_INTENT`).

aify-wrapper's launchers hold one lease per agent per host: REPLACE stops a live instance, START is
refused by one. The operator's rule (2026-09-14) is that only an explicit start replaces -- the
dashboard, or a restart/recreate -- and everything automatic is a start. `api_core/start_intent.py` has
the history.

These drive the real routes: the claim and the `running` report bring up the request's terminal the way
production does, and the launch route hands the intent to the host.

SINCE D8 THE ONLY LEGACY SPAWN REQUEST IS A MESSAGE'S COLD START of a defined agent, and it is a START.
The spawn route defines the agent instead, and the dashboard's start, restart and recreate of a defined
agent go to its host's lifecycle queue (test_legacy_routes_delegate_defined_agents.py), so the tests of
the intent those used to store went with them. A request's intent is still carried to its launch, so
`_spawn` sets the one a test needs on the request a cold start queued.
"""

from __future__ import annotations

import asyncio

from service.api_core.launch_env import ALWAYS_SET, managed_launch_env
from service.api_core.start_intent import (
    REPLACE,
    START,
    normalize_start_intent,
)
from service.tests._base import FastApiTestCase
from service.tests.defined_agents import spawn_defined


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

    def _spawn(self, agent_id, intent=START):
        """A defined agent's queued spawn request carrying `intent`. A cold start queues a START; a REPLACE
        is the intent a dashboard start's request carried before D8, and one queued then still runs."""
        spawn_id = spawn_defined(self, agent_id, environment_id=self.ENV, machine_id="linux:intent-host",
                                 bridge_id=self.BRIDGE, workspace="/work")["id"]
        self._rows("UPDATE spawn_requests SET start_intent = ? WHERE id = ?", (intent, spawn_id))
        return spawn_id

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

    def test_THE_REQUESTS_OWN_TERMINAL_carries_its_intent_into_the_launch(self):
        for created_by, want in (("dashboard", REPLACE), ("sc-manager", START)):
            with self.subTest(created_by):
                agent_id = f"up-{created_by}"
                self._bring_up(self._spawn(agent_id, intent=want))
                terminals = self._terminals(agent_id)
                self.assertEqual(len(terminals), 1, f"control: the running report brought up one terminal, got {terminals}")
                self.assertEqual(terminals[0]["requested_by"], "spawn-request")
                self.assertEqual(terminals[0]["start_intent"], want)
                self.assertEqual(self._launch_intent(terminals[0]["id"]), want)

    def test_THE_LAUNCH_UNSETS_AN_INHERITED_AGENT_LEASE(self):
        """A host started from an agent's shell carries that agent's AIFY_AGENT_LEASE. Inherited by a
        worker, it makes every start of that agent through the host read as nested in its own live
        instance, so even the dashboard's replace is refused."""
        agent_id = "lease-unset"
        self._bring_up(self._spawn(agent_id, intent=REPLACE))
        terminal = self._terminals(agent_id)[0]
        launch = self.client.get(f"/api/v1/terminals/{terminal['id']}/launch").json()["launch"]
        self.assertIn("AIFY_AGENT_ID", launch["unsetEnv"], "control: the unset list is the one the host applies")
        self.assertIn("AIFY_AGENT_LEASE", launch["unsetEnv"])
        self.assertNotIn("AIFY_AGENT_LEASE", launch["env"])

    def test_ANY_OTHER_terminal_launches_as_a_start(self):
        """A PTY recovered for a dispatch, or anything relaunched later, was not asked for by anybody: an
        old REPLACE must never reach it, or a message could kill a live instance."""
        agent_id = "other-terminal"
        self._bring_up(self._spawn(agent_id, intent=REPLACE))
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

    def test_a_worker_exit_settles_its_spawn_immediately_with_the_exit_reason(self):
        agent_id = "early-exit"
        spawn_id = self._spawn(agent_id)
        self._bring_up(spawn_id)
        terminal_id = self._terminals(agent_id)[0]["id"]
        ended = self.client.post(f"/api/v1/terminals/{terminal_id}/output", json={
            "bridgeId": self.BRIDGE, "status": "stopped", "exitCode": 75,
            "output": "[aify] a live instance already holds this agent lease\n[terminal exited]\n"})
        self.assertEqual(ended.status_code, 200, ended.text)
        spawn = self._rows("SELECT status, finished_at, error FROM spawn_requests WHERE id = ?", (spawn_id,))[0]
        self.assertEqual(spawn["status"], "failed", "do not wait for a reconciler after the host reported an exit")
        self.assertTrue(spawn["finished_at"])
        self.assertIn("75", spawn["error"])
        self.assertIn("live instance", spawn["error"])

    def test_a_late_start_completion_cannot_resurrect_an_exited_worker(self):
        agent_id = "exit-before-completion"
        self._bring_up(self._spawn(agent_id))
        terminal_id = self._terminals(agent_id)[0]["id"]
        claimed = self.client.post("/api/v1/terminals/controls/claim", json={"environmentId": self.ENV, "bridgeId": self.BRIDGE})
        self.assertEqual(claimed.status_code, 200, claimed.text)
        control_id = next(c["id"] for c in claimed.json()["controls"] if c["terminalId"] == terminal_id and c["action"] == "start")
        ended = self.client.post(f"/api/v1/terminals/{terminal_id}/output", json={"bridgeId": self.BRIDGE, "status": "stopped", "exitCode": 75, "output": "[terminal exited]\n"})
        self.assertEqual(ended.status_code, 200, ended.text)
        completed = self.client.patch(f"/api/v1/terminals/controls/{control_id}", json={"status": "completed", "terminalStatus": "attached", "processId": "12345"})
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(self._rows("SELECT status, exit_code FROM terminal_sessions WHERE id = ?", (terminal_id,)), [{"status": "stopped", "exit_code": 75}])

    def test_the_ending_fatal_line_survives_an_earlier_in_flight_output_batch(self):
        import httpx
        from unittest.mock import patch
        from service.db import get_db
        from service.terminal_write_queue import TERMINAL_OUTPUT_WRITES as queue

        spawn_id = self._spawn("in-flight-exit")
        self._bring_up(spawn_id)
        terminal_id = self._terminals("in-flight-exit")[0]["id"]

        async def exercise():
            earlier_entered, release_earlier, ending_enqueued = asyncio.Event(), asyncio.Event(), asyncio.Event()
            write, enqueue = queue._write_terminal_output, queue.enqueue

            async def held_write(*args, **kwargs):
                earlier_entered.set()
                await release_earlier.wait()
                return await write(*args, **kwargs)

            async def witnessed_enqueue(*args, **kwargs):
                result = await enqueue(*args, **kwargs)
                if kwargs.get("status") == "stopped":
                    ending_enqueued.set()
                return result

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self._app), base_url="http://test") as client:
                with patch.object(queue, "_write_terminal_output", held_write), patch.object(queue, "enqueue", witnessed_enqueue):
                    await queue.enqueue(terminal_id, "earlier startup output\n", autoschedule=False)
                    earlier = asyncio.create_task(queue.flush_terminal(terminal_id))
                    queue._track_flush_task(terminal_id, earlier)
                    await earlier_entered.wait()
                    ending = asyncio.create_task(client.post(f"/api/v1/terminals/{terminal_id}/output", json={"bridgeId": self.BRIDGE, "status": "stopped", "exitCode": 75, "output": "fatal: ending-batch-only lease refusal\n[terminal exited]\n"}))
                    try:
                        await asyncio.wait_for(ending_enqueued.wait(), 5)
                        self.assertIn("ending-batch-only", "".join(queue._pending[terminal_id]["chunks"]))
                        release_earlier.set()
                        response = await ending
                        self.assertEqual(response.status_code, 200, response.text)
                        db = await get_db()
                        try:
                            spawn = await (await db.execute("SELECT status, error FROM spawn_requests WHERE id = ?", (spawn_id,))).fetchone()
                            observed = dict(spawn)
                        finally:
                            await db.close()
                    finally:
                        release_earlier.set()
                        await asyncio.gather(earlier, ending, return_exceptions=True)
                await queue.flush_all()
                return observed

        observed = asyncio.run(exercise())
        self.assertEqual(observed["status"], "failed")
        self.assertIn("75", observed["error"])
        self.assertIn("ending-batch-only", observed["error"])

    def test_an_AUTOMATIC_cold_start_is_a_start(self):
        """The send path and the queued-run backstop cold-start a lane through one helper, and a message
        waking a lane must never replace a live instance. Its default is what they all get."""
        from service.api_core.dispatch_start import _coldstart_spawn_request_for_dispatch
        from service.api_core.settings import _load_settings
        from service.db import get_db

        agent_id = "woken-by-a-message"
        self._bring_up(self._spawn(agent_id, intent=REPLACE))
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
        newest = self._rows("SELECT start_intent FROM spawn_requests WHERE agent_id = ? ORDER BY rowid DESC LIMIT 1", (agent_id,))
        self.assertEqual(newest, [{"start_intent": START}])


class StartIntentValues(FastApiTestCase):
    DB_NAME = "aify-test-start-intent-values.db"

    def test_anything_unrecognised_reads_as_a_start(self):
        for value in (None, "", "bogus", 1, "start ", "replace-all"):
            self.assertEqual(normalize_start_intent(value), START, repr(value))
        self.assertEqual(normalize_start_intent(" Replace "), REPLACE, "control: a real REPLACE survives")

    def test_the_launch_always_writes_it(self):
        self.assertIn("AIFY_START_INTENT", ALWAYS_SET)
        self.assertEqual(managed_launch_env(terminal={"agentId": "a"})["AIFY_START_INTENT"], START)
        self.assertEqual(managed_launch_env(terminal={"agentId": "a"}, start_intent="nonsense")["AIFY_START_INTENT"], START)
        # A spawn cannot set it: the whole AIFY_ prefix is the launch's.
        env = managed_launch_env(terminal={"agentId": "a"}, start_intent=REPLACE, spawn_env={"aify_start_intent": "start"})
        self.assertEqual([k for k in env if k.upper() == "AIFY_START_INTENT"], ["AIFY_START_INTENT"])
        self.assertEqual(env["AIFY_START_INTENT"], REPLACE)
