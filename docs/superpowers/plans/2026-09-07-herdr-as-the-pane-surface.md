# herdr as aify-env's pane surface

**Status:** planned, nothing built. **Owner decision taken 2026-09-07:** do not build a multiplexer;
drive one. **Blocked on Stage 0**, which is half a day and settles whether the rest is worth doing.

The operator's ask, verbatim: *"could we build herdr plugin in aify-env or some herdr-aify as managed
agents control surface? it should basically auto open and close these aify-wrappers that are spawned
or killed etc. so that i would not have to do the attachments."*

---

## Why this instead of building panes

`aify-env` grew a keyboard, a picker and an activity mark in September 2026, and an independent
review found five rendering defects in the result — width counted in bytes, height not counted at
all, a clip that cut escape sequences in half. Every one of them is a terminal-emulation problem that
terminal libraries already solve.

[herdr](https://github.com/herdrdev/herdr) (Apache-2.0, Rust, 36k stars, keywords
`terminal / tui / ai / agents / multiplexer`) solves them by standing on three:

```toml
crossterm    = "0.29"      # raw mode, events, resize, cross-platform
ratatui      = "0.30"      # layout and rendering
portable-pty = "=0.9.0"    # WezTerm's PTY crate — ConPTY on Windows; VENDORED and pinned
```

It hand-rolls none of it, and it is Windows-native. Writing a pane manager inside aify-env means
writing a worse herdr, in a weaker language for this job, against the operator's own architecture
rule about not reimplementing what has an owner.

**The earlier objection was wrong and is retired.** "tmux does not run on Windows" is true of the
tmux binary and irrelevant: the capability is native through ConPTY, and herdr, WezTerm and Zellij
all ship it. The real choice was never *build* versus *depend on tmux* — it was **hand-roll versus
stand on a library**, and this plan is the second.

## The shape: aify-env HOSTS, herdr DISPLAYS

```
  agent process ──PTY──▶ aify-env ──SSE──▶ aify-comms dashboard (browser)
                            │
                            └──▶ herdr pane running `aify-env attach <id>`
```

**aify-env keeps every PTY.** This is not a preference:

- The dashboard streams those PTYs. If herdr owned them the browser would need `pane.read` polling,
  which is strictly worse than the SSE path that exists.
- aify-env's contract with aify-comms — claim a spawn, run it, stream it, carry input, resize, stop —
  is a contract about owning processes. Handing that to herdr forks the architecture.

**herdr owns only the client terminal.** No double ownership: aify-env owns the agent PTY, herdr owns
the terminal that is looking at it. The pane runs `aify-env attach`, which already forwards resize
(`bin/aify-env-attach.mjs:125-129`).

## What the API gives us

Read from herdr's published schema (`docs/next/api/herdr-api.schema.json`, `schema_version: 1`,
161 commands). Everything the ask needs is first-class:

| need | call |
|---|---|
| open a pane running a command | `plugin.pane.open`, `pane.split`, `tab.create` |
| close it when the worker dies | `plugin.pane.close`, `pane.close` |
| reconcile against reality | `pane.list`, `pane.get` |
| label the pane with OUR agent id | `pane.report_agent`, `pane.report_agent_session`, `pane.rename` |
| jump to an agent | `pane.focus`, `plugin.pane.focus` |
| notice a human closed one | `events.subscribe` → `pane_closed`, `pane_exited` |
| handshake / liveness | `ping` |

**Two things arrive free that we could not build.** `IntegrationTarget` already names
`claude, codex, hermes, pi, omp` — our exact runtimes — and herdr derives `AgentStatus`
(`idle / working / blocked / done`) from the pane's own screen. `blocked` is the state
`lib/activity.mjs` deliberately refuses to compute, because deciding it needs a model of what a
claude confirmation dialog looks like, and the operator ruled that knowledge out of this tier on
2026-09-03. Through herdr it is computed by a tier whose job it is, and aify-env still never learns
what a dialog looks like.

**Transport:** `interprocess::local_socket` (`src/ipc.rs`) — a Unix socket on POSIX, a **named pipe on
Windows**. Node's `net.connect({ path })` speaks both, so this needs no native module and **aify-env
stays zero-dependency** (`node-pty` remains its only, optional, dep).

## Design rules, each paid for by a scar in this repo

1. **RECONCILE, DO NOT REACT.** Converge aify-env's process list onto `pane.list` on a loop. Events
   are an optimisation that makes the loop prompt; they are never the authority. A missed
   `pane_closed` must not leave a ghost pane for the life of the process — that is
   `state-based-cleanup-over-event-based`, and this repo has paid for it twice.
2. **OPTIONAL, NEVER REQUIRED.** No herdr, an old herdr, an unreachable socket: today's behaviour,
   unchanged. Same posture as the advertising handshake — a literal positive answer or we do it
   ourselves.
3. **RESPECT A HUMAN'S CLOSE.** If the operator closes a pane, do not reopen it. Mark that process
   unsurfaced until it restarts. A feature that fights the operator gets switched off, and takes the
   useful half with it.
4. **A SURFACE SEAM, NOT THE SERVICE SEAM.** aify-env's plugin seam is where a *service* is named
   (aify-comms, aify-dashboard, aify-project-graph). herdr is a *display surface* — a different axis.
   It belongs in `lib/surfaces/`, with herdr as the first implementation, so the two cannot couple.
5. **THE SURFACE NAMES NO SERVICE.** It knows a process: id, label, service string. It must not know
   what aify-comms is. The operator's standing constraint.
6. **PIN THE SCHEMA VERSION.** herdr is moving fast. Read `schema_version` on connect and refuse
   loudly on a mismatch rather than sending commands blind at a changed API.
7. **PROVE BOTH ENDS.** A pane we open that herdr never shows, or a herdr pane we never close, is
   the carrier-mismatch defect that produced four separate findings on 2026-09-06. Drive a real
   socket, or a fake built from the published schema — never a fake built from what we assume.

## Risks to measure before building, not after

- **TWO PTY LAYERS.** agent PTY → `attach` client → herdr pane PTY. Resize must survive both hops and
  bytes must not be re-interpreted on the way. `attach` forwards resize; through herdr it is
  unproven. **This is Stage 0's real question.**
- **herdr's layout restore.** It brings panes back after a machine restart. Those panes would rerun
  `aify-env attach <id>` for processes that no longer exist. Attach must fail cleanly and the
  reconciler must close them.
- **Short-lived workers.** A spawn that dies in two seconds must not leave a pane, and a pane opened
  before the process streams shows an empty box.
- **Windows named-pipe semantics from Node** — reconnect, half-close, backpressure. Probe, do not
  assume.
- **Someone else's roadmap.** This adds a second optional third-party API to the host tier. 36k
  stars, Apache-2.0 and a published schema is about as good as that gets; it is still a dependency.

## Stages

**Stage 0 — PROBE. Half a day. Nothing else is worth planning until this answers.**
A throwaway Node script in the scratchpad: find the socket, connect, `ping`, `pane.list`, then open
one pane running `aify-env attach <id>` against a THROWAWAY process and resize the window. Answers:
does the transport work from Node on Windows, what is the framing, does resize survive two PTY
layers, and is the pane readable. If resize does not survive, this plan stops here and the answer is
the documented recipe instead.

**Stage 1 — READ-ONLY SURFACE.** `lib/surfaces/herdr.mjs`: connect, subscribe, and REPORT what it
*would* open and close. No panes touched. Proves the reconciliation against a live herdr at zero risk
to the fleet — the same shape as every doctor check here.

**Stage 2 — OPEN AND CLOSE.** The reconciler acts. Behind `AIFY_HERDR=1`, default off. This is the
operator's ask delivered: panes appear when a worker spawns, vanish when it dies, no attaching by
hand.

**Stage 3 — LABEL AND FOCUS.** `pane.report_agent` so herdr's sidebar shows our agent ids and its own
status marks; `aify-env focus <agent>` as a one-liner over `pane.focus`.

**Stage 4 — THE SHRINK, a product decision.** With herdr surfacing panes, does aify-env's built-in
view shrink to a *status* pane — the list, the marks, the doctor panel — and stop growing toward a
multiplexer? Everything except that status view composes with herdr for free. Recommendation: yes.

## What this plan deliberately does not do

**It does not write a herdr plugin.** herdr has one (`plugin.link`, `plugin.enable`,
`plugin.action.invoke`), and using it would invert control: herdr would drive us. aify-env is already
the long-lived daemon that knows the instant a process starts or dies. It should be the **client**.

**It does not build panes, splits, tabs or mouse in aify-env.** That was the whole point.
