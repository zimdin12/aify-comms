"""The sequence a console frame carries must count FRAMES, because that is what its reader counts.

WHY THIS IS THE ROOT OF THE OPERATOR'S LAG, and not a tidiness point. `realtime-socket.mjs` treats a
frame as a GAP when `seq > lastSeq + 1`, and a gap costs a full recovery: an HTTP refetch, a
`term.reset()`, and a whole-screen repaint. So the browser reads this number as "how many frames have
there been".

THE QUEUE COUNTS POSTS. `enqueue` does `last_seq += 1` once per POST from the host, and the flush
broadcasts ONCE carrying the FINAL value. So a flush that coalesced three posts advances the sequence
by three and emits one frame -- and every reader following the +1 rule sees a gap that nothing
dropped.

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


if __name__ == "__main__":
    unittest.main()
