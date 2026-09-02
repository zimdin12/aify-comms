# Real separation: aify-env is a general host, aify-comms is a consumer

Design settled with the operator 2026-09-02, after `/spawn` refused six spawns on a host running a
healthy aify-env and the reason turned out to be architectural rather than operational.

## The operator's framing, which is the design

> *"aify-env should own many things: spawning processes, hosting etc. aify-env is a general thing,
> not actually for aify-comms. it is more like interface for aify-comms to use so it could spawn
> processes that connect with aify-comms... this abstraction simplifies all components + it gives me
> opportunity to develop other aify- services on aify-env and aify-wrapper."*

> *"aify-wrapper is general wrapper for our aify-services."*

So: **aify-env hosts. aify-wrapper launches. aify-comms is one service that uses both.** A second
`aify-` service should need no change to either.

## What was actually wrong

`TARGET_ARCHITECTURE.md` says the `aify-comms` command goes when Phase 8 flips, and Phase 8 is marked
DONE 2026-08-25. That was true of spawn EXECUTION. It was not true of spawn CLAIMING, and claiming is
the half that makes the command necessary -- `POST /spawn-requests/claim` is served by the bridge's
`runSpawnLoop`, and claiming a request makes the claimer that agent's DELIVERY HOST via
`REMOTE_AGENT_STATE` and `dispatch-loop.mjs`, not merely its starter.

So the removal condition read as met for eight days while the command remained load-bearing. Two
agents and the operator each concluded the fleet was ready and were refused.

## The measured split

Classified by whether a module references the aify-comms API at all (`httpCall`,
`BRIDGE_INSTANCE_ID`, `/spawn-requests`, `/dispatch`, `/agents`, `/terminals`):

| destination | lines | modules |
|---|---|---|
| **aify-env core** (generic) | **2,717** | `terminal-runtime` 981, `runtimes` 549, `claude-console-prompts` 350, `runtimes-exec` 341, `claude-console-spinner` 178, `delegated-stream` 131, `bridge-agent-state` 70, `launcher-file` 62, `env-term-shim` 55 |
| **the comms plugin** | **1,023** | `dispatch-loop` 403, `terminal-manager` 206, `spawn-loop` 158, `managed-environment-sync` 141, `dispatch-execution` 115 |

**73% of "the bridge" is general host capability.** That is why the separation kept looking finished:
the part that moved was visible, and the ~1,000 lines of protocol that did not move were hidden
inside 3,700 lines that read as one component.

`terminal-runtime.js` is the sharpest case -- 981 lines, the largest module in the set, and ZERO
references to aify-comms. It spawns processes, owns PTYs, resolves launchers and classifies console
output. Every one of those is something a second `aify-` service would need.

## The shape

```
aify-env  (general host)          spawns processes, owns PTYs, streams output, reaps,
                                  resolves runtimes and launchers, classifies consoles
   |
   +-- plugin: aify-env-comms-bridge     claims spawn requests, polls for dispatches,
                                          reports terminal state, syncs agent identity
                                          -- everything that knows aify-comms exists

aify-wrapper (general launcher)   renders launchers for any aify- service
aify-comms   (a service)          the control plane; one consumer of the two above
```

**What makes it real rather than a rename:** the plugin is the ONLY place that may name an aify-comms
endpoint. A generic module that reaches for `httpCall` has crossed the line, and the classification
above is a measurement anyone can re-run -- which is what a gate can hold.

## What step 2 does NOT yet settle

**Dispatch delivery per runtime -- ANSWERED 2026-09-02, and the answer is favourable.** A claimed
agent still has to RECEIVE work. `dispatch-loop.mjs` long-polls whenever it hosts a single agent --
every resident claude/codex/hermes wrapper -- so an agent started by aify-env self-delivers through
its own launcher's bridge.

Managed HERMES looked like the exception, because its `hermes-managed-host.js run <agent>` delivery
loop is a separate process. It is not: the LAUNCHER spawns it. `hermes-aify:562` runs
`nohup node "$AIFY_HERMES_MANAGED_HOST_JS" run "$HERMES_AIFY_AGENT_ID" &`, and the PowerShell
launcher does the same at `.ps1:320`. The environment bridge never spawned it -- it only ever started
the launcher, which is exactly what aify-env now does.

**So nothing in the delivery path requires the `aify-comms` command.** Recorded as an open gap
earlier the same day and closed by reading the installed launcher rather than the bridge that was
assumed to own it.

**Who may claim.** Bridge authority is established by SENDING a `bridgeId` -- "no `bridgeId`, no
`bridge*` key survives from the request" -- and nothing authenticates which caller is entitled to
one beyond the shared API key. That is the same gap as D7 in the task list, and moving claiming into
aify-env does not widen it, but it does make it load-bearing in a second place.

## Step 3, corrected by measurement: "generic" splits two ways

The first classification asked whether a module names an aify-comms endpoint. That was the right
question for the PLUGIN boundary and the wrong one for step 3, because it puts two very different
kinds of module on the same side.

Counting IMPORTERS separates them:

| module | lines | importers | destination |
|---|---|---|---|
| `terminal-runtime.js` | 981 | 4 | **aify-env** |
| `claude-console-prompts.js` | 350 | 1 | **aify-env** |
| `runtimes-exec.js` | 341 | 5 | **aify-env** |
| `claude-console-spinner.js` | 178 | 2 | **aify-env** |
| `delegated-stream.mjs` | 131 | 1 | **aify-env** |
| `launcher-file.mjs` | 62 | 1 | **aify-env** |
| `env-term-shim.mjs` | 55 | 1 | **aify-env** |
| `runtimes.js` | 549 | **45** | **STAYS** |
| `bridge-agent-state.mjs` | 70 | **11** | **STAYS** |

**`runtimes.js` is CLIENT-PATH code.** `claude-channel.js` -- the MCP client entry every agent loads
-- imports it, as does `server.js`. Moving it into aify-env would make every agent's MCP bridge
depend on the HOST tier, which is the exact inversion `TARGET_ARCHITECTURE` exists to prevent: its
open item 1 is already that the client path installs too much aify-comms code, and this would add a
second tier to that path rather than removing one.

So step 3 moves **2,098 lines with a blast radius of 1-5 importers each**, and leaves 619 lines of
client-path utility where they are. The moved set is the hosting half: PTYs, launcher resolution,
console classification, delegated streams.

**WHAT STEP 3 STILL COSTS.** It needs a new dependency direction -- aify-comms consuming aify-env the
way it already consumes aify-wrapper, pinned by sha. That is an architectural commitment, and it is
the thing to weigh rather than the line count: 2,098 lines with small blast radii is ordinary work,
and a new cross-repo dependency in the path a fleet boots through is not.

## Step 3, corrected a SECOND time -- the move is mostly not supported

Two measurements have now undercut the premise, and the second is the one that matters.

**The endpoint-reference test was the wrong instrument for this question.** It asks whether a module
names an aify-comms URL, which is exactly right for deciding what belongs in the PLUGIN. It says
nothing about whether a module implements aify-comms CONCEPTS.

`terminal-runtime.js` is the case that shows it. Zero endpoint references -- and of its 982 lines,
11 mention delegation and 6 mention local spawning. The other ~965 are terminal LIFECYCLE: state,
output handling, exit reporting, console classification, resize. That lifecycle feeds aify-comms'
terminals table, its console tail and its status engine. It is aify-comms' own model of a terminal,
and it happens not to contain a URL because `terminal-manager.mjs` holds those.

**Moving it into aify-env would put one service's domain model inside the general host**, and
duplicate the `runner.mjs` aify-env already has. That is the opposite of the goal.

### What the measurements DO support

1. **aify-env owns hosting.** Done: the plugin claims, `Runner` spawns, proven end to end against a
   real socket and a real process.
2. **aify-comms keeps its terminal and runtime model.** `runtimes.js` (45 importers, reached by the
   MCP client entry) and the terminal lifecycle are its domain, not the host's.
3. **The real duplication is the bridge's LOCAL spawning fallback** -- `TerminalProcessManager`'s
   non-delegated path, taken when `envDelegation.isEnabled()` is false. Retiring it makes delegation
   the only way the bridge starts anything, which is what "aify-env owns hosting" MEANS when it is
   true rather than merely configured.

That is a much smaller step 3 than moving 2,098 lines, and it is the one the evidence carries. The
larger move is not refused -- it is unsupported, which is a different claim and a reversible one if
a second `aify-` service later needs the same lifecycle.

## Order of work

1. **The plugin seam in aify-env.** What a service plugin is handed (process spawning, terminal
   ownership, lifecycle hooks) and how it registers. Small, and everything else depends on it.
2. **The comms plugin, 1,023 lines.** This is what lets `aify-env` claim a spawn, and it is the step
   that removes the operator's need for a second command.
3. **The generic 2,717 lines move to aify-env core.** Removes the duplication and is what makes a
   second `aify-` service possible. aify-comms then consumes aify-env the way it already consumes
   aify-wrapper -- a pinned package rather than a copy.

Steps 1 and 2 are v0.6.1 with T1. Step 3 can follow without blocking the operator.

## The gate this needs

A test that re-runs the classification and fails when a module on the generic side names an
aify-comms endpoint. Without it the boundary is prose, and prose is what let the last one rot for
eight days while a document said it was done.
