"""Interrupt, Stop, Resume and Start on an AGENT — three refusals and what a second Start answers.

`POST /agents/{id}/control` is the row of buttons beside an agent in the dashboard. Three of its
refusals had no test, and all three read as exercised until fe1e22ad because `service/tests/data/`
holds a pre-split copy of the handler:

    400 Unsupported agent control action "<a>"
    409 Agent "<a>" is resident — its terminal is the CLI you launched, not a dashboard-owned worker.
    409 Agent "<a>" has no active run to interrupt

WHAT START DOES SINCE D8 is not this route's to decide. A defined agent's start is queued for its
host (D9c, `test_legacy_routes_delegate_defined_agents.py`) and an undefined one is refused
(`test_a_spawn_defines_its_agent_first.py`), so the live-session gate and the cold-start refusal this file
used to pin are no longer reached from Start. What a second click on a queued start answers is pinned
below, against the defined agent it is now for.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase
from service.tests.defined_agents import define
from service.tests.published_state import publish as publish_state

AGENT_ID = "lc-managed"
ENVIRONMENT_ID = "linux:test-host:default"
MACHINE_ID = "linux:test-host"
BRIDGE_ID = "bridge-one"

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
                "machineId": MACHINE_ID,
                "os": "linux",
                "kind": "linux",
                "bridgeId": BRIDGE_ID,
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

    # ── Start, twice ──────────────────────────────────────────────────────────────────────────

    def test_clicking_start_twice_during_a_slow_boot_is_not_an_error(self):
        """`_coldstart` returns False for an already-pending spawn too — idempotent success, not a
        failure. Surfacing a "no environment bridge" error on the second click is the false alarm
        this branch exists to prevent.

        Since D8 only a defined agent starts, and its start is queued for its host (D9c), so the second
        click lands while the first one's lifecycle request is still open."""
        define(self, AGENT_ID, environment_id=ENVIRONMENT_ID, machine_id=MACHINE_ID, bridge_id=BRIDGE_ID,
               runtime="codex", workspace="/workspace/proj")
        publish_state(self, MACHINE_ID, {AGENT_ID: (None, "available")})
        self.assertEqual(self._control("start").status_code, 200)
        second = self._control("start")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json().get("queued"), second.text)

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
