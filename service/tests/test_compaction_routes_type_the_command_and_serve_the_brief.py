"""The compaction routes, driven through the real app.

  POST /agents/{id}/compact/native         types the runtime's own command into the live console
  GET  /agents/{id}/compact/handoff-brief  the first message of a handoff's fresh session

The DECISIONS are table-tested in `test_compaction_decides_native_and_briefs_a_handoff.py`. What is
proved here is the wiring those tests cannot see: that the route reads the agent's real mode,
runtime, console and status, that a refusal queues NOTHING, and that an allowed compaction queues
exactly the command plus Enter against the right terminal with the caller audited.

The derived status is patched where a test needs a particular one: deriving `working` or `blocked`
for real needs a whole turn history, and the engine's own suites own that. One test takes the status
exactly as the service derives it, so the route is also seen reading the real thing.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from service.clock import now as _now
from service.db import get_db
from service.routers.agents import compaction as compaction_route
from service.tests._base import FastApiTestCase

ENV = "linux:test-host:default"


class CompactionRouteTests(FastApiTestCase):
    def _q(self, query: str, params=(), *, one=False, write=False):
        async def _run():
            db = await get_db()
            try:
                cursor = await db.execute(query, params)
                if write:
                    await db.commit()
                    return None
                return await (cursor.fetchone() if one else cursor.fetchall())
            finally:
                await db.close()
        return asyncio.run(_run())

    def _register(self, agent_id: str, **extra):
        response = self.client.post("/api/v1/agents", json={"agentId": agent_id, "role": "coder", **extra})
        self.assertEqual(response.status_code, 200, response.text)

    def _seed(self, agent_id: str, *, runtime="claude-code", terminal_id="term_1", command=None,
              handle="native-handle-1", workspace="/workspace/repo"):
        """A managed agent with a live, attached console of `runtime`, and a session row."""
        now = _now()
        env = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV, "label": "test", "machineId": "linux:test-host", "os": "linux", "kind": "linux",
            "bridgeId": "bridge-current", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": runtime, "modes": ["managed-warm"], "capabilities": {"interrupt": True}}],
            "metadata": {},
        })
        self.assertEqual(env.status_code, 200, env.text)
        self._register(agent_id, runtime=runtime, sessionMode="managed")
        self._q("UPDATE agents SET session_mode='managed', runtime=?, session_handle=?, runtime_state=? WHERE id=?",
                (runtime, handle, json.dumps({"consoleTerminal": {"terminalId": terminal_id, "bridgeId": "bridge-current"}}),
                 agent_id), write=True)
        self._q("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, mode, owner_mode,"
                " terminal_id, terminal_status, spawn_spec_id, spawn_request_id, status, session_handle,"
                " started_at, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"sess_{agent_id}", agent_id, ENV, runtime, workspace, "managed-warm", "managed", terminal_id,
                 "attached", None, None, "running", handle, now, now), write=True)
        self._q("INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id, runtime,"
                " workspace, command, output, status, requested_by, created_at, updated_at, stopped_at, error)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (terminal_id, f"sess_{agent_id}", agent_id, ENV, "bridge-current", runtime, workspace,
                 command or f"{runtime}-aify --aify-agent {agent_id}", "", "attached", "dashboard", now, now, None, ""),
                write=True)

    def _native(self, agent_id: str, caller="manager", *, status=None, session_id=None):
        body = {} if caller is None else {"from": caller}
        if session_id is not None:
            body["sessionId"] = session_id
        if status is None:
            return self.client.post(f"/api/v1/agents/{agent_id}/compact/native", json=body)
        async def fixed(*_a, **_k):
            return status
        with patch.object(compaction_route, "_compute_agent_status", fixed):
            return self.client.post(f"/api/v1/agents/{agent_id}/compact/native", json=body)

    def _controls(self, terminal_id="term_1"):
        return self._q("SELECT action, body, requested_by FROM terminal_controls WHERE terminal_id = ?", (terminal_id,))

    # ── native ───────────────────────────────────────────────────────────────────────────────

    def test_an_idle_managed_claude_gets_compact_typed_with_enter_and_the_caller_audited(self):
        self._seed("worker")
        self._register("manager")
        response = self._native("worker", status="online")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["command"], "/compact")
        self.assertEqual([tuple(r) for r in self._controls()], [("input", "/compact\r", "manager")])
        audit = [json.loads(r["body"]) for r in self._q(
            "SELECT body FROM terminal_events WHERE terminal_id = ? AND event_type = 'agent_console_input'", ("term_1",))]
        self.assertEqual([(a["from"], a["purpose"], a["controlId"]) for a in audit],
                         [("manager", "native-compact", data["controlId"])])

    def test_a_picked_session_that_is_not_the_live_one_is_refused_and_nothing_typed(self):
        """Review of 7f638a65: the dashboard form belongs to one session row; a historical or replaced row
        must not compact whatever session is live now."""
        self._seed("worker")
        response = self._native("worker", caller="dashboard", status="online", session_id="sess_an_older_one")
        self.assertEqual(response.json().get("refused"), "not-the-live-session", response.text)
        self.assertEqual(self._controls(), [])

    def test_CONTROL_the_live_session_picked_is_compacted(self):
        self._seed("worker")
        response = self._native("worker", caller="dashboard", status="online", session_id="sess_worker")
        self.assertTrue(response.json()["ok"], response.text)
        self.assertEqual([r["body"] for r in self._controls()], ["/compact\r"])

    def test_hermes_gets_its_own_command(self):
        self._seed("herm", runtime="hermes")
        response = self._native("herm", caller="dashboard", status="online")
        self.assertTrue(response.json()["ok"], response.text)
        self.assertEqual([r["body"] for r in self._controls()], ["/compress\r"])

    def test_a_refusal_queues_nothing(self):
        """Mid-turn, a prompt on screen, and a resident: each answered with why, and no keystroke."""
        self._seed("worker")
        self._register("manager")
        for status, code in (("working", "mid-turn"), ("blocked", "prompt-on-screen")):
            with self.subTest(status=status):
                data = self._native("worker", status=status).json()
                self.assertFalse(data["ok"], data)
                self.assertEqual(data["refused"], code)
        self._q("UPDATE agents SET session_mode='resident' WHERE id='worker'", write=True)
        data = self._native("worker", status="online").json()
        self.assertEqual(data["refused"], "resident", data)
        self.assertEqual(self._controls(), [], "a refused compaction must not reach the terminal")

    def test_the_route_reads_the_status_the_service_derives(self):
        """No patch, both directions. The fixture derives `online` and is compacted; its environment
        then goes silent, the engine derives `offline`, and the same call is refused."""
        from service.reconcilers.status_cache import _LIVE_STATE_CACHE

        self._seed("worker")
        self._register("manager")
        self.assertEqual(self.client.get("/api/v1/agents/worker").json()["agent"]["status"], "online")
        self.assertTrue(self._native("worker").json()["ok"])
        self._q("UPDATE environments SET last_seen = '2020-01-01T00:00:00Z' WHERE id = ?", (ENV,), write=True)
        _LIVE_STATE_CACHE.clear()
        self.assertEqual(self.client.get("/api/v1/agents/worker").json()["agent"]["status"], "offline")
        data = self._native("worker").json()
        self.assertEqual((data["ok"], data["refused"], data["status"]), (False, "not-at-prompt", "offline"), data)
        self.assertEqual(len(self._controls()), 1, "only the first, idle call may have queued anything")

    def test_no_live_console_is_refused_without_starting_one(self):
        """Console input lazily STARTS a console; compaction must not -- there is nothing to compact."""
        self._seed("worker")
        self._register("manager")
        self._q("UPDATE terminal_sessions SET status='stopped' WHERE id='term_1'", write=True)
        data = self._native("worker", status="online").json()
        self.assertEqual(data["refused"], "no-console", data)
        live = self._q("SELECT id FROM terminal_sessions WHERE agent_id='worker' AND status != 'stopped'")
        self.assertEqual(live, [], "no terminal may be started to satisfy a compaction")

    def test_the_caller_gate_is_console_inputs_and_admits_the_dashboard(self):
        self._seed("worker")
        missing = self._native("worker", caller=None, status="online")
        self.assertEqual(missing.status_code, 400, missing.text)
        stranger = self._native("worker", caller="ghost", status="online")
        self.assertEqual(stranger.status_code, 403, stranger.text)
        unknown = self._native("nobody", status="online")
        self.assertEqual(unknown.status_code, 404, unknown.text)
        self.assertEqual(self._controls(), [])
        dashboard = self._native("worker", caller="dashboard", status="online")
        self.assertTrue(dashboard.json()["ok"], dashboard.text)

    def test_raw_console_input_still_refuses_the_dashboard(self):
        """The dashboard exception is the compaction route's alone. Console input kept its rule."""
        self._seed("worker")
        response = self.client.post("/api/v1/agents/worker/console/input", json={"text": "x", "from": "dashboard"})
        self.assertEqual(response.status_code, 403, response.text)

    # ── handoff brief ────────────────────────────────────────────────────────────────────────

    def test_the_brief_names_the_session_its_transcript_and_the_inbox_call(self):
        self._seed("worker", workspace="C:/Docker/aify-comms")
        data = self.client.get("/api/v1/agents/worker/compact/handoff-brief").json()
        self.assertEqual(data["sourceSessionId"], "sess_worker")
        self.assertEqual(data["sessionHandle"], "native-handle-1")
        self.assertEqual(data["recentMessages"], 10)
        self.assertEqual(data["transcript"], "~/.claude/projects/C--Docker-aify-comms/native-handle-1.jsonl")
        self.assertIn('comms_inbox(agentId="worker", filter="all", limit=10, peek=true)', data["text"])
        self.assertIn("Previous session: sess_worker", data["text"])

    def test_the_brief_takes_the_count_and_the_session_and_refuses_what_is_not_this_agents(self):
        self._seed("worker")
        self._seed("other", terminal_id="term_2")
        data = self.client.get("/api/v1/agents/worker/compact/handoff-brief?recentMessages=3&sessionId=sess_worker").json()
        self.assertIn("limit=3,", data["text"])
        foreign = self.client.get("/api/v1/agents/worker/compact/handoff-brief?sessionId=sess_other")
        self.assertEqual(foreign.status_code, 404, foreign.text)
        self.assertEqual(foreign.json()["detail"], "Session 'sess_other' is not a session of agent 'worker'",
                         "another agent's session must not brief this one")
        too_many = self.client.get("/api/v1/agents/worker/compact/handoff-brief?recentMessages=81")
        self.assertEqual(too_many.status_code, 422, too_many.text)
        unknown = self.client.get("/api/v1/agents/nobody/compact/handoff-brief")
        self.assertEqual(unknown.status_code, 404, unknown.text)
