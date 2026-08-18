"""A message must come back when the run that claimed it died without ever being read.

H1, reported by an external review 2026-08-18 and ruled by comms-senior-dev the same day: do the
delete-on-terminal fix now, defer moving the receipt write to turn-start to its own packet.

THE LOSS. `dispatch_claim.py` writes a read receipt for every source message of a run at CLAIM time —
before any turn starts — and nothing removed it. Unread is the ABSENCE of a receipt, so a run that
was claimed and then FAILED without the target starting left its source message invisible to that
agent forever. It is the only proposed mechanism that explains PERMANENCE, which is what the field
reports describe.

THE PROOF THE RULING ASKED FOR, and each of these is a separate test below:
  1. claimed -> receipt written -> run fails before starting -> the message reads UNREAD again, and
     from the same SQL shape the real surfaces use (`LEFT JOIN read_receipts ... WHERE
     r.message_id IS NULL`), not from a bespoke query written to agree with the fix;
  2. a run that DID start keeps its receipt — otherwise real consumed work gets re-delivered, which
     would be a new bug traded for the old one;
  3. plus the two properties the fix's safety rests on: an earned receipt is never touched, and the
     sweep is idempotent, so a message cannot be resurrected into an inbox over and over.
"""

from __future__ import annotations

import asyncio
import unittest

import aiosqlite

from service.reconcilers.claim_receipts import _release_receipts_from_unstarted_runs

CLAIMED_AT = "2026-08-18T01:00:00Z"
EARNED_AT = "2026-08-17T09:00:00Z"

SCHEMA = """
CREATE TABLE messages (
    id TEXT PRIMARY KEY, from_agent TEXT, to_agent TEXT, subject TEXT, body TEXT, timestamp INTEGER
);
CREATE TABLE read_receipts (
    message_id TEXT NOT NULL, agent_id TEXT NOT NULL, read_at TEXT NOT NULL,
    PRIMARY KEY (message_id, agent_id)
);
CREATE TABLE dispatch_runs (
    id TEXT PRIMARY KEY, status TEXT, target_agent TEXT, message_id TEXT, body TEXT DEFAULT '',
    claimed_at TEXT, started_at TEXT, finished_at TEXT
);
"""


async def _db():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    return db


async def _seed(db, *, run_id="run-1", status="failed", claimed_at=CLAIMED_AT, started_at="",
                receipt_at=CLAIMED_AT, body=""):
    await db.execute("INSERT INTO messages (id, from_agent, to_agent, subject, body, timestamp) "
                     "VALUES ('m1','sender','target','Do the thing','please', 1)")
    await db.execute(
        "INSERT INTO dispatch_runs (id, status, target_agent, message_id, body, claimed_at, started_at, finished_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (run_id, status, "target", "m1", body, claimed_at, started_at, "2026-08-18T01:05:00Z"),
    )
    if receipt_at is not None:
        await db.execute("INSERT INTO read_receipts (message_id, agent_id, read_at) VALUES ('m1','target',?)",
                         (receipt_at,))
    await db.commit()


async def _unread_for(db, agent_id: str) -> list[str]:
    """The SAME shape the real surfaces use to decide unread — `listen.py` and the inbox both LEFT
    JOIN receipts and keep the rows with none. Asserting through this rather than by counting
    receipt rows is the difference between proving the message is VISIBLE again and proving only
    that a DELETE ran."""
    cursor = await db.execute(
        """
        SELECT m.id FROM messages m
        LEFT JOIN read_receipts r ON r.message_id = m.id AND r.agent_id = ?
        WHERE m.to_agent = ? AND r.message_id IS NULL
        """,
        (agent_id, agent_id),
    )
    return [str(row["id"]) for row in await cursor.fetchall()]


def run(coro):
    return asyncio.run(coro)


class AClaimedRunThatNeverStarted(unittest.TestCase):
    def test_the_message_is_UNREAD_again_after_the_run_fails(self):
        async def scenario():
            db = await _db()
            try:
                await _seed(db, status="failed")
                self.assertEqual(await _unread_for(db, "target"), [],
                                 "precondition: the claim receipt should be suppressing the message")
                released = await _release_receipts_from_unstarted_runs(db)
                await db.commit()
                self.assertEqual(released, 1)
                self.assertEqual(await _unread_for(db, "target"), ["m1"],
                                 "the message is still invisible: this is the permanent loss")
            finally:
                await db.close()
        run(scenario())

    def test_a_CANCELLED_run_releases_it_too(self):
        async def scenario():
            db = await _db()
            try:
                await _seed(db, status="cancelled")
                self.assertEqual(await _release_receipts_from_unstarted_runs(db), 1)
                await db.commit()
                self.assertEqual(await _unread_for(db, "target"), ["m1"])
            finally:
                await db.close()
        run(scenario())

    def test_a_MERGED_BUFFER_releases_every_source_message(self):
        # The buffer's source ids live only in its body text, so this is the path where the id set is
        # recovered rather than read from a column — and where getting it wrong loses several
        # messages at once instead of one.
        async def scenario():
            db = await _db()
            try:
                from service.api_core.dispatch_text import _MERGED_DISPATCH_FOOTER, _MERGED_DISPATCH_HEADER
                body = (f"{_MERGED_DISPATCH_HEADER}\n=== ITEM 1 ===\nMessageId: m2\n"
                        f"=== ITEM 2 ===\nMessageId: m3\n{_MERGED_DISPATCH_FOOTER}")
                await _seed(db, body=body)
                for extra in ("m2", "m3"):
                    await db.execute("INSERT INTO messages (id, from_agent, to_agent, subject, body, timestamp) "
                                     f"VALUES ('{extra}','sender','target','s','b', 1)")
                    await db.execute("INSERT INTO read_receipts (message_id, agent_id, read_at) "
                                     f"VALUES ('{extra}','target',?)", (CLAIMED_AT,))
                await db.commit()
                released = await _release_receipts_from_unstarted_runs(db)
                await db.commit()
                self.assertEqual(released, 3, "a buffered item was left suppressed")
                self.assertEqual(sorted(await _unread_for(db, "target")), ["m1", "m2", "m3"])
            finally:
                await db.close()
        run(scenario())


class WhatMustNotBeTouched(unittest.TestCase):
    def test_a_run_that_STARTED_keeps_its_receipt(self):
        # The ruling names this explicitly: releasing here would re-deliver work the agent actually
        # consumed, trading the old bug for a new one.
        async def scenario():
            db = await _db()
            try:
                await _seed(db, status="failed", started_at="2026-08-18T01:01:00Z")
                self.assertEqual(await _release_receipts_from_unstarted_runs(db), 0)
                self.assertEqual(await _unread_for(db, "target"), [],
                                 "a message the agent actually received came back as unread")
            finally:
                await db.close()
        run(scenario())

    def test_an_EARNED_receipt_survives_even_on_an_unstarted_failed_run(self):
        # The safety the whole design rests on. The claim uses INSERT OR IGNORE, so a receipt the
        # agent already earned by reading its inbox keeps its OWN read_at — and the delete matches
        # on `read_at = claimed_at`, so it cannot reach one.
        async def scenario():
            db = await _db()
            try:
                await _seed(db, status="failed", receipt_at=EARNED_AT)
                self.assertEqual(await _release_receipts_from_unstarted_runs(db), 0,
                                 "a genuinely-read message was resurrected as unread")
                self.assertEqual(await _unread_for(db, "target"), [])
            finally:
                await db.close()
        run(scenario())

    def test_a_LIVE_run_is_left_alone(self):
        async def scenario():
            db = await _db()
            try:
                await _seed(db, status="delivered")
                self.assertEqual(await _release_receipts_from_unstarted_runs(db), 0,
                                 "a run still in flight had its receipt released")
            finally:
                await db.close()
        run(scenario())

    def test_another_agents_receipt_for_the_same_message_is_untouched(self):
        # `read_receipts` is keyed (message_id, agent_id) and a channel message has one row per
        # recipient. The delete is scoped to the run's target; anything wider would mark somebody
        # else's read mail unread.
        async def scenario():
            db = await _db()
            try:
                await _seed(db, status="failed")
                await db.execute("INSERT INTO read_receipts (message_id, agent_id, read_at) "
                                 "VALUES ('m1','somebody-else',?)", (CLAIMED_AT,))
                await db.commit()
                self.assertEqual(await _release_receipts_from_unstarted_runs(db), 1)
                await db.commit()
                cursor = await db.execute("SELECT agent_id FROM read_receipts WHERE message_id = 'm1'")
                self.assertEqual([r["agent_id"] for r in await cursor.fetchall()], ["somebody-else"])
            finally:
                await db.close()
        run(scenario())


class TheSweepIsIdempotent(unittest.TestCase):
    def test_a_second_pass_releases_nothing_and_cannot_resurrect_a_reread_message(self):
        # Without this property the sweep would fight the agent: it reads the message, earns a
        # receipt, and the next pass deletes it again — the message reappearing in the inbox forever.
        # Matching on the exact claim timestamp is what prevents it, so this is the test that pins
        # the reason the design is safe rather than just the behaviour.
        async def scenario():
            db = await _db()
            try:
                await _seed(db, status="failed")
                self.assertEqual(await _release_receipts_from_unstarted_runs(db), 1)
                await db.commit()
                self.assertEqual(await _release_receipts_from_unstarted_runs(db), 0,
                                 "the second pass released something; the sweep is not idempotent")

                # the agent now genuinely reads it, earning a receipt with its own timestamp
                await db.execute("INSERT INTO read_receipts (message_id, agent_id, read_at) "
                                 "VALUES ('m1','target',?)", ("2026-08-18T02:00:00Z",))
                await db.commit()
                self.assertEqual(await _release_receipts_from_unstarted_runs(db), 0,
                                 "the sweep deleted a receipt the agent earned after the failure")
                self.assertEqual(await _unread_for(db, "target"), [])
            finally:
                await db.close()
        run(scenario())


if __name__ == "__main__":
    unittest.main()
