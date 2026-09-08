"""How long a broadcast frame takes to reach a connected client.

THE HOP, named before the number because every earlier transport claim in this project was retracted
for naming the wrong one: from `ConnectionManager.broadcast` being CALLED with a frame, to that
frame arriving at a connected client's receive. Hop four of the five between a producer and a
browser terminal -- the WEBSOCKET DELIVERY. Not the queue that decided to broadcast (hop three,
already measured), not the browser's own scheduling, and not the xterm write (hop five). Neither of
those is in this number and neither is claimed by it.

WHY THIS COULD NOT BE READ OFF THE LIVE SERVICE. The frame carries no server timestamp, so a browser
could only compare it against its own clock -- and the container's clock has been measured 4.1
seconds ahead of this host's before, which is larger than the whole effect. The live terminal row
cannot stand in either: `output_seq` is written on the LAZY TAIL, deliberately behind the broadcast,
so timing against it would measure the tail throttle and call it transport. So this stands up its
own service instead: the REAL `ConnectionManager`, a real uvicorn on a spare port, real WebSocket
clients, and ONE process clock covering both ends.

t0 TRAVELS INSIDE THE FRAME. The server stamps `perf_counter()` at the moment it calls `broadcast`
and puts it in the payload; the client subtracts on arrival. Same process, same monotonic clock, so
there is no skew to argue about and no second round trip to pay for.

THE LISTENER HAS ITS OWN THREAD, AND THAT IS A CORRECTION, NOT A PREFERENCE. The first working
version ran the emitter and the receiver on ONE event loop and reported a p50 of 0.25ms with a tail
of 17-30ms at every fan-out, including a single client. Moving the listener onto its own thread --
changing nothing else -- took the worst sample from 29.5ms to 1.8ms. So the tail travelled with the
SHARED LOOP and not with the transport, which is what disqualifies the first figures.

THE MECHANISM IS NOT ESTABLISHED HERE, and the first version of this paragraph claimed it was. It
said the emitter's `await asyncio.sleep(0.004)` prevented the receiver from being scheduled --
but awaiting a sleep YIELDS, so the receiver is free to run during it, and the sleep's duration
alone establishes nothing about scheduling. What the experiment supports is the association above.
That it is the third probe in this block to be corrected for measuring its own extractor is the
part worth carrying forward.

EACH BROADCAST IS MATCHED TO ITS OWN FRAME, BY MARKER, AND THE AGREEMENT IS EXACT IN BOTH
DIRECTIONS. An unknown marker, a non-finite stamp, a duplicate or a landed set that differs from the
emitted set is a REFUSAL -- counted, printed, and never aged against another sample. The first
version checked only for MISSING markers, so a frame the run never emitted became a datum; and it
admitted a stamp of NaN, which then PASSED the control's lower bound because `nan < 20.0` is False.
A finite figure is an obligation of its own, separate from being above a threshold.

THE CONTROLS ARE IN THE SAME RUN. A delivery that really is slow must show up as a slow hop or the
instrument cannot see one: the `slow` arm wraps every `send_text` in a 20ms sleep and its p50 must
exceed 20ms. The negative control is the `foreign` marker, broadcast to the same clients through the
same path, which must ARRIVE (proving the path carried it) and be CREDITED TO NOTHING.

NOTHING REACHES stdout UNLESS EVERY CONTROL HELD. A refusal printed after the rows cannot retract
them.

Run: python scripts/measure-ws-hop.py
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, WebSocket  # noqa: E402
from websockets.exceptions import ConnectionClosed  # noqa: E402
from websockets.sync.client import connect as ws_connect  # noqa: E402

from service.ws import ConnectionManager  # noqa: E402

PORT = 8897
SAMPLES = 200
FANOUTS = (1, 2, 4, 8)
EMIT_GAP_S = 0.004
LF = chr(10)


class SlowSocket:
    """A client whose every send costs `delay`. The control arm's whole content.

    It wraps a real accepted WebSocket rather than replacing it, so the manager's own `gather`, its
    snapshot iteration and its disconnect-on-exception all run exactly as they do in production --
    the only altered fact is how long one send takes.
    """

    def __init__(self, inner: WebSocket, delay: float):
        self._inner = inner
        self._delay = delay

    async def send_text(self, msg: str) -> None:
        await asyncio.sleep(self._delay)
        await self._inner.send_text(msg)

    def __eq__(self, other):
        return other is self or other is self._inner

    def __hash__(self):
        return hash(id(self._inner))


class Probe:
    """The service side: the real manager, and one route that broadcasts a stamped marker."""

    def __init__(self) -> None:
        self.manager = ConnectionManager()
        self.app = FastAPI()
        self._register(self.app)

    def _register(self, app: FastAPI) -> None:
        @app.websocket("/ws")
        async def ws_endpoint(ws: WebSocket):
            delay = float(ws.query_params.get("delay") or 0.0)
            await ws.accept()
            held = SlowSocket(ws, delay) if delay else ws
            self.manager._connections.append(held)
            try:
                while True:
                    await ws.receive_text()
            except Exception:
                self.manager.disconnect(held)

        @app.get("/emit/{marker}")
        async def emit(marker: str):
            # STAMPED AT THE CALL, not before the request and not after the await: this is the
            # instant hop four begins.
            t0 = time.perf_counter()
            await self.manager.broadcast("terminal_output", {"marker": marker, "t0": t0})
            return {"marker": marker, "clients": self.manager.active_count()}

        @app.get("/health")
        async def health():
            return {"clients": self.manager.active_count()}


class Listener(threading.Thread):
    """One client, on its own thread, so no emitter's sleep can delay what it records."""

    def __init__(self, url: str, recording: bool) -> None:
        super().__init__(daemon=True)
        self.url = url
        self.recording = recording
        self.landed: dict[str, float] = {}
        # WHAT THIS LISTENER ASKED FOR. Admission is agreement with this set, not absence from the
        # control's prefix.
        self.expected: set[str] = set()
        self.unexpected = 0
        self.negative = 0
        self.died = ""
        self.foreign_seen = 0
        self.unreadable = 0
        self.duplicates = 0
        self.ready = threading.Event()
        # NOT `_stop`: that is `Thread._stop`, and shadowing it makes close/join raise
        # "Event object is not callable" on 3.11. It happens to work on 3.14, which is how a
        # published run stayed green over a real defect.
        self.finished = threading.Event()
        self._sock = None

    def run(self) -> None:
        # WHATEVER KILLS THIS THREAD IS AN ARM-INVALID OUTCOME, and until review demonstrated it there
        # was no way for one to be reported at all. An unhashable marker raised TypeError OUTSIDE the
        # parse guard, the thread died, the run closed cleanly and published -- and if the crash
        # happens AFTER the expected frames have landed, every figure looks complete. A receiver that
        # stopped for a reason nobody recorded cannot certify what it collected.
        try:
            self._collect()
        except BaseException as failure:  # noqa: BLE001 - recorded, then refused by _account
            self.died = f"{type(failure).__name__}: {failure}"

    def _collect(self) -> None:
        with ws_connect(self.url) as sock:
            self._sock = sock
            self.ready.set()
            while not self.finished.is_set():
                try:
                    raw = sock.recv()
                except Exception as failure:
                    # A SHUTDOWN AND A FAILURE LOOKED IDENTICAL HERE, and treating them alike made
                    # the outer guard decorative: review stopped the receiver with a synthetic
                    # RuntimeError after every expected frame had landed, and the run published and
                    # returned 0 with `died` empty.
                    #
                    # ORDERLY IS TWO THINGS, NOT ONE, and my first repair only knew about the first.
                    # `close()` sets `finished` before closing the socket, so a teardown raise is
                    # expected -- but the SERVER can close the connection too, at any moment, and
                    # that is a clean end rather than a fault. Keying on `finished` alone reported
                    # every one of those as a death, which would have fired on ordinary runs and got
                    # the check switched off. So a websocket CLOSE is orderly whoever initiated it,
                    # and anything else while `finished` is clear is the receiver dying.
                    orderly = self.finished.is_set() or isinstance(failure, ConnectionClosed)
                    if not orderly:
                        self.died = f"receive failed: {type(failure).__name__}: {failure}"
                    return
                t1 = time.perf_counter()
                if not self.recording:
                    continue
                try:
                    data = (json.loads(raw) or {}).get("data") or {}
                    marker, t0 = data.get("marker"), data.get("t0")
                except Exception:
                    self.unreadable += 1
                    continue
                if marker is None or t0 is None:
                    self.unreadable += 1
                    continue
                # A MARKER IS A STRING. Anything else is a frame this run did not shape, and looking
                # one up in a set raises for the unhashable ones -- which is how a JSON list killed
                # the receiver rather than being counted.
                if not isinstance(marker, str):
                    self.unreadable += 1
                    continue
                if marker.startswith("foreign-"):
                    self.foreign_seen += 1
                    continue
                if marker not in self.expected:
                    # AN UNKNOWN MARKER IS NOT A SAMPLE. Excluding only the control's prefix let any
                    # unique string become a datum, so a frame nobody emitted was indistinguishable
                    # from one that was. The collector knows what it asked for; anything else is
                    # counted and refused.
                    self.unexpected += 1
                    continue
                if marker in self.landed:
                    self.duplicates += 1
                    continue
                try:
                    elapsed = (t1 - float(t0)) * 1000.0
                except (TypeError, ValueError):
                    self.unreadable += 1
                    continue
                # NON-FINITE IS NOT A NUMBER, and it is worse than a missing one because every
                # comparison downstream is quietly False. A stamp of 'nan' entered as a sample and
                # then PASSED the control's `>= 20ms` check, because `nan < 20.0` is False.
                if not math.isfinite(elapsed):
                    self.unreadable += 1
                    continue
                # AND NOT NEGATIVE, which is a SEPARATE obligation from being a number. A receive
                # clock behind the stamp produced -1000ms, `isfinite` said yes, and it was published
                # as a live sample beside a valid control. Nothing else downstream would have
                # objected: a negative drags a p50 down rather than raising a flag.
                if elapsed < 0:
                    self.negative += 1
                    continue
                self.landed[marker] = elapsed

    def close(self) -> None:
        self.finished.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
        self.join(timeout=5)


class Arm:
    """One fan-out, run end to end: connect, emit, collect, and account for every marker."""

    def __init__(self, clients: int, delay: float, label: str) -> None:
        self.clients = clients
        self.delay = delay
        self.label = label
        self.emitted: list[str] = []
        self.subject: Listener | None = None

    def run(self, http: httpx.Client) -> None:
        url = f"ws://127.0.0.1:{PORT}/ws" + (f"?delay={self.delay}" if self.delay else "")
        # THE FIRST CLIENT IS THE SUBJECT. A browser tab does not wait for the other tabs, so the
        # number a user feels is the delivery to ITS socket; the others are only the fan-out it
        # shares a `gather` with.
        listeners = [Listener(url, recording=(i == 0)) for i in range(self.clients)]
        for listener in listeners:
            listener.start()
        self.subject = listeners[0]
        try:
            for listener in listeners:
                if not listener.ready.wait(timeout=10):
                    raise RuntimeError("a client never finished its handshake")
            _wait_for_clients(http, self.clients)
            for i in range(SAMPLES):
                marker = f"{self.label}-{self.clients}-{i}"
                self.emitted.append(marker)
                self.subject.expected.add(marker)
                http.get(f"/emit/{marker}")
                time.sleep(EMIT_GAP_S)
            # THE NEGATIVE CONTROL, in this same arm and through this same path: a marker no sample
            # is waiting for. It must ARRIVE and be credited to NOTHING. A probe that cannot return
            # ABSENT cannot return PRESENT.
            http.get(f"/emit/foreign-{self.label}-{self.clients}")
            time.sleep(0.3 + self.delay * 4)
        finally:
            for listener in listeners:
                listener.close()

    @property
    def landed(self) -> list[float]:
        return sorted(self.subject.landed.values()) if self.subject else []

    @property
    def rejected(self) -> int:
        got = self.subject.landed if self.subject else {}
        return sum(1 for marker in self.emitted if marker not in got)


def _wait_for_clients(http: httpx.Client, expected: int) -> None:
    for _ in range(300):
        if http.get("/health").json().get("clients") == expected:
            return
        time.sleep(0.01)
    raise RuntimeError(f"only {http.get('/health').json().get('clients')} of {expected} connected")


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def _account(arm: Arm, who: str, refusals: list[str]) -> None:
    """Every way this arm could have produced a number it has not earned."""
    subject = arm.subject
    if subject is None:
        refusals.append(f"{who}: no subject client")
        return
    if arm.rejected:
        refusals.append(f"{who}: {arm.rejected} of {len(arm.emitted)} broadcasts never arrived")
    # BOTH DIRECTIONS. Missing was checked and EXTRA was not, so a sample the run never emitted
    # counted towards the figures. Set equality is the only statement that covers both.
    if set(subject.landed) != set(arm.emitted):
        only_received = sorted(set(subject.landed) - set(arm.emitted))[:3]
        refusals.append(f"{who}: the landed set does not equal the emitted set"
                        f"{f'; e.g. received-but-never-emitted {only_received}' if only_received else ''}")
    if subject.unexpected:
        refusals.append(f"{who}: {subject.unexpected} frame(s) carried a marker this run never emitted")
    if subject.negative:
        refusals.append(f"{who}: {subject.negative} sample(s) had a NEGATIVE elapsed time, so the "
                        f"receive clock ran behind the stamp and nothing here is trustworthy")
    if subject.died:
        refusals.append(f"{who}: the receiving thread died ({subject.died}), so whatever it "
                        f"collected cannot certify itself -- including if it died after the last "
                        f"expected frame arrived")
    if subject.foreign_seen == 0:
        refusals.append(f"{who}: the foreign frame never arrived, so nothing demonstrates the "
                        f"collector can decline one")
    if subject.unreadable:
        refusals.append(f"{who}: {subject.unreadable} frame(s) carried no marker or no stamp")
    if subject.duplicates:
        refusals.append(f"{who}: {subject.duplicates} duplicate frame(s) were seen")


def main() -> int:
    probe = Probe()
    config = uvicorn.Config(
        probe.app, host="127.0.0.1", port=PORT, log_level="error",
        # THE SERVER'S KEEPALIVE IS OFF for the same reason the client's is: a probe that lives for
        # seconds does not need one, and its teardown logging lands on the stream the refusals below
        # print to. Noise there is not cosmetic -- it is what a real refusal would hide behind.
        ws_ping_interval=None, ws_ping_timeout=None,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    http = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=10.0)
    for _ in range(300):
        try:
            http.get("/health")
            break
        except httpx.HTTPError:
            time.sleep(0.02)
    else:
        sys.stderr.write(f"the probe's own service never answered on {PORT}{LF}")
        return 1

    rows: list[str] = ["HOP FOUR: broadcast() called -> the frame is in a client's hands",
                       "  clients    p50 ms    p95 ms    max ms    samples"]
    refusals: list[str] = []
    arms: list[Arm] = []

    for clients in FANOUTS:
        arm = Arm(clients, 0.0, "live")
        arm.run(http)
        arms.append(arm)
        landed = arm.landed
        if landed:
            rows.append(f"  {clients:7d}  {_percentile(landed, 0.5):8.3f}  "
                        f"{_percentile(landed, 0.95):8.3f}  {landed[-1]:8.3f}  {len(landed):9d}")
        _account(arm, f"{clients} client(s)", refusals)

    control = Arm(1, 0.020, "slow")
    control.run(http)
    arms.append(control)
    control_landed = control.landed
    control_p50 = _percentile(control_landed, 0.5) if control_landed else float("nan")
    rows.append("")
    rows.append(f"  POSITIVE CONTROL, every send wrapped in a 20ms sleep: p50 {control_p50:.3f} ms "
                f"over {len(control_landed)} samples")
    _account(control, "the control arm", refusals)
    if not control_landed or not math.isfinite(control_p50):
        refusals.append(f"the control arm's p50 is not a finite number ({control_p50}), so it "
                        f"states nothing about whether a slow delivery is visible")
    elif control_p50 < 20.0:
        refusals.append(f"the control arm's p50 is {control_p50:.3f} ms, under the 20 ms it was "
                        f"delayed by -- this instrument cannot see a slow delivery")

    live = [ms for arm in arms[:-1] for ms in arm.landed]
    if not live:
        refusals.append("no live sample landed at all")

    http.close()
    server.should_exit = True
    thread.join(timeout=5)

    if refusals:
        sys.stderr.write(f"{LF}NOTHING IS PUBLISHED. A number is only a hop if its controls held:{LF}")
        for line in refusals:
            sys.stderr.write(f"  - {line}{LF}")
        return 1

    for row in rows:
        print(row)
    print("")
    print(f"Every one of {len(live)} live samples was matched to the broadcast that produced it, the "
          f"foreign frame arrived and was credited to nothing, and a 20ms send showed up as "
          f"{control_p50:.1f} ms.")
    print("WHAT THIS IS NOT: one Python client on loopback, recorded per fan-out, is not a browser "
          "tab under production load. Browser scheduling and the xterm write are hop five, and "
          "nothing here eliminates hop four for a browser.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
