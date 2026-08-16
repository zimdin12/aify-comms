"""`POST /contracts/hygiene/repair-read-receipts` — a data-repair endpoint nobody had ever called.

The route gate counts it as exercised because a test names its path; the underlying QUERY has a test
too. The HANDLER had never run — it was among the 71 service functions the suite never entered.

WHAT IT REPAIRS. A dispatch carries the message that triggered it. When the run reaches a terminal
state the source message should be marked read for the target agent, because the work it asked for
is done — otherwise the agent keeps being re-woken about a message it has already acted on. This
endpoint backfills the receipts that were missed while that write was absent or failed.

A REPAIR ENDPOINT IS EXACTLY WHERE A WRONG WRITE HIDES. It is run rarely, by an operator, against
live data, and its whole job is to add rows — so the tests here are about the rows it must NOT add:
no receipt for a run that is still queued, none for a message that no longer exists (a foreign key
that would fail or an orphan that would resurrect as "already read"), and none attributed to anyone
but the run's own target.

IT IS ALSO IDEMPOTENT BY CONSTRUCTION (`INSERT OR IGNORE`), which matters because an operator who
does not see an effect runs it again — and the count it reports is what tells them whether anything
was actually wrong.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

TARGET = "lc-target"
SENDER = "lc-sender"

#: The run statuses the repair looks at: work that was actually taken up or finished. A queued run
#: has not been acted on, so its source message is genuinely still unread.
REPAIRABLE = ("claimed", "running", "completed", "failed", "cancelled")
NOT_REPAIRABLE = ("queued", "delivered")


class ContractReceiptRepairTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        for agent_id in (TARGET, SENDER):
            response = self.client.post(
                "/api/v1/agents", json={"agentId": agent_id, "role": "coder"},
            )
            self.assertEqual(response.status_code, 200, response.text)

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _rows(self, sql: str, params: tuple = ()):
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(sql, params)
                return [dict(r) for r in await cursor.fetchall()]

        return asyncio.run(run())

    def _seed_message(self, message_id: str, to_agent: str = TARGET) -> None:
        self._write(
            "INSERT INTO messages (id, from_agent, to_agent, subject, body, type, priority,"
            " timestamp) VALUES (?,?,?,?,?,?,?,?)",
            (message_id, SENDER, to_agent, "s", "b", "request", "normal", 1700000000),
        )

    def _seed_run(self, run_id: str, *, message_id: str, status: str = "completed",
                  target: str = TARGET, body: str = "") -> None:
        self._write(
            "INSERT INTO dispatch_runs (id, from_agent, target_agent, status, message_id, body,"
            " requested_at) VALUES (?,?,?,?,?,?,?)",
            (run_id, SENDER, target, status, message_id, body, "2026-08-16T00:00:00Z"),
        )

    def _repair(self, **params):
        return self.client.post("/api/v1/contracts/hygiene/repair-read-receipts", params=params)

    def _receipts(self):
        return [
            (r["message_id"], r["agent_id"])
            for r in self._rows("SELECT message_id, agent_id FROM read_receipts ORDER BY message_id")
        ]

    # ── what it repairs ──────────────────────────────────────────────────────────────────────

    def test_a_finished_run_gets_its_source_message_marked_read(self):
        self._seed_message("m-1")
        self._seed_run("run-1", message_id="m-1")
        response = self._repair()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["repaired"], 1)
        self.assertEqual(self._receipts(), [("m-1", TARGET)])

    def test_every_status_that_means_the_work_was_taken_up_is_repaired(self):
        for status in REPAIRABLE:
            with self.subTest(status=status):
                self._seed_message(f"m-{status}")
                self._seed_run(f"run-{status}", message_id=f"m-{status}", status=status)
        self._repair()
        self.assertEqual(
            sorted(m for m, _ in self._receipts()),
            sorted(f"m-{status}" for status in REPAIRABLE),
        )

    def test_the_receipt_belongs_to_the_run_TARGET_and_nobody_else(self):
        """A receipt is a claim about who has seen what. Attributing it to the sender would mark the
        sender's own message read for them and leave the target still being re-woken."""
        self._seed_message("m-1")
        self._seed_run("run-1", message_id="m-1")
        self._repair()
        self.assertEqual(self._receipts(), [("m-1", TARGET)])

    # ── what it must NOT repair ──────────────────────────────────────────────────────────────

    def test_a_QUEUED_run_is_left_alone(self):
        """Nothing has acted on it yet, so its source message is genuinely unread. Marking it read
        is how a message gets silently dropped before anyone sees it."""
        for status in NOT_REPAIRABLE:
            with self.subTest(status=status):
                self._seed_message(f"m-{status}")
                self._seed_run(f"run-{status}", message_id=f"m-{status}", status=status)
        self._repair()
        self.assertEqual(self._receipts(), [], "a run that had not been taken up was marked read")

    def test_a_run_whose_message_is_GONE_produces_no_orphan_receipt(self):
        """The message was expired by rotation while the run row survived. A receipt for a message
        that no longer exists is an orphan the cleanup will have to remove, and it can resurrect as
        a false 'already read' if that id is ever reused."""
        self._seed_run("run-1", message_id="m-vanished")
        response = self._repair()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["repaired"], 0)
        self.assertEqual(self._receipts(), [])

    def test_a_MERGED_run_marks_only_the_messages_that_still_exist(self):
        """A merged buffer carries its source ids as `MessageId:` lines, so one run can name several
        messages — and rotation may have expired some of them.

        THIS IS THE FIXTURE THAT DISCRIMINATES. With a single vanished message the function returns
        early (`if not existing_ids`), so removing the per-message existence guard changes nothing;
        a MIXED batch is what reaches that guard. Verified by mutation.
        """
        self._seed_message("m-alive")
        self._seed_run(
            "run-merged", message_id="m-alive",
            body="\n".join(["MessageId: m-alive", "MessageId: m-expired", ""]),
        )
        response = self._repair()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._receipts(), [("m-alive", TARGET)],
                         "a receipt was written for a message that no longer exists")
        self.assertEqual(response.json()["repaired"], 1)

    def test_a_run_with_no_message_id_is_skipped_entirely(self):
        # The SQL `COALESCE(message_id,'') != ''` filter is an optimisation, not the guard:
        # `_dispatch_source_message_ids` finds nothing for such a row and the helper returns 0 either
        # way. Removing the filter is an uncaught mutation, recorded here rather than papered over —
        # what it saves is loading every historical run, which on a busy fleet is the whole table.
        self._seed_run("run-1", message_id="")
        self.assertEqual(self._repair().json()["repaired"], 0)
        self.assertEqual(self._receipts(), [])

    # ── operator ergonomics ──────────────────────────────────────────────────────────────────

    def test_running_it_twice_repairs_nothing_the_second_time(self):
        """An operator who sees no visible effect runs it again. The COUNT is what tells them
        whether anything was wrong, so a second run reporting the same number would read as an
        ongoing fault."""
        self._seed_message("m-1")
        self._seed_run("run-1", message_id="m-1")
        first = self._repair().json()["repaired"]
        second = self._repair().json()["repaired"]
        self.assertEqual(first, 1)
        self.assertEqual(second, 0, "the repair reported work it did not do")
        self.assertEqual(len(self._receipts()), 1, "…or wrote a duplicate receipt")

    def test_an_existing_receipt_keeps_its_ORIGINAL_read_time(self):
        """`INSERT OR IGNORE`: the repair must not restamp a receipt written when the agent really
        read it, or it rewrites history to the moment an operator ran a hygiene job."""
        self._seed_message("m-1")
        self._seed_run("run-1", message_id="m-1")
        self._write(
            "INSERT INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
            ("m-1", TARGET, "2020-01-01T00:00:00Z"),
        )
        self._repair()
        read_at = self._rows("SELECT read_at FROM read_receipts")[0]["read_at"]
        self.assertEqual(read_at, "2020-01-01T00:00:00Z")

    def test_the_limit_bounds_how_many_runs_are_examined(self):
        """It is run against live data on a busy fleet; an unbounded scan is how a hygiene job
        becomes an outage. The bound is asserted by effect, not by reading the query."""
        for index in range(5):
            self._seed_message(f"m-{index}")
            self._seed_run(f"run-{index}", message_id=f"m-{index}")
        self.assertEqual(self._repair(limit=2).json()["repaired"], 2)

    def test_a_limit_outside_the_allowed_range_is_refused(self):
        """The bound is part of the contract, so it is validated rather than clamped silently."""
        for limit in (0, -1, 5000):
            with self.subTest(limit=limit):
                self.assertEqual(self._repair(limit=limit).status_code, 422)

    def test_nothing_to_repair_is_a_success_reporting_zero(self):
        response = self._repair()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True, "repaired": 0})
