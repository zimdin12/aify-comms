"""The queue that batches terminal output, and what it does when a write fails.

Seven of its functions were among the 71 the suite never entered — the idle and max-latency flush
timers, the flush-task tracking, the done callback, and `_requeue_front`. What they protect is the
console: this queue sits in front of the single SQLite writer at ~40 terminal_output frames a second,
and every failure mode here shows up as scrambled or missing output rather than as an error.

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

    async def _write_terminal_output(self, terminal_id, output, *, status="", seq=0):
        self.attempts += 1
        if self._on_attempt is not None:
            await self._on_attempt(self, self.attempts)
        if self.attempts <= self._fail_times:
            raise RuntimeError("database is locked")
        self.writes.append({"terminalId": terminal_id, "output": output, "status": status, "seq": seq})


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

    def test_sequence_numbers_are_strictly_increasing_within_a_batch(self):
        queue = self._queue()

        async def body():
            return [await queue.enqueue(TERMINAL, f"{i}") for i in range(5)]

        seqs = run(body())
        self.assertEqual(seqs, sorted(set(seqs)), f"sequence regressed or repeated: {seqs}")

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
