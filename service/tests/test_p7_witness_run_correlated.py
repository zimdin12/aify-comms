"""P7 witnesses: the two readers that tied a turn to a run, the reply reminder (ledger B21) and the stranded-reply
guard (B26), as operator decision R3 rewrote them for the 0.9.8 switch (DECISIONS 2026-10-21).

aify-env's turns carry no run id, so neither reader asks which run a turn is on any more:

  * B21 pauses a reminder while aify-env says the target is busy with anything, while its state is unknown, or while
    a run comms delivered is still claimed or running; it is sent once the agent is known idle.
  * B26 never fails a delivered reply-owed run while the agent is busy or unknown; it fails one only once the agent
    is known idle.

Today's distinctions retire with the run attribution they read: "a turn on THIS run does not stop its own reminder"
and "a turn on other work does not protect this run" (the cost R3 accepted: a reminder about one message waits
while the agent works on another).

The reminder is driven through its route, `POST /contracts/reminders/run`; the guard through its reconciler
entry, `_fail_stranded_delivered_reply_runs`, which the dispatch sweep calls. Every pause has an idle twin in the
same world, and the outcome is read back from the run's events, its messages and its status.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time

from service.api_core.authored_failures import NO_WORKER_LEFT_TO_REPLY
from service.db import get_db
from service.reconcilers.dispatch_lifecycle import _fail_stranded_delivered_reply_runs
from service.tests import env_records
from service.tests._base import FastApiTestCase

MACHINE = "linux:test-host"


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


class _ReminderWorld(_RunWorld):
    """B21's world. The target is a live resident codex, so a reminder that is due is really sent."""

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

    def _remind(self, case: str, *, record: str, active_run: bool = False) -> dict:
        """`record`: 'busy' or 'idle' (aify-env's fresh record says so) or 'none' (nothing published: unknown)."""
        target, run_id = f"coder-{case}", f"run-{case}"
        self._resident(target)
        message_id = self._reply_owed(run_id, sender="lead", target=target, age=600)
        if record != "none":
            env_records.report(self, target, busy=record == "busy")
        if active_run:
            self._exec("INSERT INTO dispatch_runs (id, from_agent, target_agent, dispatch_mode, execution_mode, "
                       "message_type, subject, body, priority, status, require_reply, requested_at, started_at) "
                       "VALUES (?,?,?,'start_if_possible','managed','info','other','work','normal','running',0,"
                       "datetime('now'),datetime('now'))", (f"active-{case}", "dashboard", target))
        answer = self.client.post(f"/api/v1/contracts/reminders/run?runId={run_id}")
        self.assertEqual(answer.status_code, 200, answer.text)
        reminders = self._rows("SELECT id, from_agent FROM messages WHERE to_agent = ? AND in_reply_to = ?",
                               (target, message_id))
        seeded = {run_id, f"active-{case}"}
        return {
            "events": [(r["event_type"], r["body"]) for r in self._rows(
                "SELECT event_type, body FROM dispatch_events WHERE run_id = ? AND event_type LIKE 'reply_reminder%'",
                (run_id,))],
            "reminders": [r["from_agent"] for r in reminders],
            # Every run the reminder pass created for this target, by the message it carries.
            "new_runs": sorted((r["message_id"], r["from_agent"]) for r in self._rows(
                "SELECT id, message_id, from_agent FROM dispatch_runs WHERE target_agent = ?", (target,))
                if r["id"] not in seeded),
            "reminder_ids": [r["id"] for r in reminders],
        }

    def _assert_skipped(self, outcome: dict, why: str) -> None:
        self.assertEqual(outcome["reminders"], [], "a paused reminder sends no message")
        self.assertEqual(outcome["new_runs"], [], "and creates no run")
        self.assertEqual([kind for kind, _ in outcome["events"]], ["reply_reminder_skipped"])
        self.assertIn(why, outcome["events"][0][1])

    def _assert_reminded(self, outcome: dict) -> None:
        self.assertEqual(outcome["reminders"], ["lead"], "exactly one reminder, from the owed party")
        self.assertEqual(outcome["new_runs"], [(outcome["reminder_ids"][0], "lead")],
                         "exactly one new run, carrying that reminder to this target")
        self.assertEqual([kind for kind, _ in outcome["events"]], ["reply_reminder"])


class ReminderSkipWitness(_ReminderWorld):
    """B21."""

    def test_the_reminder_waits_while_the_agent_is_busy_unknown_or_occupied_and_is_sent_once_it_is_idle(self):
        for case, record, active_run, why in (
            ("busy", "busy", False, "target is busy"),
            ("unknown", "none", False, "state is unknown"),
            ("occupied", "idle", True, "target is busy"),
            ("idle", "idle", False, None),
        ):
            with self.subTest(case):
                outcome = self._remind(case, record=record, active_run=active_run)
                if why:
                    self._assert_skipped(outcome, why)
                else:
                    self._assert_reminded(outcome)


class StrandedReplyGuardWitness(_RunWorld):
    """B26. The target has no live claimer, so a delivered reply-owed run is failed once the agent is known idle."""

    def setUp(self):
        super().setUp()
        self._register("sc-manager")

    def _register(self, agent_id: str) -> None:
        answer = self.client.post("/api/v1/agents", json={"agentId": agent_id, "role": "coder"})
        self.assertEqual(answer.status_code, 200, answer.text)

    def _sweep(self, run_id: str, record: str) -> sqlite3.Row:
        """One target per run, so an earlier case's run never answers for this one. `record` as in `_remind`."""
        target = f"arch-{run_id}"
        self._register(target)
        self._reply_owed(run_id, sender="sc-manager", target=target, age=3600)
        if record != "none":
            env_records.report(self, target, busy=record == "busy")

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

    def test_a_busy_or_unknown_agent_protects_the_run_and_its_idle_twin_is_failed(self):
        for record in ("busy", "none"):
            with self.subTest(record):
                self.assertEqual(self._sweep(f"run-{record}", record)["status"], "delivered",
                                 "a busy or unknown agent may still be working on it")
        row = self._sweep("run-idle", "idle")
        self.assertEqual((row["status"], row["summary"]), ("failed", NO_WORKER_LEFT_TO_REPLY))


class TerminalWithoutBackingWitness(_ReminderWorld):
    """SW5 review R4. A terminal run whose live terminal backing is gone used to be reminded whatever the agent was
    doing. Its legacy no-backing judgment now only withdraws the run's own occupancy: busy and unknown still pause the
    reminder, and a known idle agent is still reminded about it. The judgment is the legacy cache's, read here as a
    controlled collaborator, exactly as the reviewer's parent run read it."""

    def _terminal_remind(self, case: str, record: str) -> dict:
        from unittest import mock

        from service.api_core import dispatch_sweeps

        target, run_id = f"term-{case}", f"run-term-{case}"
        self._resident(target)
        self._reply_owed(run_id, sender="lead", target=target, age=600)
        self._exec("UPDATE dispatch_runs SET dispatch_mode = 'terminal', status = 'running' WHERE id = ?", (run_id,))
        if record != "none":
            env_records.report(self, target, busy=record == "busy")

        async def no_backing(db, row, settings=None):
            return {"status": "blocked", "reason": "Active run has no live terminal backing"}

        with mock.patch.object(dispatch_sweeps, "_compute_live_status_cache", no_backing):
            answer = self.client.post(f"/api/v1/contracts/reminders/run?runId={run_id}&dryRun=true")
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()

    def test_the_no_backing_exception_never_overrides_busy_or_unknown(self):
        for case, record, reminded in (("busy", "busy", False), ("unknown", "none", False), ("idle", "idle", True)):
            with self.subTest(case):
                result = self._terminal_remind(case, record)
                self.assertEqual(([r["runId"] for r in result["reminded"]], [s["runId"] for s in result["skipped"]]),
                                 ([f"run-term-{case}"], []) if reminded else ([], [f"run-term-{case}"]), result)
