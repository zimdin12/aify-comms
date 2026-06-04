import sqlite3
from service.tests._base import FastApiTestCase

class StatusEventIngestTests(FastApiTestCase):
    def _register(self, aid="a1", mode="managed", runtime="claude-code"):
        r = self.client.post("/api/v1/agents", json={"agentId": aid, "role": "coder",
            "runtime": runtime, "sessionMode": mode, "machineId": "linux:test", "bridgeId": "b1"})
        self.assertEqual(r.status_code, 200, r.text)

    def _state(self, aid):
        c = sqlite3.connect(str(self._db_path)); c.row_factory = sqlite3.Row
        try:
            return c.execute("SELECT * FROM agent_status_state WHERE agent_id=?", (aid,)).fetchone()
        finally:
            c.close()

    def test_turn_start_event_persists_in_turn(self):
        self._register("a1")
        r = self.client.post("/api/v1/agents/a1/status-event",
                             json={"kind": "turn_start", "runId": "r1", "bridgeId": "b1"})
        self.assertEqual(r.status_code, 200, r.text)
        row = self._state("a1")
        self.assertEqual(int(row["in_turn"]), 1)
        r = self.client.post("/api/v1/agents/a1/status-event", json={"kind": "turn_end", "runId": "r1"})
        self.assertEqual(int(self._state("a1")["in_turn"]), 0)

    def test_status_engine_setting_defaults_old(self):
        r = self.client.get("/api/v1/settings")
        self.assertEqual(r.json().get("status_engine"), "old")
