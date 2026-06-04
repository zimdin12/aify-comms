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

    def _agent_status_events(self):
        return [
            args[1]
            for args, _kwargs in self.ws.broadcasts
            if args and args[0] == "agent_status"
        ]

    def test_turn_start_event_pushes_agent_status_working(self):
        # Phase D1: under status_engine=new, a turn_start event must IMMEDIATELY
        # push an agent_status WS broadcast carrying status=working — the dashboard
        # updates the instant a turn starts, not on its poll.
        self._register("d1", mode="resident")
        self.client.post("/api/v1/agents/d1/heartbeat", json={"bridgeId": "b1", "sessionMode": "resident"})
        self._set("status_engine", "new")
        self.ws.broadcasts.clear()
        r = self.client.post("/api/v1/agents/d1/status-event", json={"kind": "turn_start", "runId": "r1"})
        self.assertEqual(r.status_code, 200, r.text)
        events = self._agent_status_events()
        self.assertTrue(events, "turn_start (flag=new) must push an agent_status event")
        evt = events[-1]
        self.assertEqual(evt["agentId"], "d1")
        self.assertEqual(evt["status"], "working")

    def test_hot_read_serves_cached_status_under_new_flag(self):
        # Phase E1 (the CPU fix): under status_engine=new, a hot read
        # (_compute_agent_status) must serve the ALREADY-CACHED
        # agent_live_state.status when the cache row is still fresh, instead of
        # recomputing the legacy matrix / engine per call. Seed a fresh cache row
        # with a sentinel status and assert the hot read returns it verbatim.
        import asyncio, json
        from service.db import get_db
        from service.routers import api_v2
        self._register("e1", mode="resident")
        self.client.post("/api/v1/agents/e1/heartbeat", json={"bridgeId": "b1", "sessionMode": "resident"})
        self._set("status_engine", "new")
        # Seed a cache row whose status would NEVER be derived (sentinel) and a
        # refresh_after far in the future so it is unambiguously "fresh".
        c = sqlite3.connect(str(self._db_path))
        try:
            c.execute(
                """
                INSERT INTO agent_live_state (agent_id, status, reason, environment_id,
                    session_id, terminal_id, active_run_id, refresh_after, updated_at)
                VALUES (?, 'idle', 'sentinel', '', '', '', '', '9999-12-31T23:59:59Z', '2026-06-04T00:00:00Z')
                ON CONFLICT(agent_id) DO UPDATE SET status='idle', reason='sentinel',
                    refresh_after='9999-12-31T23:59:59Z'
                """,
                ("e1",),
            )
            c.commit()
        finally:
            c.close()

        async def run():
            db = await get_db()
            try:
                row = await (await db.execute("SELECT * FROM agents WHERE id='e1'")).fetchone()
                return await api_v2._compute_agent_status(row, 5, 30, db)
            finally:
                await db.close()

        # If the hot path recomputed, a fresh resident with no turn would derive
        # `online`; serving the cache must yield the sentinel `idle` instead.
        self.assertEqual(asyncio.run(run()), "idle",
                         "hot read under flag=new must serve the fresh cached status, not recompute")

    def test_turn_start_event_no_push_under_old_flag(self):
        # Safety: with the default `old` flag the status-event ingest does NOT
        # broadcast engine-derived agent_status (old path is unchanged).
        self._register("d2", mode="resident")
        self.client.post("/api/v1/agents/d2/heartbeat", json={"bridgeId": "b1", "sessionMode": "resident"})
        self.ws.broadcasts.clear()
        r = self.client.post("/api/v1/agents/d2/status-event", json={"kind": "turn_start", "runId": "r1"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._agent_status_events(), [],
                         "old flag must not push engine agent_status from status-event")
