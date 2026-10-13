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

    def test_a_push_does_not_switch_so_a_launch_loop_claiming_before_its_resident_registers_keeps_running(self):
        # dashboard-senior-dev, 2026-10-13: the push switched the record to resident and not driving, the launcher's
        # hermes delivery loop beat before the TUI's bridge registered, was told to release, and exited for good.
        self.define(mode="resident", revision=2)
        self.assertEqual(self.mode(), "managed", "the push switched the record before any resident registered")
        beat = self.client.post("/api/v1/agents/lead/heartbeat",
                                json={"bridgeId": "loop-bridge", "bridgeKind": "channel-sidecar", "liveness": True})
        self.assertEqual(beat.status_code, 200, beat.text)
        self.assertFalse(beat.json().get("release"), "the launch's delivery loop was told to release")
        self.register_resident()
        self.assertEqual(self.mode(), "resident", "the resident's registration follows the flip")

    def test_a_resident_registering_for_an_agent_defined_resident_is_adopted_not_parked(self):
        # The order Steven hit: the definition says resident while the record still says managed.
        self.sql("UPDATE agent_definitions SET body = json_set(body, '$.mode', 'resident') WHERE agent_id = 'lead'")
        self.assertEqual(self.mode(), "managed")
        self.register_resident()
        self.assertEqual(self.mode(), "resident")
        state = self.sql("SELECT runtime_state FROM agents WHERE id = 'lead'")[0]["runtime_state"] or ""
        self.assertNotIn("manualResidentCandidate", state, "the resident was parked as a candidate")

    def test_no_reader_sees_the_switch_before_the_resident_s_bridge_is_registered(self):
        # Whole-diff review of 0.9.2: the switch committed on its own, and a hermes delivery loop claiming before the
        # registration's bridge row landed read "resident, nobody driving", was told to release and exited.
        from unittest import mock

        from service.routers.agents import registration

        self.sql("UPDATE agent_definitions SET body = json_set(body, '$.mode', 'resident') WHERE agent_id = 'lead'")
        original = registration._upsert_registered_agent_row
        seen = {}

        async def upsert_after_a_reader_looks(*args, **kwargs):
            other = sqlite3.connect(self._db_path, timeout=0.2)
            try:
                seen["mode"] = other.execute("SELECT session_mode FROM agents WHERE id = 'lead'").fetchone()[0]
            finally:
                other.close()
            return await original(*args, **kwargs)

        with mock.patch.object(registration, "_upsert_registered_agent_row", upsert_after_a_reader_looks):
            self.register_resident()
        self.assertEqual(seen.get("mode"), "managed", "a reader saw the switch before the bridge that drives it")
        self.assertEqual(self.mode(), "resident", "CONTROL: the switch and the registration did commit")

    def test_a_managed_terminal_cannot_commit_between_the_check_and_the_switch(self):
        # 0.9.2 review, A race: registration read "no managed worker", an independent writer committed a running
        # managed terminal before the switch, and the switch then stopped it under the resident.
        from unittest import mock

        from service.api_core import session_mode_switch

        self.sql("UPDATE agent_definitions SET body = json_set(body, '$.mode', 'resident') WHERE agent_id = 'lead'")
        original = session_mode_switch.managed_worker_drives
        raced = {}

        async def drives_then_a_writer_races(db, agent_id):
            answer = await original(db, agent_id)
            other = sqlite3.connect(self._db_path, timeout=0.2)
            try:
                other.execute(
                    "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id, runtime, "
                    "workspace, command, argv, status, requested_by, created_at, updated_at) VALUES ('term-race', "
                    "'sess-race', 'lead', ?, ?, 'claude-code', '/workspace', 'claude', '[]', 'running', 'dashboard', "
                    "'2026-10-13T00:00:00Z', '2026-10-13T00:00:00Z')", (ENV["environment_id"], ENV["bridge_id"]))
                other.commit()
                raced["committed"] = True
            except sqlite3.OperationalError as error:
                raced["refused"] = str(error)
            finally:
                other.close()
            return answer

        with mock.patch.object(session_mode_switch, "managed_worker_drives", drives_then_a_writer_races):
            self.register_resident()
        self.assertIn("refused", raced, "a managed terminal committed between the check and the switch")
        self.assertIn("locked", raced["refused"])
        self.assertEqual(self.mode(), "resident")
        self.assertEqual(self.sql("SELECT id FROM terminal_sessions WHERE agent_id = 'lead'"), [])

    def test_a_managed_worker_not_yet_gone_still_parks_the_resident(self):
        # Two drivers on one agent is what the parking protects against, and a definition flip does not change that.
        # `stopping` is not gone: the stop was requested and no host has confirmed the departure (0.9.2 review, A P1).
        for status in ("running", "stopping"):
            with self.subTest(status=status):
                self.sql("DELETE FROM terminal_sessions")
                self.sql("UPDATE agents SET session_mode = 'managed', runtime_state = '{}' WHERE id = 'lead'")
                self.sql("INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id, runtime, "
                         "workspace, command, argv, status, requested_by, created_at, updated_at) VALUES "
                         "('term-live', 'sess-live', 'lead', ?, ?, 'claude-code', '/workspace', 'claude', '[]', ?, "
                         "'dashboard', '2026-10-13T00:00:00Z', '2026-10-13T00:00:00Z')",
                         (ENV["environment_id"], ENV["bridge_id"], status))
                self.define(mode="resident", revision=self.__dict__.setdefault("_rev", 1) + 1)
                self.__dict__["_rev"] += 1
                self.assertEqual(self.mode(), "managed", "a managed worker not yet gone was switched away under itself")
                self.register_resident()
                self.assertEqual(self.mode(), "managed")
                state = self.sql("SELECT runtime_state FROM agents WHERE id = 'lead'")[0]["runtime_state"] or ""
                self.assertIn("manualResidentCandidate", state)

    def test_the_parked_resident_is_adopted_when_the_managed_worker_s_terminal_ends(self):
        # Whole-diff review of 0.9.2: the operator flips the definition, stops the worker and starts the resident within
        # seconds; the resident is parked while the terminal is `stopping`, and nothing asked again once it ended.
        self.sql("INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id, runtime, workspace, "
                 "command, argv, status, requested_by, created_at, updated_at) VALUES ('term-live', 'sess-live', 'lead', "
                 "?, ?, 'claude-code', '/workspace', 'claude', '[]', 'stopping', 'dashboard', '2026-10-13T00:00:00Z', "
                 "'2026-10-13T00:00:00Z')", (ENV["environment_id"], ENV["bridge_id"]))
        self.define(mode="resident", revision=2)
        self.register_resident()
        self.assertEqual(self.mode(), "managed", "CONTROL: the resident is parked while the worker is stopping")
        ended = self.client.post("/api/v1/terminals/term-live/output",
                                 json={"bridgeId": ENV["bridge_id"], "output": "bye", "status": "stopped", "exitCode": 0})
        self.assertEqual(ended.status_code, 200, ended.text)
        self.assertEqual(self.mode(), "resident", "the parked resident was not adopted when the worker's terminal ended")
        state = self.sql("SELECT runtime_state FROM agents WHERE id = 'lead'")[0]["runtime_state"]
        self.assertIn('"bridgeInstanceId": "resident-bridge"', state.replace('":"', '": "'))

    def test_a_terminal_ending_with_no_parked_resident_switches_nothing(self):
        self.sql("INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id, runtime, workspace, "
                 "command, argv, status, requested_by, created_at, updated_at) VALUES ('term-live', 'sess-live', 'lead', "
                 "?, ?, 'claude-code', '/workspace', 'claude', '[]', 'running', 'dashboard', '2026-10-13T00:00:00Z', "
                 "'2026-10-13T00:00:00Z')", (ENV["environment_id"], ENV["bridge_id"]))
        self.define(mode="resident", revision=2)
        ended = self.client.post("/api/v1/terminals/term-live/output",
                                 json={"bridgeId": ENV["bridge_id"], "output": "bye", "status": "stopped", "exitCode": 0})
        self.assertEqual(ended.status_code, 200, ended.text)
        self.assertEqual(self.mode(), "managed", "a stopped managed agent with no resident waiting was switched")
