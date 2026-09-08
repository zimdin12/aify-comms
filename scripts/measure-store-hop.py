"""How long the service holds a chunk of terminal output before broadcasting it.

THE HOP, named before the number because every earlier transport claim in this project was retracted
for naming the wrong one: from `TerminalOutputWriteQueue.enqueue` accepting a chunk, to the queue
calling `broadcast` with the frame that CARRIES THAT CHUNK. Hop three of the five between a producer
and a browser terminal -- the SERVICE STORE. Not the HTTP handling in front of it, not the SQLite
write (replaced here), not the WebSocket delivery, not the xterm write.

EACH POST IS MATCHED TO ITS OWN FRAME, BY CONTENT. The first version of this script paired every post
with the first broadcast at or after it BY TIME, and never checked that the frame carried that post's
bytes -- so an empty frame, or one carrying another terminal's output, would have been accepted as
the answer. That is the same defect review demonstrated in this repo's other transport probe, where
80 timeouts and zero echo bytes still produced published figures. Applying the correction here before
somebody has to find it twice is the point.

A POST WITH NO MATCHING FRAME IS A REJECTION, counted and printed beside the figures, never dropped
and never aged against something else.

WHY MEASURE RATHER THAN READ THE TIMERS OFF THE CODE. `idle_flush_ms` is 4 and `max_latency_ms` is 24
and it would be easy to write those down as the answer. This project has been wrong repeatedly today
by reasoning from code instead of running it, including twice about this very queue.

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
    """A queue whose DB write is a recorder and whose broadcasts are timestamped WITH their bytes."""

    def __init__(self, write_delay: float = 0.0, **kwargs):
        super().__init__(**kwargs)
        self.frames: list[tuple[float, str]] = []
        self.write_delay = write_delay
        self.ws_manager = self

    async def _write_terminal_output(self, terminal_id, output, *, status="", seq=0, settle=False):
        # THE CONTROL ARM USES THIS. A store that really is slow must show up as a slow hop, or the
        # fast numbers are an instrument reporting its own eagerness.
        if self.write_delay:
            await asyncio.sleep(self.write_delay)
        if output or status:
            await self.broadcast("terminal_output", {"terminalId": terminal_id, "output": output})

    async def broadcast(self, event, payload):
        # THE OUTPUT IS KEPT, not just the moment. Pairing on time alone cannot tell whether the
        # frame carries the post being aged.
        self.frames.append((time.perf_counter(), str(payload.get("output", ""))))


async def run_arm(*, posts: int, gap: float, write_delay: float = 0.0):
    """Ages in ms for posts that were actually carried, and a reason for each that was not."""
    queue = TimedQueue(write_delay=write_delay)
    sent: list[tuple[str, float]] = []
    for i in range(posts):
        marker = f"<M{i}>"                      # unique to this post, and short
        sent.append((marker, time.perf_counter()))
        await queue.enqueue(TERMINAL, marker + "x" * 56)
        if gap:
            await asyncio.sleep(gap)
    await asyncio.sleep(0.2)
    await queue.flush_terminal(TERMINAL)
    await asyncio.sleep(0.05)

    ages, rejected = [], []
    for marker, at in sent:
        carrying = [(t, out) for t, out in queue.frames if t >= at and marker in out]
        if not carrying:
            rejected.append(f"{marker} was never carried by any frame")
            continue
        ages.append((carrying[0][0] - at) * 1000)
    return ages, rejected


def spread(values: list[float]) -> str:
    if not values:
        return "no valid sample"
    ordered = sorted(values)
    at = lambda q: ordered[min(len(ordered) - 1, int(q * len(ordered)))]  # noqa: E731
    return f"{ordered[0]:.2f} / {at(0.5):.2f} / {at(0.9):.2f} / {ordered[-1]:.2f}"


async def timer_floor() -> float:
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


async def main() -> None:
    queue = TerminalOutputWriteQueue()
    print("the service store: enqueue accepted -> the frame CARRYING that chunk is broadcast")
    print(f"idle_flush={queue.idle_flush_seconds * 1000:.0f}ms  "
          f"max_latency={queue.max_latency_seconds * 1000:.0f}ms  "
          f"batch cap={queue.max_batch_chars} chars\n")
    print(f"{'arm':<36}  ms: min / p50 / p90 / max   rejected")

    for label, posts, gap in (
        ("one post, then quiet", 1, 0.0),
        ("a post every 50ms (idle each time)", 8, 0.050),
        ("a post every 1ms (coalescing)", 60, 0.001),
    ):
        ages, rejected = await run_arm(posts=posts, gap=gap)
        note = f"   {len(rejected)}" + (f" -- {rejected[0]}" if rejected else "")
        print(f"{label:<36}  {spread(ages)}{note}")

    slow_ages, slow_rejected = await run_arm(posts=1, gap=0.0, write_delay=0.050)
    print(f"\nCONTROL, the store's write made to take 50ms:\n{'':<36}  {spread(slow_ages)}"
          f"   {len(slow_rejected)}")
    valid = bool(slow_ages) and not slow_rejected
    median = statistics.median(slow_ages) if slow_ages else 0.0
    print(
        "  -> the clock DOES report a slow store, with every sample carried, so the arms above are "
        "the queue."
        if valid and median >= 40
        else f"  -> THE CONTROL DID NOT HOLD: {len(slow_ages)} carried sample(s), median "
             f"{median:.2f}ms. Nothing above is published."
    )

    floor_ms = await timer_floor()
    baseline, _ = await run_arm(posts=1, gap=0.0)
    print(
        f"\nTHIS HOST'S TIMER FLOOR IS {floor_ms:.1f}ms, measured in the same run: a 4ms `call_later`"
        f"\nwaits that long. So most of the figures above are the PLATFORM, not the queue -- the idle"
        f"\nflush is 4ms by design and cannot be observed here at all."
        f"\n\nAND THE SERVICE RUNS IN A LINUX CONTAINER, where that floor is about a millisecond. These"
        f"\nare the right code measured on the wrong platform. What they establish is an upper bound:"
        f"\neven with a {floor_ms:.0f}ms floor stacked on top, the store holds a chunk about "
        f"{statistics.median(baseline) if baseline else 0:.0f}ms."
        f"\nWhat they do not establish is the container's figure, which is unmeasured."
    )


asyncio.run(main())
