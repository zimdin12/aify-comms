"""Which run is live for an agent — and which queued one a new send may join.

`service/api_core/active_run_lookup.py` is named by no test file. Four SELECTs whose answers decide
whether an agent reads as working, whether a new dispatch is blocked, and whether a send creates a
run or joins one that already exists.

THEY LOOK NEAR-IDENTICAL AND ARE NOT, which the module docstring says and this file measures. Same
table, similar WHERE, four different status sets — because they answer four different questions. Two
of the differences are the interesting ones and both are load-bearing:

  * `_current_active_run_row` deliberately EXCLUDES `delivered`. Terminal-delivery runs sit
    delivered-and-unfinished as their normal lingering state long after the agent stopped, so
    counting them pins idle agents to "working" — the worse failure, and the reason the module
    carries a comment telling the next reader not to re-add the heuristic.
  * `_current_channel_awaiting_reply_run_row` is delivered-ONLY, and safe precisely because it also
    requires `execution_mode IN ('channel','resident')`. That is the discriminator: terminal
    deliveries carry `managed`, so they cannot reach it.

The two also ORDER OPPOSITE WAYS — oldest active, newest awaiting-reply — and nothing but a test
would tell a reader that is deliberate rather than a copy-paste slip.

`_find_mergeable_queued_run` is scoped to one SENDER as well as one target. A cross-sender merge
would fold two senders' work into one run and hand the reply contract to whichever of them owns it,
so the other never gets its answer.
"""

from __future__ import annotations

import asyncio
import unittest

import aiosqlite

from service.api_core.active_run_lookup import (
    _current_active_run_row,
    _current_channel_awaiting_reply_run_row,
    _find_mergeable_queued_run,
    _get_blocking_active_run,
)
from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT = "arl-worker"
OTHER = "arl-other"
SENDER = "arl-sender"
SENDER_B = "arl-sender-b"


class ActiveRunLookupTestCase(FastApiTestCase):
    DB_NAME = "aify-active-run-lookup-test.db"

    def setUp(self):
        super().setUp()
        for agent_id in (AGENT, OTHER, SENDER, SENDER_B):
            response = self.client.post(
                "/api/v1/agents", json={"agentId": agent_id, "role": "coder"})
            self.assertEqual(response.status_code, 200, response.text)

    def _query(self, coro_factory):
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                return await coro_factory(db)

        return asyncio.run(run())

    def _seed(self, run_id: str, *, status: str = "claimed", target: str = AGENT,
              sender: str = SENDER, execution_mode: str = "managed", require_reply: int = 0,
              subject: str = "s", requested_at: str = "2026-08-17T00:00:00Z",
              claimed_at=None, started_at=None) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent,"
                    " dispatch_mode, execution_mode, subject, body, status, require_reply,"
                    " requested_at, claimed_at, started_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, f"msg-{run_id}", sender, target, "dispatch", execution_mode,
                     subject, "b", status, require_reply, requested_at, claimed_at, started_at),
                )
                await db.commit()

        asyncio.run(run())

    def _active(self, agent_id: str = AGENT):
        return self._query(lambda db: _current_active_run_row(db, agent_id))

    def _awaiting(self, agent_id: str = AGENT):
        return self._query(lambda db: _current_channel_awaiting_reply_run_row(db, agent_id))

    def _mergeable(self, *, recipient: str = AGENT, sender: str = SENDER):
        return self._query(lambda db: _find_mergeable_queued_run(
            db, recipient_id=recipient, from_agent=sender))


class CurrentActiveRunTests(ActiveRunLookupTestCase):
    def test_nothing_in_flight_is_None(self):
        self.assertIsNone(self._active())

    def test_a_CLAIMED_run_is_active(self):
        self._seed("run-1", status="claimed")
        self.assertEqual(self._active()["id"], "run-1")

    def test_a_RUNNING_run_is_active(self):
        self._seed("run-1", status="running")
        self.assertEqual(self._active()["id"], "run-1")

    def test_a_DELIVERED_run_is_NOT_active(self):
        """The anti-heuristic the module is built around. Terminal-delivery runs sit
        delivered-and-unfinished as their normal resting state — they reconcile lazily — so counting
        them pins idle agents to "working", which is the worse of the two errors."""
        self._seed("run-1", status="delivered")
        self.assertIsNone(self._active())

    def test_neither_QUEUED_nor_finished_runs_are_active(self):
        """Queued is work not started; the rest are over. An active-run report that included any of
        them would block new dispatches against a run nobody is executing."""
        for status in ("queued", "completed", "cancelled", "failed"):
            with self.subTest(status=status):
                self._seed(f"run-{status}", status=status)
                self.assertIsNone(self._active())

    def test_another_agents_run_is_not_this_agents(self):
        self._seed("run-1", status="running", target=OTHER)
        self.assertIsNone(self._active())

    def test_the_OLDEST_in_flight_run_is_the_active_one(self):
        """Ascending. When two runs are somehow both claimed, the one that started first is the one
        the agent is actually executing — reporting the newest would name a run that is waiting
        behind it."""
        self._seed("run-new", status="claimed", started_at="2026-08-17T10:00:00Z")
        self._seed("run-old", status="claimed", started_at="2026-08-17T09:00:00Z")
        self.assertEqual(self._active()["id"], "run-old")

    def test_the_ORDER_falls_back_through_claimed_at_to_requested_at(self):
        """`COALESCE(started_at, claimed_at, requested_at)`. A claimed run has no start time yet, so
        without the fallback its NULL sorts first and it outranks a run that is genuinely running."""
        self._seed("run-started", status="running", started_at="2026-08-17T09:00:00Z",
                   claimed_at="2026-08-17T08:00:00Z", requested_at="2026-08-17T07:00:00Z")
        self._seed("run-claimed-later", status="claimed", claimed_at="2026-08-17T11:00:00Z",
                   requested_at="2026-08-17T01:00:00Z")
        self.assertEqual(self._active()["id"], "run-started")

    def test_the_row_carries_what_a_reader_needs_to_identify_the_run(self):
        """The caller renders this to an operator and uses it to block a second dispatch. A row
        without the subject or the claiming bridge is a block with no explanation."""
        self._seed("run-1", status="running", subject="rebuild the index")
        row = self._active()
        for column in ("id", "status", "subject", "from_agent", "execution_mode",
                       "claim_bridge_id"):
            self.assertIn(column, row.keys())
        self.assertEqual(row["subject"], "rebuild the index")


class ChannelAwaitingReplyTests(ActiveRunLookupTestCase):
    def test_a_DELIVERED_channel_run_awaiting_a_reply_is_found(self):
        """Delivered here means the agent has the work and owes an answer — it IS working, and this
        is the query that lets the dashboard say so."""
        self._seed("run-1", status="delivered", execution_mode="channel", require_reply=1)
        self.assertEqual(self._awaiting()["id"], "run-1")

    def test_a_RESIDENT_delivery_counts_too(self):
        self._seed("run-1", status="delivered", execution_mode="resident", require_reply=1)
        self.assertEqual(self._awaiting()["id"], "run-1")

    def test_a_MANAGED_delivery_is_excluded(self):
        """THE DISCRIMINATOR. Terminal-delivery runs carry `managed` and linger in delivered
        indefinitely; without this clause the delivered-only query would report every one of them as
        an agent awaiting a reply, which is exactly what `_current_active_run_row` refuses to do."""
        self._seed("run-1", status="delivered", execution_mode="managed", require_reply=1)
        self.assertIsNone(self._awaiting())

    def test_a_delivery_that_owes_NO_REPLY_is_excluded(self):
        """No contract, nothing to wait for. The agent received a message, not an assignment."""
        self._seed("run-1", status="delivered", execution_mode="channel", require_reply=0)
        self.assertIsNone(self._awaiting())

    def test_a_CLAIMED_channel_run_is_not_awaiting_a_reply(self):
        """This query is delivered-only on purpose: a claimed run has not reached the agent yet, and
        `_current_active_run_row` already covers it."""
        self._seed("run-1", status="claimed", execution_mode="channel", require_reply=1)
        self.assertIsNone(self._awaiting())

    def test_the_NEWEST_awaiting_run_is_the_one_reported(self):
        """Descending — the OPPOSITE of the active-run query above, and deliberately so. Delivered
        runs accumulate; the reply an agent is composing answers the most recent thing it was told,
        while the active-run query wants the oldest thing still executing."""
        self._seed("run-old", status="delivered", execution_mode="channel", require_reply=1,
                   started_at="2026-08-17T09:00:00Z")
        self._seed("run-new", status="delivered", execution_mode="channel", require_reply=1,
                   started_at="2026-08-17T10:00:00Z")
        self.assertEqual(self._awaiting()["id"], "run-new")

    def test_another_agents_awaiting_run_is_not_this_agents(self):
        self._seed("run-1", status="delivered", execution_mode="channel", require_reply=1,
                   target=OTHER)
        self.assertIsNone(self._awaiting())


class BlockingActiveRunTests(ActiveRunLookupTestCase):
    def test_no_active_run_does_not_block(self):
        self.assertIsNone(self._query(lambda db: _get_blocking_active_run(db, AGENT)))

    def test_an_active_run_blocks_a_new_dispatch(self):
        self._seed("run-1", status="running")
        blocking = self._query(lambda db: _get_blocking_active_run(db, AGENT))
        self.assertIsNotNone(blocking)
        self.assertEqual(blocking["runId"], "run-1")

    def test_a_run_EXCLUDES_ITSELF(self):
        """The caller passes the run it just created. Without the exclusion every new dispatch would
        report itself as the thing blocking it."""
        self._seed("run-1", status="running")
        self.assertIsNone(self._query(
            lambda db: _get_blocking_active_run(db, AGENT, exclude_run_id="run-1")))

    def test_the_exclusion_only_matches_THAT_run(self):
        """Excluding by id, not "exclude whatever is active". A different id must leave the block in
        place, or a caller could accidentally disable the gate entirely."""
        self._seed("run-1", status="running")
        blocking = self._query(
            lambda db: _get_blocking_active_run(db, AGENT, exclude_run_id="some-other-run"))
        self.assertEqual(blocking["runId"], "run-1")

    def test_a_BLANK_exclusion_excludes_nothing(self):
        """The default value of the parameter, and the common case: almost every caller omits it.

        The `exclude_run_id and ...` truthiness guard in front of the comparison cannot be
        distinguished by this test, or by any realistic one — dropping it leaves `"" == runId`,
        which is false for every run that has an id, and `id` is the primary key of
        `dispatch_runs`. A mutation removing the guard survives, and manufacturing a run with an
        empty primary key to kill it would be testing the test rather than the service."""
        self._seed("run-1", status="running")
        blocking = self._query(lambda db: _get_blocking_active_run(db, AGENT, exclude_run_id=""))
        self.assertEqual(blocking["runId"], "run-1")


class MergeableQueuedRunTests(ActiveRunLookupTestCase):
    def test_a_queued_run_from_the_SAME_SENDER_is_mergeable(self):
        self._seed("run-1", status="queued")
        self.assertEqual(self._mergeable()["id"], "run-1")

    def test_a_queued_run_from_ANOTHER_SENDER_is_NOT_mergeable(self):
        """The rule the module states and the reason it exists: merging across senders folds two
        senders' work into one run, and the reply contract belongs to whichever of them owns it — so
        the other never gets its answer, and nothing reports that it did not."""
        self._seed("run-1", status="queued", sender=SENDER_B)
        self.assertIsNone(self._mergeable(sender=SENDER))

    def test_a_queued_run_for_ANOTHER_TARGET_is_not_mergeable(self):
        self._seed("run-1", status="queued", target=OTHER)
        self.assertIsNone(self._mergeable())

    def test_a_run_that_is_no_longer_queued_is_not_mergeable(self):
        """Merging into work that has already been claimed would append to a brief the worker has
        already read."""
        for status in ("claimed", "running", "delivered", "completed", "cancelled"):
            with self.subTest(status=status):
                self._seed(f"run-{status}", status=status)
                self.assertIsNone(self._mergeable())

    def test_the_OLDEST_queued_run_is_the_merge_target(self):
        """Ascending, so a merge joins the work that has been waiting longest rather than starting a
        second queue behind it."""
        self._seed("run-new", status="queued", requested_at="2026-08-17T10:00:00Z")
        self._seed("run-old", status="queued", requested_at="2026-08-17T09:00:00Z")
        self.assertEqual(self._mergeable()["id"], "run-old")

    def test_the_whole_row_comes_back(self):
        """`SELECT *`: the caller appends to the existing run's body and subject, so it needs the
        columns it is about to rewrite, not just an id."""
        self._seed("run-1", status="queued", subject="first brief")
        row = self._mergeable()
        self.assertEqual(row["subject"], "first brief")
        self.assertIn("body", row.keys())
        self.assertIn("require_reply", row.keys())


if __name__ == "__main__":
    unittest.main()
