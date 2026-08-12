"""Reconcile backstop: a delivered require_reply run whose worker turn DIED without
replying (model 429 / mid-turn interrupt / stall) must be FAILED past a staleness window
so it doesn't strand as 'delivered' forever (sc-manager live repro 2026-07-10).

The existing _sweep_unmirrored_failed_handoffs then mirrors the failure to the sender.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from service.db import get_db
from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now

from service.tests._base import FastApiTestCase


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _minutes_ago(m: int) -> str:
    return _iso(datetime.now(timezone.utc) - timedelta(minutes=m))


class StrandedReplyFailTests(FastApiTestCase):
    DB_NAME = "aify-stranded-reply-test.db"

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

    def _run_reaper(self):
        async def _run():
            db = await get_db()
            try:
                out = await api_v2._fail_stranded_delivered_reply_runs(db)
                await db.commit()
                return out
            finally:
                await db.close()
        return asyncio.run(_run())

    def _seed_run(self, run_id, *, target, from_agent="sc-manager", status="delivered",
                  require_reply=1, result_message_id="", requested_at=None):
        self._execute(
            """
            INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority, status, require_reply,
                result_message_id, requested_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (run_id, None, from_agent, target, "start_if_possible", "managed", "request",
             "do X", "please do X", "normal", status, require_reply, result_message_id,
             requested_at or _minutes_ago(60)),
        )

    def setUp(self):
        super().setUp()
        self._register("sc-manager")
        self._register("sc-architect")

    def test_stale_delivered_reply_run_is_failed_with_cause(self):
        self._seed_run("run_stale", target="sc-architect", requested_at=_minutes_ago(60))
        out = self._run_reaper()
        self.assertEqual(len(out), 1, f"the stale stranded run should be failed: {out}")
        r = self._fetchone("SELECT status, summary, error_text FROM dispatch_runs WHERE id='run_stale'")
        self.assertEqual(r["status"], "failed")
        self.assertIn("presumed dead", (r["summary"] or "").lower())
        # Idempotent: a second pass fails nothing new.
        self.assertEqual(self._run_reaper(), [])

    def test_fresh_delivered_run_not_failed(self):
        self._seed_run("run_fresh", target="sc-architect", requested_at=_minutes_ago(5))
        self.assertEqual(self._run_reaper(), [], "a recent delivered run is still in flight")
        r = self._fetchone("SELECT status FROM dispatch_runs WHERE id='run_fresh'")
        self.assertEqual(r["status"], "delivered")

    def test_run_with_reply_not_failed(self):
        self._seed_run("run_replied", target="sc-architect", requested_at=_minutes_ago(60),
                       result_message_id="msg-123")
        self.assertEqual(self._run_reaper(), [])
        r = self._fetchone("SELECT status FROM dispatch_runs WHERE id='run_replied'")
        self.assertEqual(r["status"], "delivered")

    def test_non_reply_run_not_failed(self):
        self._seed_run("run_norr", target="sc-architect", requested_at=_minutes_ago(60),
                       require_reply=0)
        self.assertEqual(self._run_reaper(), [])

    def test_actively_working_on_this_run_is_skipped(self):
        # The agent is CURRENTLY in a live turn on this exact run → never fail it.
        self._seed_run("run_live", target="sc-architect", requested_at=_minutes_ago(60))
        self._execute(
            "INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_updated_at) VALUES (?,?,?,?)",
            ("sc-architect", 1, "run_live", api_v2._now()),
        )
        self.assertEqual(self._run_reaper(), [], "a live turn on this run must be skipped")
        r = self._fetchone("SELECT status FROM dispatch_runs WHERE id='run_live'")
        self.assertEqual(r["status"], "delivered")

    def test_working_on_a_different_run_still_fails_the_stranded_one(self):
        # turn_busy=1 but on a DIFFERENT run → the stranded one is not protected.
        self._seed_run("run_orphan", target="sc-architect", requested_at=_minutes_ago(60))
        self._execute(
            "INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_updated_at) VALUES (?,?,?,?)",
            ("sc-architect", 1, "some_other_run", api_v2._now()),
        )
        out = self._run_reaper()
        self.assertEqual(len(out), 1)
        r = self._fetchone("SELECT status FROM dispatch_runs WHERE id='run_orphan'")
        self.assertEqual(r["status"], "failed")

    def test_disabled_when_setting_zero(self):
        self.client.put("/api/v1/settings", json={"stranded_reply_fail_minutes": 0})
        self._seed_run("run_off", target="sc-architect", requested_at=_minutes_ago(120))
        self.assertEqual(self._run_reaper(), [], "0 disables the reaper")
        r = self._fetchone("SELECT status FROM dispatch_runs WHERE id='run_off'")
        self.assertEqual(r["status"], "delivered")
