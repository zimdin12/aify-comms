"""A defined agent whose definition is flipped to resident becomes resident (0.9.2 status fix A).

Steven, 2026-10-13: dashboard-manager's definition was flipped to resident three times and its record stayed
managed, then the resident he started in herdr-aify was parked as a candidate, so it read `available` throughout.
The definition flip is the operator's deliberate switch, so the record follows it, unless a managed worker still
runs: then two drivers would share the agent, and the resident is parked as before.
"""
from __future__ import annotations

import sqlite3

from service.tests._base import FastApiTestCase
from service.tests.defined_agents import _defined_on, definition_entry, publish

ENV = {"environment_id": "linux:test-host:default", "machine_id": "linux:test-host", "bridge_id": "env-bridge"}


class ADefinitionFlippedToResidentIsFollowed(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV["environment_id"], "machineId": ENV["machine_id"], "os": "linux", "kind": "linux",
            "bridgeId": ENV["bridge_id"], "cwdRoots": ["/workspace"], "runtimes": [],
            "metadata": {"terminal": True, "pty": True, "terminalRuntimes": ["claude-code"]}}).raise_for_status()
        self.define(mode="managed", revision=1)

    def define(self, *, mode: str, revision: int) -> None:
        _defined_on(self, ENV["machine_id"])["lead"] = definition_entry("lead", revision=revision, mode=mode)
        publish(self, **ENV)

    def sql(self, query, params=()):
        db = sqlite3.connect(self._db_path)
        try:
            db.row_factory = sqlite3.Row
            rows = [dict(r) for r in db.execute(query, params)]
            db.commit()
            return rows
        finally:
            db.close()

    def mode(self) -> str:
        return self.sql("SELECT session_mode FROM agents WHERE id = 'lead'")[0]["session_mode"]

    def register_resident(self):
        answer = self.client.post("/api/v1/agents", json={
            "agentId": "lead", "role": "coder", "runtime": "claude-code", "sessionMode": "resident",
            "sessionHandle": "resident-session", "machineId": ENV["machine_id"], "bridgeId": "resident-bridge",
            "capabilities": ["resident-run", "resume", "interrupt"]})
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()

    def test_a_push_flipping_the_definition_to_resident_switches_the_record(self):
        self.assertEqual(self.mode(), "managed")
        self.define(mode="resident", revision=2)
        self.assertEqual(self.mode(), "resident", "the record ignored the definition's flip")

    def test_a_resident_registering_for_an_agent_defined_resident_is_adopted_not_parked(self):
        # The order Steven hit: the definition says resident while the record still says managed.
        self.sql("UPDATE agent_definitions SET body = json_set(body, '$.mode', 'resident') WHERE agent_id = 'lead'")
        self.assertEqual(self.mode(), "managed")
        self.register_resident()
        self.assertEqual(self.mode(), "resident")
        state = self.sql("SELECT runtime_state FROM agents WHERE id = 'lead'")[0]["runtime_state"] or ""
        self.assertNotIn("manualResidentCandidate", state, "the resident was parked as a candidate")

    def test_a_running_managed_worker_still_parks_the_resident(self):
        # Two drivers on one agent is what the parking protects against, and a definition flip does not change that.
        self.sql("INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id, runtime, "
                 "workspace, command, argv, status, requested_by, created_at, updated_at) VALUES "
                 "('term-live', 'sess-live', 'lead', ?, ?, 'claude-code', '/workspace', 'claude', '[]', 'running', "
                 "'dashboard', '2026-10-13T00:00:00Z', '2026-10-13T00:00:00Z')",
                 (ENV["environment_id"], ENV["bridge_id"]))
        self.define(mode="resident", revision=2)
        self.assertEqual(self.mode(), "managed", "a running managed worker was switched away under itself")
        self.register_resident()
        self.assertEqual(self.mode(), "managed")
        state = self.sql("SELECT runtime_state FROM agents WHERE id = 'lead'")[0]["runtime_state"] or ""
        self.assertIn("manualResidentCandidate", state)
