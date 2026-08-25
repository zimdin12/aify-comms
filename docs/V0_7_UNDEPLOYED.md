# v0.7 review round — what is committed and NOT deployed

Written 2026-08-25, while the fleet was live and the standing instruction was *commit and push freely,
deploy nothing*. Everything below is green in all five suites and running nowhere.

**Re-derive every number here before acting on it.** The commands are given; this file is a snapshot,
and a snapshot of a moving thing is wrong the moment something moves.

## The three versions currently in play

| what | sha | how to read it |
|---|---|---|
| repo HEAD | `git rev-parse --short HEAD` | what is committed |
| service container | `curl -s localhost:8800/health` → `build` | what is RUNNING as the API |
| live environment bridge | `/api/v1/environments` → `metadata.bridgeBuild` | what is RUNNING as the bridge |

Measured 2026-08-26: HEAD `71781055`, container `1a3de61a`, bridge `579dd546` — **three
different shas**, and the bridge is behind even the container. `bridgeCurrentVerdict` called on that
live data returns `code: "stale-process"`, not `unknown-all`: the bridge does report a build, and it
is old. Its own remedy line is the one to follow — *RESTART those bridges/wrappers; re-running
install.sh will not help if `bridge-installed` is already green, because the code is on disk and not
in memory.*

## Two deploy paths, and they are not interchangeable

Run `git diff --name-only <container-build-sha>..HEAD -- mcp/stdio/ | grep -v tests/` to re-derive the
second list. Re-measured 2026-08-26: **55 commits since the container build, 10 of which touch
`mcp/stdio/` outside tests, across 13 files.** It read "21 commits and three files" when this
file was written, which is what a snapshot of a moving thing is worth a day later.

**1. Container rebuild** — everything under `service/`, including `service/new_dashboard/`.

    bash scripts/stamp.sh && docker compose up -d --build
    curl -s localhost:8800/health          # build should now equal repo HEAD

Carries: gzip on the API app AND on the dashboard shell app (two separate FastAPI apps — the first
gzip commit covered only the API), the `spawn_requests` N+1 removal, `idx_messages_source`, the
assets mount that stops publishing the test tree, and every dashboard module change.

**2. `install.sh` re-run, THEN a wrapper restart** — anything under `mcp/stdio/`.

    bash install.sh --client claude <endpoint>     # re-copies mcp/stdio into ~/.aify-comms
    # then relaunch each wrapper; `aify-comms doctor` should turn bridge-current green

Carries thirteen files: `child-env-hygiene.mjs`, `doctor-predicates.js`, `doctor.js`,
`env-client.mjs`, `hermes-channel.js`, `hermes-delivery-loop.mjs`, `hermes-delivery-run.mjs`,
`hermes-gateway-liveness.js`, `hermes-gateway.mjs`, `server.js`, `terminal-env.js`,
`terminal-runtime.js`, `turn-busy-heartbeat.js`.

**A container rebuild does not deploy the bridge fix.** `install.sh` copies `mcp/stdio` into a native
dotfolder and every wrapper runs THAT copy, so a bridge change reaches nothing until the copy is
refreshed and the wrapper process restarts. The behavioural fix in that set is the one that matters:
a worker could inherit the bridge's role through `AIFY_COMMS_AGENT_ROLE`, the alias that
`NEVER_INHERITED` missed. Measured before the fix — a bridge holding `AIFY_COMMS_AGENT_ROLE=manager`
spawned a worker whose role resolved to `manager` instead of its own default.

## What restarting costs

Restarting the environment bridge reaps its managed workers. That is why none of this was deployed
during the round. Pick a window where the fleet is idle, or accept losing the managed sessions.

## Verifying afterwards

`aify-comms doctor --json` is the tool for it — never a bare `aify-comms`, which starts an
environment bridge, supersedes the live one, and reaps its workers. Expect `service` to go green on
the rebuild and `bridge-current` on the wrapper restart; they are independent and either can be done
without the other.
