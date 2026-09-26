"""Terminal output a bridge was told was accepted is written before the service shuts down.

The output route answers once a chunk is QUEUED, and nothing flushed the queue at shutdown, so a
restart dropped it (v0.7 scan A6). The drain is bounded: a queue that will not empty cannot hold
shutdown open for ever.
"""

import asyncio
import inspect
import unittest

from service import main
from service.terminal_write_queue import TerminalOutputWriteQueue, drain_terminal_output_writes


class _Recording(TerminalOutputWriteQueue):
    def __init__(self, hang=False):
        super().__init__()
        self.flushed = False
        self.hang = hang

    async def flush_all(self):
        if self.hang:
            await asyncio.sleep(60)
        self.flushed = True


class ShutdownDrainTests(unittest.TestCase):
    def test_the_drain_flushes_what_is_queued(self):
        queue = _Recording()
        self.assertTrue(asyncio.run(drain_terminal_output_writes(queue, timeout=1)))
        self.assertTrue(queue.flushed)

    def test_a_queue_that_will_not_empty_is_abandoned_in_bounded_time(self):
        self.assertFalse(asyncio.run(drain_terminal_output_writes(_Recording(hang=True), timeout=0.05)))

    def test_the_lifespan_drains_before_the_pool_closes(self):
        # The drain is only useful if shutdown CALLS it, and before the connections it writes through
        # are closed. A proven helper with no call site is the failure this repo has paid for twice.
        source = inspect.getsource(main)
        drain = source.index("await drain_terminal_output_writes()")
        close = source.index("await CONNECTION_POOL.aclose()")
        self.assertLess(drain, close)


class _BlockingWrite(TerminalOutputWriteQueue):
    """The REAL queue; only the database write is replaced, so a write can be held in flight or failed."""

    def __init__(self, *, fail=False):
        super().__init__(idle_flush_ms=60_000, max_latency_ms=60_000)
        self.release = None
        self.written = []
        self.fail = fail

    async def _write_terminal_output(self, terminal_id, output, *, status="", seq=0):
        await self.release.wait()
        if self.fail:
            raise RuntimeError("database is locked")
        self.written.append(output)

    def _schedule_settle(self, terminal_id):
        pass


class ADrainWaitsForWritesAlreadyInFlightTests(unittest.TestCase):
    """v0.7.1 review (W01): `flush_terminal` takes a batch off `_pending` BEFORE writing it, and the drain
    returned as soon as `_pending` was empty, so shutdown reported a clean drain and closed the pool
    under a write the bridge had already been told was accepted."""

    def _in_flight(self, queue, drain_timeout, release_after):
        async def scenario():
            queue.release = asyncio.Event()
            await queue.enqueue("t1", "acknowledged", autoschedule=False)
            flushing = asyncio.create_task(queue.flush_terminal("t1"))
            await asyncio.sleep(0.01)  # the batch is popped and the write is blocked
            self.assertEqual(queue._pending, {}, "control: the batch has left _pending")
            if release_after is not None:
                asyncio.get_running_loop().call_later(release_after, queue.release.set)
            drained = await drain_terminal_output_writes(queue, timeout=drain_timeout)
            written_at_return = list(queue.written)
            queue.release.set()
            await asyncio.gather(flushing, return_exceptions=True)
            return drained, written_at_return
        return asyncio.run(scenario())

    def test_a_drain_does_not_report_success_while_a_write_is_in_flight(self):
        drained, written = self._in_flight(_BlockingWrite(), drain_timeout=0.2, release_after=None)
        self.assertFalse(drained, "the drain reported success with a write still in flight")
        self.assertEqual(written, [])

    def test_a_drain_returns_after_the_in_flight_write_lands(self):
        drained, written = self._in_flight(_BlockingWrite(), drain_timeout=2, release_after=0.05)
        self.assertTrue(drained)
        self.assertEqual(written, ["acknowledged"], "the drain returned before the write landed")

    def test_a_failing_write_ends_the_drain_as_not_drained_rather_than_raising(self):
        # S5: the drain caught only a timeout, so a write error escaped the lifespan and skipped closing
        # the pool and the change feed.
        async def scenario():
            queue = _BlockingWrite(fail=True)
            queue.release = asyncio.Event()
            queue.release.set()
            await queue.enqueue("t1", "x", autoschedule=False)
            return await drain_terminal_output_writes(queue, timeout=0.3)
        self.assertFalse(asyncio.run(scenario()))
