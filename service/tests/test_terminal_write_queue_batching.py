"""The queue that batches terminal output, and what it does when a write fails.

Seven of its functions were among the 71 the suite never entered — the idle and max-latency flush
timers, the flush-task tracking, the done callback, and `_requeue_front`. What they protect is the
console: this queue sits in front of the single SQLite writer, and every failure mode here shows up
as scrambled or missing output rather than as an error.

WHAT IT ACTUALLY FLUSHES, measured 2026-09-08 against this class with real timers rather than
inferred from `max_latency_ms`: 65 frames a second for 100 KB/s of output and 255 for 8 MB/s. The
"~40" this paragraph used to claim was 1/0.024, the max-latency ceiling -- but past `max_batch_chars`
the batch cap fires first, so the ceiling is not the thing that decides. Posts arrive far more
often again: 16,219 of them became 67 frames in a second at 1 MB/s, about 242 to one. That is a
ratio of FREQUENCIES and says nothing about relative runtime cost.

TESTED WITHOUT A DATABASE, by overriding the ONE method that touches it. `_write_terminal_output` is
the queue's entire contact with SQLite, so replacing it on the instance leaves every scheduling,
batching, bounding and requeue decision running exactly as it does in production — and lets a test
make a write FAIL, which is the branch that matters most and the one a live database will not
produce on demand.

THE ORDER PROPERTIES ARE THE POINT. Output is a stream: a batch written twice, out of order, or with
a regressed sequence number is worse than a batch dropped, because the dashboard's seq-dedupe then
discards real frames. So the requeue path is asserted to put the failed output back at the FRONT,
and sequence numbers are asserted to be strictly increasing even across the pending state being
recreated between flushes.
"""

from __future__ import annotations

import asyncio
import unittest

from service.api_core.terminal_tail_buffer import forget, pending, record
from service.terminal_write_queue import TerminalOutputWriteQueue

TERMINAL = "term-1"


class RecordingQueue(TerminalOutputWriteQueue):
    """A queue whose only DB call is replaced by a recorder. `fail_times` makes the first N writes
    raise, which is how the requeue and retry paths are reached."""

    def __init__(self, *, fail_times: int = 0, on_attempt=None, **kwargs):
        super().__init__(**kwargs)
        self.writes: list[dict] = []
        self.attempts = 0
        self._fail_times = fail_times
        # Called at the START of a write, i.e. WHILE the flush is in flight. That is the only place
        # a test can reproduce a POST arriving mid-flush, which is what puts a newer chunk in the
        # pending state before the failed batch is handed back.
        self._on_attempt = on_attempt

    async def _write_terminal_output(self, terminal_id, output, *, status="", seq=0, settle=False):
        self.attempts += 1
        if self._on_attempt is not None:
            await self._on_attempt(self, self.attempts)
        if self.attempts <= self._fail_times:
            raise RuntimeError("database is locked")
        self.writes.append({"terminalId": terminal_id, "output": output, "status": status,
                            "seq": seq, "settle": settle})


def run(coro):
    return asyncio.run(coro)


class TerminalWriteQueueTests(unittest.TestCase):
    def _queue(self, **kwargs) -> RecordingQueue:
        # Millisecond timings so the timers are observable in a test rather than in 24ms of real
        # console latency. The RELATIONSHIPS are what the assertions are about, not the numbers.
        kwargs.setdefault("idle_flush_ms", 5)
        kwargs.setdefault("max_latency_ms", 40)
        return RecordingQueue(**kwargs)

    # ── batching ─────────────────────────────────────────────────────────────────────────────

    def test_writes_inside_the_idle_window_become_ONE_write(self):
        """The whole purpose. Three POSTs from a chatty PTY must not be three transactions against
        the single SQLite writer."""
        queue = self._queue()

        async def body():
            for chunk in ("one ", "two ", "three"):
                await queue.enqueue(TERMINAL, chunk)
            await asyncio.sleep(0.05)

        run(body())
        self.assertEqual(len(queue.writes), 1, f"expected one batched write, got {queue.writes}")
        self.assertEqual(queue.writes[0]["output"], "one two three")

    def test_the_batch_preserves_the_order_it_was_written_in(self):
        queue = self._queue()

        async def body():
            for i in range(10):
                await queue.enqueue(TERMINAL, f"{i}")
            await asyncio.sleep(0.05)

        run(body())
        self.assertEqual(queue.writes[0]["output"], "0123456789")

    def test_each_terminal_batches_independently(self):
        queue = self._queue()

        async def body():
            await queue.enqueue("term-a", "AAA")
            await queue.enqueue("term-b", "BBB")
            await asyncio.sleep(0.05)

        run(body())
        by_terminal = {w["terminalId"]: w["output"] for w in queue.writes}
        self.assertEqual(by_terminal, {"term-a": "AAA", "term-b": "BBB"})

    def test_a_full_batch_flushes_immediately_rather_than_waiting(self):
        """`max_batch_chars` is a memory bound as well as a latency one: a burst must not sit in
        RAM until the idle timer notices."""
        queue = self._queue(max_batch_chars=1024)

        async def body():
            await queue.enqueue(TERMINAL, "x" * 2000)
            await asyncio.sleep(0.003)  # SHORTER than the idle window
            return len(queue.writes)

        self.assertEqual(run(body()), 1, "a full batch waited for the idle timer")

    def test_a_full_batch_ARMS_ITS_FLUSH_ONCE_not_once_per_post(self):
        """The flush a full batch asks for is deferred, so asking again for each post is a task storm.

        `_schedule_flush_locked` CANNOT flush inline: `flush_terminal` takes the lock the caller is
        already holding, so it defers by a millisecond. Every post arriving inside that millisecond
        still sees `chars >= max_batch_chars` -- the batch has not been emptied yet -- and asked for
        another flush, each of which creates an asyncio task that finds the state already gone.

        MEASURED against the real queue at rates a PTY reaches, counting scheduled flush tasks per
        second of output: 65 at 100 KB/s (the timers doing the work, nothing wasted), 251 at 1 MB/s,
        and 56,465 at 8 MB/s -- the rate a verbose build log hits. THE SERVICE IS SINGLE-WORKER BY
        DESIGN, so those tasks are created on the one event loop that also serves every dashboard
        poll, status read and message send.

        The batch also overshot the cap it exists to enforce, because posts keep joining during the
        deferred millisecond: 31,680 characters against a 16,384 limit. Arming once does not remove
        that overshoot -- only an inline flush would, and the lock forbids it -- so the assertion
        below is about the ARMING, which is the half that is actually a defect.
        """
        armed = []
        queue = self._queue(max_batch_chars=1024)
        original = queue._schedule_flush_locked

        def counting(terminal_id, *, delay):
            armed.append(delay)
            return original(terminal_id, delay=delay)

        queue._schedule_flush_locked = counting

        async def burst():
            # No sleep between posts: an uncontended lock does not yield, so all of these join ONE
            # batch, which is exactly the burst a PTY reader delivers.
            for _ in range(40):
                await queue.enqueue(TERMINAL, "x" * 100)
            await asyncio.sleep(0.02)

        async def body():
            await burst()
            first = len(armed)
            # THE SECOND BATCH IS THE ARM THAT KEEPS THE FLAG HONEST. "Already armed" is only true
            # of the batch that armed it; a flag hung on the QUEUE instead would be permanent, and
            # every later full batch would fall back to waiting for a timer with no test noticing.
            await burst()
            return first, len(armed), "".join(w["output"] for w in queue.writes)

        first, total, written = run(body())
        self.assertEqual(
            first, 1,
            f"the full batch armed {first} flushes; each one becomes an asyncio task on the single "
            "event loop the whole service shares, and all but the first find nothing to do",
        )
        self.assertEqual(
            total, 2,
            f"two full batches armed {total} flushes between them; a second batch that arms nothing "
            "has to wait for a timer, which is the latency the immediate flush exists to avoid",
        )
        # POSITIVE CONTROL for both counts: a queue that armed NOTHING would satisfy neither the
        # arithmetic above nor this, because the output would still be sitting in the pending state.
        self.assertEqual(len(written), 8000, "the batched output did not all reach the writer")

    def test_a_terminal_ending_status_flushes_immediately(self):
        """`stopped`/`failed` is the last thing a console ever says. Holding it for the idle window
        leaves the dashboard showing a running terminal that has already exited."""
        for status in ("stopped", "failed"):
            with self.subTest(status=status):
                queue = self._queue()

                async def body():
                    await queue.enqueue(TERMINAL, "bye", status=status)
                    await asyncio.sleep(0.003)
                    return list(queue.writes)

                writes = run(body())
                self.assertEqual(len(writes), 1, "the final status waited for a timer")
                self.assertEqual(writes[0]["status"], status)

    def test_continuous_writes_still_flush_at_the_MAX_LATENCY_bound(self):
        """The idle timer restarts on every chunk, so a console that never pauses would never flush
        without this second bound — the operator would watch a live terminal print nothing."""
        queue = self._queue(idle_flush_ms=20, max_latency_ms=40)

        async def body():
            for _ in range(12):
                await queue.enqueue(TERMINAL, "tick ")
                await asyncio.sleep(0.008)  # always shorter than the idle window
            return len(queue.writes)

        self.assertGreaterEqual(run(body()), 1, "a never-idle terminal never flushed")

    # ── sequence numbers ─────────────────────────────────────────────────────────────────────

    def test_every_post_in_one_batch_shares_the_frame_it_will_become(self):
        """A batch is ONE broadcast, so the posts in it share ONE sequence.

        THIS TEST USED TO REQUIRE THE OPPOSITE -- a strictly increasing number per POST -- and that
        requirement was the operator's console lag. `realtime-socket.mjs` reads this number as a
        count of FRAMES: `seq > lastSeq + 1` means one was dropped, and a drop costs a full recovery
        (an HTTP refetch, a `term.reset()`, a whole-screen repaint). A per-post counter advanced by
        three on a flush that coalesced three posts, so the browser saw a gap that nothing had
        dropped. Measured before the change: two flushes of two posts each carried 2 then 4.

        THE OLD PROPERTY HAD NO CONSUMER, which is why retargeting it costs nothing. The return
        value becomes `outputSeq` in the POST response, and the sole caller is aify-env, which does
        not read it -- zero matches across its `lib/` and `bin/`. The number's only readers are in
        the dashboard, and all three of them want frames.

        The regression guarantee is a SEPARATE property and is asserted by the test below, which is
        the one that protects real output: the dashboard drops `seq <= lastSeq` outright.
        """
        queue = self._queue()

        async def body():
            return [await queue.enqueue(TERMINAL, f"{i}") for i in range(5)]

        seqs = run(body())
        self.assertEqual(
            len(set(seqs)), 1,
            f"posts in one batch were given different sequences: {seqs}. They become a single "
            "broadcast, so a reader counting frames would see gaps that nothing dropped.",
        )
        self.assertGreater(seqs[0], 0, "the batch was never given a sequence at all")

    def test_a_sequence_never_regresses_across_flushes(self):
        """`_seq_floor` exists because a concurrent request can read a stale `output_seq` from the
        DB while a prior flush has not committed. A regressed seq is silently DROPPED by the
        dashboard's dedupe, so it looks like missing output, not like an error."""
        queue = self._queue()

        async def body():
            first = await queue.enqueue(TERMINAL, "a")
            await asyncio.sleep(0.05)
            # A stale base_seq, exactly what a racing reader supplies.
            second = await queue.enqueue(TERMINAL, "b", base_seq=0)
            await asyncio.sleep(0.05)
            return first, second

        first, second = run(body())
        self.assertGreater(second, first, "a stale base_seq pulled the sequence backwards")

    # ── the backlog bound ────────────────────────────────────────────────────────────────────

    def test_an_over_long_backlog_drops_the_OLDEST_and_says_so(self):
        """A console that outruns the writer must lose its SCROLLBACK, not its present — and the
        gap has to be visible, or the operator reads a doctored transcript as a complete one."""
        queue = self._queue(max_batch_chars=1024, max_pending_chars=2048)

        async def body():
            # Enqueued with autoschedule off so the backlog can exceed the bound without a flush
            # racing it; the explicit flush then writes whatever survived.
            for marker in ("A", "B", "C", "D"):
                await queue.enqueue(TERMINAL, marker * 1000, autoschedule=False)
            await queue.flush_terminal(TERMINAL)
            return list(queue.writes)

        writes = run(body())
        self.assertEqual(len(writes), 1)
        output = writes[0]["output"]
        self.assertIn("dropped", output, "output was silently discarded")
        self.assertIn("D" * 100, output, "the NEWEST output was dropped instead of the oldest")
        self.assertNotIn("A" * 100, output, "the oldest output survived past the bound")

    # ── failure ──────────────────────────────────────────────────────────────────────────────

    def test_a_failed_write_puts_the_output_back_at_the_FRONT(self):
        """Order again: the failed batch is older than whatever arrived while it was in flight, so
        appending it would interleave the console's history into its present."""
        async def newer_arrives_mid_flush(q, attempt):
            # THE RACE, made deterministic. The "newer" chunk has to be pending BEFORE the failed
            # batch comes back, or `appendleft` and `append` do the same thing to an empty deque —
            # which is how my first version of this test passed against the wrong one.
            if attempt == 1:
                await q.enqueue(TERMINAL, "newer", autoschedule=False)

        queue = self._queue(fail_times=1, on_attempt=newer_arrives_mid_flush)

        async def body():
            await queue.enqueue(TERMINAL, "older")
            await asyncio.sleep(0.25)          # first attempt fails, the retry succeeds
            return list(queue.writes)

        writes = run(body())
        self.assertTrue(writes, "the failed batch was never retried — that output is lost")
        self.assertEqual(
            "".join(w["output"] for w in writes), "oldernewer",
            "the requeued batch came back out of order",
        )

    def test_a_failed_write_is_RETRIED_rather_than_dropped(self):
        queue = self._queue(fail_times=1)

        async def body():
            await queue.enqueue(TERMINAL, "keep me")
            await asyncio.sleep(0.2)
            return list(queue.writes), queue.attempts

        writes, attempts = run(body())
        self.assertGreaterEqual(attempts, 2, "the write was attempted once and abandoned")
        self.assertEqual([w["output"] for w in writes], ["keep me"])

    def test_autoschedule_false_stores_without_scheduling_anything(self):
        """The caller that passes this flushes explicitly. If it scheduled anyway, that caller would
        get two writes for one batch."""
        queue = self._queue()

        async def body():
            await queue.enqueue(TERMINAL, "held", autoschedule=False)
            await asyncio.sleep(0.05)
            return len(queue.writes)

        self.assertEqual(run(body()), 0, "an unscheduled enqueue flushed itself")

    def test_an_empty_enqueue_is_ignored(self):
        queue = self._queue()

        async def body():
            return await queue.enqueue(TERMINAL, "", status="")

        self.assertEqual(run(body()), 0)

    # ── a handed-back batch keeps its own number ─────────────────────────────────────────────

    def test_A_RETRIED_BATCH_DOES_NOT_SWALLOW_THE_NEXT_ONES_SEQUENCE(self):
        """FOUND BY REVIEW, followed all the way through the real browser consumer.

        `_requeue_front` used to prepend the failed bytes to whatever batch was pending, and
        `enqueue` claims a sequence only when there is NO batch -- so a post arriving during a failed
        write joined the failed one under a number a browser had ALREADY consumed. Review's trace:
        write AA/1 then B/2, snapshot the browser at AAB/2, fail B's flush, enqueue C. The row ends
        AABC correctly and the BROADCAST is `BC` at seq 2, which the browser drops as already seen.
        C is never painted, no gap is detected, and nothing recovers.

        ADVANCING THE NUMBER INSTEAD WOULD PAINT B TWICE, because the payload still carries bytes the
        snapshot already showed. The batches stay separate, so the retry goes out as the frame it
        already was and the new bytes go out adjacent to what the browser holds.
        """
        queue = self._queue(fail_times=1)

        async def body():
            await queue.enqueue(TERMINAL, "B", base_seq=1)
            try:
                await queue.flush_terminal(TERMINAL)
            except RuntimeError:
                pass
            # C arrives while B is being handed back, which is the whole window.
            await queue.enqueue(TERMINAL, "C", base_seq=1, autoschedule=False)
            await queue.flush_terminal(TERMINAL)
            return [(w["output"], w["seq"]) for w in queue.writes]

        written = run(body())
        self.assertEqual(written, [("B", 2), ("C", 3)],
                         f"the retried batch and the new one were merged: {written}")

    def test_AND_A_RETRY_THAT_FAILS_AGAIN_KEEPS_ITS_ORDER(self):
        """NEGATIVE CONTROL for handing back a LIST: only the batch that threw and everything after
        it may go back, and in order. Handing back one batch alone would reorder the stream, which is
        exactly what the sequence exists to expose."""
        queue = self._queue(fail_times=2)

        async def body():
            await queue.enqueue(TERMINAL, "B", base_seq=1)
            for _ in range(2):
                try:
                    await queue.flush_terminal(TERMINAL)
                except RuntimeError:
                    pass
                await queue.enqueue(TERMINAL, "C", base_seq=1, autoschedule=False)
            await queue.flush_terminal(TERMINAL)
            return [(w["output"], w["seq"]) for w in queue.writes]

        written = run(body())
        self.assertEqual([text for text, _ in written], ["B", "C", "C"],
                         f"the handed-back batches lost their order: {written}")
        self.assertEqual(written[0][1], 2, "the retried batch did not keep its own number")

    # ── the settle resolves its generation under the lock ────────────────────────────────────

    def test_a_settle_writes_the_GENERATION_THAT_EXISTS_WHEN_IT_WRITES(self):
        """FOUND BY REVIEW, 2026-09-08, and constructed rather than caught.

        `settle_terminal_tail` read the held tail and its sequence BEFORE taking `_write_lock`. A
        writer already holding that lock can fold another chunk in and advance the sequence while
        the settle waits -- and the settle then wrote the tail as it now stands with the number it
        read before that writer existed. The row and every response say 2 for bytes numbered 3, and
        the held tail is marked clean, so nothing corrects it. A client seeded from that pair holds
        bytes its sequence does not cover, which is the same tear as the snapshot one on the read
        side, arriving through the writer.

        THE LOCK IS THE ONLY THING THAT ORDERS THESE, so the read has to be inside it.
        """
        queue = self._queue()
        seen = []

        async def body():
            # A DIRTY HELD TAIL: the first record is a fresh key and reports itself due, the second
            # lands inside the flush interval and is what `pending` will hand the settle.
            record(TERMINAL, "AB", 1)
            record(TERMINAL, "AB", 2)
            self.assertEqual(pending(TERMINAL), {"tail": "AB", "seq": 2},
                             "the fixture did not leave a dirty held tail to settle")
            async with queue._write_lock:
                settling = asyncio.create_task(queue.settle_terminal_tail(TERMINAL))
                # The settle is now waiting for the lock this block holds, which is exactly where
                # review's paused writer sits.
                await asyncio.sleep(0.02)
                seen.append(len(queue.writes))
                record(TERMINAL, "ABC", 3)
            await settling

        try:
            run(body())
        finally:
            forget(TERMINAL)

        self.assertEqual(seen, [0], "the settle wrote while another writer held the lock")
        self.assertEqual(len(queue.writes), 1, f"expected one settle write, got {queue.writes}")
        self.assertTrue(queue.writes[0]["settle"], "the write did not go through the settle path")
        self.assertEqual(queue.writes[0]["seq"], 3,
                         f"the settle wrote sequence {queue.writes[0]['seq']} for a tail that had "
                         f"advanced to 3 while it waited for the lock")

    def test_a_settle_with_NOTHING_HELD_writes_nothing(self):
        """NEGATIVE CONTROL for the test above: the assertion there is about WHICH generation is
        written, and it would read the same if the settle simply always wrote."""
        queue = self._queue()

        async def body():
            forget(TERMINAL)
            await queue.settle_terminal_tail(TERMINAL)

        try:
            run(body())
        finally:
            forget(TERMINAL)
        self.assertEqual(queue.writes, [], "a settle with nothing held still wrote")
