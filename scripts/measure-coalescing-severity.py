"""How bad does the deployed sequence defect get as a console gets busier?

THE QUESTION LEFT OPEN. `check-deployed-console-transport.py` shows the running build numbers frames
per POST, and `measure-live-frame-gaps.mjs` shows it not firing on a quiet fleet -- the closest
approach to the 4ms coalescing window was 8.3ms, about 2.1x outside. That leaves the shape of the
risk unstated: does a slightly busier console degrade gently, or fall off a cliff?

MEASURED RATHER THAN REASONED, and the answer is a cliff. The relationship is driven directly:
enqueue k posts, flush once, read the sequence the broadcast carries, and apply the browser's own
rule to consecutive frames. Both modules are driven the same way -- the repo's and the CONTAINER's --
so the contrast is a paired comparison rather than two runs described together.

WHY BATCH SIZE AND NOT POST RATE. Pacing posts at sub-millisecond intervals is not measurable here:
Windows timers have a ~15ms floor, which this repo has already been caught by once ("a test waited
80ms for ticks needing 90"). Asking for a 0.5ms interval and getting 15 would measure the timer, not
the queue. Batch size is the variable the flush logic actually keys on, and the mapping to a rate is
stated rather than smuggled: a batch of k means k posts landed inside one flush window.

    python scripts/measure-coalescing-severity.py

Exit 0 when both modules were driven and the controls held, 2 when they could not be.
"""
from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONTAINER = "aify-comms-service"
TERMINAL = "term-severity"

#: Batch sizes to drive. 1 is the control -- if it does not advance by exactly one, the harness is
#: measuring something other than the sequence and every other row is meaningless.
BATCHES = (1, 2, 3, 4, 8, 16)

#: Frames per row, so a "100%" is a hundred percent of something rather than of one observation.
FRAMES = 12


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _harness(queue_class):
    """The queue with its DB write recorded and its broadcasts captured, as its own test does."""

    class Broadcasting(queue_class):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.broadcasts: list[dict] = []
            self.ws_manager = self

        async def _write_terminal_output(self, terminal_id, output, *, status="", seq=0,
                                         settle=False):
            if output or status:
                await self.broadcast("terminal_output",
                                     {"terminalId": terminal_id, "output": output, "seq": seq})

        async def broadcast(self, event, payload):
            self.broadcasts.append(payload)

    return Broadcasting


async def _drive(queue_class, batch: int, frames: int) -> list[int]:
    """`frames` flushes of `batch` posts each. Returns the sequence each broadcast carried.

    `autoschedule=False` keeps the TIMERS out of it. This measures the NUMBER a flush emits, which
    is the thing the browser reads; when a flush happens is a separate question and one the Windows
    timer floor makes unmeasurable at these intervals anyway.
    """
    queue = _harness(queue_class)()
    for _ in range(frames):
        for _ in range(batch):
            await queue.enqueue(TERMINAL, "x", base_seq=0, autoschedule=False)
        await queue.flush_terminal(TERMINAL)
    return [int(b["seq"]) for b in queue.broadcasts]


def _recoveries(seqs: list[int]) -> int:
    """`realtime-socket.mjs`'s rule, mirrored: a step over one costs a refetch, reset and repaint.

    The cursor is NOT lowered on a regression, because the browser returns without touching
    `lastSeq` -- a hand-written version of this counted a gap the browser never takes.
    """
    last = -1
    count = 0
    for seq in seqs:
        if last >= 0:
            if seq <= last:
                continue
            if seq > last + 1:
                count += 1
        last = seq
    return count


def main() -> int:
    from service.terminal_write_queue import TerminalOutputWriteQueue as RepoQueue

    scratch = Path(tempfile.mkdtemp(prefix="aify-severity-"))
    pulled = scratch / "deployed_queue.py"
    got = subprocess.run(["docker", "cp",
                          f"{CONTAINER}:/app/service/terminal_write_queue.py", str(pulled)],
                         capture_output=True, text=True)
    if got.returncode != 0 or not pulled.exists():
        print("UNKNOWN: the container's queue could not be copied out, so there is nothing to")
        print("contrast the repo's against.")
        return 2
    DeployedQueue = _load("deployed_terminal_write_queue", pulled).TerminalOutputWriteQueue

    print(f"THE SEQUENCE A FLUSH EMITS, driven at {FRAMES} flushes per row.")
    print("A batch of k means k posts landed inside one flush window (the deployed idle flush is 4ms).")
    print()
    print(f"  {'batch':>6}   {'DEPLOYED step':>14} {'recoveries':>11}   {'REPO step':>10} {'recoveries':>11}")

    control_ok = True
    rows = []
    for batch in BATCHES:
        dep = asyncio.run(_drive(DeployedQueue, batch, FRAMES))
        rep = asyncio.run(_drive(RepoQueue, batch, FRAMES))
        dep_step = (dep[1] - dep[0]) if len(dep) > 1 else 0
        rep_step = (rep[1] - rep[0]) if len(rep) > 1 else 0
        dep_rec, rep_rec = _recoveries(dep), _recoveries(rep)
        frames_seen = len(dep)
        rows.append((batch, dep_step, dep_rec, rep_step, rep_rec, frames_seen))
        print(f"  {batch:>6}   {dep_step:>14} {dep_rec:>4}/{frames_seen - 1:<6}   "
              f"{rep_step:>10} {rep_rec:>4}/{len(rep) - 1:<6}")

        if batch == 1 and not (dep_step == 1 and rep_step == 1):
            control_ok = False

    print()
    if not control_ok:
        print("UNKNOWN: the batch-of-one control did not advance either sequence by exactly one,")
        print("so this harness is not reading the field it claims to read.")
        return 2

    # THE CONCLUSION IS DERIVED FROM THE ROWS, never captioned. A run whose numbers did not show a
    # cliff must not print one -- this file's neighbours have made exactly that mistake.
    cliff = [r for r in rows if r[0] >= 2 and r[2] == r[5] - 1]
    flat = all(r[4] == 0 for r in rows)
    print("WHAT THE ROWS SAY:")
    if cliff and flat:
        print(f"  The deployed queue's step equals the BATCH SIZE, so from a batch of two onward")
        print(f"  EVERY frame after the first is a gap -- {len(cliff)} of the {len(BATCHES) - 1}")
        print("  multi-post rows recovered on every single frame. This is a CLIFF, not a slope: a")
        print("  console does not degrade gently as it gets busier, it goes from no recoveries to")
        print("  recovering on every frame the moment any two posts share a flush window.")
        print("  The repo's queue advances by one at every batch size and never recovers.")
    else:
        print("  The rows do not show a uniform cliff; read them rather than this line.")
    print()
    print("WHAT THIS IS NOT: a rate. It says what happens PER COALESCED FLUSH, not how often a")
    print("flush coalesces -- `measure-live-frame-gaps.mjs` is the instrument for that, and on this")
    print("fleet it has measured zero. Both are needed: this is the severity, that is the exposure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
