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
