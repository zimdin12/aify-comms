"""A defined-managed agent whose record was left resident follows its definition once no resident drives it (B3).

llama-manager, 2026-10-19: its definition said managed, its record still said resident from an earlier resident run,
so it read offline while its host offered it as startable. aify-env re-sends an unchanged snapshot every minute, and
an unchanged definition never applies again, so the follow has to run on a replayed push too. Only the record moves:
nothing is started, since a start is the host's. Every case that keeps the record names what keeps it.
"""
from __future__ import annotations

import json
import sqlite3
from unittest import mock

from service.clock import now
from service.tests._base import FastApiTestCase
from service.tests.defined_agents import STORE, _defined_on, definition_entry
from service.tests.test_agent_definition_push import snapshot_digest

ENV = {"environment_id": "linux:test-host:default", "machine_id": "linux:test-host", "bridge_id": "env-bridge"}


class ARecordLeftResidentFollowsItsManagedDefinition(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV["environment_id"], "machineId": ENV["machine_id"], "os": "linux", "kind": "linux",
            "bridgeId": ENV["bridge_id"], "cwdRoots": ["/workspace"], "runtimes": [],
            "metadata": {"terminal": True, "pty": True, "terminalRuntimes": ["claude-code"]}}).raise_for_status()
        self.revision = 0
        self.define(mode="managed")
        self.leave_resident()

    def define(self, *, mode: str) -> None:
        self.revision += 1
        _defined_on(self, ENV["machine_id"])["lead"] = definition_entry("lead", revision=self.revision, mode=mode)
        self.push()

    def push(self):
        """Push the current snapshot at the current revision: a second call is the host's unchanged replay."""
        published = list(_defined_on(self, ENV["machine_id"]).values())
        answer = self.client.put(f"/api/v1/environments/{ENV['environment_id']}/agent-definitions", json={
            "bridgeId": ENV["bridge_id"], "machineId": ENV["machine_id"], "storeId": STORE, "revision": self.revision,
            "snapshotDigest": snapshot_digest(published), "entries": published})
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()

    def sql(self, query, params=()):
        db = sqlite3.connect(self._db_path)
        try:
            db.row_factory = sqlite3.Row
            rows = [dict(r) for r in db.execute(query, params)]
            db.commit()
            return rows
        finally:
            db.close()

    def leave_resident(self, runtime_state=None):
        """The record an earlier resident run left: resident, with nothing of it still alive."""
        self.sql("UPDATE agents SET session_mode = 'resident', runtime_state = ? WHERE id = 'lead'",
                 (json.dumps(runtime_state or {}),))

    def mode(self) -> str:
        return self.sql("SELECT session_mode FROM agents WHERE id = 'lead'")[0]["session_mode"]

    def bridge(self, bridge_id, kind, last_seen=None):
        self.sql("INSERT INTO bridge_instances (id, agent_id, bridge_kind, registered_at, last_seen) "
                 "VALUES (?, 'lead', ?, ?, ?)", (bridge_id, kind, now(), last_seen or now()))

    def assert_kept(self, why):
        self.assertEqual(self.push()["outcome"], "replay")
        self.assertEqual(self.mode(), "resident", why)

    def test_a_replayed_push_switches_a_record_nothing_drives(self):
        self.assertEqual(self.mode(), "resident", "CONTROL: the setup left the record resident")
        self.assertEqual(self.push()["outcome"], "replay", "CONTROL: this push is the host's unchanged replay")
        self.assertEqual(self.mode(), "managed")
        [owner] = self.sql("SELECT runtime_state FROM agents WHERE id = 'lead'")
        self.assertEqual(json.loads(owner["runtime_state"])["ownership"]["requestedBy"], "definition")

    def test_an_applied_push_switches_it_too(self):
        self.define(mode="managed")
        self.assertEqual(self.mode(), "managed")

    def test_the_switch_starts_nothing(self):
        from service.api_core import session_mode_switch

        starts = []

        async def start(*args, **kwargs):
            starts.append(args[1])

        with mock.patch.object(session_mode_switch, "_start_managed_backing_after_switch", start):
            self.push()
        self.assertEqual(self.mode(), "managed", "CONTROL: the switch happened")
        self.assertEqual(starts, [], "following the definition asked for a managed backing")
        queued = self.client.get("/api/v1/spawn-requests?agentId=lead").json()["spawnRequests"]
        self.assertEqual(queued, [], "following the definition started a worker; a start is the host's")

    def test_a_definition_that_says_resident_is_left_alone(self):
        self.define(mode="resident")
        self.push()
        self.assertEqual(self.mode(), "resident")

    def test_a_live_console_keeps_the_resident(self):
        self.sql("INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, runtime, workspace, command, "
                 "argv, status, requested_by, created_at, updated_at) VALUES ('term-1', 'sess-1', 'lead', ?, "
                 "'claude-code', '/workspace', 'claude', '[]', 'running', 'operator', ?, ?)",
                 (ENV["environment_id"], now(), now()))
        self.assert_kept("a resident with a live console was switched under it")

    def test_a_live_channel_sidecar_keeps_the_resident(self):
        self.bridge("sidecar-1", "channel-sidecar")
        self.assert_kept("a resident with a live channel sidecar was switched under it")

    def test_a_fresh_resident_bridge_keeps_the_resident(self):
        self.bridge("resident-1", "")
        self.leave_resident({"bridgeInstanceId": "resident-1"})
        self.assert_kept("a resident with a fresh bridge was switched under it")

    def test_a_stale_resident_bridge_does_not(self):
        self.bridge("resident-1", "", last_seen="2026-01-01T00:00:00Z")
        self.leave_resident({"bridgeInstanceId": "resident-1"})
        self.push()
        self.assertEqual(self.mode(), "managed", "a bridge gone for months still held the record")

    def test_a_liveness_read_that_failed_switches_nothing(self):
        from service.api_core import live_process_probes, session_mode_switch

        with mock.patch.object(session_mode_switch, "terminal_state", return_value=live_process_probes.PROBE_ERROR):
            self.assert_kept("a console read that failed was taken as no console")

        # The bridge fallback reads the sidecar again and fails open (`_has_live_channel_sidecar`), so both reads fail.
        with mock.patch.object(session_mode_switch, "channel_sidecar_state", return_value=live_process_probes.PROBE_ERROR),                 mock.patch.object(live_process_probes, "channel_sidecar_state", return_value=live_process_probes.PROBE_ERROR):
            self.assert_kept("a sidecar read that failed was taken as no sidecar")

        async def raises(*args, **kwargs):
            raise RuntimeError("bridge read failed")

        with mock.patch.object(session_mode_switch, "_resident_bridge_is_fresh", raises):
            self.assert_kept("a bridge read that failed was taken as no bridge")
        self.push()
        self.assertEqual(self.mode(), "managed", "CONTROL: with the reads restored the record follows")

    def test_an_active_run_keeps_the_record_and_the_push_still_lands(self):
        from service.api_core import session_mode_gates

        async def blocking(db, agent_id):
            return {"runId": "run-1"}

        with mock.patch.object(session_mode_gates, "_get_blocking_active_run", blocking):
            self.assert_kept("the switch ran under an active run")
        self.push()
        self.assertEqual(self.mode(), "managed", "CONTROL: once the run ends the record follows")
