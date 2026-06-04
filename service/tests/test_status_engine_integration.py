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

    def test_engine_status_working_after_turn_start(self):
        self._register("a2", mode="resident", runtime="claude-code")
        # mark a fresh resident bridge so alive=True (mirror existing heartbeat path)
        self.client.post("/api/v1/agents/a2/heartbeat", json={"bridgeId": "b1", "sessionMode": "resident"})
        self.client.post("/api/v1/agents/a2/status-event", json={"kind": "turn_start", "runId": "r1"})
        import asyncio
        from service.db import get_db
        from service.routers import api_v2
        async def run():
            db = await get_db()
            try:
                row = await (await db.execute("SELECT * FROM agents WHERE id='a2'")).fetchone()
                return await api_v2.engine_status(db, row)
            finally:
                await db.close()
        self.assertEqual(asyncio.run(run()), "working")

    def _set(self, key, val):
        c = sqlite3.connect(str(self._db_path))
        try:
            import json
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(val))); c.commit()
        finally: c.close()

    def test_flag_new_serves_engine_status(self):
        self._register("a3", mode="resident")
        self.client.post("/api/v1/agents/a3/heartbeat", json={"bridgeId": "b1", "sessionMode": "resident"})
        self.client.post("/api/v1/agents/a3/status-event", json={"kind": "turn_start", "runId": "r1"})
        self._set("status_engine", "new")
        r = self.client.get("/api/v1/agents")
        a = r.json()["agents"]["a3"]
        self.assertEqual(a["status"], "working")
