"""The read-receipt repair endpoint, which was the other route no test called.

`POST /api/v1/contracts/hygiene/repair-read-receipts` walks recent dispatch runs and marks their
SOURCE messages read on behalf of the target agent. It exists because a run can complete while its
source message stays unread — the contract then reads as outstanding forever — and it is the manual
fix an operator reaches for when that has happened.

It was one of two real routes in the service that no test exercised (measured against
`create_app()`: 127 method+path routes, 7 unmentioned, 5 of them favicons and the oauth redirect).
Both were data-repair endpoints, which is the pattern: rarely run, mutating, and nobody looks.

WHAT IS ACTUALLY TESTED HERE is `_mark_dispatch_source_messages_read`, the helper the route calls
once per row — the route itself is a `SELECT … LIMIT ?` loop around it plus a commit and a
broadcast. The helper is where every decision lives, and it runs against real sqlite because a mock
would agree with whatever I believed about `INSERT OR IGNORE`.

THE THREE PROPERTIES THAT MATTER, one test each:
  * it marks a run's source message read FOR THE TARGET AGENT, not for the sender;
  * it is IDEMPOTENT — the endpoint is a repair tool, so running it twice is the normal case, and
    `INSERT OR IGNORE` is what stops a second run doubling the receipts;
  * it never invents a receipt for a message that no longer exists, which is what the existence
    check in the middle of the helper is for. A dangling receipt would make a deleted message look
    read rather than gone.
"""

from __future__ import annotations

import asyncio
import unittest

import aiosqlite

from service.api_core.claim_gating import _mark_dispatch_source_messages_read

SCHEMA = """
CREATE TABLE messages (id TEXT PRIMARY KEY, from_agent TEXT, to_agent TEXT, subject TEXT);
CREATE TABLE read_receipts (
    message_id TEXT, agent_id TEXT, read_at TEXT, PRIMARY KEY (message_id, agent_id)
);
"""


class _Row(dict):
    """A dispatch_runs row as the route hands it over: subscriptable, with `.keys()`."""


def _run(coro):
    return asyncio.run(coro)


async def _receipts_after(row, *, messages, times=1):
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await db.executescript(SCHEMA)
        for message_id in messages:
            await db.execute(
                "INSERT INTO messages (id, from_agent, to_agent, subject) VALUES (?,?,?,?)",
                (message_id, "sender", "target", "s"),
            )
        marked = 0
        for _ in range(times):
            marked = await _mark_dispatch_source_messages_read(db, row, "target", "2026-08-16T00:00:00Z")
        cursor = await db.execute("SELECT message_id, agent_id FROM read_receipts ORDER BY message_id")
        receipts = [(r["message_id"], r["agent_id"]) for r in await cursor.fetchall()]
        return marked, receipts


class RepairReadReceiptsTests(unittest.TestCase):
    def test_it_marks_the_source_message_read_for_the_TARGET_agent(self):
        row = _Row(message_id="m1", body="")
        marked, receipts = _run(_receipts_after(row, messages=["m1"]))
        self.assertEqual(marked, 1)
        self.assertEqual(
            receipts, [("m1", "target")],
            "the receipt belongs to the recipient — marking it for the sender would leave the "
            "recipient's inbox still showing it unread, which is the state this repairs",
        )

    def test_running_it_twice_does_not_double_the_receipts(self):
        """The endpoint is a repair tool, so a second run is the NORMAL case, not an edge one."""
        row = _Row(message_id="m1", body="")
        marked, receipts = _run(_receipts_after(row, messages=["m1"], times=2))
        self.assertEqual(marked, 1)
        self.assertEqual(receipts, [("m1", "target")], "INSERT OR IGNORE is what makes this safe")

    def test_it_never_invents_a_receipt_for_a_message_that_is_gone(self):
        """A dangling receipt would make a DELETED message read as read rather than absent — and
        `_delete_messages_by_ids` nulls a run's `message_id` on deletion, so a stale run row
        pointing at a vanished message is a real state, not a hypothetical."""
        row = _Row(message_id="deleted-message", body="")
        marked, receipts = _run(_receipts_after(row, messages=[]))
        self.assertEqual(marked, 0)
        self.assertEqual(receipts, [])

    def test_a_run_with_no_source_message_marks_nothing(self):
        for row in (_Row(message_id=None, body=""), _Row(message_id="", body="")):
            with self.subTest(row=dict(row)):
                marked, receipts = _run(_receipts_after(row, messages=["m1"]))
                self.assertEqual(marked, 0)
                self.assertEqual(receipts, [])

    def test_a_MERGED_run_recovers_the_ids_from_its_buffer_body(self):
        """I got this wrong first and the code corrected me, which is the useful half.

        I assumed a fan-out run carried a `source_message_ids` map and wrote a test around it.
        `_dispatch_source_message_ids` reads no such column: the ids of a MERGED buffer are recovered
        from structural `MessageId: <id>` lines in the body, which is what
        `_render_pending_dispatch_item` writes. A test built on the field I imagined would have
        passed against a helper that ignored it.
        """
        row = _Row(
            message_id="m1",
            body="=== ITEM 1 ===\nMessageId: m2\nsome text\n=== ITEM 2 ===\nMessageId: m3\n",
        )
        marked, receipts = _run(_receipts_after(row, messages=["m1", "m2"]))
        self.assertEqual(marked, 2, "m3 no longer exists and must not produce a receipt")
        self.assertEqual(sorted(receipts), [("m1", "target"), ("m2", "target")])

    def test_an_id_MENTIONED_IN_PROSE_earns_no_receipt(self):
        """The security property behind the anchored pattern, asserted from this end too.

        A body is free text written by the SENDING agent, and unread is computed as the ABSENCE of a
        receipt — so a receipt an agent never earned SUPPRESSES that message from `comms_listen`.
        Agents quote message ids in sentences routinely, so this needs no ill intent.
        """
        row = _Row(message_id="", body="As I said in MessageId: m1 earlier, the fix landed.\n")
        marked, receipts = _run(_receipts_after(row, messages=["m1"]))
        self.assertEqual(marked, 0, "a mid-sentence mention is prose, not a structural line")
        self.assertEqual(receipts, [])

        # …and the structural spelling of the same id still works, so the guard is not just refusing
        # everything.
        structural = _Row(message_id="", body="MessageId: m1\n")
        marked_ok, receipts_ok = _run(_receipts_after(structural, messages=["m1"]))
        self.assertEqual(marked_ok, 1)
        self.assertEqual(receipts_ok, [("m1", "target")])
