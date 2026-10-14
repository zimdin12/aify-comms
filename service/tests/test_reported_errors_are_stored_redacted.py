"""A credential in an error a host or bridge reports is not stored (review of 0.9.5, L3).

The first L3 pass redacted the failure line the service chooses and the serializers that show it, but two
reporting writers stored `error` verbatim: `PATCH /spawn-requests/{id}` and `PATCH /dispatch/runs/{id}`.
Each is driven through its real route and read back from the database, not through a serializer, which
hides it either way. Synthetic sentinels only.
"""

from __future__ import annotations

import sqlite3

from service.models import EnvironmentControlUpdate, TerminalControlUpdate
from service.tests._base import FastApiTestCase

SENTINEL = "SENTINELgatewayToken0123456789"
LEAKY = f'gateway host ready: {{"port":9430,"token":"{SENTINEL}"}}'
ORDINARY = "spawn failed: exit 1 (claude: command not found)"
ENV = "linux:test-host:default"


class ReportedErrorsAreStoredRedacted(FastApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        beat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV, "machineId": "linux:test-host", "os": "linux", "kind": "linux", "bridgeId": "b1",
            "cwdRoots": ["/w"], "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(beat.status_code, 200, beat.text)
        db = sqlite3.connect(self._db_path)
        try:
            for sid in ("s-leaky", "s-plain"):
                db.execute("INSERT INTO spawn_specs (id, agent_id, environment_id, runtime, created_at, updated_at) "
                           "VALUES (?,?,?,?,?,?)", (f"spec-{sid}", f"agent-{sid}", ENV, "claude-code", "2026-10-14T00:00:00Z", "2026-10-14T00:00:00Z"))
                db.execute("INSERT INTO spawn_requests (id, spawn_spec_id, agent_id, environment_id, runtime, status, "
                           "claimed_by_bridge_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                           (sid, f"spec-{sid}", f"agent-{sid}", ENV, "claude-code", "claimed", "b1", "2026-10-14T00:00:00Z", "2026-10-14T00:00:00Z"))
            for rid in ("r-leaky", "r-plain"):
                db.execute("INSERT INTO dispatch_runs (id, from_agent, target_agent, message_type, subject, body, priority, "
                           "status, require_reply, requested_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (rid, "a", "b", "request", "s", "b", "normal", "running", 0, "2026-10-14T00:00:00Z"))
            db.commit()
        finally:
            db.close()

    def _stored(self, sql: str, key: str) -> str:
        db = sqlite3.connect(self._db_path)
        try:
            return db.execute(sql, (key,)).fetchone()[0] or ""
        finally:
            db.close()

    def test_a_spawn_report_is_stored_without_the_token(self):
        for sid, error in (("s-leaky", LEAKY), ("s-plain", ORDINARY)):
            done = self.client.patch(f"/api/v1/spawn-requests/{sid}", json={"status": "failed", "error": error, "bridgeId": "b1"})
            self.assertEqual(done.status_code, 200, done.text)
        leaky = self._stored("SELECT error FROM spawn_requests WHERE id = ?", "s-leaky")
        self.assertNotIn(SENTINEL, leaky)
        self.assertIn('"token":"[redacted]"', leaky, "the name stays, so the line still says what it was")
        self.assertEqual(self._stored("SELECT error FROM spawn_requests WHERE id = ?", "s-plain"), ORDINARY,
                         "control: an ordinary diagnostic is stored as reported")

    def test_a_run_report_is_stored_without_the_token(self):
        for rid, error in (("r-leaky", LEAKY), ("r-plain", ORDINARY)):
            done = self.client.patch(f"/api/v1/dispatch/runs/{rid}", json={"status": "failed", "error": error})
            self.assertEqual(done.status_code, 200, done.text)
        self.assertNotIn(SENTINEL, self._stored("SELECT error_text FROM dispatch_runs WHERE id = ?", "r-leaky"))
        self.assertEqual(self._stored("SELECT error_text FROM dispatch_runs WHERE id = ?", "r-plain"), ORDINARY)

    def test_the_control_reports_parse_redacted(self):
        """Their routes need a host proof to reach; the model every one of them parses is where it happens."""
        for model in (EnvironmentControlUpdate, TerminalControlUpdate):
            with self.subTest(model=model.__name__):
                parsed = model(status="failed", error=LEAKY)
                self.assertNotIn(SENTINEL, parsed.error)
                self.assertEqual(model(status="failed", error=ORDINARY).error, ORDINARY)
