# Terminal transport: what carries keystrokes and output, and what it costs

2026-09-20. Written after the operator's Herdr panes lagged and scrambled typed text under load
(fixed in aify-env `98324e8`), and their question: pipes, one persistent connection, or both, and
what happens to the machine when many agents run.

**The rule this plan is built on: work happens when somebody is watching.** A transport that is
cheap per keystroke but runs a renderer for an unwatched agent costs more than the transport ever
saves. Every decision below is read against that.

## The four paths, as they are today

| Path | Input | Output | Where it runs |
|---|---|---|---|
| **A. Herdr pane** | keys -> `aify-env attach` -> HTTP POST per chunk -> daemon -> PTY | daemon -> SSE -> stdout, untouched | Both ends on one host |
| **B. Dashboard console** | browser -> aify-comms HTTP -> terminal owner | PTY -> service -> WebSocket -> xterm.js | Browser to container; PTY on the host |
| **C. Managed delivery** | messages, not keystrokes | service reads console output | Container and host |
| **D. Agent tools** | MCP stdio, one process per session | same | Host |

Only A and B carry keystrokes. C and D are not terminals and are out of scope here.

## Measured, 2026-09-20, this host, idle, Node 22, 300 iterations each

Per keystroke, one round trip unless stated (`scratchpad/bench.mjs`, rerun before quoting):

| Transport | ms per keystroke | vs `fetch` |
|---|---|---|
| `fetch` POST (what path A used) | 0.390 | 1x |
| HTTP keep-alive POST, one socket | 0.216 | 1.8x faster |
| Named pipe, write and wait for an ack | 0.025 | 16x faster |
| Named pipe, write only (the stream carries order) | 0.0002 | ~2000x faster |

**Read these as ceilings, not as the problem.** A human types at most ~10 keys per second, so even
the slowest row costs 4 ms per second of typing, or 0.4% of one core. The defect the operator felt
was never the per-key cost: it was that keystrokes were sent **concurrently**, so under load they
arrived out of order. That is fixed by ordering (aify-env `98324e8`), not by a faster transport.

**What load does to these numbers is the open question.** They were measured on an idle machine. The
same benchmark run while the fleet is busy is the measurement that decides step 2 below, and it has
not been taken.

## Where the cost actually is: output, not input

One agent printing a full-screen redraw at 30 frames per second moves ~200 KB/s. That is the stream
every consumer pays for, and aify-comms has already paid this bill twice:

- `_LiveScreen.render` ran a full ANSI render **per output chunk, per agent**, and put the service at
  119% CPU with consoles lagging seconds. Fixed with a generation-keyed cache and plain text for the
  checks that did not need a render: 119% -> 38-50% CPU, keystroke-to-claim 3,000 ms -> 5-18 ms
  (v0.6.13).
- Same-value writes made one open tab refetch 84 times per 25 s (v0.6.14).

So the plan's first duty is to keep per-agent cost near zero when nobody is looking, and its second
is to make the watched path cheap.

**What each tier does today, checked rather than assumed:**
- **aify-env** keeps a capped replay buffer and a listener set per process (`lib/runner.mjs`). A
  screen emulator is built when a subscriber needs a checkpoint, not continuously. Unwatched agents
  are already close to free.
- **aify-comms** stores console output per terminal and renders on demand with the v0.6.13 cache.

## The plan

### Step 1 — done. Order without a protocol change

`InputSender` keeps one request in flight and coalesces what is typed meanwhile (aify-env
`98324e8`). Fast typing costs one request per round trip instead of one per key, so at 0.39 ms per
request a burst of 10 keys costs ~0.4 ms of wire time, not 3.9 ms. This alone removed the reported
defect. Everything below is an improvement on a working system.

### Step 2 — DONE in aify-env 0.6.6 (`1b6da12`), on the operator's decision

Built rather than deferred: the operator asked for it, and the shape is the one described below.
What shipped: the daemon listens on a named pipe (Windows) or a unix socket (elsewhere) IN ADDITION
to HTTP; the address is advertised on `/health`; `aify-env attach` prefers it and falls back to HTTP
when it cannot connect, when this host switched it off, or when the socket dies mid-session; the
socket carries input and resize only, never the whole API; a unix socket is chmod 0600 and a Windows
pipe inherits the creating token's DACL. The switch is `transport.localSocket` in
`~/.aify/config.json`, default true, written once at install and never over an operator's own value.

**PASSES IN TESTS, NOT YET PROVEN ON THIS HOST.** 1917 tests including a real pipe carrying a
keystroke into a real process's stdin, with an unknown-process refusal as the control. The running
daemon is older code, so nothing has yet typed through a socket on this machine: that happens when
the operator restarts aify-env, and the proof is a pane whose `/health` reports an address and whose
typing still works when the address is removed.

### Step 2 as originally scoped — a local socket for path A, when measurement justifies it

`aify-env attach` and the daemon are on the same host, always: the pane is opened by the daemon
itself. That makes a local socket available, and Node's `net` speaks it with the same API as TCP.

**Do it when** a busy-machine rerun of the benchmark shows per-keystroke cost above ~5 ms, or the
operator reports lag again after step 1. Not before: 0.39 ms on an idle machine is not a problem to
solve, and a second transport is a second thing to keep working.

**Shape.** The daemon listens on a local socket *in addition to* HTTP, never instead of it. The
client prefers the socket and falls back to HTTP when it cannot connect, which is what keeps every
remote and cross-namespace case working. Input frames are length-prefixed; order comes from the
stream, so no acknowledgement is needed per keystroke and the write-only row above applies.

**Platform support** (the operator's question):

| Platform | Local socket | Note |
|---|---|---|
| Windows | Named pipe, `\\.\pipe\aify-env-<instance>` | Measured above on this host. Node supports it through the same `net` API |
| macOS, Linux | Unix domain socket under the runtime dir | Same code path; the address is a file path |
| WSL to Windows | **No.** TCP only | A Linux process cannot open a Windows named pipe, and WSL2 is a separate kernel. This is why HTTP stays the default rather than a fallback nobody exercises |
| Another PC | **No.** TCP only | Attaching across machines is a real case; a socket cannot serve it |

Permissions matter and differ: a Windows pipe needs an ACL limiting it to the owning user (aify-env
already has `lib/windows-acl.mjs`), and a Unix socket needs mode 0600 in a private directory. A
socket anyone on the machine can open is a keystroke injection channel into an agent's terminal.

**Herdr is unaffected.** Herdr runs `aify-env attach` in a pane and never sees the transport under
it. Nothing about pipes or sockets changes how panes are opened, closed or laid out.

### Step 3 — one connection for path B, at the same time as an SSE resync

The browser cannot use a local socket, so its options are what it already has: a WebSocket, which
aify-comms already runs for change events. Input over the existing socket removes one HTTP request
per keystroke and gives ordering by construction, exactly as step 2 does for path A.

**Do it when** the console input path is next opened for another reason, not as its own project. The
current cost is one keep-alive HTTP request per key (~0.2 ms server-side), which is invisible next to
the render cost that v0.6.13 addressed.

### Step 4 — the standing rule: nothing runs for an unwatched agent

This is the part that decides whether many agents clog the machine, and it outranks steps 2 and 3.

1. **No subscriber, no work beyond capture.** Keep raw bytes in a bounded buffer; build screens,
   diffs and frames only while a consumer is attached. Both tiers do this today; a test in each
   should pin it, because it is the property that silently regresses.
2. **Bound every buffer.** A per-process replay buffer with a byte cap, dropped from the head. Memory
   per idle agent must be flat, not a function of uptime.
3. **Coalesce on the producing side.** Frames merged to the consumer's refresh interval, not the
   PTY's write rate. One 30 fps redraw storm must not become 30 messages per second per viewer.
4. **Backpressure over dropping.** A slow consumer gets a coalesced snapshot, never a queue that
   grows without limit; the producer never blocks the PTY.
5. **Measure per agent, not in total.** The figure that matters is idle CPU per additional agent. It
   is the number to quote when the fleet grows, and nothing has measured it yet.

## What would prove each step

- **Step 2:** the same benchmark, rerun while the fleet is busy, plus a typing-latency measurement in
  a real pane. A socket that is faster in a micro-benchmark and not in a pane is not worth a second
  transport.
- **Step 3:** input-to-echo time in the browser console, before and after, on a busy machine.
- **Step 4:** idle CPU and memory with N agents running and zero consoles open, for N = 1, 5, 20,
  with the profiler naming the top frames. If per-agent idle cost is flat, the fleet scales; if it is
  linear in output rate, the renderer is running for nobody.

## Decided by the operator, 2026-09-20

**Build order: the pipe for aify-env first, then the dashboard's connection.** Both in the current
tag.

**1. Local socket for `aify-env attach` (Herdr panes).**
- On by default, with HTTP as the fallback that keeps WSL, other machines and any unsupported
  platform working. The operator's words: "http is like oldschool fallback".
- The switch lives in a HOST config file, `~/.aify/config.json`, because no such file exists today:
  `~/.aify` holds the service REGISTRY (`services.json`), credentials and logs, and aify-env itself
  has no config at all, only environment variables. A transport preference is a property of the host,
  not of a service entry, so it does not belong in the registry.
- `{ "version": 1, "transport": { "localSocket": true } }`, written by install, overridable per run by
  an environment variable. A file that cannot be read means the default, never a refusal to start.
- Output stays on the existing stream. This is the INPUT path only, which is where ordering and
  latency live.

**2. One connection for the dashboard console, opened only while a console is visible.**
- Open when the operator switches to a terminal view, close when they leave it or switch agents.
- The operator's reasoning, which is correct on both counts: a connection that exists only while
  somebody is looking is the safest (nothing can arrive for a console nobody has open) and the
  cheapest (it is the "nothing runs for an unwatched agent" rule applied to input).
- A setting in the aify-comms settings page, so it can be turned off without a deploy.

## Two operator ideas, judged

**A spawn menu in the ordinary `herdr-aify` resident launcher, fed by aify-comms: not worth
building.** aify-env's TUI already has the picker, and `herdr-aify env` already runs it. A second
menu in the launcher tier would duplicate a picker AND add a dependency from the launcher to
aify-comms' agent list, which is the boundary the three-repo split exists to keep. The cheap version
of the same want: a key that opens a new Herdr pane running the aify-env TUI, so the picker is one
keystroke away from any pane. That is a pane-open call, not a feature.

**Marking which managed agents get a pane, and reconciling panes to that mark: worth building.**
It is the operator's own design and it is sound:
- a per-agent mark, `pane: yes | no`, owned by aify-env, because aify-env owns panes;
- the pane opener reads the mark when a worker starts, so a background agent never opens one;
- a refresh action that reconciles the panes that ARE open against the marks, closing and opening
  only where they differ, so nothing is destroyed that already matches.
It also pays for itself in load: each pane is an attached client with a live output stream, so an
agent with no pane costs nothing to watch. Sequencing: after the two transport items, because it
touches the same opener and the same TUI.

## What this plan does not do

- It does not replace HTTP. HTTP is the portable path, the remote path and the one that works from
  WSL and another PC.
- It does not add a transport setting for the operator. The client prefers the fastest transport it
  can open and says which one it used; a switch is for when there are two worth choosing between.
