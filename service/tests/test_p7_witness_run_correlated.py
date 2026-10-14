"""P7 witnesses: the two readers that tie a turn to a run, the reply reminder (ledger B21) and the stranded-reply
guard (B26).

Neither reads the hold. P0 C6 keeps both on comms' own delivery turn with today's expressions:

  * B21 skips a reminder while the target is busy for OTHER work: a turn touched within 120 s attributed to a
    different run, or a claimed/running dispatch run. A turn on the run being reminded is that run's own
    delivery, and must not suppress its own reminder (the deadlock that once closed handoffs stale).
  * B26 leaves a delivered reply-owed run alone while the turn is open on that exact run, however old.

The reminder is driven through its route, `POST /contracts/reminders/run`; the guard through its reconciler
entry, `_fail_stranded_delivered_reply_runs`, which the dispatch sweep calls. Every skip has a delivered twin
in the same world, and the outcome is read back from the run's events, its messages and its status.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import replace

from service.api_core.authored_failures import NO_WORKER_LEFT_TO_REPLY
from service.db import get_db
from service.reconcilers.dispatch_lifecycle import _fail_stranded_delivered_reply_runs
from service.tests._base import FastApiTestCase
from service.tests.delivery_witnesses import INPUT_ADAPTER, TurnSpecimen

MACHINE = "linux:test-host"
#: Stands for "the run this witness is about" in a specimen, filled in once the run exists.
OWN = "<this run>"


class _RunWorld(FastApiTestCase):

    def _rows(self, query: str, params=()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute(query, params).fetchall())
        finally:
            conn.close()

    def _exec(self, query: str, params=()) -> None:
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(query, params)
            conn.commit()
        finally:
            conn.close()

    def _reply_owed(self, run_id: str, *, sender: str, target: str, age: float) -> str:
        """A delivered require-reply run from `sender` to `target`, requested `age` seconds ago."""
        message_id = f"msg-{run_id}"
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age))
        self._exec("INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, "
                   "dispatch_requested, timestamp) VALUES (?,?,?,'direct','request','handoff','please answer',"
                   "'normal',1,?)", (message_id, sender, target, int(time.time() * 1000)))
        self._exec("INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, dispatch_mode, execution_mode, "
                   "message_type, subject, body, priority, status, require_reply, requested_at) "
                   "VALUES (?,?,?,?,'start_if_possible','managed','request','handoff','please answer','normal',"
                   "'delivered',1,?)", (run_id, message_id, sender, target, at))
        return message_id


class ReminderSkipWitness(_RunWorld):
    """B21. The target is a live resident codex, so a reminder that is due is really sent."""

    def setUp(self):
        super().setUp()
        self._resident("lead")
        answer = self.client.put("/api/v1/settings", json={
            "reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 1, "reply_reminder_max_count": 0})
        self.assertEqual(answer.status_code, 200, answer.text)

    def _resident(self, agent_id: str) -> None:
        answer = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": "codex", "sessionMode": "resident",
            "sessionHandle": f"thread-{agent_id}", "machineId": MACHINE, "bridgeId": f"bridge-{agent_id}",
            "capabilities": ["resident-run", "resume", "interrupt", "steer"],
            "runtimeConfig": {"appServerUrl": "ws://127.0.0.1:9"}})
        self.assertEqual(answer.status_code, 200, answer.text)

    def _remind(self, case: str, *, turn: TurnSpecimen, active_run: bool = False) -> dict:
        target, run_id = f"coder-{case}", f"run-{case}"
        self._resident(target)
        message_id = self._reply_owed(run_id, sender="lead", target=target, age=600)
        INPUT_ADAPTER.apply(self._db_path, target, replace(turn, run_id=run_id) if turn.run_id == OWN else turn)
        if active_run:
            self._exec("INSERT INTO dispatch_runs (id, from_agent, target_agent, dispatch_mode, execution_mode, "
                       "message_type, subject, body, priority, status, require_reply, requested_at, started_at) "
                       "VALUES (?,?,?,'start_if_possible','managed','info','other','work','normal','running',0,"
                       "datetime('now'),datetime('now'))", (f"active-{case}", "dashboard", target))
        answer = self.client.post(f"/api/v1/contracts/reminders/run?runId={run_id}")
        self.assertEqual(answer.status_code, 200, answer.text)
        return {
            "events": [(r["event_type"], r["body"]) for r in self._rows(
                "SELECT event_type, body FROM dispatch_events WHERE run_id = ? AND event_type LIKE 'reply_reminder%'",
                (run_id,))],
            "reminders": [r["from_agent"] for r in self._rows(
                "SELECT from_agent FROM messages WHERE to_agent = ? AND in_reply_to = ?", (target, message_id))],
        }

    def _assert_skipped(self, outcome: dict) -> None:
        self.assertEqual(outcome["reminders"], [], "a skipped reminder sends no message")
        self.assertEqual([kind for kind, _ in outcome["events"]], ["reply_reminder_skipped"])
        self.assertIn("target is busy", outcome["events"][0][1])

    def _assert_reminded(self, outcome: dict) -> None:
        self.assertEqual(outcome["reminders"], ["lead"], "exactly one reminder, from the owed party")
        self.assertEqual([kind for kind, _ in outcome["events"]], ["reply_reminder"])

    def test_a_fresh_turn_on_other_work_skips_and_its_twins_are_reminded(self):
        other = TurnSpecimen("other-work", touched_age=5, run_id="run-elsewhere")
        for case, turn, skipped in (
            ("other", other, True),
            ("closed", other.closed(), False),                                    # the turn ended
            ("own", TurnSpecimen("own-delivery", touched_age=5, run_id=OWN), False),  # this run's own delivery
            ("quiet", TurnSpecimen("quiet-other", started_age=600, touched_age=300, run_id="run-elsewhere"), False),
        ):
            with self.subTest(case):
                outcome = self._remind(case, turn=turn)
                if skipped:
                    self._assert_skipped(outcome)
                else:
                    self._assert_reminded(outcome)

    def test_a_running_dispatch_run_skips_with_the_turn_closed(self):
        closed = TurnSpecimen("closed", open=False, run_id="run-elsewhere")
        self._assert_skipped(self._remind("occupied", turn=closed, active_run=True))
        self._assert_reminded(self._remind("occupied-twin", turn=closed))


class StrandedReplyGuardWitness(_RunWorld):
    """B26. The target has no live claimer, so a delivered reply-owed run is failed unless its own turn is open."""

    def setUp(self):
        super().setUp()
        self._register("sc-manager")

    def _register(self, agent_id: str) -> None:
        answer = self.client.post("/api/v1/agents", json={"agentId": agent_id, "role": "coder"})
        self.assertEqual(answer.status_code, 200, answer.text)

    def _sweep(self, run_id: str, turn: TurnSpecimen) -> sqlite3.Row:
        """One target per run, so an earlier case's run never answers for this one."""
        target = f"arch-{run_id}"
        self._register(target)
        self._reply_owed(run_id, sender="sc-manager", target=target, age=3600)
        INPUT_ADAPTER.apply(self._db_path, target, turn)

        async def sweep():
            db = await get_db()
            try:
                out = await _fail_stranded_delivered_reply_runs(db)
                await db.commit()
                return out
            finally:
                await db.close()

        failed = [f for f in asyncio.run(sweep()) if f["agentId"] == target]
        row = self._rows("SELECT status, summary FROM dispatch_runs WHERE id = ?", (run_id,))[0]
        self.assertEqual(failed, [{"runId": run_id, "agentId": target}] if row["status"] == "failed" else [])
        return row

    def _assert_failed(self, row: sqlite3.Row) -> None:
        self.assertEqual((row["status"], row["summary"]), ("failed", NO_WORKER_LEFT_TO_REPLY))

    def test_a_turn_open_on_this_run_is_left_alone_however_old(self):
        for label, touched in (("fresh", 5), ("hours-old", 3 * 3600)):
            with self.subTest(label):
                run_id = f"run-own-{label}"
                row = self._sweep(run_id, TurnSpecimen("own", started_age=touched, touched_age=touched, run_id=run_id))
                self.assertEqual(row["status"], "delivered", "the agent is working on this run")

    def test_its_twins_are_failed_a_turn_on_other_work_or_a_closed_turn_on_this_run(self):
        for label, run_id, turn in (
            ("other-work", "run-other", TurnSpecimen("other", touched_age=5, run_id="run-elsewhere")),
            ("closed-on-this-run", "run-closed", TurnSpecimen("own", touched_age=5, run_id="run-closed").closed()),
        ):
            with self.subTest(label):
                self._assert_failed(self._sweep(run_id, turn))
