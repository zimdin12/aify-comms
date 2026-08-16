"""Interrupt, Stop, Resume and Start on an AGENT — three refusals and the incident behind one gate.

`POST /agents/{id}/control` is the row of buttons beside an agent in the dashboard. Three of its
refusals had no test, and all three read as exercised until fe1e22ad because `service/tests/data/`
holds a pre-split copy of the handler:

    400 Unsupported agent control action "<a>"
    409 Agent "<a>" is resident — its terminal is the CLI you launched, not a dashboard-owned worker.
    409 Agent "<a>" has no active run to interrupt

THE START GATE IS AN ALLOWLIST BECAUSE A BLOCKLIST BROKE A WHOLE TEAM. It used to ask
`status NOT IN ('stopped','failed','ended','cancelled')`, which treats every status NOT on that list
as live. `lost` is not on it, so an agent whose worker died months ago read as "already running"
forever: Start returned `alreadyRunning`, no spawn request was ever created, and clicking again just
repeated the toast. Four sessions of the ef- team sat `lost` since 2026-04-30 and were permanently
unstartable. What made it invisible is that `derive()` reported `available` off real liveness, so the
status the operator saw and the gate that refused them disagreed.

So the gate is tested from BOTH sides against the two canonical sets: every status in the union must
read as live, and the ones outside it — `lost` first among them — must not. Plus the `ended_at`
clause, because a live status with an end time is a stale row the reconcilers heal, and trusting it
re-creates exactly the permanent block.

THE START REFUSAL CARRIES THE REASON COLD-START RECORDED, for the same N8 reason as the restart path:
this call site used to pass no warnings list, so all five causes rendered "no environment bridge is
available to run it. Start one on its host with `aify-comms`." That sentence names a cause — falsely,
for two of them — and the advice is worse than vagueness would have been, because a bare
`aify-comms` on a host that already runs one supersedes the live bridge and reaps its managed
workers. A wrong diagnosis here steers the operator into an outage.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from service.api_core.tuning import LIVE_SESSION_STATUSES
from service.routers.agents.shared import _borrowed_live_session_statuses
from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT_ID = "lc-managed"
ENVIRONMENT_ID = "linux:test-host:default"

#: The union the gate builds, derived here the same way rather than re-typed — the point of the fix
#: was that a new session status must never silently mean "live" in one place and not the other.
LIVE_STATUSES = sorted(
    {s.lower() for s in LIVE_SESSION_STATUSES}
    | {s.lower() for s in _borrowed_live_session_statuses()}
)

#: `lost` is FIRST for a reason: it is the one the old blocklist let through as live.
NOT_LIVE_STATUSES = ("lost", "stopped", "failed", "ended", "cancelled", "managed-warm", "")


class AgentControlRefusalTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self._register(AGENT_ID, session_mode="managed")
        self._heartbeat()

    # ── seeding ──────────────────────────────────────────────────────────────────────────────

    def _register(self, agent_id: str, session_mode: str = "managed", runtime: str = "codex"):
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": runtime,
                "sessionMode": session_mode,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _heartbeat(self, runtimes=("codex",)) -> None:
        response = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": ENVIRONMENT_ID,
                "label": "Linux on test-host",
                "machineId": "linux:test-host",
                "os": "linux",
                "kind": "linux",
                "bridgeId": "bridge-one",
                "cwdRoots": ["/workspace"],
                "runtimes": [{"runtime": r, "available": True} for r in runtimes],
                "status": "online",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _seed_session(self, status: str, ended_at: str = "", agent_id: str = AGENT_ID) -> None:
        self._write(
            "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, ended_at,"
            " started_at, last_seen) VALUES (?,?,?,?,?,?,?,?)",
            (f"sess-{agent_id}-{status or 'blank'}", agent_id, ENVIRONMENT_ID, "codex", status,
             ended_at, "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z"),
        )

    def _control(self, action: str, agent_id: str = AGENT_ID):
        return self.client.post(
            f"/api/v1/agents/{agent_id}/control",
            json={"action": action, "from_agent": "dashboard"},
        )

    # ── the action allowlist ─────────────────────────────────────────────────────────────────

    def test_the_action_allowlist_is_exactly_the_four_buttons(self):
        for action in ("cancel", "kill", "restart", "pause", "", "start-now"):
            with self.subTest(refused=action):
                response = self._control(action)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(
                    response.json()["detail"], f'Unsupported agent control action "{action}"',
                )

    def test_the_action_is_normalised_but_the_refusal_echoes_what_was_sent(self):
        """`.strip().lower()` decides, `req.action` is quoted back. An operator debugging a rejected
        value needs their own spelling, not a normalised one — and the normalisation is what makes
        the allowlist safe to write in one casing."""
        self._seed_session("running")
        self.assertEqual(self._control("  STOP ").status_code, 200)
        refused = self._control("  Nonsense ")
        self.assertEqual(refused.json()["detail"], 'Unsupported agent control action "  Nonsense "')

    def test_the_action_is_checked_before_the_agent_is_looked_up(self):
        response = self._control("nonsense", agent_id="lc-never-existed")
        self.assertEqual(response.status_code, 400, response.text)

    def test_an_unknown_agent_is_404(self):
        response = self._control("stop", agent_id="lc-never-existed")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Agent 'lc-never-existed' not found")

    # ── Start on a resident agent ────────────────────────────────────────────────────────────

    def test_starting_a_RESIDENT_agent_is_refused_and_explains_what_it_is(self):
        """A resident agent's terminal is the operator's own CLI. Starting a worker for it would
        create a second, dashboard-owned process alongside the one they are typing in."""
        self._register("lc-resident", session_mode="resident")
        response = self._control("start", agent_id="lc-resident")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"],
            'Agent "lc-resident" is resident — its terminal is the CLI you launched, '
            "not a dashboard-owned worker. Switch it to managed to start one from here.",
        )

    def test_the_other_three_actions_still_work_on_a_resident_agent(self):
        """The resident refusal is scoped to Start. Stop and Resume are how an operator quiets a
        resident agent, and refusing them would take away the only controls it has."""
        self._register("lc-resident", session_mode="resident")
        for action in ("stop", "resume"):
            with self.subTest(action=action):
                self.assertEqual(self._control(action, agent_id="lc-resident").status_code, 200)

    # ── Start when a worker is (or is not) already live ──────────────────────────────────────

    def test_every_live_session_status_reads_as_already_running(self):
        for status in LIVE_STATUSES:
            with self.subTest(status=status):
                agent_id = f"lc-live-{status}"
                self._register(agent_id)
                self._seed_session(status, agent_id=agent_id)
                response = self._control("start", agent_id=agent_id)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(
                    response.json().get("alreadyRunning"),
                    f"{status} is a live session; starting again would spawn a duplicate worker",
                )

    def test_a_LOST_session_does_not_block_a_start(self):
        """THE INCIDENT. Four sessions sat `lost` since 2026-04-30 and were permanently unstartable
        because the old blocklist treated every unlisted status as live. Each of these must reach
        the cold-start path instead of returning alreadyRunning."""
        for status in NOT_LIVE_STATUSES:
            with self.subTest(status=status):
                agent_id = f"lc-dead-{status or 'blank'}"
                self._register(agent_id)
                self._seed_session(status, agent_id=agent_id)
                response = self._control("start", agent_id=agent_id)
                self.assertFalse(
                    response.status_code == 200 and response.json().get("alreadyRunning"),
                    f"a {status!r} session read as already running — the ef- team's exact block",
                )

    def test_a_live_status_with_an_end_time_is_a_stale_row_not_a_live_worker(self):
        """The `ended_at` clause. The reconcilers heal these rows; trusting one re-creates the
        permanent block with a status that IS on the allowlist."""
        self._seed_session("running", ended_at="2026-08-16T00:00:00Z")
        response = self._control("start")
        self.assertFalse(
            response.status_code == 200 and response.json().get("alreadyRunning"),
            "a session marked ended must not count as a live worker",
        )

    def test_a_start_that_cannot_cold_start_reports_the_RECORDED_reason(self):
        """Not the invented one. This call site passed no warnings list, so all five cold-start
        causes rendered "no environment bridge is available to run it. Start one on its host with
        `aify-comms`" — false for two of them, and the advice steers the operator into superseding a
        live bridge and reaping its managed workers."""
        self._heartbeat(runtimes=())
        response = self._control("start")
        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertIn("Cannot start managed codex for this agent", detail)
        self.assertNotIn(
            "Start one on its host with", detail,
            "the discarded-reason wording is back — see the N8 note in the handler",
        )

    def test_a_start_with_a_claimable_environment_creates_a_spawn_request(self):
        """The accepting side, so the refusals above are not the only outcome pinned."""
        response = self._control("start")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json().get("spawnRequested"))

    def test_clicking_start_twice_during_a_slow_boot_is_not_an_error(self):
        """`_coldstart` returns False for an already-pending spawn too — idempotent success, not a
        failure. Surfacing a "no environment bridge" error on the second click is the false alarm
        this branch exists to prevent."""
        self.assertEqual(self._control("start").status_code, 200)
        second = self._control("start")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json().get("spawnPending") or second.json().get("spawnRequested"))

    # ── Interrupt with nothing to interrupt ──────────────────────────────────────────────────

    def test_interrupt_with_no_active_run_is_refused(self):
        response = self._control("interrupt")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"], f'Agent "{AGENT_ID}" has no active run to interrupt',
        )

    def test_STOP_with_no_active_run_is_NOT_refused(self):
        """The asymmetry, pinned. Interrupt without a run has nothing to act on; Stop still has work
        to do — it cancels queued dispatches and marks the agent stopped."""
        response = self._control("stop")
        self.assertEqual(response.status_code, 200, response.text)
