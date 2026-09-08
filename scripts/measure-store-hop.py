"""How long the service holds a chunk of terminal output before broadcasting it.

THE HOP THIS MEASURES, named before the number because every earlier transport claim in this project
was retracted for naming the wrong one: from `TerminalOutputWriteQueue.enqueue` accepting a chunk, to
the queue calling `broadcast` with the frame that carries it. That is hop three of the five between a
producer and a browser terminal -- the SERVICE STORE. It does NOT measure the HTTP request handling
in front of it, the SQLite write (replaced here), the WebSocket delivery to a browser, or the xterm
write, and nothing here should be read as though it did.

WHY IT IS WORTH MEASURING RATHER THAN READING OFF THE TIMERS. `idle_flush_ms` is 4 and
`max_latency_ms` is 24, and it would be easy to write those down as the answer. This project has been
wrong four times today by reasoning from code instead of running it, including twice about this very
queue -- a cadence that turned out to be driven by neither timer, and a "post every 1ms" arm that
really posted every 15ms.

THE DATABASE IS REPLACED, following the queue's own test file: `_write_terminal_output` is its entire
contact with SQLite, so replacing it leaves every batching and scheduling decision running as it does
in production while removing a variable this measurement is not about.

Run: python scripts/measure-store-hop.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.terminal_write_queue import TerminalOutputWriteQueue  # noqa: E402

TERMINAL = "term-store-hop"


class TimedQueue(TerminalOutputWriteQueue):
    """A queue whose DB write is a recorder and whose broadcasts are timestamped."""

    def __init__(self, write_delay: float = 0.0, **kwargs):
        super().__init__(**kwargs)
        self.broadcast_at: list[float] = []
        self.write_delay = write_delay
        self.ws_manager = self

    async def _write_terminal_output(self, terminal_id, output, *, status="", seq=0, settle=False):
        # THE CONTROL ARM USES THIS. A store that really is slow must show up as a slow hop, or the
        # fast numbers below are an instrument reporting its own eagerness.
        if self.write_delay:
            await asyncio.sleep(self.write_delay)
        if output or status:
            await self.broadcast("terminal_output", {"terminalId": terminal_id, "output": output})

    async def broadcast(self, event, payload):
        self.broadcast_at.append(time.perf_counter())


async def age_at_broadcast(*, posts: int, gap: float, write_delay: float = 0.0) -> list[float]:
    """Milliseconds between a post being accepted and the frame carrying it being broadcast."""
    queue = TimedQueue(write_delay=write_delay)
    posted_at: list[float] = []
    for _ in range(posts):
        posted_at.append(time.perf_counter())
        await queue.enqueue(TERMINAL, "x" * 64)
        if gap:
            await asyncio.sleep(gap)
    # Let every scheduled flush run; the queue's own bounds are milliseconds.
    await asyncio.sleep(0.2)
    await queue.flush_terminal(TERMINAL)
    await asyncio.sleep(0.05)

    if not queue.broadcast_at:
        return []
    # EACH POST IS AGED AGAINST THE FIRST BROADCAST AT OR AFTER IT, which is the frame that could
    # have carried it. Pairing by index would be wrong: posts coalesce, so there are fewer frames
    # than posts and the mapping is many-to-one.
    ages = []
    for at in posted_at:
        later = [b for b in queue.broadcast_at if b >= at]
        if later:
            ages.append((later[0] - at) * 1000)
    return ages


def spread(values: list[float]) -> str:
    if not values:
        return "no frame carried any post"
    ordered = sorted(values)
    at = lambda q: ordered[min(len(ordered) - 1, int(q * len(ordered)))]  # noqa: E731
    return (f"{ordered[0]:.2f} / {statistics.median(ordered):.2f} / {at(0.9):.2f} / "
            f"{ordered[-1]:.2f}")


async def main() -> None:
    queue = TerminalOutputWriteQueue()
    print("the service store: enqueue accepted -> broadcast of the frame carrying it")
    print(f"idle_flush={queue.idle_flush_seconds * 1000:.0f}ms  "
          f"max_latency={queue.max_latency_seconds * 1000:.0f}ms  "
          f"batch cap={queue.max_batch_chars} chars\n")
    print(f"{'arm':<34}  age at broadcast ms: min / p50 / p90 / max")

    for label, posts, gap in (
        ("one post, then quiet", 1, 0.0),
        ("a post every 50ms (idle each time)", 8, 0.050),
        ("a post every 1ms (coalescing)", 60, 0.001),
    ):
        ages = await age_at_broadcast(posts=posts, gap=gap)
        print(f"{label:<34}  {spread(ages)}")

    # ── the control ───────────────────────────────────────────────────────────────────────────
    slow = await age_at_broadcast(posts=1, gap=0.0, write_delay=0.050)
    print(f"\nCONTROL, the store's write made to take 50ms:\n{'':<34}  {spread(slow)}")
    median = statistics.median(slow) if slow else 0.0
    print(
        "  -> the clock DOES report a slow store, so the figures above are the queue."
        if median >= 40
        else f"  -> THE CONTROL FAILED: a 50ms write measured {median:.2f}ms, so this instrument is "
             "not timing the broadcast and none of the figures above mean anything."
    )

    # ── the caveat that decides how much these numbers are worth ──────────────────────────────
    floor_ms = await _timer_floor()
    print(
        f"\nTHIS HOST'S TIMER FLOOR IS {floor_ms:.1f}ms, measured in the same run: a 4ms `call_later`"
        f"\nwaits that long. So most of the figures above are the PLATFORM, not the queue -- the idle"
        f"\nflush is 4ms by design and cannot be observed here at all."
        f"\n\nAND THE SERVICE RUNS IN A LINUX CONTAINER, where that floor is about a millisecond. These"
        f"\nnumbers are the right code measured on the wrong platform, which is this project's"
        f"\n\"correct but wrong\" shape. What they DO establish is an upper bound: even with a 15ms"
        f"\nfloor on top, the store holds a chunk for about {statistics.median(await age_at_broadcast(posts=1, gap=0.0)):.0f}ms."
        f"\nWhat they do NOT establish is the container's figure, which is unmeasured."
    )


async def _timer_floor() -> float:
    """What a 4ms `call_later` actually waits here. Measured, not assumed."""
    loop = asyncio.get_running_loop()
    waits = []
    for _ in range(5):
        fired = loop.create_future()
        started = time.perf_counter()
        loop.call_later(0.004, lambda f=fired: f.done() or f.set_result(time.perf_counter()))
        at = await fired
        waits.append((at - started) * 1000)
    return statistics.median(waits)


asyncio.run(main())
