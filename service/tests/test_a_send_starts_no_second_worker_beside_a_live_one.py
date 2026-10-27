"""A send never cold-starts a managed agent whose worker is still live.

Live 2026-10-27: comms-senior-dev's hermes worker kept its attached terminal and running session while its comms
bridge stopped reporting. A send cold-started a second worker; aify-env refused it (it never starts a second worker
beside a live one), and settling that refused spawn ended the live session's row. The host's refusal is the right
answer, so the service gives it first: a live worker, by the one shared definition (`_has_live_worker_for`), refuses
the cold start, and nothing is created or ended.
"""
import asyncio

from service.api_core import dispatch_start
from service.api_core.settings import _load_settings
from service.clock import now as _now
from service.db import get_db
from service.tests._base import FastApiTestCase
from service.tests.defined_agents import define

ENV_ID = "linux:test-host:default"
AGENT = "live-worker"
TERMINAL = "term_live_worker"


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

    def _coldstart(self):
        async def _q(db):
            warnings = []
            created = await dispatch_start._coldstart_spawn_request_for_dispatch(
                db, AGENT, runtime="codex", settings=await _load_settings(db), requested_by="test", warnings=warnings)
            return created, warnings

        return self._db(_q)

    def setUp(self):
        super().setUp()
        response = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV_ID, "label": "test", "machineId": "linux:test-host", "os": "linux", "kind": "linux",
            "bridgeId": "bridge-current", "cwdRoots": ["/workspace"], "metadata": {},
            "runtimes": [{"runtime": "codex", "modes": ["managed-warm"], "capabilities": {"nativeResume": True}}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post("/api/v1/agents", json={"agentId": AGENT, "role": "coder"})
        self.assertEqual(response.status_code, 200, response.text)
        define(self, AGENT, environment_id=ENV_ID, machine_id="linux:test-host", bridge_id="bridge-current",
               runtime="codex", workspace="/workspace")
        now = _now()
        self._execute(
            """INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, mode, owner_mode,
               terminal_id, terminal_status, spawn_spec_id, spawn_request_id, status, started_at, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("sess_live", AGENT, ENV_ID, "codex", "/workspace/repo", "managed-warm", "managed", TERMINAL, "attached",
             None, None, "running", now, now))
        self._execute(
            """INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id, runtime, workspace,
               command, output, status, process_id, requested_by, created_at, updated_at, error)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (TERMINAL, "sess_live", AGENT, ENV_ID, "bridge-current", "codex", "/workspace/repo",
             f"codex-aify --aify-agent {AGENT}", "", "attached", "4242", "dashboard", now, now, ""))

    def test_a_live_worker_refuses_the_cold_start_and_its_session_survives(self):
        created, warnings = self._coldstart()
        self.assertFalse(created, "a second worker beside a live one is what the host refuses")
        self.assertTrue(any(TERMINAL in w for w in warnings), f"the refusal names the live terminal: {warnings}")
        self.assertEqual(self._fetchall("SELECT id FROM spawn_requests WHERE agent_id = ?", (AGENT,)), [])
        [session] = self._fetchall("SELECT status FROM agent_sessions WHERE id = 'sess_live'")
        self.assertEqual(session["status"], "running")

    def test_once_the_worker_is_gone_the_cold_start_proceeds(self):
        # CONTROL: the same agent with its terminal stopped has no live worker, and the cold start must still run.
        self._execute("UPDATE terminal_sessions SET status = 'stopped' WHERE id = ?", (TERMINAL,))
        created, warnings = self._coldstart()
        self.assertTrue(created, warnings)
