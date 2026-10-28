"""A send to a role wakes only agents already running; it never cold-starts one.

Live 2026-10-28: one comms_send carrying a stray toRole="coder" reached 26 agents and cold-started the 16 that were
idle. A send to one agent still starts it when it is idle; a send to a role stores it, unwoken, for every holder that
is not running, and says why.
"""
import asyncio

from service.clock import now as _now
from service.db import get_db
from service.tests._base import FastApiTestCase
from service.tests.defined_agents import define
from service.tests.env_records import report

ENV_ID = "linux:test-host:default"
ROLE = "rolecast"
IDLE = "idle-holder"
RUNNING = "running-holder"


class ARoleSendStartsNoAgent(FastApiTestCase):
    DB_NAME = "aify-role-send-test.db"

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

    def _holder(self, agent):
        response = self.client.post("/api/v1/agents", json={"agentId": agent, "role": ROLE})
        self.assertEqual(response.status_code, 200, response.text)
        define(self, agent, environment_id=ENV_ID, machine_id="linux:test-host", bridge_id="bridge-current",
               runtime="codex", workspace="/workspace", role=ROLE)
        self._execute("UPDATE agents SET runtime = 'codex', session_mode = 'managed', launch_mode = 'auto' WHERE id = ?",
                      (agent,))
        report(self, agent)

    def setUp(self):
        super().setUp()
        response = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV_ID, "label": "test", "machineId": "linux:test-host", "os": "linux", "kind": "linux",
            "bridgeId": "bridge-current", "cwdRoots": ["/workspace"], "metadata": {},
            "runtimes": [{"runtime": "codex", "modes": ["managed-warm"], "capabilities": {"nativeResume": True}}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        self._holder(IDLE)
        self._holder(RUNNING)
        now = _now()
        self._execute(
            """INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, mode, owner_mode,
               terminal_id, terminal_status, spawn_spec_id, spawn_request_id, status, started_at, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("sess-running", RUNNING, ENV_ID, "codex", "/workspace/repo", "managed-warm", "managed", "term-running",
             "attached", None, None, "running", now, now))
        self._execute(
            """INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id, runtime, workspace,
               command, output, status, process_id, requested_by, created_at, updated_at, error)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("term-running", "sess-running", RUNNING, ENV_ID, "bridge-current", "codex", "/workspace/repo",
             f"codex-aify --aify-agent {RUNNING}", "", "attached", "4242", "dashboard", now, now, ""))

    def _send(self, **target):
        response = self.client.post("/api/v1/messages/send", json={
            "from_agent": "sender", "type": "request", "subject": "work", "body": "do it", "trigger": True, **target,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _spawns(self, agent):
        return self._fetchall("SELECT id FROM spawn_requests WHERE agent_id = ?", (agent,))

    def test_a_role_send_starts_nobody_and_wakes_the_running_holder(self):
        body = self._send(toRole=ROLE)
        self.assertEqual(sorted(body["recipients"]), sorted([IDLE, RUNNING]), "control: the role reached both holders")
        self.assertEqual(self._spawns(IDLE), [], "a role send cold-started an idle holder")
        stored = {hint["targetAgentId"]: hint["reason"] for hint in body["notStarted"]}
        self.assertIn("wakes only agents already running", stored.get(IDLE, ""), body["notStarted"])
        self.assertNotIn(RUNNING, stored, "the running holder must still be woken")
        self.assertIn(RUNNING, [run["targetAgentId"] for run in body["dispatchRuns"]], body["dispatchRuns"])
        self.assertEqual(len(self._fetchall("SELECT id FROM messages WHERE to_agent = ?", (IDLE,))), 1,
                         "the idle holder still gets the message in its inbox")

    def test_a_send_to_the_one_idle_agent_still_starts_it(self):
        # CONTROL: the same idle agent addressed by name is cold-started, so the refusal above is the role's doing.
        self._send(to=IDLE)
        self.assertNotEqual(self._spawns(IDLE), [], "a direct send no longer starts an idle agent")
