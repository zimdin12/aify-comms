"""/turn-end no-op fast path (2026-07-19).

A KEEP-CLEARED turn-end detector re-asserts turn-end every ~45s for the WHOLE idle
life of every agent. When there is genuinely nothing to clear — agent_turn_state.turn_busy
already 0 AND agent_status_state.in_turn already 0 — the handler must short-circuit BEFORE
the write/commit/broadcast (the periodic-write anti-pattern the _LIVE_STATE_CACHE redesign
removed). But a real stray (either bit set) must still take the full clear, because served
`working` derives from in_turn — the KEEP-CLEARED re-assert is exactly what heals a stray
in_turn=1. So the short-circuit requires BOTH bits 0.
"""
import asyncio

from service.db import get_db
from service.routers import api_v2

from service.tests._base import FastApiTestCase


class TurnEndNoopFastPathTests(FastApiTestCase):
    DB_NAME = "aify-turn-end-noop-test.db"

    def _register(self, agent_id: str, *, role: str = "coder", **extra):
        payload = {"agentId": agent_id, "role": role}
        payload.update(extra)
        r = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(r.status_code, 200, r.text)

    def _execute(self, q, params=()):
        async def _run():
            db = await get_db()
            try:
                await db.execute(q, params); await db.commit()
            finally:
                await db.close()
        asyncio.run(_run())

    def _fetchone(self, q, params=()):
        async def _run():
            db = await get_db()
            try:
                return await (await db.execute(q, params)).fetchone()
            finally:
                await db.close()
        return asyncio.run(_run())

    def _turn_end(self, agent_id: str):
        return self.client.post(f"/api/v1/agents/{agent_id}/turn-end")

    def setUp(self):
        super().setUp()
        self._register("sc-worker")

    def test_full_clear_when_turn_busy_set(self):
        # turn_busy=1 → NOT a no-op; the full clear runs and turn_busy → 0.
        self._execute(
            "INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_updated_at) VALUES (?,?,?,?)",
            ("sc-worker", 1, "run_x", "2020-01-01T00:00:00Z"),
        )
        r = self._turn_end("sc-worker")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("noop", r.json(), "a real clear must not short-circuit")
        row = self._fetchone("SELECT turn_busy FROM agent_turn_state WHERE agent_id='sc-worker'")
        self.assertEqual(int(row["turn_busy"]), 0)

    def test_noop_when_both_bits_already_clear(self):
        # turn_busy=0 (fixed old stamp) AND in_turn absent/0 → short-circuit, NO write.
        self._execute(
            "INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_updated_at) VALUES (?,?,?,?)",
            ("sc-worker", 0, "", "2020-01-01T00:00:00Z"),
        )
        r = self._turn_end("sc-worker")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json().get("noop"), "already-cleared")
        row = self._fetchone("SELECT turn_updated_at FROM agent_turn_state WHERE agent_id='sc-worker'")
        self.assertEqual(row["turn_updated_at"], "2020-01-01T00:00:00Z",
                         "the no-op path must not rewrite turn_updated_at")

    def test_stray_in_turn_is_healed_even_when_turn_busy_clear(self):
        # The regression guard: turn_busy=0 but in_turn=1 (a stray latched by the
        # detector semantic gap). Served `working` derives from in_turn, so this MUST
        # take the full clear — a turn_busy-only skip would strand it 'working' forever.
        self._execute(
            "INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_updated_at) VALUES (?,?,?,?)",
            ("sc-worker", 0, "", "2020-01-01T00:00:00Z"),
        )
        self._execute(
            "INSERT INTO agent_status_state (agent_id, in_turn) VALUES (?, 1)",
            ("sc-worker",),
        )
        r = self._turn_end("sc-worker")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("noop", r.json(), "a stray in_turn must NOT be treated as already-cleared")
        row = self._fetchone("SELECT in_turn FROM agent_status_state WHERE agent_id='sc-worker'")
        self.assertEqual(int(row["in_turn"]), 0, "the stray in_turn must be healed to 0")
