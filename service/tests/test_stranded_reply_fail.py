"""Reconcile backstop: a delivered require_reply run that nothing alive can answer is FAILED, so it
doesn't strand as 'delivered' forever, and the sender is told (via _sweep_unmirrored_failed_handoffs).

STATE, NOT TIME (v0.7.4): it used to fail any such run 45 minutes after it was requested, which failed
real long work. Now only a run whose agent has no live claimer, or whose turn was interrupted, is failed,
however long the work takes.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from service.db import get_db

from service.tests._base import FastApiTestCase
from service.clock import now as _now
from service.reconcilers.dispatch_lifecycle import _fail_stranded_delivered_reply_runs


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
                out = await _fail_stranded_delivered_reply_runs(db)
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

    def _seed_interrupt(self, run_id):
        self._execute(
            "INSERT INTO dispatch_controls (id, run_id, from_agent, action, status, requested_at) VALUES (?,?,?,?,?,?)",
            (f"ctl-{run_id}", run_id, "dashboard", "interrupt", "completed", _now()),
        )

    def test_a_run_nothing_alive_can_answer_is_failed_with_the_observed_cause(self):
        self._seed_run("run_dead", target="sc-architect", requested_at=_minutes_ago(10))
        out = self._run_reaper()
        self.assertEqual(len(out), 1, f"a run with no live claimer should be failed: {out}")
        r = self._fetchone("SELECT status, summary FROM dispatch_runs WHERE id='run_dead'")
        self.assertEqual(r["status"], "failed")
        self.assertIn("nothing is left that could send one", r["summary"])
        self.assertEqual(self._run_reaper(), [], "idempotent: a second pass fails nothing new")

    def test_a_live_agent_keeps_its_run_however_long_the_work_takes(self):
        """The ruling this module now follows: agents may work as long as the work needs."""
        self._register("sc-live", sessionMode="resident", runtime="claude-code", bridgeId="live-bridge",
                       machineId="win32:test-host", capabilities=["resident-run"])
        self._seed_run("run_long", target="sc-live", requested_at=_minutes_ago(600))
        self.assertEqual(self._run_reaper(), [], "a live claimer can still reply, so the run stays open")
        self.assertEqual(self._fetchone("SELECT status FROM dispatch_runs WHERE id='run_long'")["status"], "delivered")

    def test_CONTROL_the_same_run_fails_once_its_agent_is_gone(self):
        self._register("sc-live", sessionMode="resident", runtime="claude-code", bridgeId="live-bridge",
                       machineId="win32:test-host", capabilities=["resident-run"])
        self._seed_run("run_long", target="sc-live", requested_at=_minutes_ago(600))
        self._execute("UPDATE bridge_instances SET last_seen = '2026-01-01T00:00:00Z' WHERE agent_id = 'sc-live'")
        self._execute("UPDATE agents SET last_seen = '2026-01-01T00:00:00Z' WHERE id = 'sc-live'")
        self.assertEqual(len(self._run_reaper()), 1)

    def test_an_interrupted_run_is_failed_even_with_a_live_agent(self):
        """Its turn was stopped on purpose; left open, the Work Loop would re-wake an agent the operator stopped."""
        self._register("sc-live", sessionMode="resident", runtime="claude-code", bridgeId="live-bridge",
                       machineId="win32:test-host", capabilities=["resident-run"])
        self._seed_run("run_stopped", target="sc-live", requested_at=_minutes_ago(10))
        self._seed_interrupt("run_stopped")
        self.assertEqual(len(self._run_reaper()), 1)
        self.assertIn("interrupt", self._fetchone("SELECT summary FROM dispatch_runs WHERE id='run_stopped'")["summary"].lower())

    def test_a_run_inside_the_grace_is_not_failed(self):
        self._seed_run("run_fresh", target="sc-architect", requested_at=_minutes_ago(1))
        self.assertEqual(self._run_reaper(), [], "a worker between restarts must not read as gone")
        self.assertEqual(self._fetchone("SELECT status FROM dispatch_runs WHERE id='run_fresh'")["status"], "delivered")

    def test_run_with_reply_not_failed(self):
        self._seed_run("run_replied", target="sc-architect", requested_at=_minutes_ago(60),
                       result_message_id="msg-123")
        self.assertEqual(self._run_reaper(), [])

    def test_non_reply_run_not_failed(self):
        self._seed_run("run_norr", target="sc-architect", requested_at=_minutes_ago(60), require_reply=0)
        self.assertEqual(self._run_reaper(), [])

    def test_actively_working_on_this_run_is_skipped(self):
        self._seed_run("run_live", target="sc-architect", requested_at=_minutes_ago(60))
        self._execute(
            "INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_updated_at) VALUES (?,?,?,?)",
            ("sc-architect", 1, "run_live", _now()),
        )
        self.assertEqual(self._run_reaper(), [], "a live turn on this run must be skipped")
