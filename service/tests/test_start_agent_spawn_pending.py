"""Start-agent control must not surface a false 409 while a spawn is already pending.

control_agent(action="start") cold-starts a managed worker via
_coldstart_spawn_request_for_dispatch, which returns False for an ALREADY-PENDING
spawn (idempotent success, not a failure). Clicking Start twice during a slow boot —
before the session row exists — must return spawnPending, not the misleading
"no environment bridge is available" 409 (2026-07-19).
"""
import asyncio

from service.db import get_db
from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now

from service.tests._base import FastApiTestCase
from service.clock import now as _now


class StartAgentSpawnPendingTests(FastApiTestCase):
    DB_NAME = "aify-start-spawn-pending-test.db"

    def _register_managed(self, agent_id: str, *, runtime: str = "hermes"):
        r = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": runtime,
            "sessionMode": "managed",
        })
        self.assertEqual(r.status_code, 200, r.text)

    def _execute(self, q, params=()):
        async def _run():
            db = await get_db()
            try:
                await db.execute(q, params); await db.commit()
            finally:
                await db.close()
        asyncio.run(_run())

    def _seed_queued_spawn(self, agent_id: str, runtime: str = "hermes"):
        # A queued spawn_request makes _coldstart return False (idempotent — one
        # already pending) AND _has_pending_or_booting_spawn_request return True.
        # _has_pending_or_booting_spawn_request only reads spawn_requests; skip the
        # spec/env FK parents for this focused seed by disabling FK on this connection.
        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    """
                    INSERT INTO spawn_requests (id, spawn_spec_id, environment_id, agent_id, runtime, status, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (f"spawn-{agent_id}", "spec-1", "env-1", agent_id, runtime, "queued", _now(), _now()),
                )
                await db.commit()
            finally:
                await db.close()
        asyncio.run(_run())

    def _start(self, agent_id: str):
        return self.client.post(f"/api/v1/agents/{agent_id}/control", json={"action": "start"})

    def test_pending_spawn_returns_spawnpending_not_409(self):
        self._register_managed("sc-hermes")
        self._seed_queued_spawn("sc-hermes")
        r = self._start("sc-hermes")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body.get("spawnPending"), f"expected spawnPending, got {body}")

    def test_no_bridge_and_no_pending_still_409(self):
        # No pending spawn and no resolvable environment → coldstart returns False → the genuine
        # 409 is preserved. The message now names the RECORDED cause instead of asserting one:
        # this agent has no environment to resolve, which is not the same failure as a runtime that
        # cannot be cold-started or an agent that is resident.
        self._register_managed("sc-hermes2")
        r = self._start("sc-hermes2")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("environment bound to this agent could not be resolved", r.text.lower())
