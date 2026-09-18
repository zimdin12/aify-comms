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

THE PER-ROW DECISIONS LIVE IN `_mark_dispatch_source_messages_read` and are pinned once, against real
sqlite, in `test_repair_read_receipts_marks_the_right_messages.py`: the receipt is the target's, a
second pass writes and reports nothing, a vanished message earns no receipt, a merged buffer marks
only what still exists. This file keeps what the ROUTE decides: which run statuses it selects, the
target it passes, the limit, the original read time, and the response shape.
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

    # ── operator ergonomics ──────────────────────────────────────────────────────────────────

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
