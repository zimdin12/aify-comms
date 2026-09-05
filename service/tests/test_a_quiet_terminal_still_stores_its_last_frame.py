"""A terminal that goes QUIET must still store its last frame.

THE REGRESSION, shipped in the lazy tail and found by an independent review two days later. The tail
is written by the NEXT chunk once the interval has passed. When output STOPS there is no next chunk,
so the last frame was held for ever. The design note justified that with "nothing reads the stored
tail on the status path any more" -- and that sentence was false. Two readers take the stored column
with no live-screen path at all:

  * `reconcilers/terminal_runs.py` (twice) -- the idle-prompt hint that closes a finished run. That
    file contains ZERO references to a live-screen reader.
  * `api_core/claim_block_reason.py` -- whether a hermes console is still resuming, which gates
    claiming channel work.

THE BIAS IS THE WRONG WAY ROUND BY CONSTRUCTION, which is why this is not a corner case. For any
terminal streaming faster than 1 Hz the LAST chunk of a burst is always within one second of the one
before it, so it is always the frame that goes unwritten -- and the frames those two readers want
are exactly that: a claude worker's idle prompt and a hermes console's ready line are both the last
thing printed before silence. The result is a run that never closes and channel work that is never
claimed, with every other signal healthy. The "up but deaf" shape.

NO REAL DATABASE AND NO WRITE QUEUE HERE. `_write_terminal_output` calls `get_db()`, which is the
operator's live database; the queue is driven only through the seam, never end to end.
"""

from __future__ import annotations

import asyncio
import unittest

from service.api_core.terminal_output import _append_terminal_output
from service.api_core.terminal_tail_buffer import (
    pending,
    reset_for_tests,
    set_flush_interval_for_tests,
)
from service.tests.test_the_terminal_write_path_stays_cheap import RecordingDb, Row


def _stored_tail(db, terminal):
    """Carry a written tail forward the way the real caller does, and report the last one."""
    last = None
    for verb, params, sql in db.calls:
        if verb != "UPDATE" or not RecordingDb.tail_bytes(sql, params):
            continue
        set_clause = sql[sql.upper().index(" SET ") + 5: sql.upper().index(" WHERE ")]
        for index, assignment in enumerate(a.strip() for a in set_clause.split(",")):
            if assignment.startswith("output = "):
                last = params[index]
    return last


class AQuietTerminalStillStoresItsLastFrame(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_for_tests()
        # The real one-second window, so this tests the production cadence rather than a fixture.
        set_flush_interval_for_tests(None)

    def tearDown(self):
        reset_for_tests()
        set_flush_interval_for_tests(None)

    async def test_THE_REGRESSION_the_last_frame_is_held_and_not_written(self):
        """The control. If a burst's final chunk were already written, the settle would guard
        nothing and this file should say so rather than stay quietly green."""
        db = RecordingDb()
        terminal = Row(id="t1", output="", status="running", cols=120, rows=30)
        await _append_terminal_output(db, terminal, "first\n", seq=1)
        terminal["output"] = _stored_tail(db, terminal) or ""
        await _append_terminal_output(db, terminal, "the idle prompt >\n", seq=2)

        self.assertNotIn(
            "the idle prompt", _stored_tail(db, terminal) or "",
            "the second chunk was written within the interval, so the premise of this file is gone "
            "-- re-derive rather than deleting the settle",
        )
        held = pending("t1")
        self.assertIsNotNone(held, "nothing is held, so there would be nothing for a settle to write")
        self.assertIn("the idle prompt", held["tail"])

    async def test_A_SETTLE_WRITES_IT(self):
        """The fix: one write with no new bytes, for a stream that has stopped."""
        db = RecordingDb()
        terminal = Row(id="t1", output="", status="running", cols=120, rows=30)
        await _append_terminal_output(db, terminal, "first\n", seq=1)
        terminal["output"] = _stored_tail(db, terminal) or ""
        await _append_terminal_output(db, terminal, "the idle prompt >\n", seq=2)

        await _append_terminal_output(db, terminal, "", seq=2, settle=True)

        self.assertIn(
            "the idle prompt", _stored_tail(db, terminal) or "",
            "the settle did not store the frame the idle-prompt reader and the hermes resume check "
            "both need",
        )
        self.assertIsNone(pending("t1"), "the settle wrote the tail but left it marked dirty")

    async def test_a_settle_with_nothing_held_writes_NOTHING(self):
        """Output that arrived after the settle was scheduled has already written it -- the common
        case on a busy terminal. A settle must not cost a write for that."""
        db = RecordingDb()
        terminal = Row(id="t1", output="", status="running", cols=120, rows=30)
        await _append_terminal_output(db, terminal, "first\n", seq=1)
        self.assertIsNone(pending("t1"), "the first chunk should have written and cleared the tail")

        before = len(db.calls)
        await _append_terminal_output(db, terminal, "", seq=1, settle=True)
        self.assertEqual(len(db.calls), before, "a settle with nothing held still hit the database")

    async def test_a_settle_pairs_output_and_output_seq(self):
        """`output` and `output_seq` lag TOGETHER -- the dashboard seeds `lastSeq` from the row and
        drops frames at or below it, so a seq written without its output loses the gap."""
        db = RecordingDb()
        terminal = Row(id="t1", output="", status="running", cols=120, rows=30)
        await _append_terminal_output(db, terminal, "first\n", seq=1)
        terminal["output"] = _stored_tail(db, terminal) or ""
        await _append_terminal_output(db, terminal, "second\n", seq=7)
        await _append_terminal_output(db, terminal, "", seq=7, settle=True)

        wrote = [c for c in db.calls if c[0] == "UPDATE" and RecordingDb.tail_bytes(c[2], c[1])]
        set_clause = wrote[-1][2]
        self.assertIn("output = ?", set_clause)
        self.assertIn("output_seq = ?", set_clause,
                      "the settle wrote the tail without its sequence, so the client would drop the "
                      "frames between what it holds and what it was told it holds")

    async def test_EVERY_end_status_flushes_and_forgets_not_just_two(self):
        """`ending` read {stopped, failed} while the vocabulary has six members, so a terminal ending
        as `ended`/`cancelled`/`lost`/`completed` got neither the forced final write nor the
        `forget()` that releases its buffer -- while the router closed it out on the full set."""
        for status in ("stopped", "failed", "lost", "ended", "completed", "cancelled"):
            with self.subTest(status=status):
                reset_for_tests()
                db = RecordingDb()
                terminal = Row(id="t1", output="", status="running", cols=120, rows=30)
                await _append_terminal_output(db, terminal, "first\n", seq=1)
                terminal["output"] = _stored_tail(db, terminal) or ""
                await _append_terminal_output(db, terminal, "last words\n", status=status, seq=2)

                self.assertIn("last words", _stored_tail(db, terminal) or "",
                              f"a terminal ending as {status!r} did not force its final write")
                self.assertIsNone(pending("t1"),
                                  f"a terminal ending as {status!r} kept its buffer, leaking 64 KB "
                                  "for the life of the process")


if __name__ == "__main__":
    unittest.main()
