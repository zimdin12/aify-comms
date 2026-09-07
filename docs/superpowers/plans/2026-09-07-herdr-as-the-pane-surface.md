# herdr as aify-env's pane surface

**SEQUENCED BEHIND v0.6.3.** Fixes ship first; this is v0.6.4. See
[2026-09-07-v0.6.3-fixes-then-v0.6.4-herdr.md](2026-09-07-v0.6.3-fixes-then-v0.6.4-herdr.md), which
also carries the three operator revisions of 2026-09-07 (the locked pane runs `aify-env` rather than
`aify-env tui`; herdr's own sidebar is the picker; the measured performance budget).

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

## A PANE PER AGENT IS OUT. Measured 2026-09-07.

The first sketch opened a pane for every managed process. **An idle Node process on this host is
48 MB**, and each pane runs one `aify-env attach` client:

| fleet | attach clients | floor |
|---|---|---|
| 3 agents | 3 | ~144 MB |
| 21 agents (this project's documented fleet size) | 21 | **~1 GB**, plus 21 SSE streams and 21 PTY fan-outs |

That is not a tuning problem, it is the wrong design. It is also unusable: twenty panes is not a
view, and herdr's own model does not work that way either -- its sidebar lists agents while panes
show the one or two you are working on.

**So the reconciler is asymmetric, and the asymmetry is the whole idea:**

- **CLOSING IS AUTOMATIC AND ALWAYS ON.** A pane whose process is gone must go. It costs nothing, it
  is the half that removes ghosts, and it is pure state reconciliation.
- **OPENING IS ON DEMAND.** You pick an agent and a pane opens. Nothing opens by itself.
- **ONE EXCEPTION, LATER AND OPT-IN: ATTENTION.** herdr derives `blocked` from the pane's own screen.
  Auto-opening a pane for an agent that is *waiting on a human* is rare, and it is the only case
  where a pane appearing unasked is a help rather than an ambush. Stage 3 at the earliest.

**THE PICKER ALREADY EXISTS.** `aify-env tui` grew `g` -> filter -> Enter in September 2026. That is
exactly the selection surface this needs: select an agent in the status pane, and a herdr pane opens
for it. No new UI, and the keyboard work already done pays for itself here.

**AND THIS DISSOLVES THE `--shared` CAVEAT.** Nothing opens automatically, so a `--shared` session you
are already driving can never get a second pane fighting you for its keyboard. Recording the process
ORIGIN (`claimed` vs `run`) drops from a blocker to a nicety -- worth doing so the picker can mark
"you are already attached to this one", not worth blocking on.

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

## Installing it: every repo installs its own, and points at the next

The operator's rule, 2026-09-07: *"each repo is responsible for installing its own components, if
something external is needed then that external thing should have its own installation instructions
that agent can follow."* This chain already matches where `docs/TARGET_ARCHITECTURE.md` was heading
(two installers: backend is docker compose, frontend is aify-env plus aify-wrapper).

```
aify-comms install.sh
   └─ "do you want the client side on this machine?"  -> aify-env's OWN install guide
aify-env install
   └─ "do you want herdr as the pane surface?"        -> herdr's OWN installer
                                                         https://herdr.dev/install.sh (PowerShell on Windows)
```

Nobody installs anybody else's product. aify-comms does not `npm i -g aify-env`; it names the guide
and the agent follows it. aify-env does not curl herdr; it names herdr's installer.

**`aify-env herdr` EXISTS WHETHER OR NOT HERDR DOES.** A command that is missing when the thing it
needs is missing teaches nothing. With no herdr on PATH it says so, in one line, with the install
command -- the same shape as `--shared` refusing without aify-env (`exit 69`, "Install it, or run
without `--shared`"). Never silently do nothing, and never install something on the operator's behalf.

**It is a subcommand, not a fourth binary.** `aify-env` already refuses a fourth name on PATH: one
product, one command, subcommands underneath. `aify-env herdr` sits beside `tui`, `attach`, `doctor`,
`run` and `credential`.

## Stages

**Stage 0 — PROBE. Half a day. Nothing else is worth planning until this answers.**
*Partly de-risked already: the operator ran `aify-env attach sc-coder` against the live fleet on
2026-09-07 and it worked. That proves the client and one PTY layer. herdr is NOT installed on this
host, so the second layer is still unproven and Stage 0 still gates everything.*
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

## What `--shared` actually is, since it keeps coming up

Not "managed with a PTY" — a managed worker has a PTY too. The wrapper and the hosting are identical;
what differs is **who asked** and **what aify-comms thinks it is**:

| | managed | `--shared` resident | plain resident |
|---|---|---|---|
| who starts it | aify-comms spawn request, claimed by aify-env | the human, at a shell | the human, at a shell |
| who hosts the process | **aify-env** | **aify-env** (`aify-env run`) | the operator's shell |
| PTY owner | aify-env | aify-env | the terminal |
| a human attached | no | **yes, from the start** | yes, it IS the terminal |
| survives closing the window | yes | yes | **no** |
| in `/processes`, the TUI, this plan | yes | **yes** | no |
| `AIFY_SESSION_MODE` | managed | resident | resident |

**THE OPERATOR'S SIMPLIFICATION IS THE RIGHT ONE** (2026-09-07): *"--shared is functionally same as
managed. it is hosted by our managing software (aify-env). but it has one more interaction point."*
Exactly — and the equivalence is literal: `claude-aify --shared` is the same end state as starting
aify-env, spawning a managed agent, and running `aify-env attach` on it. Same host, same PTY
ownership, same survival of a closed window. The table above splits hairs the hosting does not.

So `--shared` is a RESIDENT identity with MANAGED-style hosting. That is why it is in scope here, and
why an auto-opening pane would have collided with the operator's own keyboard.

## What this plan deliberately does not do

**It does not write a herdr plugin.** herdr has one (`plugin.link`, `plugin.enable`,
`plugin.action.invoke`), and using it would invert control: herdr would drive us. aify-env is already
the long-lived daemon that knows the instant a process starts or dies. It should be the **client**.

**It does not build panes, splits, tabs or mouse in aify-env.** That was the whole point.
