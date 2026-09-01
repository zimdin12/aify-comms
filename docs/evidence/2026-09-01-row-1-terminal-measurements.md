# Row 1 measured: what the terminal path costs, and what blocks the obvious fixes — 2026-09-01

Two questions the roadmap row turns on, both answered by measurement rather than reading. Neither
answer is a fix: both end at a decision with a real cost, which is why they are written down instead
of acted on.

## 1. The write path amplifies output by about 870x at the real chunk size

`service/api_core/terminal_output.py` keeps a 64KB **tail** in one column and rewrites the whole
column on every flush. So the bytes written per flush do not depend on how much output arrived.

Measured by driving the real writer with a recording db, buffer pre-seeded at the cap:

| chunk | measured | predicted (65536 / chunk) |
|---|---|---|
| 64 B | 1023.5x | 1024.0x |
| 256 B | 255.8x | 256.0x |
| 1 KB | 63.6x | 64.0x |
| 4 KB | 15.5x | 16.0x |
| 16 KB | 3.5x | 4.0x |

**The first run of this was wrong and is worth recording.** Driving 400 flushes of 64 B from an EMPTY
buffer reported 200x, not 1024x — because 400 x 64 = 25,600 B never reaches the 65,536 B cap, so it
measured the buffer FILLING rather than steady state. The control is the same chunk size run from an
EMPTY buffer, which must amplify strictly less than from a full one: 100.5x against 1023.5x. Two runs
that agree there mean the seeded run was not seeded.

### The real chunk is 75 bytes, not 16 KB

`TerminalOutputWriteQueue.max_batch_chars` is `16 * 1024`, which invites the assumption that flushes
are large. They are not. `idle_flush_ms` is 4 and `max_latency_ms` is 24, so a flush carries whatever
arrived in a few milliseconds.

Measured from the LIVE database — `terminal_events` holds one row per flushed chunk, 5,329 of them:

    min 3   p25 65   median 75   p75 80   p90 136   p99 2000   max 2000   mean 131.6

2.7% (143 of 5,329) reach the 2000-char cap the event body is truncated at, so the true mean is
higher than 131.6 by an unknown amount, bounded above by the 16 KB batch cap. That puts production at
the TOP of the table above, not the bottom.

### Why the cheap fix is not available

The obvious remedy is to write the durable tail less often and let the live screen
(`_feed_live_terminal_screen`, already in the path) serve reads in between. That is not a tuning
change. **Eight modules touch `terminal_sessions.output` directly** -- seven readers plus the writer
itself, which reads the current value to concatenate onto it -- and two of the readers are on the
status path:

* `service/api_core/status_inputs.py`
* `service/reconcilers/terminal_runs.py` — parses the stored tail for idle-prompt hints

A ninth, `service/sse/console_tools.py`, reads an `output` field too but takes it from the
`GET /agents/{id}/console` RESPONSE rather than the column. It is named here because the first
count said nine and included it as a direct reader; the distinction does not change the
conclusion, and a number published without checking what it counted is the habit this file is
otherwise arguing against.

Making that column stale by a second makes the input that decides whether an agent is idle stale by a
second. That is the flapping this repo spent months removing.

**Gated, not fixed:** `service/tests/test_the_terminal_write_path_stays_cheap.py` pins one flush at
one UPDATE and one INSERT, keeps the event prune amortised, and asserts the amplification
relationship — so whoever fixes it is told by a red test rather than having to notice.

## 2. The `/ws` Origin check cannot authorise terminal input, by design

The row wants dashboard pseudo-terminal INPUT. Today `service/main.py`'s `/ws` handler ends at

    await ws.receive_text()  # Keep alive, ignore client messages

so the socket is read-only from the client's side. The handshake guard is present, unconditional, and
runs before the key check.

**It also admits any caller that sends no Origin, on purpose.** `websocket_origin_is_allowed` says so
in its own docstring: "no Origin means not a browser, and those callers — bridges, CLIs, tests — are
what this endpoint exists to serve."

Proven against the running service, with a control:

| handshake | result |
|---|---|
| no `Origin` at all (a program) | **101 Switching Protocols** |
| `Origin: http://evil.example` | 403 Forbidden |
| `Origin: http://localhost:8800` | 101 Switching Protocols |

The hostile origin being refused is what makes the first row evidence rather than a broken probe.

### What that means for the row

The requirement is NOT "preserve the Origin check" — it is present and working. It is that the Origin
check **cannot be the gate for input**, because omitting Origin is the documented way non-browser
clients connect. With no `API_KEY` configured (the default) and the port published on `0.0.0.0`, any
program that can reach it opens this socket today. That is currently bounded to READING, because the
handler discards frames.

Accepting keystrokes on that socket under the current handshake policy would let the same program
type into a live agent's terminal. So terminal input needs its own authorisation — per connection and
per terminal — rather than inheriting the handshake's.

This is the same root as the open ownership-authentication defect: the service authenticates
MEMBERSHIP and has no notion of authority to act on a particular agent's behalf. A shared `API_KEY`
does not close either one.

## What is not claimed here

Neither measurement says what the remedy should be. The write path costs what it costs because its
readers want a fresh tail; the socket admits programs because programs are its purpose. Both are
design decisions with a stated cost, and the numbers above are what a decision needs, not a
substitute for one.
