"""An explicit start clears a stop (operator report, 2026-10-22).

A stopped agent's claim answers `stopped`, and its delivery loop exits on that. The dashboard's Start was delegated to
the host (D9c) before anything cleared the stop, so the started agent quit seconds after launch. A Start is the
operator's explicit "run this again" (the 2026-09-14 rule), so it clears the stop the way Resume does, then delegates.
"""
from __future__ import annotations

import sqlite3

from service.tests._base import FastApiTestCase
from service.tests.published_state import publish
from service.tests.test_agent_definition_push import A, snapshot_digest, valid

RUNTIMES = [{"runtime": runtime, "available": True} for runtime in ("claude-code", "codex", "hermes")]


class AnExplicitStartClearsAStop(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": A["env"], "machineId": A["machine"], "os": "win32", "kind": "win32", "bridgeId": A["bridge"],
            "cwdRoots": ["/work"], "runtimes": RUNTIMES, "metadata": {}}).raise_for_status()
        entries = [valid("coder")]
        self.client.put(f"/api/v1/environments/{A['env']}/agent-definitions", json={
            "bridgeId": A["bridge"], "machineId": A["machine"], "storeId": "s1", "revision": 1,
            "snapshotDigest": snapshot_digest(entries), "entries": entries}).raise_for_status()
        publish(self, A["machine"], {"coder": (None, "available")})

    def sql(self, query, params=()):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in conn.execute(query, params)]
            conn.commit()
            return rows
        finally:
            conn.close()

    def stopped(self):
        self.sql("UPDATE agents SET status = 'stopped', status_note = 'Stopped from dashboard.', launch_mode = 'none' "
                 "WHERE id = 'coder'")
        [row] = self.sql("SELECT status, launch_mode FROM agents WHERE id = 'coder'")
        self.assertEqual(row, {"status": "stopped", "launch_mode": "none"}, "CONTROL: the agent is stopped")

    def control(self, action):
        response = self.client.post("/api/v1/agents/coder/control", json={"action": action, "from_agent": "dashboard"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_a_start_of_a_stopped_agent_clears_the_stop_and_is_still_delegated(self):
        self.stopped()
        body = self.control("start")
        self.assertEqual((body.get("queued"), body.get("action")), (True, "start"), "the start was not delegated")
        [row] = self.sql("SELECT status, status_note, launch_mode FROM agents WHERE id = 'coder'")
        self.assertNotEqual(row["status"], "stopped", "the started agent still reads stopped, so its loop will exit")
        self.assertEqual((row["status_note"], row["launch_mode"]), ("", "detached"), "cleared the way Resume clears it")

    def test_a_stop_still_stops(self):
        publish(self, A["machine"], {"coder": ("L1", "idle")})
        body = self.control("stop")
        self.assertEqual(body.get("action"), "stop")
        [row] = self.sql("SELECT action FROM agent_lifecycle_requests")
        self.assertEqual(row["action"], "stop", "the stop must still reach the host")
