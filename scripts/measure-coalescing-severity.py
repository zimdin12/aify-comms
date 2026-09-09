"""How bad does the deployed sequence defect get as a console gets busier?

THE QUESTION LEFT OPEN. `check-deployed-console-transport.py` shows the running build numbers frames
per POST, and `measure-live-frame-gaps.mjs` shows it not firing on this fleet -- zero wire gaps
across every window taken. That leaves the shape of the risk unstated: does a slightly busier console
degrade gently, or fall off a cliff? (How CLOSE the fleet runs to coalescing is a different question
and an unmeasured one: an arrival-interval margin once quoted here was withdrawn, because receiver
arrival spacing cannot bound post spacing.)

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


def _read_as_browser(seqs: list[int]) -> tuple[int, int, bool]:
    """`realtime-socket.mjs`'s rule, mirrored: wire gaps, recovery EPISODES, and whether it settles.

    THE CURSOR IS NOT LOWERED ON A REGRESSION, because the browser returns without touching
    `lastSeq`. A hand-written version of this counted a gap the browser never takes.

    AND A GAP DURING A PENDING RECOVERY IS HELD, NOT A SECOND RECOVERY. The browser sets
    `entry.resyncing`, and the next gapped frame takes `holdFrame(...)` and returns. Counting
    every gap as a recovery is the model the OBSERVER was corrected away from a round earlier,
    and two instruments in this repo disagreeing about the same consumer is itself the defect.

    THE THIRD RETURN IS THE ONE THAT MATTERS HERE. The hold ends when a CONTIGUOUS frame
    arrives. If every flush coalesces, none ever is -- so the console enters recovery and never
    settles, which is a worse outcome than a large recovery count and a more accurate one.
    """
    last = -1
    gaps = 0
    episodes = 0
    pending = False
    for seq in seqs:
        if last >= 0:
            if seq <= last:
                continue
            if seq > last + 1:
                gaps += 1
                if not pending:
                    episodes += 1
                    pending = True
                last = seq
                continue
        pending = False
        last = seq
    return gaps, episodes, not pending


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
    print(f"  {'batch':>6}   {'DEPLOYED':>12} {'gaps':>5} {'eps':>4} {'settles':>7}   "
          f"{'REPO':>9} {'gaps':>5} {'eps':>4} {'settles':>7}")

    control_ok = True
    rows = []
    for batch in BATCHES:
        dep = asyncio.run(_drive(DeployedQueue, batch, FRAMES))
        rep = asyncio.run(_drive(RepoQueue, batch, FRAMES))
        # EVERY FLUSH MUST HAVE BROADCAST. Taking the denominator from the RESULT lets a queue
        # that emitted fewer frames than asked for silently shrink its own row.
        if len(dep) != FRAMES or len(rep) != FRAMES:
            print(f"  {batch:>6}   UNKNOWN: {len(dep)} and {len(rep)} broadcasts against "
                  f"{FRAMES} flushes requested")
            control_ok = False
            continue
        # THE WHOLE RELATION, NOT ONE PAIR. `dep[1] - dep[0]` printed the first pair as "the
        # step", so a run whose steps varied would report whichever came first.
        dep_steps = {b - a for a, b in zip(dep, dep[1:])}
        rep_steps = {b - a for a, b in zip(rep, rep[1:])}
        dep_step = str(dep_steps.pop()) if len(dep_steps) == 1 else f"varies {sorted(dep_steps)}"
        rep_step = str(rep_steps.pop()) if len(rep_steps) == 1 else f"varies {sorted(rep_steps)}"
        dep_gaps, dep_eps, dep_settles = _read_as_browser(dep)
        rep_gaps, rep_eps, rep_settles = _read_as_browser(rep)
        rows.append((batch, dep_step, dep_gaps, dep_eps, dep_settles,
                     rep_step, rep_gaps, rep_eps, rep_settles))
        print(f"  {batch:>6}   {dep_step:>12} {dep_gaps:>5} {dep_eps:>4} "
              f"{'yes' if dep_settles else 'NO':>7}   "
              f"{rep_step:>9} {rep_gaps:>5} {rep_eps:>4} {'yes' if rep_settles else 'NO':>7}")

        if batch == 1 and not (dep_step == "1" and rep_step == "1"):
            control_ok = False

    print()
    if not control_ok:
        print("UNKNOWN: the batch-of-one control did not advance either sequence by exactly one,")
        print("so this harness is not reading the field it claims to read.")
        return 2

    # THE CONCLUSION IS DERIVED FROM THE ROWS, never captioned. A run whose numbers did not show a
    # cliff must not print one -- this file's neighbours have made exactly that mistake.
    # DERIVED FROM THE ROWS, never captioned. Each clause below is a predicate over what was
    # actually measured, so a run whose numbers contradict the story prints the numbers instead.
    multi = [r for r in rows if r[0] >= 2]
    every_frame_gaps = [r for r in multi if r[2] == FRAMES - 1]
    never_settles = [r for r in multi if not r[4]]
    repo_clean = all(r[6] == 0 and r[8] for r in rows)
    print("WHAT THE ROWS SAY:")
    if multi and len(every_frame_gaps) == len(multi) and repo_clean:
        print("  The deployed queue's step equals the BATCH SIZE, so from a batch of two onward")
        print(f"  EVERY frame after the first is a wire gap -- all {len(multi)} multi-post rows.")
        print("  This is a CLIFF, not a slope: nothing degrades gently, it goes from no gaps to")
        print("  every frame the moment two posts share one flush window.")
        if len(never_settles) == len(multi):
            print()
            print("  AND THE EPISODE COLUMN IS THE SHARPER READING. The browser holds gapped")
            print("  frames while a recovery is pending and releases on a CONTIGUOUS one -- which")
            print("  never arrives here. So it is not N recoveries: the console enters recovery")
            print("  ONCE and never settles, which is worse than a large count, not milder.")
        print()
        print("  The repo's queue advances by one at every batch size, gaps nothing and settles.")
    else:
        print("  The rows do not support a uniform cliff; read them rather than this line.")
        print(f"  (multi-post rows {len(multi)}, all-frames-gap {len(every_frame_gaps)}, "
              f"never-settling {len(never_settles)}, repo clean {repo_clean})")
    print()
    print("WHAT THIS IS NOT: a rate. It says what happens PER COALESCED FLUSH, not how often a")
    print("flush coalesces -- `measure-live-frame-gaps.mjs` is the instrument for that, and on this")
    print("fleet it has measured zero. Both are needed: this is the severity, that is the exposure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
