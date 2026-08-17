"""Advancing a dispatch run: append a control, mark one answered, cancel the queued ones.

`service/api_core/dispatch_run_state.py` is named by no test file. Three writes that decide when a
run stops being open, and the two directions are both silent.

A RUN THAT SHOULD CLOSE AND DOES NOT keeps its reply contract open: the reminder sweep keeps chasing
an answer that already arrived, and the agent shows queued work it has finished. A RUN THAT CLOSES
WHEN IT SHOULD NOT loses the thing that was tracking real in-flight work — the reply is recorded, the
run reads completed, and whatever the worker is still doing is now unaccounted for.

`_mark_dispatch_run_answered` is where that decision lives, and its gate is three clauses wide
because "a reply arrived" means different things per delivery path. A queued or delivered run is
closed by any reply. A CLAIMED or RUNNING run is closed only when the path has no separate completion
signal of its own — channel and resident deliveries, and terminal dispatches — because for the others
the worker will report its own end and a reply mid-run is just a message.

The tests below drive the three functions directly against a real database and assert the ROWS, not
the return values: two of the three return nothing, and every effect that matters here is a write.
"""

from __future__ import annotations

import asyncio
import unittest

import aiosqlite

from service.api_core.dispatch_run_state import (
    _append_dispatch_control,
    _cancel_queued_dispatch_runs_for_message_ids,
    _mark_dispatch_run_answered,
)
from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT = "drs-worker"
SENDER = "drs-sender"


class DispatchRunStateTestCase(FastApiTestCase):
    DB_NAME = "aify-dispatch-run-state-test.db"

    def setUp(self):
        super().setUp()
        for agent_id in (AGENT, SENDER):
            response = self.client.post(
                "/api/v1/agents", json={"agentId": agent_id, "role": "coder"})
            self.assertEqual(response.status_code, 200, response.text)

    # ── running the units under test against a real connection ───────────────────────────────

    def _run(self, coro_factory):
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                result = await coro_factory(db)
                await db.commit()
                return result

        return asyncio.run(run())

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

    def _seed_run(self, run_id: str, *, status: str = "queued", message_id: str = "msg-1",
                  dispatch_mode: str = "", finished_at=None,
                  target: str = AGENT) -> None:
        # NULL, not "". `finished_at` has no column default, and a real run is created without it —
        # my first version seeded an empty string, which `COALESCE(finished_at, ?)` treats as an
        # existing value, and the stamp test failed against correct code. The fixture was wrong.
        self._write(
            "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, dispatch_mode,"
            " subject, body, status, requested_at, finished_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, message_id, SENDER, target, dispatch_mode, "s", "b", status,
             "2026-08-17T00:00:00Z", finished_at),
        )

    def _run_row(self, run_id: str) -> dict:
        rows = self._rows("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(len(rows), 1, rows)
        return rows[0]


class AppendControlTests(DispatchRunStateTestCase):
    def test_a_control_is_recorded_as_PENDING_for_its_run(self):
        """Pending is what a bridge claims. Writing it in any other state would create a control no
        claim query ever sees — an interrupt the operator asked for that never leaves the table."""
        self._seed_run("run-1")
        control_id = self._run(lambda db: _append_dispatch_control(
            db, "run-1", from_agent=SENDER, action="interrupt"))
        rows = self._rows("SELECT * FROM dispatch_controls WHERE id = ?", (control_id,))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["run_id"], "run-1")
        self.assertEqual(rows[0]["action"], "interrupt")
        self.assertEqual(rows[0]["status"], "pending")
        self.assertEqual(rows[0]["from_agent"], SENDER)

    def test_the_returned_id_is_the_one_that_was_written(self):
        """Callers hand this id back to the requester so the control can be tracked. A returned id
        that is not the stored one produces a control nobody can find."""
        self._seed_run("run-1")
        control_id = self._run(lambda db: _append_dispatch_control(
            db, "run-1", from_agent=SENDER, action="steer", body="do the other thing"))
        stored = self._rows("SELECT id, body FROM dispatch_controls WHERE run_id = 'run-1'")
        self.assertEqual([row["id"] for row in stored], [control_id])
        self.assertEqual(stored[0]["body"], "do the other thing")

    def test_two_controls_in_the_SAME_MILLISECOND_get_distinct_ids(self):
        """The id is a millisecond timestamp plus a random tail, and the tail is the whole of the
        collision protection. THE CLOCK IS FROZEN here: two ordinary calls land in different
        milliseconds and pass against an id built from the timestamp alone, so a mutation that drops
        the tail survives a test that merely calls twice. An operator double-clicking Interrupt is
        the real version of this, and the second insert would fail on the primary key."""
        import time as time_module
        from unittest import mock

        from service.api_core import dispatch_run_state

        self._seed_run("run-1")
        with mock.patch.object(dispatch_run_state, "time",
                               mock.Mock(time=lambda: 1_700_000_000.0)):
            first = self._run(lambda db: _append_dispatch_control(
                db, "run-1", from_agent=SENDER, action="interrupt"))
            second = self._run(lambda db: _append_dispatch_control(
                db, "run-1", from_agent=SENDER, action="interrupt"))
        self.assertNotEqual(first, second)
        self.assertEqual(len(self._rows("SELECT id FROM dispatch_controls WHERE run_id='run-1'")), 2)
        self.assertIs(dispatch_run_state.time, time_module, "the clock patch leaked")

    def test_the_control_is_ALSO_recorded_as_a_run_EVENT(self):
        """The run's event list is what an operator reads to understand what happened to it. A
        control that only exists in its own table is invisible in that story."""
        self._seed_run("run-1")
        self._run(lambda db: _append_dispatch_control(
            db, "run-1", from_agent=SENDER, action="interrupt"))
        events = self._rows("SELECT * FROM dispatch_events WHERE run_id = 'run-1'")
        self.assertEqual([event["event_type"] for event in events], ["control:interrupt"])
        self.assertIn(SENDER, events[0]["body"])

    def test_a_control_with_NO_REQUESTER_still_records_who_is_unknown(self):
        """Nothing enforces a requester at this layer. "requested by unknown" is a worse answer than
        a name and a much better one than a blank the reader has to interpret."""
        self._seed_run("run-1")
        self._run(lambda db: _append_dispatch_control(db, "run-1", from_agent="", action="stop"))
        events = self._rows("SELECT body FROM dispatch_events WHERE run_id = 'run-1'")
        self.assertIn("unknown", events[0]["body"])


class MarkAnsweredCompletionTests(DispatchRunStateTestCase):
    """When a reply CLOSES the run."""

    def _answer(self, run_id: str, *, status: str = "", mode: str = "") -> None:
        self._run(lambda db: _mark_dispatch_run_answered(
            db, run_id, "reply-1", status, mode))

    def test_a_QUEUED_run_is_completed_by_a_reply(self):
        """Nothing has started, so a reply is the whole of the work."""
        self._seed_run("run-1", status="queued")
        self._answer("run-1", status="queued")
        row = self._run_row("run-1")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["result_message_id"], "reply-1")

    def test_a_DELIVERED_run_is_completed_by_a_reply(self):
        self._seed_run("run-1", status="delivered")
        self._answer("run-1", status="delivered")
        self.assertEqual(self._run_row("run-1")["status"], "completed")

    def test_completion_stamps_FINISHED_AT(self):
        """The run's duration is read from it, and an open finished_at is how a completed run keeps
        showing as in-flight on every board that sorts by it."""
        self._seed_run("run-1", status="queued")
        self._answer("run-1", status="queued")
        self.assertTrue(self._run_row("run-1")["finished_at"])

    def test_an_EXISTING_finished_at_is_not_overwritten(self):
        """`COALESCE`. A run that already recorded when it ended keeps that moment — re-answering
        must not move a historical timestamp forward to now."""
        self._seed_run("run-1", status="delivered", finished_at="2020-01-01T00:00:00Z")
        self._answer("run-1", status="delivered")
        self.assertEqual(self._run_row("run-1")["finished_at"], "2020-01-01T00:00:00Z")

    def test_an_EMPTY_STRING_finished_at_is_treated_as_a_value_and_left_blank(self):
        """`COALESCE` skips NULL, not emptiness — so a run carrying `''` completes with no stamp.

        Measured and recorded rather than fixed. Nothing writes `''` to this column today (it has no
        default and every insert omits it), so this is unreachable in practice — but it is NOT a
        theoretical shape: `reconcilable_runs_query.py` reads `COALESCE(finished_at, '') = ''` as
        "unfinished", so the empty string is a state that half this schema's readers expect. If a
        writer ever produces one, a completed run keeps a blank finish time and its duration is
        unreadable."""
        self._seed_run("run-1", status="delivered", finished_at="")
        self._answer("run-1", status="delivered")
        row = self._run_row("run-1")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["finished_at"], "")

    def test_a_CHANNEL_delivery_completes_even_while_CLAIMED(self):
        """Channel and resident deliveries have no separate completion signal — the reply IS the
        end of the turn. Leaving these open is the "agent still shows working after it answered"
        shape this gate was widened for."""
        for status in ("claimed", "running"):
            with self.subTest(status=status):
                self._seed_run(f"run-{status}", status=status)
                self._answer(f"run-{status}", status=status, mode="channel")
                self.assertEqual(self._run_row(f"run-{status}")["status"], "completed")

    def test_a_RESIDENT_delivery_completes_even_while_claimed(self):
        self._seed_run("run-1", status="claimed")
        self._answer("run-1", status="claimed", mode="resident")
        self.assertEqual(self._run_row("run-1")["status"], "completed")

    def test_a_TERMINAL_dispatch_completes_even_while_claimed(self):
        """Keyed on the RUN's own dispatch_mode rather than the passed execution mode — a terminal
        dispatch is recognised from the row, not from what the caller happened to know."""
        self._seed_run("run-1", status="running", dispatch_mode="terminal")
        self._answer("run-1", status="running")
        self.assertEqual(self._run_row("run-1")["status"], "completed")

    def test_the_STATUS_is_matched_case_insensitively(self):
        """Both arrive as free text from callers that read them out of different rows."""
        self._seed_run("run-1", status="delivered")
        self._answer("run-1", status="  DELIVERED  ")
        self.assertEqual(self._run_row("run-1")["status"], "completed")

    def test_the_MODE_is_matched_case_insensitively(self):
        """A case where ONLY the mode can decide: a claimed run is not completed by the first
        clause, so it closes here or not at all. My first version passed a messy status AND a messy
        mode together, which meant the status clause carried the test and a mutation dropping the
        mode's normalisation survived it."""
        self._seed_run("run-1", status="claimed")
        self._answer("run-1", status="claimed", mode="  CHANNEL  ")
        self.assertEqual(self._run_row("run-1")["status"], "completed")


class MarkAnsweredNonCompletionTests(DispatchRunStateTestCase):
    """When a reply is only a message, and the run stays open."""

    def _answer(self, run_id: str, *, status: str = "", mode: str = "") -> None:
        self._run(lambda db: _mark_dispatch_run_answered(
            db, run_id, "reply-1", status, mode))

    def test_a_CLAIMED_managed_run_is_NOT_completed(self):
        """A managed worker reports its own end. Closing the run on a mid-turn message would stop
        tracking work that is still running, and nothing later reopens it."""
        self._seed_run("run-1", status="claimed")
        self._answer("run-1", status="claimed", mode="managed")
        row = self._run_row("run-1")
        self.assertEqual(row["status"], "claimed")
        self.assertEqual(row["result_message_id"], "reply-1",
                         "the reply must still be recorded on the run")

    def test_a_RUNNING_managed_run_is_NOT_completed(self):
        self._seed_run("run-1", status="running")
        self._answer("run-1", status="running", mode="managed")
        self.assertEqual(self._run_row("run-1")["status"], "running")

    def test_an_ALREADY_COMPLETED_run_is_not_re_finished(self):
        """Idempotence from the other side: the gate does not list `completed`, so a late reply
        records itself without rewriting the terminal state or its timestamp."""
        self._seed_run("run-1", status="completed", finished_at="2020-01-01T00:00:00Z")
        self._answer("run-1", status="completed")
        row = self._run_row("run-1")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["finished_at"], "2020-01-01T00:00:00Z")

    def test_a_CANCELLED_run_is_not_resurrected_as_completed(self):
        self._seed_run("run-1", status="cancelled")
        self._answer("run-1", status="cancelled")
        self.assertEqual(self._run_row("run-1")["status"], "cancelled")

    def test_an_UNKNOWN_run_id_does_not_raise(self):
        """Reply linking runs against ids resolved a moment earlier; a run deleted in between must
        not turn a delivered reply into an error."""
        self._answer("run-does-not-exist", status="queued")

    def test_a_BLANK_status_does_not_complete_anything(self):
        """The caller passes the run's current status; when it has none to pass, the safe reading is
        "not a state I recognise" rather than "close it"."""
        self._seed_run("run-1", status="claimed")
        self._answer("run-1")
        self.assertEqual(self._run_row("run-1")["status"], "claimed")


class CancelQueuedRunsTests(DispatchRunStateTestCase):
    """Unsending a message cancels the work it asked for — and only that work."""

    def _cancel(self, message_ids: list[str], **kwargs) -> list[str]:
        return self._run(lambda db: _cancel_queued_dispatch_runs_for_message_ids(
            db, message_ids, **kwargs))

    def test_a_QUEUED_run_for_the_unsent_message_is_cancelled(self):
        self._seed_run("run-1", status="queued", message_id="msg-1")
        self.assertEqual(self._cancel(["msg-1"]), ["run-1"])
        row = self._run_row("run-1")
        self.assertEqual(row["status"], "cancelled")
        self.assertIn("unsent", row["summary"])
        self.assertTrue(row["finished_at"])

    def test_a_run_that_has_ALREADY_STARTED_is_left_alone(self):
        """The message can be withdrawn; the work cannot. A worker is mid-turn on it, and marking
        the run cancelled underneath would leave a live worker attached to a terminal row."""
        for status in ("claimed", "running", "delivered", "completed"):
            with self.subTest(status=status):
                self._seed_run(f"run-{status}", status=status, message_id="msg-x")
                self.assertEqual(self._cancel(["msg-x"]), [])
                self.assertEqual(self._run_row(f"run-{status}")["status"], status)

    def test_only_the_named_messages_runs_are_touched(self):
        self._seed_run("run-1", status="queued", message_id="msg-1")
        self._seed_run("run-2", status="queued", message_id="msg-2")
        self.assertEqual(self._cancel(["msg-1"]), ["run-1"])
        self.assertEqual(self._run_row("run-2")["status"], "queued")

    def test_every_cancellation_is_recorded_as_a_run_EVENT(self):
        self._seed_run("run-1", status="queued", message_id="msg-1")
        self._cancel(["msg-1"])
        events = self._rows("SELECT * FROM dispatch_events WHERE run_id = 'run-1'")
        self.assertEqual([event["event_type"] for event in events], ["cancelled"])
        self.assertIn("unsent", events[0]["body"])

    def test_nothing_to_cancel_returns_an_empty_list(self):
        self.assertEqual(self._cancel(["msg-nothing"]), [])

    def test_blank_and_duplicate_message_ids_are_dropped_before_the_query(self):
        """Unsend hands over whatever ids it collected. Duplicates would bind the same id twice and
        blanks would widen the IN clause with a value that matches rows written with an empty
        message_id."""
        self._seed_run("run-blank", status="queued", message_id="")
        self._seed_run("run-1", status="queued", message_id="msg-1")
        self.assertEqual(self._cancel(["msg-1", "msg-1", "", "   ", None]), ["run-1"])
        self.assertEqual(self._run_row("run-blank")["status"], "queued")

    def test_an_EMPTY_input_does_no_work_at_all(self):
        self._seed_run("run-1", status="queued", message_id="msg-1")
        self.assertEqual(self._cancel([]), [])
        self.assertEqual(self._run_row("run-1")["status"], "queued")

    def test_more_ids_than_one_CHUNK_are_all_processed(self):
        """SQLite has a hard ceiling on bound variables per statement, so the ids are chunked. A
        chunk loop that returned after the first pass would silently leave later runs queued — the
        failure only appears at scale, which is where an unsend of a fan-out lands."""
        for index in range(7):
            self._seed_run(f"run-{index}", status="queued", message_id=f"msg-{index}")
        cancelled = self._cancel([f"msg-{index}" for index in range(7)], chunk_size=2)
        self.assertEqual(sorted(cancelled), [f"run-{index}" for index in range(7)])
        statuses = {row["status"] for row in self._rows("SELECT status FROM dispatch_runs")}
        self.assertEqual(statuses, {"cancelled"})

    def test_the_returned_ids_are_the_ones_actually_cancelled(self):
        """Unsend reports the count to the operator. A list that includes runs it did not touch is
        the reported-work-never-done defect the contract-repair endpoint had."""
        self._seed_run("run-queued", status="queued", message_id="msg-1")
        self._seed_run("run-running", status="running", message_id="msg-1")
        self.assertEqual(self._cancel(["msg-1"]), ["run-queued"])


if __name__ == "__main__":
    unittest.main()
