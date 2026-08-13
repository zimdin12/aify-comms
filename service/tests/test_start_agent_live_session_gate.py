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

from service.api_core.liveness import _LIVE_SESSION_STATUSES
from service.db import get_db
from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now

from service.tests._base import FastApiTestCase


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
                     api_v2._now(), ended_at, api_v2._now()),
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
        cold-start legitimately fails with 409 "no environment bridge is available". That 409 is
        PROOF the gate allowed a start attempt — the bug being fixed never got that far, it
        returned 200 + alreadyRunning and created nothing.
        """
        if r.status_code == 200:
            self.assertNotEqual(r.json().get("alreadyRunning"), True, f"{why}: {r.json()}")
            return
        self.assertEqual(r.status_code, 409, f"{why}: unexpected status {r.status_code} {r.text}")
        self.assertIn(
            "environment bridge", r.json().get("detail", "").lower(),
            f"{why}: expected the cold-start path to be reached, got {r.text}",
        )

    def test_lost_session_does_not_block_start(self):
        """THE REGRESSION. A `lost` session is terminal — it must not report alreadyRunning."""
        self._register_managed("gate-lost")
        self._seed_session("gate-lost", "sess-lost", "lost", ended_at="2026-04-30T13:59:11Z")
        r = self._start("gate-lost")
        self._assert_gate_let_it_through(
            r, "a terminal `lost` session must not make the agent permanently unstartable"
        )

    def test_every_terminal_status_leaves_the_agent_startable(self):
        """Guard the whole class, not just `lost` — any non-live status must allow a start."""
        for status in ("lost", "ended", "stopped", "failed", "cancelled", "completed"):
            with self.subTest(status=status):
                agent = f"gate-term-{status}"
                self._register_managed(agent)
                self._seed_session(agent, f"sess-{status}", status,
                                   ended_at="2026-04-30T13:59:11Z")
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

    def test_gate_uses_the_canonical_constants(self):
        """Pin the allowlist to the shared constants so a new session status cannot silently
        become 'live' in this gate again."""
        # v0.5.4: `_LIVE_SESSION_STATUSES` moved to api_core/liveness.py, so it is read from its OWNER
        # rather than through the carrier. Reading it off `api_v2` kept working only because the carrier
        # re-exported it, which is the indirection this series is removing.
        union = {s.lower() for s in api_v2.LIVE_SESSION_STATUSES} | {
            s.lower() for s in _LIVE_SESSION_STATUSES
        }
        for terminal in ("lost", "ended", "stopped", "failed", "cancelled", "completed"):
            self.assertNotIn(terminal, union, f"{terminal} must never count as a live session")
        for live in ("running", "starting", "recovering"):
            self.assertIn(live, union)
