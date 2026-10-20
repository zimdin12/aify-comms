"""An environment stop that would disable a defined agent is refused before it writes anything (B2, path 3).

`POST /environments/{id}/control` with `stop` marks every agent with ANY session on that environment `offline` with
`launch_mode='none'`, and queues an `environment_controls` row nothing has consumed since v0.6.2. So a defined agent
read disabled in comms while aify-env, which owns its lifecycle, kept running it. The refusal names those agents.

THE POPULATION IS THE STOP'S OWN: an agent with an old session here and a definition on another machine is written by
the stop, so it is protected too. Forget's narrower query (definitions this environment's store governs) misses it.

THE FENCE: the guard read and every write share one `BEGIN IMMEDIATE`, the lock a definition push takes. The race
test pushes a definition into the gap right after the guard read; `write_fence_race.py` says how the order is read
back from the database.
"""

from __future__ import annotations

import sqlite3

import service.routers.environments as environments
from service.routers.api_v2 import router  # noqa: F401 -- the base builds the app from it
from service.tests._base import FastApiTestCase
from service.tests.defined_agents import define
from service.tests.write_fence_race import Rival, dump, racing

A = {"env": "win32:host-a:default", "machine": "win32:host-a", "bridge": "bridge-a"}
B = {"env": "win32:host-b:default", "machine": "win32:host-b", "bridge": "bridge-b"}
NOW = "2026-10-20T00:00:00Z"


class AnEnvironmentStopRefusesDefinedAgents(FastApiTestCase):
    DB_NAME = "aify-b2-environment-stop.db"

    def setUp(self):
        super().setUp()
        for host in (A, B):
            beat = self.client.post("/api/v1/environments/heartbeat", json={
                "id": host["env"], "machineId": host["machine"], "os": "win32", "kind": "win32",
                "bridgeId": host["bridge"], "cwdRoots": ["/work"],
                "runtimes": [{"runtime": "claude-code", "available": True}], "metadata": {}})
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

    def _undefined(self, agent_id: str) -> None:
        response = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": "claude-code", "sessionMode": "managed",
            "launchMode": "managed", "machineId": A["machine"]})
        self.assertEqual(response.status_code, 200, response.text)

    def _defined(self, agent_id: str, host=A) -> None:
        define(self, agent_id, environment_id=host["env"], machine_id=host["machine"], bridge_id=host["bridge"],
               workspace="/work")

    def _session_on_a(self, agent_id: str, status: str = "running") -> None:
        self._execute("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, status, started_at,"
                      " last_seen) VALUES (?,?,?,?,?,?,?,?)",
                      (f"sess-{agent_id}", agent_id, A["env"], "claude-code", "/work", status, NOW, NOW))

    def _stop_a(self):
        return self.client.post(f"/api/v1/environments/{A['env']}/control",
                                json={"action": "stop", "requestedBy": "dashboard"})

    def _assert_refused_unchanged(self, *named: str) -> None:
        """Nothing written, broadcast or expired: the status-cache expiry is in memory, so the dump cannot see it."""
        before, broadcasts = dump(self._db_path), len(self.ws.broadcasts)
        expired, real = [], environments._invalidate_agent_live_state

        async def expiring(db, agent_id):
            expired.append(agent_id)
            return await real(db, agent_id)

        environments._invalidate_agent_live_state = expiring
        try:
            refused = self._stop_a()
        finally:
            environments._invalidate_agent_live_state = real
        self.assertEqual(expired, [], "a refused stop expired the status cache")
        self.assertEqual(refused.status_code, 409, refused.text)
        detail = refused.json()["detail"]
        self.assertIn("): stopping it would disable them here while their host keeps running them", detail)
        for agent_id in named:
            self.assertIn(agent_id, detail)
        self.assertEqual(dump(self._db_path), before, "a refused stop wrote to the database")
        self.assertEqual(len(self.ws.broadcasts), broadcasts, "a refused stop broadcast")

    def test_a_definition_committed_before_the_stop_refuses_it(self):
        self._defined("local-defined")
        self._session_on_a("local-defined")
        self._assert_refused_unchanged("local-defined")

    def test_an_agent_defined_elsewhere_with_an_old_session_here_refuses_it(self):
        """Forget's governing-store query would not see this one: its definition lives on host B."""
        self._defined("foreign-defined", host=B)
        self._session_on_a("foreign-defined", status="ended")
        self._assert_refused_unchanged("foreign-defined")

    def test_a_mixed_population_refuses_it_and_disables_nobody(self):
        self._defined("mixed-defined")
        self._session_on_a("mixed-defined")
        self._undefined("mixed-undefined")
        self._session_on_a("mixed-undefined")
        self._assert_refused_unchanged("mixed-defined")

    def test_CONTROL_undefined_agents_only_are_still_disabled(self):
        self._undefined("only-undefined")
        self._session_on_a("only-undefined")
        expired, real = [], environments._invalidate_agent_live_state

        async def expiring(db, agent_id):
            expired.append(agent_id)
            return await real(db, agent_id)

        environments._invalidate_agent_live_state = expiring
        try:
            stopped = self._stop_a()
        finally:
            environments._invalidate_agent_live_state = real
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertEqual(expired, ["only-undefined"], "the spy the refusals rely on sees a stop's expiry")
        self.assertEqual(self._one("SELECT status, launch_mode FROM agents WHERE id = 'only-undefined'"), ("offline", "none"))
        self.assertEqual(self._one("SELECT status FROM environments WHERE id = ?", (A["env"],)), ("disabled",))
        self.assertEqual(self._one("SELECT COUNT(*) FROM environment_controls WHERE environment_id = ?", (A["env"],)), (1,))

    def test_a_definition_racing_the_guard_lands_after_the_stop_commits(self):
        """X is undefined at the guard read; a definition push tries to commit right then. Under the fence it waits
        for the stop's commit; outside it, it commits first and the stop then disables a defined agent."""
        self._undefined("racing-agent")
        self._session_on_a("racing-agent")
        rival = Rival(self._db_path, "racing-agent", A["machine"], committed_before=lambda conn: conn.execute(
            "SELECT COUNT(*) FROM environment_controls WHERE environment_id = ?", (A["env"],)).fetchone()[0] > 0)
        restore = racing(environments, "defined_among_sessions_on", rival)
        try:
            stopped = self._stop_a()
        finally:
            restore()
        rival.finish()
        self.assertTrue(rival.committed, "the rival definition never committed")
        if rival.handler_committed_first:
            self.assertEqual(stopped.status_code, 200, stopped.text)
        else:
            self.assertEqual(stopped.status_code, 409, "the definition committed first, then the stop disabled a defined agent")
            self.assertEqual(self._one("SELECT launch_mode FROM agents WHERE id = 'racing-agent'"), ("managed",))
        self.assertTrue(rival.blocked, "the rival was not blocked after the guard read: the stop held no write fence")
