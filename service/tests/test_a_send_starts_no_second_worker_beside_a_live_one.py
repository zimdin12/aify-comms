"""A send never cold-starts a managed agent whose worker is still live.

Live 2026-10-27: comms-senior-dev's hermes worker kept its attached terminal and running session while its comms
bridge stopped reporting. A send cold-started a second worker; aify-env refused it (it never starts a second worker
beside a live one), and settling that refused spawn ended the live session's row. The host's refusal is the right
answer, so the service gives it first: a live managed session with a live console refuses the cold start whether or
not its bridge reports, and the message queues for that worker.

Every runtime goes through the public send route with no bridge reporting. Claude and hermes are the cases that
matter: their delivery readiness also wants a fresh channel sidecar, and a guard built on readiness let both through.
"""
import asyncio

from service.clock import now as _now
from service.db import get_db
from service.tests._base import FastApiTestCase
from service.tests.defined_agents import define
from service.tests.env_records import report

ENV_ID = "linux:test-host:default"
RUNTIMES = ("codex", "hermes", "claude-code")


class ASendStartsNoSecondWorkerBesideALiveOne(FastApiTestCase):
    DB_NAME = "aify-no-second-worker-test.db"

    def _db(self, fn):
        async def _run():
            db = await get_db()
            try:
                result = await fn(db)
                await db.commit()
                return result
            finally:
                await db.close()

        return asyncio.run(_run())

    def _execute(self, query, params=()):
        return self._db(lambda db: db.execute(query, params))

    def _fetchall(self, query, params=()):
        async def _q(db):
            return await (await db.execute(query, params)).fetchall()

        return self._db(_q)

    def _seed_live_worker(self, runtime, terminal_status="attached"):
        """A managed agent with a running session and a console in `terminal_status`, and no bridge reporting."""
        agent = f"live-{runtime}"
        response = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV_ID, "label": "test", "machineId": "linux:test-host", "os": "linux", "kind": "linux",
            "bridgeId": "bridge-current", "cwdRoots": ["/workspace"], "metadata": {},
            "runtimes": [{"runtime": r, "modes": ["managed-warm"], "capabilities": {"nativeResume": True}} for r in RUNTIMES],
        })
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post("/api/v1/agents", json={"agentId": agent, "role": "coder"})
        self.assertEqual(response.status_code, 200, response.text)
        define(self, agent, environment_id=ENV_ID, machine_id="linux:test-host", bridge_id="bridge-current",
               runtime=runtime, workspace="/workspace")
        self._execute("UPDATE agents SET runtime = ?, session_mode = 'managed', launch_mode = 'auto', runtime_config = ? "
                      "WHERE id = ?", (runtime, '{"channelEnabled":true}', agent))
        now = _now()
        launcher = "claude-aify" if runtime == "claude-code" else f"{runtime}-aify"
        self._execute(
            """INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, mode, owner_mode,
               terminal_id, terminal_status, spawn_spec_id, spawn_request_id, status, started_at, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"sess-{agent}", agent, ENV_ID, runtime, "/workspace/repo", "managed-warm", "managed", f"term-{agent}",
             terminal_status, None, None, "running", now, now))
        self._execute(
            """INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id, runtime, workspace,
               command, output, status, process_id, requested_by, created_at, updated_at, error)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"term-{agent}", f"sess-{agent}", agent, ENV_ID, "bridge-current", runtime, "/workspace/repo",
             f"{launcher} --aify-agent {agent}", "", terminal_status, "4242", "dashboard", now, now, ""))
        report(self, agent)
        return agent

    def _send(self, agent):
        response = self.client.post("/api/v1/messages/send", json={
            "from_agent": "sender", "to": agent, "type": "request", "subject": "work", "body": "do it", "trigger": True,
        })
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["stored"], body)
        self.assertTrue(body["dispatchRuns"], f"the send must dispatch, or a refusal proves nothing: {body}")
        return [r["id"] for r in self._fetchall("SELECT id FROM spawn_requests WHERE agent_id = ?", (agent,))]

    def test_a_live_console_with_a_silent_bridge_gets_no_second_worker(self):
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                agent = self._seed_live_worker(runtime)
                self.assertEqual(self._send(agent), [], "a second worker beside a live one is what the host refuses")
                [session] = self._fetchall("SELECT status FROM agent_sessions WHERE agent_id = ?", (agent,))
                self.assertEqual(session["status"], "running")

    def test_once_the_console_is_stopped_the_send_cold_starts(self):
        # CONTROL: the same agent with its console stopped has no live worker, and the send must still start one.
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                agent = self._seed_live_worker(runtime, terminal_status="stopped")
                self.assertNotEqual(self._send(agent), [], "with no live worker the send starts one")
