"""The dashboard Start gate must decide "already running" from an ALLOWLIST of live statuses.

Live incident 2026-07-26: clicking Start on `ef-manager` did nothing — the agent stayed
`available` — and clicking again toasted "ef-manager is already running". Cause: the gate asked
``status NOT IN ('stopped','failed','ended','cancelled')``, so every OTHER status counted as
live. ``lost`` is not on that list, so four ef- sessions stuck ``lost`` with ``ended_at``
2026-04-30 made those agents permanently unstartable: Start returned ``alreadyRunning`` and never
created a spawn request.

The tell was the disagreement — ``derive()`` reported ``available`` off real liveness while this
gate insisted the agent was running.
"""
import asyncio

from service.db import get_db

from service.tests._base import FastApiTestCase
from service.clock import now as _now


class StartAgentLiveSessionGateTests(FastApiTestCase):
    DB_NAME = "aify-start-live-gate-test.db"

    def _register_managed(self, agent_id):
        r = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "manager",
            "runtime": "claude-code", "sessionMode": "managed",
        })
        self.assertEqual(r.status_code, 200, r.text)

    def _seed_session(self, agent_id, session_id, status, *, ended_at=""):
        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    """
                    INSERT INTO agent_sessions
                        (id, agent_id, environment_id, runtime, mode, status,
                         started_at, ended_at, last_seen)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (session_id, agent_id, "env-test", "claude-code", "managed-warm", status,
                     _now(), ended_at, _now()),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _start(self, agent_id):
        return self.client.post(
            f"/api/v1/agents/{agent_id}/control",
            json={"action": "start", "from": "dashboard"},
        )

    def _assert_gate_let_it_through(self, r, why):
        """The invariant is "the gate did not short-circuit as alreadyRunning".

        These tests seed no online environment, so once the gate lets the request past, the
        cold-start legitimately refuses. That 409 is PROOF the gate allowed a start attempt — the
        bug being fixed never got that far, it returned 200 + alreadyRunning and created nothing.

        Keyed on the CAUSE-INDEPENDENT prefix that `_coldstart_refusal_message` always renders, not
        on one cause's wording. This used to look for "environment bridge", which was the single
        sentence the router emitted for all five refusal causes; that made the assertion pass just
        as happily when the reported cause was wrong as when it was right.
        """
        if r.status_code == 200:
            self.assertNotEqual(r.json().get("alreadyRunning"), True, f"{why}: {r.json()}")
            return
        self.assertEqual(r.status_code, 409, f"{why}: unexpected status {r.status_code} {r.text}")
        self.assertIn(
            "cannot start managed", r.json().get("detail", "").lower(),
            f"{why}: expected the cold-start path to be reached, got {r.text}",
        )

    def test_every_terminal_status_leaves_the_agent_startable(self):
        """THE REGRESSION (`lost`) and the rest of its class: no non-live status may block a start.

        Seeded with NO `ended_at`, so the status ALLOWLIST alone decides. The incident rows did carry
        an `ended_at`, and seeding that made the gate's separate `ended_at` clause answer for them:
        adding `lost` to the live set left this test green. The `ended_at` clause has its own test
        below (`test_live_status_with_ended_at_is_treated_as_stale`).
        """
        for status in ("lost", "ended", "stopped", "failed", "cancelled", "completed"):
            with self.subTest(status=status):
                agent = f"gate-term-{status}"
                self._register_managed(agent)
                self._seed_session(agent, f"sess-{status}", status)
                r = self._start(agent)
                self._assert_gate_let_it_through(
                    r, f"status={status!r} is terminal and must not block Start"
                )

    def test_genuinely_live_session_still_blocks_start(self):
        """The gate must keep doing its job — a real live worker must not be duplicated."""
        for status in ("running", "starting", "active", "idle", "recovering",
                       "attached", "restarting", "cli-takeover"):
            with self.subTest(status=status):
                agent = f"gate-live-{status}"
                self._register_managed(agent)
                self._seed_session(agent, f"sess-live-{status}", status)
                r = self._start(agent)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertTrue(
                    r.json().get("alreadyRunning"),
                    f"status={status!r} is live and Start must refuse to spawn a duplicate: {r.json()}",
                )

    def test_live_status_with_ended_at_is_treated_as_stale(self):
        """A live status carrying ended_at is a stale row the reconcilers heal; trusting it
        would recreate the permanent-block bug."""
        self._register_managed("gate-contradictory")
        self._seed_session("gate-contradictory", "sess-contra", "running",
                           ended_at="2026-04-30T13:59:11Z")
        r = self._start("gate-contradictory")
        self._assert_gate_let_it_through(r, "running+ended_at is stale, not live")


ENVIRONMENT_ID = "linux:start-host:default"
BRIDGE_ID = "bridge-start-host"


class StartAgainstAnOnlineEnvironmentTests(FastApiTestCase):
    """The gate's two remaining holes, driven where a start can actually create a spawn request.

    The class above seeds no environment, so a start it lets through is refused further down; here
    one is online, so "let through" and "spawned" are the same observation.
    """

    DB_NAME = "aify-start-online-env-test.db"

    def setUp(self):
        super().setUp()
        r = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENVIRONMENT_ID, "machineId": "linux:start-host", "os": "linux", "kind": "linux",
            "bridgeId": BRIDGE_ID, "cwdRoots": ["/work"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "terminal": True, "pty": True, "terminalRuntimes": ["claude-code"], "metadata": {},
        })
        self.assertEqual(r.status_code, 200, r.text)

    def _register(self, agent_id):
        r = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": "claude-code", "sessionMode": "managed",
            "machineId": "linux:start-host", "bridgeId": BRIDGE_ID,
        })
        self.assertEqual(r.status_code, 200, r.text)

    def _start_body(self):
        return {"action": "start", "from_agent": "dashboard"}

    def _spawn_requests(self, agent_id):
        async def _run():
            db = await get_db()
            try:
                return [dict(row) for row in await (await db.execute(
                    "SELECT id, status FROM spawn_requests WHERE agent_id = ?", (agent_id,),
                )).fetchall()]
            finally:
                await db.close()
        return asyncio.run(_run())

    def _seed_managed_session_with_terminal(self, agent_id, session_status, terminal_status):
        async def _run():
            db = await get_db()
            try:
                await db.execute(
                    # NULL, not the columns' '' default: the foreign keys stay ON here, and '' names
                    # a spec and a request that do not exist.
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, mode, owner_mode,"
                    " status, started_at, ended_at, last_seen, spawn_spec_id, spawn_request_id)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,NULL,NULL)",
                    (f"sess-{agent_id}", agent_id, ENVIRONMENT_ID, "claude-code", "managed-warm",
                     "managed", session_status, _now(), "", _now()),
                )
                await db.execute(
                    "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, runtime,"
                    " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (f"term-{agent_id}", f"sess-{agent_id}", agent_id, ENVIRONMENT_ID, "claude-code",
                     terminal_status, _now(), _now()),
                )
                await db.commit()
            finally:
                await db.close()
        asyncio.run(_run())

    def test_a_session_whose_only_terminal_is_dead_does_not_block_start(self):
        """THE STORED STATUS IS NOT THE WORKER. A managed session keeps `running` until the sweep
        settles it, about a minute after its terminal died; the gate read that row alone and
        answered alreadyRunning for a worker that was gone, so Start created nothing. The restart
        guard (`_live_session_for`) already asks the sweep's rule; this gate now asks it too."""
        self._register("dead-term")
        self._seed_managed_session_with_terminal("dead-term", "running", "exited")
        r = self.client.post("/api/v1/agents/dead-term/control", json=self._start_body())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("spawnRequested"), r.json())
        self.assertEqual(len(self._spawn_requests("dead-term")), 1)

    def test_a_session_whose_terminal_is_live_still_blocks_start(self):
        """THE CONTROL, and the chosen limit: a live terminal is a live worker, and a `restarting`
        session whose old terminal is dead is a restart in flight, which a second start would
        duplicate. Neither may spawn."""
        for session_status, terminal_status in (("running", "running"), ("restarting", "exited")):
            with self.subTest(session=session_status, terminal=terminal_status):
                agent = f"live-{session_status}"
                self._register(agent)
                self._seed_managed_session_with_terminal(agent, session_status, terminal_status)
                r = self.client.post(f"/api/v1/agents/{agent}/control", json=self._start_body())
                self.assertEqual(r.status_code, 200, r.text)
                self.assertTrue(r.json().get("alreadyRunning"), r.json())
                self.assertEqual(self._spawn_requests(agent), [])

    def test_a_second_start_inside_the_first_ones_window_waits_and_answers_pending(self):
        """TWO STARTS, ONE WORKER. Both requests read "nothing live, nothing pending" before either
        wrote, and each queued a `replace` spawn: two workers, the second told to kill the first.

        The first start's pending read is where the window is widest, so the second start is
        launched from inside it and given half a second to make the same read. Unfixed, it does,
        and both insert. With the writer reserved before the reads, the second waits on the lock,
        reads the first one's committed request and answers spawnPending.
        """
        import httpx

        from service.api_core import dispatch_start

        self._register("double")
        real = dispatch_start._has_pending_or_booting_spawn_request

        async def run():
            transport = httpx.ASGITransport(app=self._app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                second_read = asyncio.Event()
                second = []

                async def read_then_race(db, agent_id):
                    answer = await real(db, agent_id)
                    if second:
                        second_read.set()
                        return answer
                    second.append(asyncio.create_task(
                        client.post("/api/v1/agents/double/control", json=self._start_body())))
                    try:
                        await asyncio.wait_for(second_read.wait(), timeout=0.5)
                    except asyncio.TimeoutError:
                        pass
                    return answer

                dispatch_start._has_pending_or_booting_spawn_request = read_then_race
                try:
                    first = await client.post("/api/v1/agents/double/control", json=self._start_body())
                    later = await second[0] if second else None
                finally:
                    dispatch_start._has_pending_or_booting_spawn_request = real
                return first, later

        first, later = asyncio.run(run())
        self.assertIsNotNone(later, "the second start was never launched, so this test judged nothing")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(later.status_code, 200, later.text)
        self.assertTrue(first.json().get("spawnRequested"), first.json())
        self.assertTrue(later.json().get("spawnPending"), later.json())
        self.assertEqual(len(self._spawn_requests("double")), 1, "two spawn requests for one agent")
