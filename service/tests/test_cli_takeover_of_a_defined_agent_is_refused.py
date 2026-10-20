"""A CLI takeover of a defined agent is refused before it writes anything (B2, path 2).

`cli_takeover` stopped the managed worker in comms alone: the agent read `stopped` with `launch_mode='none'`, and
aify-env, which owns a defined agent's lifecycle, never heard of it. Handing it to aify-env as a stop is no better:
aify-env's stop fact publishes `stopped` ahead of any process (its agent-state derivation), so the operator's CLI
would run under a stopped row. A defined agent's takeover is a flip of its definition to resident, which comms
follows; the refusal says so.

THE FENCE IS THE TAKEOVER HANDLER'S OWN. The definition check and an undefined agent's legacy writes share one
`BEGIN IMMEDIATE`, the lock a definition push takes, so a definition lands wholly before the check (refused) or
wholly after the writes. The race test drives a definition into the gap after the check; `write_fence_race.py`
says how the order is read back from the database.
"""

from __future__ import annotations

import sqlite3

import service.routers.session_control as session_control
from service.routers.api_v2 import router  # noqa: F401 -- the base builds the app from it
from service.tests._base import FastApiTestCase
from service.tests.defined_agents import define
from service.tests.write_fence_race import Rival, dump, racing

ENV = {"env": "win32:host-a:default", "machine": "win32:host-a", "bridge": "bridge-a"}
AGENT = "takeover-worker"
SESSION = "sess-takeover"
NOW = "2026-10-20T00:00:00Z"


class CliTakeoverOfADefinedAgentIsRefused(FastApiTestCase):
    DB_NAME = "aify-b2-takeover.db"

    def setUp(self):
        super().setUp()
        beat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV["env"], "machineId": ENV["machine"], "os": "win32", "kind": "win32", "bridgeId": ENV["bridge"],
            "cwdRoots": ["/work"], "runtimes": [{"runtime": "claude-code", "available": True}], "metadata": {}})
        self.assertEqual(beat.status_code, 200, beat.text)

    def _execute(self, sql: str, params=()) -> None:
        db = sqlite3.connect(self._db_path)
        try:
            db.execute(sql, params)
            db.commit()
        finally:
            db.close()

    def _one(self, sql: str, params=()):
        db = sqlite3.connect(self._db_path)
        try:
            return db.execute(sql, params).fetchone()
        finally:
            db.close()

    def _undefined_agent(self) -> None:
        response = self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "claude-code", "sessionMode": "managed",
            "launchMode": "managed", "machineId": ENV["machine"]})
        self.assertEqual(response.status_code, 200, response.text)

    def _defined_agent(self) -> None:
        define(self, AGENT, environment_id=ENV["env"], machine_id=ENV["machine"], bridge_id=ENV["bridge"],
               workspace="/work")

    def _live_work(self) -> None:
        """Everything a takeover touches: the session, a queued spawn, a running run and a live terminal."""
        self._execute("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, status, session_handle,"
                      " started_at, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
                      (SESSION, AGENT, ENV["env"], "claude-code", "/work", "running", "handle-1", NOW, NOW))
        self._execute("INSERT INTO spawn_requests (id, spawn_spec_id, environment_id, agent_id, runtime, status, created_at,"
                      " updated_at) VALUES (?,?,?,?,?,?,?,?)",
                      ("spawn-queued", "spec-1", ENV["env"], AGENT, "claude-code", "queued", NOW, NOW))
        self._execute("INSERT INTO dispatch_runs (id, from_agent, target_agent, status, requested_at) VALUES (?,?,?,?,?)",
                      ("run-live", "dashboard", AGENT, "running", NOW))
        self._execute("INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id, runtime, status,"
                      " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      ("term-live", SESSION, AGENT, ENV["env"], ENV["bridge"], "claude-code", "running", NOW, NOW))

    def _takeover(self):
        return self.client.post(f"/api/v1/sessions/{SESSION}/control",
                                json={"action": "cli_takeover", "from_agent": "dashboard"})

    def test_a_defined_agents_takeover_is_refused_and_writes_nothing(self):
        self._defined_agent()
        self._live_work()
        before, broadcasts = dump(self._db_path), len(self.ws.broadcasts)
        refused = self._takeover()
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertIn('" is defined by its host, so its session cannot be taken over here', refused.json()["detail"])
        self.assertEqual(dump(self._db_path), before, "a refused takeover wrote to the database")
        self.assertEqual(len(self.ws.broadcasts), broadcasts, "a refused takeover broadcast")

    def test_CONTROL_an_undefined_agents_takeover_keeps_every_legacy_effect(self):
        self._undefined_agent()
        self._live_work()
        taken = self._takeover()
        self.assertEqual(taken.status_code, 200, taken.text)
        self.assertEqual(self._one("SELECT status FROM agent_sessions WHERE id = ?", (SESSION,)), ("cli-takeover",))
        self.assertEqual(self._one("SELECT status, launch_mode FROM agents WHERE id = ?", (AGENT,)), ("stopped", "none"))
        self.assertEqual(self._one("SELECT status FROM spawn_requests WHERE id = 'spawn-queued'"), ("cancelled",))
        self.assertEqual(self._one("SELECT COUNT(*) FROM terminal_controls WHERE terminal_id = 'term-live' AND action = 'stop'"), (1,))
        self.assertTrue(taken.json()["interruptControlId"], "the running run was interrupted")
        self.assertIn("session_control_requested", [args[0] for args, _ in self.ws.broadcasts])

    def test_a_definition_racing_the_check_lands_after_the_takeover_commits(self):
        """X is undefined when the handler checks; a definition push tries to commit right then. Under the fence it
        waits for the takeover's commit; outside it, it commits first and the takeover then stops a defined agent."""
        self._undefined_agent()
        self._live_work()
        rival = Rival(self._db_path, AGENT, ENV["machine"], committed_before=lambda conn: conn.execute(
            "SELECT status FROM agent_sessions WHERE id = ?", (SESSION,)).fetchone()[0] == "cli-takeover")
        restore = racing(session_control, "defined_on", rival)
        try:
            taken = self._takeover()
        finally:
            restore()
        rival.finish()
        self.assertTrue(rival.committed, "the rival definition never committed")
        if rival.handler_committed_first:
            self.assertEqual(taken.status_code, 200, taken.text)
        else:
            self.assertEqual(taken.status_code, 409, "the definition committed first, then the takeover wrote to a defined agent")
            self.assertEqual(self._one("SELECT status FROM agent_sessions WHERE id = ?", (SESSION,)), ("running",))
        self.assertTrue(rival.blocked, "the rival was not blocked after the check: the takeover held no write fence")
