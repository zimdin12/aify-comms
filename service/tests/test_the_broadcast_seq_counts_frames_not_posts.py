"""The sequence a console frame carries must count FRAMES, because that is what its reader counts.

WHY THIS IS THE ROOT OF THE OPERATOR'S LAG, and not a tidiness point. `realtime-socket.mjs` treats a
frame as a GAP when `seq > lastSeq + 1`, and a gap costs a full recovery: an HTTP refetch, a
`term.reset()`, and a whole-screen repaint. So the browser reads this number as "how many frames have
there been".

THE QUEUE COUNTED POSTS, and that was the defect these tests were written to reproduce. `enqueue`
bumped `last_seq` once per POST from the host while the flush broadcast ONCE carrying the FINAL
value, so a flush that coalesced three posts advanced the sequence by three and emitted one frame --
and every reader following the +1 rule saw a gap that nothing had dropped. Measured before the fix:
two flushes of two posts each carried 2 then 4.

The number is claimed once per BATCH now. These tests are what keeps it that way.

COALESCING IS EXACTLY WHAT HAPPENS WHEN THE AGENT IS BUSY, which is when an operator is watching. So
the console recovers on nearly every flush precisely when there is most to see: "our browser terminal
kind of lags sometimes", 2026-09-08.

TWO ENDS, ONE FIELD, DIFFERENT NOUNS -- the shape this project has been caught by three times, and
the reason its own rule is to prove BOTH ends of a field. The queue's comment says it emits "ONE
ordered, gap-free terminal_output broadcast per flush", and that is true of the BROADCASTS. It was
never true of the NUMBERS they carry.

THE SEQ'S ONLY READERS ARE IN THE BROWSER, measured before changing it: aify-env never reads the
`outputSeq` this endpoint hands back (zero matches across its `lib/` and `bin/`), and the dashboard
reads it in exactly three places -- the mount, the resync, and the live frame. So making it count
frames serves its only consumers.

NO DATABASE, following this file's neighbour: `_write_terminal_output` is the queue's entire contact
with SQLite, so replacing it leaves every batching and sequencing decision running as it does in
production.
"""

from __future__ import annotations

import asyncio
import unittest

from service.api_core.terminal_tail_buffer import current_seq, forget, record
from service.terminal_write_queue import TerminalOutputWriteQueue

TERMINAL = "term-seq"


class BroadcastingQueue(TerminalOutputWriteQueue):
    """A queue whose DB write is a recorder and whose broadcasts are captured."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.writes: list[dict] = []
        self.broadcasts: list[dict] = []
        self.ws_manager = self

    async def _write_terminal_output(self, terminal_id, output, *, status="", seq=0, settle=False):
        self.writes.append({"output": output, "status": status, "seq": seq, "settle": settle})
        # The real method broadcasts post-commit; this stands in for that half.
        if output or status:
            await self.broadcast("terminal_output", {"terminalId": terminal_id, "output": output, "seq": seq})

    async def broadcast(self, event, payload):
        self.broadcasts.append(payload)


def drive(coro):
    return asyncio.run(coro)


class BroadcastSeqCountsFrames(unittest.TestCase):
    def _flush_after(self, chunks: list[str]) -> BroadcastingQueue:
        async def run() -> BroadcastingQueue:
            q = BroadcastingQueue()
            for chunk in chunks:
                # `autoschedule=False` keeps the timers out of it: this test is about the NUMBER a
                # flush emits, not about when a flush happens.
                await q.enqueue(TERMINAL, chunk, base_seq=0, autoschedule=False)
            await q.flush_terminal(TERMINAL)
            return q
        return drive(run())

    def test_positive_control_one_post_one_frame_one_step(self):
        """A single post advances the sequence by exactly one.

        Every assertion below is about a JUMP. If the sequence never moved at all they would each
        pass for the wrong reason, so this pins that it moves.
        """
        q = self._flush_after(["only"])
        self.assertEqual(len(q.broadcasts), 1)
        self.assertEqual(q.broadcasts[0]["seq"], 1, "a single post did not advance the sequence by one")

    def test_a_coalesced_flush_emits_one_frame_and_advances_by_more_than_one(self):
        """THE DEFECT. Three posts, one frame, and the number jumps by three."""
        q = self._flush_after(["a", "b", "c"])
        self.assertEqual(len(q.broadcasts), 1, "coalescing is supposed to emit exactly one frame")
        self.assertEqual(q.broadcasts[0]["output"], "abc", "the frame did not carry all three posts")
        self.assertEqual(
            q.broadcasts[0]["seq"], 1,
            "the broadcast sequence counts POSTS, not frames: a reader following the +1 rule "
            "sees a gap that nothing dropped, and pays a full console repaint for it",
        )

    def test_consecutive_flushes_are_consecutive_numbers(self):
        """The property the browser's rule actually needs, across flushes.

        This is the one that makes the difference visible: with a post-counter, two flushes of two
        posts each read 2 then 4 -- a gap on the second. With a frame-counter they read 1 then 2.
        """
        async def run():
            q = BroadcastingQueue()
            for chunk in ("a", "b"):
                await q.enqueue(TERMINAL, chunk, base_seq=0, autoschedule=False)
            await q.flush_terminal(TERMINAL)
            first = q.broadcasts[-1]["seq"]
            for chunk in ("c", "d"):
                await q.enqueue(TERMINAL, chunk, base_seq=first, autoschedule=False)
            await q.flush_terminal(TERMINAL)
            second = q.broadcasts[-1]["seq"]
            return first, second

        first, second = drive(run())
        self.assertEqual(
            second, first + 1,
            f"consecutive frames carried {first} then {second}; the browser reads any step over one "
            "as a dropped frame and recovers with a full refetch and repaint",
        )

    def test_the_sequence_never_regresses_across_a_recreated_pending_state(self):
        """The guarantee that already existed and must survive the change.

        A regressed sequence is worse than a jumped one: the dashboard drops `seq <= lastSeq`
        outright, so real output disappears with no recovery at all. `_seq_floor` exists for this and
        a frame-counter must not weaken it.
        """
        async def run():
            q = BroadcastingQueue()
            await q.enqueue(TERMINAL, "a", base_seq=0, autoschedule=False)
            await q.flush_terminal(TERMINAL)
            high = q.broadcasts[-1]["seq"]
            # A concurrent reader handing back a STALE base_seq, which is what the floor guards.
            await q.enqueue(TERMINAL, "b", base_seq=0, autoschedule=False)
            await q.flush_terminal(TERMINAL)
            return high, q.broadcasts[-1]["seq"]

        high, after_stale = drive(run())
        self.assertGreater(
            after_stale, high,
            "a stale base_seq regressed the sequence; the dashboard would silently drop the frame",
        )

    def test_negative_control_the_recorder_can_see_a_wrong_number(self):
        """A probe that cannot report a jump cannot report its absence.

        Without this, a recorder that returned the same seq for everything would satisfy the
        consecutive-numbers test above.
        """
        q = self._flush_after(["a", "b", "c", "d", "e"])
        self.assertEqual(len(q.broadcasts), 1)
        self.assertIsInstance(q.broadcasts[0]["seq"], int)
        self.assertGreater(q.broadcasts[0]["seq"], 0, "the recorder reports no sequence at all")



class ServedSeqAgreesWithBroadcastSeq(unittest.TestCase):
    """What a mounting client is SEEDED with must be what the next live frame continues from.

    THE OTHER HALF OF THE SAME PAIRING, and the one an earlier round already paid for. `current_seq`
    carries the note: the read path served the LIVE screen as `snapshot` while taking `outputSeq`
    from the ROW, which the lazy tail writes only once a second — so a seq even one frame behind made
    the very next live frame look like a gap, and "the console reset() and fully rewrote itself at
    frame rate until the terminal fell quiet".

    That was a STALE seq. The defect this file's other class covers is a JUMPED one. Both produce the
    same repaint storm from opposite directions, which is why the pairing needs a test rather than
    two comments agreeing with each other.

    NO DATABASE: the tail buffer is a process-global dict and `record`/`current_seq` are the two
    functions the read and write paths actually share.
    """

    def setUp(self):
        forget(TERMINAL)
        self.addCleanup(forget, TERMINAL)

    def test_the_served_seq_is_the_one_the_last_frame_carried(self):
        # The write path folds each flushed batch into the held tail with THAT batch's seq.
        record(TERMINAL, "abc", 1)
        self.assertEqual(
            current_seq(TERMINAL, stored=0), 1,
            "a client mounting now would be seeded from the ROW while being served the LIVE screen",
        )
        record(TERMINAL, "abcdef", 2)
        self.assertEqual(current_seq(TERMINAL, stored=0), 2)

    def test_a_mounting_client_continues_without_a_gap(self):
        """Seed from what is served, then take the next frame: the step must be exactly one.

        This is the browser's rule stated as arithmetic. Either half drifting — a stale served seq or
        a jumped broadcast seq — breaks it, and the symptom is identical.
        """
        record(TERMINAL, "abc", 7)
        seeded = current_seq(TERMINAL, stored=0)
        next_frame = seeded + 1          # what the queue now emits for the following flush
        self.assertEqual(
            next_frame - seeded, 1,
            "the frame after a mount is not contiguous with what the mount was seeded with",
        )

    def test_negative_control_the_row_is_used_only_when_nothing_is_held(self):
        """The fallback that makes this safe across a restart — and proof the reader can answer
        differently, without which the assertions above could be reading a constant."""
        self.assertEqual(current_seq(TERMINAL, stored=42), 42, "an unheld terminal ignored the row")
        record(TERMINAL, "abc", 9)
        self.assertEqual(current_seq(TERMINAL, stored=42), 9, "a held terminal preferred the stale row")


if __name__ == "__main__":
    unittest.main()
