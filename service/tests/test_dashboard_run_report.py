"""Getting a managed run's final text in front of the operator, as a real message in chat.

`service/api_core/dashboard_run_report.py` is named by no test file. Two functions answer the same
question for two different askers — a run the DASHBOARD started, and an async run a manager-style
coordinator kicked off — and they are mutually exclusive on exactly one field: who sent it.

WHY IT IS A BACKEND STEP AT ALL. The bridge already captures a managed runtime's final text as the
run summary. Whether the operator ever SEES it used to depend on the agent choosing to call
`comms_send(to="dashboard")`, which older running agents — launched before the prompt that says so —
simply do not do. The difference is between "the coordinator usually reports back" and "the report
exists".

WHICH MAKES DUPLICATION THE COST OF GETTING IT WRONG. An agent that DID report back must not have a
second, machine-written copy of the same thing pasted underneath it, so both functions look for an
explicit dashboard message in the run's own time window before writing anything. Both are also
idempotent, by different means: the manager path checks for its own `dashboard_report` event, and the
mirror checks `result_message_id` — twice, once on the row it was handed and once re-read from the
database, because the row may be a stale snapshot.

AND A DELIVERY RECEIPT IS NOT A REPLY. `_is_delivery_only_claude_run` is what stops "Delivered to
Claude channel session; awaiting explicit reply" being persisted as a fake `Re:` response — a bug an
operator caught live, and the reason the mirror asks a named predicate rather than deciding locally.
"""

from __future__ import annotations

import asyncio
import unittest

import aiosqlite

from service.api_core.dashboard_run_report import (
    _maybe_report_async_manager_result_to_dashboard,
    _mirror_dashboard_run_summary_to_chat,
)
from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

MANAGER = "drr-manager"
CODER = "drr-coder"


class DashboardReportTestCase(FastApiTestCase):
    DB_NAME = "aify-dashboard-run-report-test.db"

    def setUp(self):
        super().setUp()
        for agent_id, role in ((MANAGER, "manager"), (CODER, "coder")):
            response = self.client.post(
                "/api/v1/agents", json={"agentId": agent_id, "role": role})
            self.assertEqual(response.status_code, 200, response.text)

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(sql, params)
                return [dict(row) for row in await cursor.fetchall()]

        return asyncio.run(run())

    def _seed_run(self, run_id: str = "run-1", *, sender: str = "manager-bot",
                  target: str = MANAGER, status: str = "completed", summary: str = "all done",
                  require_reply: int = 0, subject: str = "check the build",
                  message_id: str = "", runtime: str = "hermes",
                  result_message_id: str = "",
                  started_at: str = "2020-01-01T00:00:00Z") -> None:
        # A date FIRMLY IN THE PAST, not the current one. The suppression windows these tests
        # exercise open at `started_at`, so a fixture dated today makes every window depend on the
        # wall clock — which is how the separation test above passed at 08:00Z and failed at 10:00Z.
        self._write(
            "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, dispatch_mode,"
            " runtime, subject, body, status, summary, require_reply, result_message_id,"
            " priority, requested_at, started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, message_id, sender, target, "dispatch", runtime, subject, "b", status,
             summary, require_reply, result_message_id, "normal", started_at, started_at),
        )

    def _seed_message(self, message_id: str, *, sender: str, timestamp: int,
                      to_agent: str = "dashboard", source: str = "direct") -> None:
        self._write(
            "INSERT INTO messages (id, from_agent, to_agent, channel, source, subject, body,"
            " type, priority, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (message_id, sender, to_agent, "", source, "s", "b", "info", "normal", timestamp),
        )

    def _run_row(self, run_id: str = "run-1"):
        rows = self._rows("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(len(rows), 1)
        return rows[0]

    def _call(self, fn, run_id: str = "run-1"):
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
                row = await cursor.fetchone()
                result = await fn(db, row)
                await db.commit()
                return result

        return asyncio.run(run())

    def _report(self, run_id: str = "run-1"):
        return self._call(_maybe_report_async_manager_result_to_dashboard, run_id)

    def _mirror(self, run_id: str = "run-1"):
        return self._call(_mirror_dashboard_run_summary_to_chat, run_id)

    def _dashboard_messages(self) -> list[dict]:
        return self._rows(
            "SELECT * FROM messages WHERE to_agent = 'dashboard' ORDER BY timestamp, id")

    def _events(self, run_id: str = "run-1") -> list[str]:
        return [row["event_type"] for row in self._rows(
            "SELECT event_type FROM dispatch_events WHERE run_id = ? ORDER BY id", (run_id,))]


class ManagerReportTests(DashboardReportTestCase):
    def test_a_completed_managers_summary_becomes_a_dashboard_message(self):
        self._seed_run()
        message_id = self._report()
        self.assertTrue(message_id)
        messages = self._dashboard_messages()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["from_agent"], MANAGER)
        self.assertEqual(messages[0]["body"], "all done")

    def test_the_report_is_an_INFO_message_not_a_response(self):
        """It is a report nobody asked for. Typing it as a response would thread it onto a
        conversation the operator never started."""
        self._seed_run()
        self._report()
        self.assertEqual(self._dashboard_messages()[0]["type"], "info")

    def test_the_subject_is_PREFIXED_so_it_reads_as_an_update(self):
        self._seed_run(subject="check the build")
        self._report()
        self.assertEqual(self._dashboard_messages()[0]["subject"], "Update: check the build")

    def test_an_already_prefixed_subject_is_NOT_prefixed_twice(self):
        """`Update: Update: ...` and `Update: Re: ...` are both what an unguarded prefix produces
        on a threaded run."""
        for subject in ("Update: check the build", "Re: check the build",
                        "update: check the build", "RE: check the build"):
            with self.subTest(subject=subject):
                self._write("DELETE FROM messages", ())
                self._write("DELETE FROM dispatch_runs", ())
                self._write("DELETE FROM dispatch_events", ())
                self._seed_run(subject=subject)
                self._report()
                self.assertEqual(self._dashboard_messages()[0]["subject"], subject)

    def test_a_run_with_NO_SUBJECT_gets_a_usable_one(self):
        self._seed_run(subject="")
        self._report()
        self.assertEqual(self._dashboard_messages()[0]["subject"], "Update from managed run")

    def test_the_report_is_written_ONCE(self):
        """Idempotence via its own event. The reconciler that calls this runs on a sweep, so a
        second pass over the same completed run must not paste the summary again."""
        self._seed_run()
        self.assertTrue(self._report())
        self.assertIsNone(self._report())
        self.assertEqual(len(self._dashboard_messages()), 1)

    def test_the_report_is_RECORDED_on_the_run(self):
        """The event is both the idempotence key and the trace. Writing the message without it
        would make the next sweep write another."""
        self._seed_run()
        self._report()
        self.assertIn("dashboard_report", self._events())


class ManagerReportGateTests(DashboardReportTestCase):
    def test_a_run_that_OWES_A_REPLY_is_left_to_the_contract_machinery(self):
        """Reply debt is tracked elsewhere. Reporting here as well would produce two records of one
        answer, and only one of them closes the contract."""
        self._seed_run(require_reply=1)
        self.assertIsNone(self._report())
        self.assertEqual(self._dashboard_messages(), [])

    def test_a_DASHBOARD_STARTED_run_is_not_this_functions_job(self):
        """The two functions split on exactly this field. Both firing would write the summary
        twice, once as info and once as a response."""
        self._seed_run(sender="dashboard")
        self.assertIsNone(self._report())

    def test_an_UNFINISHED_run_is_not_reported(self):
        for status in ("queued", "claimed", "running", "delivered", "failed", "cancelled"):
            with self.subTest(status=status):
                self._write("DELETE FROM dispatch_runs", ())
                self._seed_run(status=status)
                self.assertIsNone(self._report())

    def test_a_run_with_NO_SUMMARY_is_not_reported(self):
        """There is nothing to say. An empty report is worse than none — it looks like the agent
        answered with silence."""
        for summary in ("", "   "):
            with self.subTest(summary=summary):
                self._write("DELETE FROM dispatch_runs", ())
                self._seed_run(summary=summary)
                self.assertIsNone(self._report())

    def test_only_COORDINATOR_ROLES_report_to_the_dashboard(self):
        """A coder finishing a task reports to whoever asked, not to the operator. Without the role
        gate every managed run in the fleet would land in dashboard chat."""
        self._seed_run(target=CODER)
        self.assertIsNone(self._report())

    def test_every_coordinator_role_is_accepted(self):
        for role in ("manager", "operator", "lead", "coordinator"):
            with self.subTest(role=role):
                self._write("DELETE FROM messages", ())
                self._write("DELETE FROM dispatch_runs", ())
                self._write("DELETE FROM dispatch_events", ())
                self._write("UPDATE agents SET role = ? WHERE id = ?", (role, MANAGER))
                self._seed_run()
                self.assertTrue(self._report(), role)

    def test_the_role_is_matched_case_insensitively(self):
        self._write("UPDATE agents SET role = 'Manager' WHERE id = ?", (MANAGER,))
        self._seed_run()
        self.assertTrue(self._report())

    def test_an_EXPLICIT_report_from_the_agent_suppresses_the_machine_one(self):
        """The agent did call `comms_send(to="dashboard")`. Writing ours underneath would show the
        operator the same result twice, in two voices."""
        self._seed_run(started_at="2026-08-17T09:00:00Z")
        self._seed_message("m-explicit", sender=MANAGER, timestamp=1_800_000_000_000)
        self.assertIsNone(self._report())
        self.assertEqual(len(self._dashboard_messages()), 1)

    def test_the_suppression_is_RECORDED_rather_than_silent(self):
        """A skipped mirror and a mirror that never ran look identical from the outside. The event
        is the only thing that distinguishes them."""
        self._seed_run()
        self._seed_message("m-explicit", sender=MANAGER, timestamp=1_800_000_000_000)
        self._report()
        self.assertIn("dashboard_report_skipped", self._events())

    def test_a_message_from_BEFORE_the_run_does_not_suppress_it(self):
        """The window starts when the run did. An older report answers an older question, and
        treating it as this run's would leave the operator with no result for this one."""
        self._seed_run(started_at="2026-08-17T09:00:00Z")
        self._seed_message("m-old", sender=MANAGER, timestamp=1_000)
        self.assertTrue(self._report())

    def test_another_agents_dashboard_message_does_not_suppress_it(self):
        self._seed_run()
        self._seed_message("m-other", sender=CODER, timestamp=1_800_000_000_000)
        self.assertTrue(self._report())


class MirrorTests(DashboardReportTestCase):
    def test_a_dashboard_started_runs_summary_becomes_a_RESPONSE(self):
        """The operator asked; this is the answer. Typing it as info would leave the ask looking
        unanswered in a chat that shows responses against their question."""
        self._seed_run(sender="dashboard")
        message_id = self._mirror()
        self.assertTrue(message_id)
        message = self._dashboard_messages()[0]
        self.assertEqual(message["type"], "response")
        self.assertEqual(message["body"], "all done")

    def test_the_mirrored_reply_is_LINKED_to_the_run(self):
        """`result_message_id` is what closes the loop — without it the next sweep mirrors again."""
        self._seed_run(sender="dashboard")
        message_id = self._mirror()
        self.assertEqual(self._run_row()["result_message_id"], message_id)

    def test_the_subject_is_the_shared_handoff_subject(self):
        self._seed_run(sender="dashboard", subject="check the build")
        self._mirror()
        self.assertEqual(self._dashboard_messages()[0]["subject"], "Re: check the build")

    def test_an_EXISTING_dashboard_reply_is_LINKED_rather_than_duplicated(self):
        """The agent already answered in chat. The run still needs a result, so the existing
        message becomes it — one answer, recorded once."""
        self._seed_run(sender="dashboard")
        self._seed_message("m-explicit", sender=MANAGER, timestamp=1_800_000_000_000)
        message_id = self._mirror()
        self.assertEqual(message_id, "m-explicit")
        self.assertEqual(len(self._dashboard_messages()), 1)
        self.assertEqual(self._run_row()["result_message_id"], "m-explicit")

    def test_the_EARLIEST_explicit_reply_is_the_one_linked(self):
        """Ascending, with an id tiebreaker. The first thing the agent said after the ask is the
        answer to it; a later message is a follow-up."""
        self._seed_run(sender="dashboard")
        self._seed_message("m-late", sender=MANAGER, timestamp=1_800_000_002_000)
        self._seed_message("m-first", sender=MANAGER, timestamp=1_800_000_001_000)
        self.assertEqual(self._mirror(), "m-first")

    def test_linking_is_recorded_as_a_HANDOFF_event(self):
        self._seed_run(sender="dashboard")
        self._seed_message("m-explicit", sender=MANAGER, timestamp=1_800_000_000_000)
        self._mirror()
        self.assertIn("handoff", self._events())


class MirrorGateTests(DashboardReportTestCase):
    def test_a_run_NOT_started_by_the_dashboard_is_not_mirrored(self):
        self._seed_run(sender="manager-bot")
        self.assertIsNone(self._mirror())

    def test_an_UNFINISHED_run_is_not_mirrored(self):
        for status in ("queued", "claimed", "running", "delivered"):
            with self.subTest(status=status):
                self._write("DELETE FROM dispatch_runs", ())
                self._seed_run(sender="dashboard", status=status)
                self.assertIsNone(self._mirror())

    def test_a_run_that_ALREADY_HAS_A_RESULT_is_not_mirrored(self):
        """TWO guards enforce this — one on the handed-in row, one on a fresh read — and either
        ABSORBS the other for any state these tests can construct. Removing just the snapshot guard
        survives, because the re-read below it stops the same run; removing BOTH is caught here.

        They are not redundant in principle: the snapshot guard covers a row whose result was
        cleared in the database after the caller read it, and the re-read covers a row linked by
        another writer after the caller read it. Only the second of those is reachable in a test
        that controls both, which is why the absorption is recorded rather than assumed away."""
        self._seed_run(sender="dashboard", result_message_id="m-already")
        self.assertIsNone(self._mirror())
        self.assertEqual(self._dashboard_messages(), [])

    def test_the_result_is_re_read_from_the_DATABASE_before_writing(self):
        """The row handed in may be a snapshot taken before another writer linked a reply. Trusting
        it alone would write a second answer and overwrite the first one's link."""
        self._seed_run(sender="dashboard")
        self._write("UPDATE dispatch_runs SET result_message_id = 'm-linked-elsewhere'"
                    " WHERE id = 'run-1'", ())

        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                stale = dict((await (await db.execute(
                    "SELECT * FROM dispatch_runs WHERE id = 'run-1'")).fetchone()))
                stale["result_message_id"] = ""
                result = await _mirror_dashboard_run_summary_to_chat(db, stale)
                await db.commit()
                return result

        self.assertIsNone(asyncio.run(run()))
        self.assertEqual(self._dashboard_messages(), [])

    def test_a_DELIVERY_RECEIPT_is_not_persisted_as_a_reply(self):
        """The live bug this predicate exists for: "Delivered to Claude channel session; awaiting
        explicit reply" is the bridge confirming the hand-off, not the agent answering. Persisted as
        a `Re:` response it reads to the operator as a reply that says nothing."""
        for prefix in ("Delivered to Claude resident session",
                       "Delivered to Claude channel session"):
            with self.subTest(prefix=prefix):
                self._write("DELETE FROM dispatch_runs", ())
                self._seed_run(sender="dashboard", runtime="claude-code",
                               summary=f"{prefix}; awaiting explicit reply")
                self.assertIsNone(self._mirror())
                self.assertEqual(self._dashboard_messages(), [])

    def test_a_REAL_claude_reply_is_still_mirrored(self):
        """The predicate keys on the receipt's text, not on the runtime. A genuine claude summary
        must not be swallowed with the receipts."""
        self._seed_run(sender="dashboard", runtime="claude-code",
                       summary="I rebuilt the index and the suite is green.")
        self.assertTrue(self._mirror())

    def test_a_run_with_no_summary_or_no_target_is_not_mirrored(self):
        self._seed_run("run-nosummary", sender="dashboard", summary="")
        self.assertIsNone(self._mirror("run-nosummary"))


class SeparationTests(DashboardReportTestCase):
    def test_the_two_functions_never_both_fire_for_one_run(self):
        """They split on the sender and nothing else, so every run is exactly one of the two cases.
        If both could fire, a completed run would produce two dashboard messages saying the same
        thing in different voices.

        THE TWO RUNS TARGET DIFFERENT AGENTS, and my first version did not — which made this test
        TIME-DEPENDENT and it began failing hours after it was committed. Both runs targeted the
        manager, so the message the MIRROR wrote for the first run landed inside the second run's
        suppression window and the report was correctly skipped. The window opens at the run's
        `started_at`, which these fixtures seed at 09:00Z on the current date, so the test passed
        only while the wall clock was still before that time. Different targets remove the
        interaction entirely, and are the truer statement of separation: each function fires for its
        own run."""
        self._seed_run("run-dash", sender="dashboard", target=CODER)
        self._seed_run("run-manager", sender="manager-bot", target=MANAGER)

        self.assertIsNone(self._report("run-dash"))
        self.assertTrue(self._mirror("run-dash"))

        self.assertIsNone(self._mirror("run-manager"))
        self.assertTrue(self._report("run-manager"))

        self.assertEqual(len(self._dashboard_messages()), 2)


if __name__ == "__main__":
    unittest.main()
