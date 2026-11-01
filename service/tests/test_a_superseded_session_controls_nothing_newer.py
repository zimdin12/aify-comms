"""A session row a newer one has superseded controls nothing (action audit #2, 2026-11-02).

Stop, Restart and Reset on a session go to its agent's host as the agent's lifecycle action, which acts on the
lifetime the host publishes NOW. Sent from an old row, they stopped or restarted the agent's newer worker. A row is
current when it is the agent's live session, or, when none is live, its latest; only a current row's controls are
admitted, and that is judged inside the admission's own transaction. The cold "Start agent" on the latest ended
session (session-console.mjs) is a restart and stays.
"""
from __future__ import annotations

import sqlite3

import service.routers.session_control as session_control
from service.tests._base import FastApiTestCase
from service.tests.published_state import publish
from service.tests.test_agent_definition_push import A, snapshot_digest, valid

ACTIONS = ("stop", "restart", "recreate")


class ASupersededSessionControlsNothingNewer(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": A["env"], "machineId": A["machine"], "os": "win32", "kind": "win32", "bridgeId": A["bridge"],
            "cwdRoots": ["/work"], "runtimes": [{"runtime": "claude-code", "available": True}],
            "metadata": {}}).raise_for_status()
        entries = [valid("coder")]
        self.client.put(f"/api/v1/environments/{A['env']}/agent-definitions", json={
            "bridgeId": A["bridge"], "machineId": A["machine"], "storeId": "s1", "revision": 1,
            "snapshotDigest": snapshot_digest(entries), "entries": entries}).raise_for_status()

    def sql(self, query, params=()):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in conn.execute(query, params)]
            conn.commit()
            return rows
        finally:
            conn.close()

    def session(self, session_id, status, started_at):
        self.sql("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, mode, status, started_at, last_seen) "
                 "VALUES (?, 'coder', ?, 'claude-code', 'managed-warm', ?, ?, ?)",
                 (session_id, A["env"], status, started_at, started_at))

    def control(self, session_id, action):
        return self.client.post(f"/api/v1/sessions/{session_id}/control", json={"action": action, "from_agent": "dashboard"})

    def lifecycle_requests(self):
        return self.sql("SELECT action, fresh_context FROM agent_lifecycle_requests")

    def test_an_old_row_cannot_stop_restart_or_reset_the_newer_live_worker(self):
        self.session("sess-old", "ended", "2026-10-01T00:00:00Z")
        self.session("sess-new", "running", "2026-11-01T00:00:00Z")
        publish(self, A["machine"], {"coder": ("L2", "idle")})
        for action in ACTIONS:
            with self.subTest(action=action):
                self.sql("DELETE FROM agent_lifecycle_requests")
                answer = self.control("sess-old", action)
                self.assertEqual(answer.status_code, 409, answer.text)
                self.assertIn("sess-new", answer.json()["detail"])
                self.assertEqual(self.lifecycle_requests(), [], f"an old row's {action} was queued for the new worker")

    def test_CONTROL_the_live_row_still_controls_its_worker(self):
        self.session("sess-old", "ended", "2026-10-01T00:00:00Z")
        self.session("sess-new", "running", "2026-11-01T00:00:00Z")
        publish(self, A["machine"], {"coder": ("L2", "idle")})
        for action, queued in (("stop", ("kill", 0)), ("restart", ("restart", 0)), ("recreate", ("restart", 1))):
            with self.subTest(action=action):
                self.sql("DELETE FROM agent_lifecycle_requests")
                answer = self.control("sess-new", action)
                self.assertEqual(answer.status_code, 200, answer.text)
                self.assertEqual(self.lifecycle_requests(), [{"action": queued[0], "fresh_context": queued[1]}])

    def test_the_latest_ended_row_of_a_cold_agent_still_restarts_and_resets_it(self):
        """The dashboard's cold Start agent (and Reset) on the latest ended session: nothing newer supersedes it."""
        self.session("sess-older", "ended", "2026-10-01T00:00:00Z")
        self.session("sess-latest", "ended", "2026-11-01T00:00:00Z")
        publish(self, A["machine"], {"coder": (None, "available")})
        for action, fresh in (("restart", 0), ("recreate", 1)):
            with self.subTest(action=action):
                self.sql("DELETE FROM agent_lifecycle_requests")
                answer = self.control("sess-latest", action)
                self.assertEqual(answer.status_code, 200, answer.text)
                self.assertEqual(self.lifecycle_requests(), [{"action": "restart", "fresh_context": fresh}])
        with self.subTest("an older ended row of the same cold agent is superseded by the latest"):
            self.sql("DELETE FROM agent_lifecycle_requests")
            self.assertEqual(self.control("sess-older", "restart").status_code, 409)
            self.assertEqual(self.lifecycle_requests(), [])

    def test_a_row_superseded_while_its_control_is_admitted_is_refused(self):
        """The route reads the session before the lifecycle admission takes its write lock. A newer live session that
        lands in between is judged under that lock, so the old row's control is refused rather than retargeted."""
        self.session("sess-was-current", "running", "2026-10-01T00:00:00Z")
        publish(self, A["machine"], {"coder": ("L1", "idle")})
        real = session_control._session_row

        async def read_then_supersede(db, session_id):
            row = await real(db, session_id)
            self.session("sess-newer", "running", "2026-11-01T00:00:00Z")
            return row

        session_control._session_row = read_then_supersede
        self.addCleanup(setattr, session_control, "_session_row", real)
        answer = self.control("sess-was-current", "stop")
        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertEqual(self.lifecycle_requests(), [])
