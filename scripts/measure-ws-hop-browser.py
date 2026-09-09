"""Whether a REAL BROWSER keeps up with a broadcast stream, measured on the browser's own clock.

WHAT ITS SIBLING LEFT OPEN. `measure-ws-hop.py` measured hop four -- `ConnectionManager.broadcast`
called, to the frame arriving at a client -- for a PYTHON client on loopback, and its own closing
paragraph says exactly what that does not cover: "one Python client on loopback is not a browser tab
under production load", and "this does NOT eliminate hop four for a browser". This probe covers the
browser, and covers ONLY what one clock can answer.

THE NOUN, NAMED BEFORE THE NUMBER. This measures the GAP BETWEEN CONSECUTIVE FRAMES as a real Chrome
tab receives them: `performance.now()` at each `onmessage`, differenced. It is NOT a one-way latency
and must never be reported as one. A one-way figure would need the server's stamp compared against
the browser's clock, and those are two process clocks -- this project has measured a 4.1-second skew
between two clocks on one machine, three orders of magnitude larger than the effect.

WHY INTER-ARRIVAL IS THE RIGHT QUESTION ANYWAY. The operator's report is "our browser terminal kind
of lags sometimes". Sometimes is a word about VARIANCE, not about a mean: a console that renders
every frame 3ms late looks perfect, and one that renders 40 frames on time and then stalls for
300ms looks broken. A stall is a gap, and a gap is a difference of two readings of ONE clock.

THE EMISSION CADENCE IS MEASURED, NOT DECLARED, and the first run of this probe is why that
sentence is here. `EMIT_MS = 16` is a REQUEST: `asyncio.sleep(0.016)` on this host is bounded
below by the ~15.6ms timer floor this project has measured repeatedly, so the real cadence is
about 31ms. A browser compared against the CONSTANT reads as falling behind by a factor of two
while it is in fact keeping up exactly -- the probe measuring its own instrument, which is now
the fourth time in this block.

SO TWO DISTRIBUTIONS ARE PUBLISHED, each measured on ONE clock and never subtracted from the
other: the server's own inter-BROADCAST gaps from `perf_counter()`, and the tab's
inter-ARRIVAL gaps from `performance.now()`. Whether the browser keeps up is a comparison of
their SHAPES. If the tab's distribution matches the server's, every frame the server sent was
taken as fast as it was offered, and no part of the operator's lag lives in this hop.

THREE CONTROLS, ALL IN THE SAME RUN:

  POSITIVE   every emitted marker lands, exactly once, in order. Without this a browser that
             received NOTHING would report a beautiful empty distribution.
  TRANSPORT  one broadcast is held back by `STALL_MS` on the server. The tab must record a gap of at
             least that at that index -- a probe that cannot see a stall it caused itself cannot
             report the absence of one.
  BROWSER    the page blocks its own main thread for `BLOCK_MS` mid-stream. The tab must record a
             gap there too. This is the control that matters most for attribution: it proves the
             instrument reports a delay that lives in the BROWSER, which is the suspect the Python
             client could not speak to at all.

NOTHING IS PUBLISHED UNLESS EVERY CONTROL HELD. A refusal printed after the rows cannot retract them.

THE BROWSER IS NOT THE OPERATOR'S. This is driven against a headless Chrome on this host. Its
scheduling, its GPU and its lack of a hundred other tabs are all unlike the machine the report came
from, so a clean result here does not clear hop four on the operator's browser -- it establishes
what this transport does when the tab is not the problem, and gives the stall controls that say the
instrument would have seen it.

Run:  python scripts/measure-ws-hop-browser.py
      then open http://127.0.0.1:8898/ in the browser under test. The run prints when the tab
      reports, or refuses after WAIT_S if nothing does.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request, WebSocket  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402

from service.ws import ConnectionManager  # noqa: E402

PORT = 8898
#: How many frames the run emits. Enough that one stall is a small part of the distribution rather
#: than half of it, and small enough that a tab reports in a few seconds.
FRAMES = 120
#: The server's requested emission cadence, overridable as the first argument so one script can
#: run both arms. 16 is about one display frame, the rate a console repainting per frame would be
#: fed -- though this host's timer floor turns it into about 31. ZERO is the FIREHOSE arm: no
#: sleep between broadcasts at all, which is the busy-agent case a 31ms cadence says nothing
#: about, and the one where a tab that cannot keep up would show it.
EMIT_MS = float(sys.argv[1]) if len(sys.argv) > 1 else 16
#: The frame the server deliberately holds back, and by how long.
STALL_AT = 40
STALL_MS = 120
#: The frame during which the PAGE blocks its own main thread, and for how long.
BLOCK_AT = 80
BLOCK_MS = 150
#: How long to wait for a tab to connect and report before refusing.
WAIT_S = 180
LF = chr(10)

PAGE = """<!doctype html>
<title>hop four, in a browser</title>
<body style="font:14px system-ui;padding:2rem">
<h1 id="s">connecting...</h1>
<pre id="o"></pre>
<script>
// ONE CLOCK. Every number this page produces comes from `performance.now()`, read in the message
// handler. Nothing here is compared against a server timestamp: the frame's own `i` is used only
// to identify which frame arrived, never to time it.
const FRAMES = __FRAMES__, BLOCK_AT = __BLOCK_AT__, BLOCK_MS = __BLOCK_MS__;
const at = new Map();
const s = document.getElementById('s');
const ws = new WebSocket(`ws://${location.host}/ws`);
ws.onopen = () => { s.textContent = 'connected, waiting for frames'; };
ws.onmessage = (ev) => {
  const now = performance.now();
  let frame;
  try { frame = JSON.parse(ev.data); } catch { return; }
  const i = frame?.data?.i;
  if (typeof i !== 'number') return;
  // RECORDED BEFORE THE BLOCK, so the blocking frame's OWN arrival is honest and the gap lands on
  // the frame after it -- which is what a main-thread stall actually does to a stream.
  if (!at.has(i)) at.set(i, now);
  if (i === BLOCK_AT) {
    const until = performance.now() + BLOCK_MS;
    while (performance.now() < until) { /* the browser control: a busy main thread */ }
  }
  if (at.size >= FRAMES) report();
};
function report() {
  const seen = [...at.entries()].sort((a, b) => a[0] - b[0]);
  s.textContent = 'reported ' + seen.length + ' frames';
  document.getElementById('o').textContent = JSON.stringify(seen);
  fetch('/report', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ arrivals: seen, agent: navigator.userAgent }),
  });
}
</script>
"""


class Probe:
    """The service side: the REAL `ConnectionManager`, a page, and a stream with one held frame."""

    def __init__(self) -> None:
        self.manager = ConnectionManager()
        self.app = FastAPI()
        self.reported: dict | None = None
        self.emitted: list[int] = []
        #: `perf_counter()` AT EACH BROADCAST, so the emission cadence is a measurement rather
        #: than the constant that was requested. `asyncio.sleep(EMIT_MS)` is bounded below by
        #: this host's ~15.6ms timer floor, so the two differ by about a factor of two -- and a
        #: browser compared against the CONSTANT would read as falling behind when it is keeping
        #: up exactly.
        self.emit_at: list[float] = []
        self._connected = threading.Event()
        self._done = threading.Event()
        self._register(self.app)

    def _register(self, app: FastAPI) -> None:
        @app.get("/", response_class=HTMLResponse)
        async def page():
            return (PAGE.replace("__FRAMES__", str(FRAMES))
                        .replace("__BLOCK_AT__", str(BLOCK_AT))
                        .replace("__BLOCK_MS__", str(BLOCK_MS)))

        @app.websocket("/ws")
        async def ws_endpoint(ws: WebSocket):
            await ws.accept()
            self.manager._connections.append(ws)
            self._connected.set()
            asyncio.get_running_loop().create_task(self._stream())
            try:
                while True:
                    await ws.receive_text()
            except Exception:
                self.manager.disconnect(ws)

        @app.post("/report")
        async def report(request: Request):
            self.reported = await request.json()
            self._done.set()
            return {"ok": True}

    async def _stream(self) -> None:
        """Emit `FRAMES` frames through the real manager, holding exactly one of them back."""
        for i in range(FRAMES):
            # THE TRANSPORT CONTROL. Held BEFORE the broadcast, so the delay is in the server's
            # decision to send rather than inside the manager -- which is the shape a busy event
            # loop actually has, and keeps the manager itself unmodified.
            if i == STALL_AT:
                await asyncio.sleep(STALL_MS / 1000)
            self.emit_at.append(time.perf_counter())
            await self.manager.broadcast("terminal_output", {"i": i})
            self.emitted.append(i)
            # `sleep(0)` YIELDS, which is what makes the firehose arm a real fan-out rather than
            # one broadcast that never lets the socket write. The requested gap is zero; the
            # ACHIEVED gap is measured above like every other arm.
            await asyncio.sleep(EMIT_MS / 1000)

    def wait(self, seconds: float) -> bool:
        return self._done.wait(seconds)


def gaps(arrivals: list[list[float]]) -> list[tuple[int, float]]:
    """The gap before each frame, keyed by the frame it precedes."""
    return [(arrivals[k][0], arrivals[k][1] - arrivals[k - 1][1]) for k in range(1, len(arrivals))]


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


def account(probe: Probe) -> tuple[list[str], list[tuple[int, float]]]:
    """Every control, checked before a single figure is allowed out."""
    refusals: list[str] = []
    report = probe.reported or {}
    arrivals = [[int(i), float(t)] for i, t in (report.get("arrivals") or [])]

    landed = [i for i, _ in arrivals]
    if landed != probe.emitted:
        missing = sorted(set(probe.emitted) - set(landed))
        extra = sorted(set(landed) - set(probe.emitted))
        refusals.append(
            f"POSITIVE: the tab's frames are not the emitted set, in order "
            f"(missing {missing[:6]}, unexpected {extra[:6]}, "
            f"{'out of order' if sorted(landed) == sorted(probe.emitted) else 'set differs'})")
    if any(not (t == t) or t in (float("inf"), float("-inf")) for _, t in arrivals):
        refusals.append("POSITIVE: a non-finite arrival time was reported")

    measured = gaps(arrivals)
    by_frame = dict(measured)

    held = by_frame.get(STALL_AT)
    if held is None or held < STALL_MS:
        refusals.append(
            f"TRANSPORT CONTROL: a {STALL_MS}ms hold before frame {STALL_AT} was recorded as "
            f"{held!r}ms, so this instrument cannot see a stall it caused itself")

    blocked = by_frame.get(BLOCK_AT + 1)
    if blocked is None or blocked < BLOCK_MS:
        refusals.append(
            f"BROWSER CONTROL: a {BLOCK_MS}ms main-thread block at frame {BLOCK_AT} was recorded as "
            f"{blocked!r}ms on frame {BLOCK_AT + 1}, so a delay living in the BROWSER would not be "
            f"reported")

    if len(probe.emit_at) != len(probe.emitted):
        refusals.append(
            f"CADENCE: {len(probe.emit_at)} broadcast timestamps for {len(probe.emitted)} "
            f"frames, so the emission rate this run actually achieved is unknown")

    return refusals, measured


def main() -> int:
    probe = Probe()
    config = uvicorn.Config(probe.app, host="127.0.0.1", port=PORT, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    time.sleep(1.0)

    print(f"open http://127.0.0.1:{PORT}/ in the browser under test", flush=True)
    if not probe.wait(WAIT_S):
        print(f"REFUSED: no tab reported within {WAIT_S}s", flush=True)
        server.should_exit = True
        return 1

    refusals, measured = account(probe)
    server.should_exit = True
    if refusals:
        print("NOTHING IS PUBLISHED:")
        for line in refusals:
            print(f"  - {line}")
        return 1

    # THE CONTROLLED FRAMES ARE EXCLUDED FROM THE DISTRIBUTION and said so. They are delays this run
    # CAUSED; leaving them in would report the instrument's own arms as transport behaviour.
    ordinary = [ms for frame, ms in measured if frame not in (STALL_AT, BLOCK_AT + 1)]
    # THE SERVER'S OWN CADENCE, over the same frames, so the two columns are like for like. The
    # held frame is excluded from BOTH; the main-thread block exists only on the browser side and
    # is excluded only there, which is why the two counts differ by one.
    server = [(i, (probe.emit_at[i] - probe.emit_at[i - 1]) * 1000)
              for i in range(1, len(probe.emit_at))]
    server_ordinary = [ms for frame, ms in server if frame != STALL_AT]
    print()
    print("HOP FOUR IN A BROWSER. Two distributions, each measured on ONE clock and never")
    print("subtracted from the other. Whether the tab keeps up is whether their shapes agree.")
    print()
    print(f"  {'':22} {'server broadcast':>18} {'tab onmessage':>16}")
    print(f"  {'gaps measured':22} {len(server_ordinary):>18} {len(ordinary):>16}")
    for label, q in (("p50", 0.5), ("p95", 0.95)):
        print(f"  {label:22} {percentile(server_ordinary, q):>15.2f} ms "
              f"{percentile(ordinary, q):>13.2f} ms")
    print(f"  {'worst':22} {max(server_ordinary):>15.2f} ms {max(ordinary):>13.2f} ms")
    print(f"  {'over 50ms':22} {sum(1 for ms in server_ordinary if ms > 50):>18} "
          f"{sum(1 for ms in ordinary if ms > 50):>16}")
    # WHAT THE TAB CAN EVEN SEE. `performance.now()` is CLAMPED in Chrome, so on the firehose arm
    # the gaps land at or below its granularity and a p50 of 0.00 is the CLOCK, not a measurement.
    # The smallest non-zero gap observed is that granularity, read off this run rather than
    # remembered from a specification -- so a reader can tell a real zero from an unreadable one.
    finest = min((ms for ms in ordinary if ms > 0), default=float("nan"))
    print(f"  {'clock granularity':22} {'':>18} {finest:>13.2f} ms"
          "   <- the tab's smallest non-zero gap; a p50 at or under this is a CEILING")
    print()
    print(f"  asked for {EMIT_MS:g}ms between broadcasts and ACHIEVED a median of "
          f"{statistics.median(server_ordinary):.2f}ms."
          + (" This host's timer floor, not a choice." if EMIT_MS else ""))
    print()
    print(f"CONTROLS, both held: a {STALL_MS}ms server hold read as "
          f"{dict(measured)[STALL_AT]:.0f}ms; a {BLOCK_MS}ms main-thread block read as "
          f"{dict(measured)[BLOCK_AT + 1]:.0f}ms.")
    print(f"  tab       {json.dumps((probe.reported or {}).get('agent', ''))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
