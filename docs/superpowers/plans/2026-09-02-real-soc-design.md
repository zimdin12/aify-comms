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
