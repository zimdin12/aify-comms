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

    def test_turn_start_endpoint_feeds_engine_state(self):
        # The harness-level /turn-start + /turn-end endpoints (the SAME signal the
        # old engine uses for turn_busy) must ALSO feed the new engine's
        # agent_status_state, so the existing per-runtime detectors make the `new`
        # path show working/idle without a separate status-event post. Flag-agnostic.
        self._register("t1", mode="resident")
        r = self.client.post("/api/v1/agents/t1/turn-start",
                             json={"runtime": "claude-code", "bridgeId": "b1"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(int(self._state("t1")["in_turn"]), 1,
                         "/turn-start must set in_turn in agent_status_state")
        r = self.client.post("/api/v1/agents/t1/turn-end", json={})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(int(self._state("t1")["in_turn"]), 0,
                         "/turn-end must clear in_turn in agent_status_state")

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


class StatusEngineHotRefreshParityTests(FastApiTestCase):
    """status v2 CPU-fix: under status_engine=new, _refresh_agent_live_state must
    derive the served status from the live-status-cache byproduct (a pure derive()
    call), NOT by re-running the expensive _gather_status_inputs double-gather. The
    derived value MUST equal what engine_status(db, row) returns (parity)."""

    def _register(self, aid, mode="resident", runtime="claude-code", machine="linux:test"):
        r = self.client.post("/api/v1/agents", json={"agentId": aid, "role": "coder",
            "runtime": runtime, "sessionMode": mode, "machineId": machine, "bridgeId": "b1"})
        self.assertEqual(r.status_code, 200, r.text)

    def _set(self, key, val):
        import json
        c = sqlite3.connect(str(self._db_path))
        try:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(val)))
            c.commit()
        finally:
            c.close()

    def _run(self, coro_factory):
        import asyncio
        from service.db import get_db

        async def runner():
            db = await get_db()
            try:
                return await coro_factory(db)
            finally:
                await db.close()

        return asyncio.run(runner())

    def _refreshed_status(self, aid):
        """Run _refresh_agent_live_state and read back the written status."""
        from service.routers import api_v2

        async def factory(db):
            settings = await api_v2._load_settings(db)
            await api_v2._invalidate_agent_live_state(db, aid)
            await api_v2._refresh_agent_live_state(db, aid, settings=settings)
            row = await (await db.execute(
                "SELECT status FROM agent_live_state WHERE agent_id=?", (aid,))).fetchone()
            return str(row["status"]) if row else None

        return self._run(factory)

    def _engine_status(self, aid):
        from service.routers import api_v2

        async def factory(db):
            settings = await api_v2._load_settings(db)
            row = await (await db.execute("SELECT * FROM agents WHERE id=?", (aid,))).fetchone()
            return await api_v2.engine_status(db, row, settings=settings)

        return self._run(factory)

    def _register_env(self, env_id, machine, runtime):
        r = self.client.post("/api/v1/environments/heartbeat", json={
            "id": env_id, "machineId": machine, "status": "online",
            "runtimes": [{"runtime": runtime}]})
        self.assertEqual(r.status_code, 200, r.text)

    # ---- Test #1: correctness parity (refresh-written status == engine_status) ----

    def test_parity_resident_working(self):
        # (a) resident + fresh heartbeat + turn_start status-event -> both `working`.
        self._register("p_work", mode="resident")
        self.client.post("/api/v1/agents/p_work/heartbeat", json={"bridgeId": "b1", "sessionMode": "resident"})
        self.client.post("/api/v1/agents/p_work/status-event", json={"kind": "turn_start", "runId": "r1"})
        self._set("status_engine", "new")
        engine = self._engine_status("p_work")
        self.assertEqual(engine, "working")
        self.assertEqual(self._refreshed_status("p_work"), engine)

    def test_parity_managed_available(self):
        # (b) managed + reachable env + no live worker -> both `available`.
        self._register("p_avail", mode="managed", machine="linux:test")
        self._register_env("env-a", "linux:test", "claude-code")
        self._set("status_engine", "new")
        engine = self._engine_status("p_avail")
        self.assertEqual(engine, "available")
        self.assertEqual(self._refreshed_status("p_avail"), engine)

    def test_wake_disabled_serves_stopped_not_available(self):
        """2026-06-12 audit: launch_mode='none' (operator Stop wake) must read as
        DISABLED in BOTH gathers — the engine previously served `available` for
        wake-disabled managed agents (inviting sends that can never wake them).
        derive(disabled) → 'stopped'; parity between engine_status and the
        refresh-written byproduct value must hold."""
        self._register("p_disabled", mode="managed", machine="linux:test")
        self._register_env("env-d", "linux:test", "claude-code")
        c = sqlite3.connect(str(self._db_path))
        try:
            c.execute("UPDATE agents SET launch_mode='none' WHERE id='p_disabled'")
            c.commit()
        finally:
            c.close()
        self._set("status_engine", "new")
        engine = self._engine_status("p_disabled")
        self.assertEqual(engine, "stopped", "wake-disabled must not read available")
        self.assertEqual(self._refreshed_status("p_disabled"), engine)

    def test_parity_resident_stale_offline(self):
        # (c) resident with no fresh bridge -> both yield the same (stale/offline).
        self._register("p_stale", mode="resident")
        # Age the registration bridge well past the resident lease (150s) so the
        # bridge reads stale (registration seeds a fresh bridge_instances row).
        c = sqlite3.connect(str(self._db_path))
        try:
            c.execute(
                "UPDATE bridge_instances SET last_seen='2020-01-01T00:00:00Z' WHERE agent_id=?",
                ("p_stale",))
            c.commit()
        finally:
            c.close()
        self._set("status_engine", "new")
        engine = self._engine_status("p_stale")
        self.assertIn(engine, {"stale", "offline"})
        self.assertEqual(self._refreshed_status("p_stale"), engine)

    # ---- Test #2: no-double-gather proof (the CPU-fix regression net) ----

    def test_refresh_does_not_call_gather_status_inputs(self):
        # The actual CPU-fix assertion: under status_engine=new the refresh path
        # must derive from the cache byproduct, NOT via engine_status ->
        # _gather_status_inputs (the expensive double-gather). Monkeypatch
        # _gather_status_inputs to blow up; the refresh must STILL succeed, write a
        # VALID status, AND must NOT have hit the engine-failure fallback (which is
        # what the OLD code does when _gather_status_inputs raises via
        # engine_status). FAILS before the fix (engine_status -> _gather_status_inputs
        # raises -> logger.exception fallback fires); PASSES after, when
        # derive(cache byproduct) is used and the gather is never touched.
        from service.status_engine import VALID_STATUSES
        from service.routers import api_v2

        self._register("p_nogather", mode="resident")
        self.client.post("/api/v1/agents/p_nogather/heartbeat", json={"bridgeId": "b1", "sessionMode": "resident"})
        self._set("status_engine", "new")

        original_gather = api_v2._gather_status_inputs
        original_log_exc = api_v2.logger.exception
        fallback_hits = []

        async def boom(*args, **kwargs):
            raise AssertionError("hot path must not call _gather_status_inputs")

        def spy_exception(msg, *args, **kwargs):
            fallback_hits.append((msg, args))
            return original_log_exc(msg, *args, **kwargs)

        api_v2._gather_status_inputs = boom
        api_v2.logger.exception = spy_exception
        try:
            status = self._refreshed_status("p_nogather")
        finally:
            api_v2._gather_status_inputs = original_gather
            api_v2.logger.exception = original_log_exc
        self.assertIsNotNone(status)
        self.assertIn(status, VALID_STATUSES)
        self.assertEqual(
            fallback_hits, [],
            "refresh must not fall back through the engine_status exception handler "
            "— it must derive from the cache byproduct without calling "
            "_gather_status_inputs",
        )


class HeartbeatTurnBusyFeedsEngineTests(FastApiTestCase):
    """status v2 (Fix A + Fix B, 2026-06-05): the dominant turn signal for managed
    runtimes (hermes/codex/pi/opencode) and claude channel-woken turns is the
    /heartbeat `turnBusy` field — but it only wrote agent_turn_state (OLD engine)
    and never fed agent_status_state, so the `new` engine showed online/idle
    mid-turn. Fix A bridges turnBusy into the event engine; Fix B adds an in_turn
    staleness backstop so a dropped turn-end can't latch `working` forever."""

    ENV_ID = "linux:test-host:default"
    MACHINE = "linux:test-host"

    def _heartbeat_environment(self, runtime="hermes"):
        r = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": self.ENV_ID,
                "label": "Linux on test-host",
                "machineId": self.MACHINE,
                "os": "linux",
                "kind": "linux",
                "bridgeId": "env-bridge",
                "cwdRoots": ["/workspace"],
                "runtimes": [
                    {
                        "runtime": runtime,
                        "modes": ["managed-warm"],
                        "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True},
                    }
                ],
                "metadata": {},
            },
        )
        self.assertEqual(r.status_code, 200, r.text)

    def _register(self, aid, mode="managed", runtime="hermes"):
        r = self.client.post("/api/v1/agents", json={"agentId": aid, "role": "coder",
            "runtime": runtime, "sessionMode": mode, "machineId": self.MACHINE, "bridgeId": "env-bridge"})
        self.assertEqual(r.status_code, 200, r.text)

    def _state(self, aid):
        c = sqlite3.connect(str(self._db_path)); c.row_factory = sqlite3.Row
        try:
            return c.execute("SELECT * FROM agent_status_state WHERE agent_id=?", (aid,)).fetchone()
        finally:
            c.close()

    def _set(self, key, val):
        import json
        c = sqlite3.connect(str(self._db_path))
        try:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(val)))
            c.commit()
        finally:
            c.close()

    # 1. Heartbeat turnBusy feeds in_turn (set + owning clear) ───────────────
    def test_heartbeat_turn_busy_true_sets_in_turn(self):
        self._register("hb1")
        r = self.client.post("/api/v1/agents/hb1/heartbeat",
                             json={"bridgeId": "sidecar-1", "turnBusy": True,
                                   "turnRunId": "run-1", "turnRuntime": "hermes"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(int(self._state("hb1")["in_turn"]), 1,
                         "turnBusy:true heartbeat must set agent_status_state.in_turn")
        # Owning bridge + run clears it.
        r = self.client.post("/api/v1/agents/hb1/heartbeat",
                             json={"bridgeId": "sidecar-1", "turnBusy": False,
                                   "turnRunId": "run-1", "turnRuntime": "hermes"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(int(self._state("hb1")["in_turn"]), 0,
                         "turnBusy:false from the OWNING bridge+run must clear in_turn")

    # 2. Ownership guard: a non-owning/stale bridge must NOT clear in_turn ────
    def test_heartbeat_turn_busy_false_from_stale_bridge_does_not_clear(self):
        self._register("hb2")
        self.client.post("/api/v1/agents/hb2/heartbeat",
                         json={"bridgeId": "sidecar-owner", "turnBusy": True,
                               "turnRunId": "run-9", "turnRuntime": "hermes"})
        self.assertEqual(int(self._state("hb2")["in_turn"]), 1)
        # A DIFFERENT (superseded) bridge tries to clear — must be ignored.
        r = self.client.post("/api/v1/agents/hb2/heartbeat",
                             json={"bridgeId": "stale-other", "turnBusy": False,
                                   "turnRunId": "run-9", "turnRuntime": "hermes"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(int(self._state("hb2")["in_turn"]), 1,
                         "a non-owning bridge's turnBusy:false must NOT clear in_turn")

    # 3. End-to-end under new: managed agent with fresh turnBusy -> working ───
    def test_managed_turn_busy_serves_working_under_new(self):
        self._heartbeat_environment("hermes")
        self._register("hb3", mode="managed", runtime="hermes")
        self._set("status_engine", "new")
        self.client.post("/api/v1/agents/hb3/heartbeat",
                         json={"bridgeId": "sidecar-1", "turnBusy": True,
                               "turnRunId": "run-3", "turnRuntime": "hermes"})
        import asyncio
        from service.db import get_db
        from service.routers import api_v2

        async def run():
            db = await get_db()
            try:
                row = await (await db.execute("SELECT * FROM agents WHERE id='hb3'")).fetchone()
                return await api_v2.engine_status(db, row)
            finally:
                await db.close()

        self.assertEqual(asyncio.run(run()), "working",
                         "managed agent mid-turn (fresh turnBusy heartbeat) must serve `working` under new")

    # 4. in_turn staleness backstop ──────────────────────────────────────────
    def test_in_turn_backstop_treats_stale_turn_as_ended(self):
        from datetime import datetime, timezone, timedelta
        from service.routers import api_v2
        self._heartbeat_environment("hermes")
        self._register("hb4", mode="managed", runtime="hermes")
        # Seed an in_turn=1 row whose last_event_at is older than the backstop.
        stale = (datetime.now(timezone.utc)
                 - timedelta(seconds=api_v2.TURN_BUSY_BACKSTOP_SECONDS + 120)
                 ).strftime("%Y-%m-%dT%H:%M:%SZ")
        c = sqlite3.connect(str(self._db_path))
        try:
            c.execute(
                """
                INSERT INTO agent_status_state (agent_id, in_turn, awaiting_input,
                    turn_run_id, last_event, last_event_at, updated_at)
                VALUES (?, 1, 0, '', 'turn_start', ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET in_turn=1, last_event_at=excluded.last_event_at
                """,
                ("hb4", stale, stale),
            )
            c.commit()
        finally:
            c.close()
        import asyncio
        from service.db import get_db

        async def run():
            db = await get_db()
            try:
                row = await (await db.execute("SELECT * FROM agents WHERE id='hb4'")).fetchone()
                inputs = await api_v2._gather_status_inputs(db, row)
                return inputs.in_turn
            finally:
                await db.close()

        self.assertFalse(asyncio.run(run()),
                         "an in_turn older than TURN_BUSY_BACKSTOP_SECONDS must be treated as not-in-turn")

    def test_in_turn_backstop_keeps_fresh_turn(self):
        # Control: a FRESH in_turn (recent last_event_at) is NOT clamped.
        from service.routers import api_v2
        self._heartbeat_environment("hermes")
        self._register("hb5", mode="managed", runtime="hermes")
        self.client.post("/api/v1/agents/hb5/heartbeat",
                         json={"bridgeId": "sidecar-1", "turnBusy": True,
                               "turnRunId": "run-5", "turnRuntime": "hermes"})
        import asyncio
        from service.db import get_db

        async def run():
            db = await get_db()
            try:
                row = await (await db.execute("SELECT * FROM agents WHERE id='hb5'")).fetchone()
                inputs = await api_v2._gather_status_inputs(db, row)
                return inputs.in_turn
            finally:
                await db.close()

        self.assertTrue(asyncio.run(run()),
                        "a fresh in_turn must remain in-turn (backstop must not over-clamp)")


class ConsoleLeaseAndStalenessByproductTests(FastApiTestCase):
    """M-A + M-B (2026-06-05): behavioral coverage of the SERVED byproduct path
    (_compute_live_status_cache -> status_inputs -> derive), which is what
    status_engine=new actually serves.

    M-A: a console-working lease surfaces `working` ONLY with a live worker (the
    has_live_worker gate) — the H1/M1 fold the lease feature relies on, previously
    untested at the Python level.
    M-B: a stale in_turn (dropped/absent turn-END) is clamped on THIS path too, matching
    _gather_status_inputs (the byproduct read previously skipped the staleness clamp)."""

    ENV_ID = "linux:test-host:default"
    MACHINE = "linux:test-host"

    def _heartbeat_environment(self):
        r = self.client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV_ID, "label": "L", "machineId": self.MACHINE, "os": "linux",
            "kind": "linux", "bridgeId": "env-bridge", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"],
                          "capabilities": {"nativeResume": True}}], "metadata": {}})
        self.assertEqual(r.status_code, 200, r.text)

    def _register(self, aid):
        r = self.client.post("/api/v1/agents", json={"agentId": aid, "role": "coder",
            "runtime": "claude-code", "sessionMode": "managed", "machineId": self.MACHINE,
            "bridgeId": "env-bridge"})
        self.assertEqual(r.status_code, 200, r.text)

    def _seed_live_worker(self, aid):
        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
        tid = f"term_{aid}"
        c = sqlite3.connect(str(self._db_path))
        try:
            c.execute(
                """INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace,
                    mode, owner_mode, terminal_id, terminal_status, spawn_spec_id,
                    spawn_request_id, status, started_at, last_seen)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f"sess_{aid}", aid, self.ENV_ID, "claude-code", "/workspace/repo",
                 "managed-warm", "managed", tid, "attached", None, None, "running", now, now))
            c.execute(
                """INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id,
                    bridge_id, runtime, workspace, command, output, status, requested_by,
                    created_at, updated_at, stopped_at, error)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (tid, f"sess_{aid}", aid, self.ENV_ID, "bridge-current", "claude-code",
                 "/workspace/repo", "claude-aify --aify-agent " + aid, "", "attached",
                 "dashboard", now, now, None, ""))
            c.commit()
        finally:
            c.close()

    def _byproduct_status(self, aid):
        import asyncio
        from service.db import get_db
        from service.routers import api_v2
        from service.status_engine import derive

        async def run():
            db = await get_db()
            try:
                row = await (await db.execute("SELECT * FROM agents WHERE id=?", (aid,))).fetchone()
                cache = await api_v2._compute_live_status_cache(db, row)
                return derive(cache["status_inputs"])
            finally:
                await db.close()
        return asyncio.run(run())

    # NOTE: the lease→`working` HAPPY PATH (lease + live worker) is covered by the bridge JS
    # lease/pulse tests + live validation; reproducing has_live_worker in a pure Python fixture
    # is brittle (the terminal-row liveness resolution), so here we lock the two correctness
    # properties instead: the worker GATE (below) and the staleness clamp (M-B).

    def test_console_lease_without_live_worker_not_working(self):  # M-A: the worker gate
        self._heartbeat_environment(); self._register("cl2")  # NO live worker seeded
        self.client.post("/api/v1/agents/cl2/console-working", json={})
        self.assertNotEqual(self._byproduct_status("cl2"), "working",
                            "a lease with no live worker must NOT manufacture working")

    def test_stale_in_turn_clamped_on_byproduct_path(self):  # M-B
        import datetime as _dt
        self._heartbeat_environment(); self._register("cl3"); self._seed_live_worker("cl3")
        # Establish in_turn via the real endpoint, then age last_event_at past the backstop.
        self.client.post("/api/v1/agents/cl3/status-event", json={"kind": "turn_start", "runId": "r"})
        stale = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=45)).isoformat().replace("+00:00", "Z")
        c = sqlite3.connect(str(self._db_path))
        try:
            c.execute("UPDATE agent_status_state SET last_event_at=? WHERE agent_id=?", (stale, "cl3"))
            c.commit()
        finally:
            c.close()
        self.assertNotEqual(self._byproduct_status("cl3"), "working",
                            "a stale in_turn (dropped turn-end) must be clamped on the served byproduct path")

    def test_fresh_in_turn_not_clamped_on_byproduct_path(self):  # M-B control
        self._heartbeat_environment(); self._register("cl4"); self._seed_live_worker("cl4")
        self.client.post("/api/v1/agents/cl4/status-event", json={"kind": "turn_start", "runId": "r"})
        self.assertEqual(self._byproduct_status("cl4"), "working",
                         "a FRESH in_turn must still serve working (clamp must not over-fire)")

