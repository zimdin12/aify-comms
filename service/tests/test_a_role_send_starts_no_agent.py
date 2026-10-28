"""A send to a role wakes only agents already running; it never cold-starts one.

Live 2026-10-28: one comms_send carrying a stray toRole="coder" reached 26 agents and cold-started the 16 that were
idle. A send to one agent still starts it when it is idle; a send to a role stores it, unwoken, for every holder that
is not running, and says why.
"""
import asyncio

from service.clock import now as _now
from service.db import get_db
from service.tests._base import FastApiTestCase
from service.tests.defined_agents import cold_start, define
from service.tests.env_records import report, startable

ENV_ID = "linux:test-host:default"
ROLE = "rolecast"
IDLE = "idle-holder"
RUNNING = "running-holder"
RUNTIMES = ("codex", "hermes", "claude-code")


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

    # THE SECOND BIRTH PATH (found in review of e9ed4c25, 2026-10-28). A managed-warm session whose worker has gone gets a
    # new console from _ensure_managed_pty_for_dispatch, not a spawn request, so refusing only the cold start let a role
    # send insert a terminal and a pending `start` control. The warm session is produced through the real claim and
    # running routes, with terminal capability announced only afterwards, as a host does it.
    def _warm_session_without_a_worker(self, runtime):
        def heartbeat(terminal):
            response = self.client.post("/api/v1/environments/heartbeat", json={
                "id": ENV_ID, "label": "test", "machineId": "linux:test-host", "os": "linux", "kind": "linux",
                "bridgeId": "bridge-current", "cwdRoots": ["/workspace"], "metadata": {},
                "terminal": terminal, "pty": terminal, "terminalRuntimes": list(RUNTIMES) if terminal else [],
                "runtimes": [{"runtime": r, "modes": ["managed-warm"], "capabilities": {"nativeResume": True}}
                             for r in RUNTIMES],
            })
            self.assertEqual(response.status_code, 200, response.text)

        heartbeat(False)
        define(self, IDLE, environment_id=ENV_ID, machine_id="linux:test-host", bridge_id="bridge-current",
               runtime=runtime, workspace="/workspace", role=ROLE)
        self._execute("UPDATE agents SET runtime = ?, session_mode = 'managed', launch_mode = 'auto' WHERE id = ?",
                      (runtime, IDLE))
        report(self, IDLE, records={IDLE: startable(IDLE)})
        reasons = cold_start(self, IDLE, runtime=runtime, requested_by="sender")
        self.assertEqual(len(self._spawns(IDLE)), 1, reasons)
        spawn = self._spawns(IDLE)[0]["id"]
        claim = self.client.post("/api/v1/spawn-requests/claim", json={
            "environmentId": ENV_ID, "bridgeId": "bridge-current", "machineId": "linux:test-host"})
        self.assertEqual(claim.status_code, 200, claim.text)
        running = self.client.patch(f"/api/v1/spawn-requests/{spawn}", json={
            "status": "running", "bridgeId": "bridge-current", "processId": "1234", "sessionHandle": "native-context"})
        self.assertEqual(running.status_code, 200, running.text)
        heartbeat(True)
        report(self, IDLE, records={IDLE: startable(IDLE)})
        self.assertEqual(self._starts(), [], "control: no start control before the send")

    def _starts(self):
        return self._fetchall("SELECT id FROM terminal_controls WHERE action = 'start'")

    def test_a_role_send_starts_no_console_for_a_warm_session_without_a_worker(self):
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                if runtime != RUNTIMES[0]:
                    self.tearDown()
                    self.setUp()
                self._warm_session_without_a_worker(runtime)
                body = self._send(toRole=ROLE)
                self.assertIn(RUNNING, [run["targetAgentId"] for run in body["dispatchRuns"]], "control: still woken")
                self.assertEqual(self._starts(), [], "a role send started a console for an idle holder")
                self.assertEqual(self._fetchall("SELECT id FROM terminal_sessions WHERE agent_id = ?", (IDLE,)), [])

    def test_a_direct_send_still_recovers_the_missing_console(self):
        # CONTROL: the same state addressed by name gets its console back, so the refusal above is the role's doing.
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                if runtime != RUNTIMES[0]:
                    self.tearDown()
                    self.setUp()
                self._warm_session_without_a_worker(runtime)
                self._send(to=IDLE)
                self.assertNotEqual(self._starts(), [], "a direct send no longer recovers the console")
