# v0.7 review round — what is committed and NOT deployed

> **THE CODE BELOW WAS DELETED ON 2026-09-04, and this file is kept as the RECORD OF WHY.**
> v0.6.2 removed the environment-bridge cluster from `mcp/stdio` -- the spawn, environment-control,
> terminal-control and managed-environment-sync loops, all four managed-teardown sweeps, the
> terminal manager and its exit report, the ownership readers, the survivor reaper and the console
> pulse. aify-env owns processes, PTYs, spawn claiming and console streaming now, PROVEN on real
> hardware 2026-09-03 with no bridge running at all.
>
> So every file path below that names one of those modules resolves to nothing. The TRACES ARE
> STILL TRUE of the code as it stood, which is the whole value of keeping them: they record what
> the behaviour was and how it was measured. Read them as history, and look for the behaviour in
> aify-env.

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

Re-measured 2026-08-26 (later): HEAD `8644da36`, container `1a3de61a`, bridge `579dd546` — **three
different shas**, and the bridge is behind even the container. `bridgeCurrentVerdict` called on that
live data returns `code: "stale-process"`, not `unknown-all`: the bridge does report a build, and it
is old. Its own remedy line is the one to follow — *RESTART those bridges/wrappers; re-running
install.sh will not help if `bridge-installed` is already green, because the code is on disk and not
in memory.*

## Two deploy paths, and they are not interchangeable

Run `git diff --name-only <container-build-sha>..HEAD -- mcp/stdio/ | grep -v tests/` to re-derive the
second list. Re-measured 2026-08-26, later the same night: **68 commits since the container build,
15 bridge files and 48 service files changed outside tests.** This paragraph has now been corrected
THREE times in one night -- it read "21 commits and three files", then "55 commits and 13 files" --
which is the argument for the instruction at the top rather than a failing of the numbers. Re-derive;
do not read.

**1. Container rebuild** — everything under `service/`, including `service/new_dashboard/`.

    bash scripts/stamp.sh && docker compose up -d --build
    curl -s localhost:8800/health          # build should now equal repo HEAD

Carries: gzip on the API app AND on the dashboard shell app (two separate FastAPI apps — the first
gzip commit covered only the API), the `spawn_requests` N+1 removal, `idx_messages_source`, the
assets mount that stops publishing the test tree, and every dashboard module change.

**2. `install.sh` re-run, THEN a wrapper restart** — anything under `mcp/stdio/`.

    bash install.sh --client claude <endpoint>     # re-copies mcp/stdio into ~/.aify-comms
    # then relaunch each wrapper; `aify-comms doctor` should turn bridge-current green

Carries fifteen files: `child-env-hygiene.mjs`, `doctor-predicates.js`, `doctor.js`,
`env-client.mjs`, `hermes-channel.js`, `hermes-delivery-loop.mjs`, `hermes-delivery-run.mjs`,
`hermes-gateway-liveness.js`, `hermes-gateway.mjs`, `server.js`, `terminal-attach-notice.js`,
`terminal-control-loop.mjs`, `terminal-env.js`, `terminal-runtime.js`, `turn-busy-heartbeat.js`.

**A container rebuild does not deploy the bridge fix.** `install.sh` copies `mcp/stdio` into a native
dotfolder and every wrapper runs THAT copy, so a bridge change reaches nothing until the copy is
refreshed and the wrapper process restarts. The behavioural fix in that set is the one that matters:
a worker could inherit the bridge's role through `AIFY_COMMS_AGENT_ROLE`, the alias that
`NEVER_INHERITED` missed. Measured before the fix — a bridge holding `AIFY_COMMS_AGENT_ROLE=manager`
spawned a worker whose role resolved to `manager` instead of its own default.

## What a container rebuild actually delivers, as of `8644da36`

So the operator can weigh the window against the payoff rather than guessing. Behavioural changes
only -- documentation and test commits are omitted:

| | |
|---|---|
| status correctness | a superseded bridge could SET a turn it was not allowed to CLEAR (`c71b0fe4`), a one-way ratchet toward `working` whose 45s re-post also kept the 30-minute ceiling from ever firing |
| reconcile sweep | 44 + 17N -> 46 + 12N round-trips per pass, 894 -> 646 at 50 agents (27.7%), on a loop that runs every 60 seconds |
| roster | `GET /api/v1/agents` **285 -> 97** round-trips per call at 50 agents across the round, and now FLAT: 97 at 20 agents and 97 at 50, because the refresh is capped at 8 and every per-agent lookup in the gate loop is batched. Both figures are at the same agent count -- an earlier draft of this row spliced a 50-agent number to a 20-agent one |
| dashboard poll | `/spawn-requests` page-gated and the inbox made a real fallback: 715,262 of 1,305,436 bytes off a closed-Environments cycle, or 145,914 of 294,403 once gzip ships |
| diagnostics | a dead TUI's last line is no longer reported as its cause of death; the terminal detail returns the NEWEST 200 events rather than the oldest; the one path that stops many terminals at once now records that it did |
| console honesty | the attach line says when a console came back without a PTY; the connection chip says `polling` instead of `live` when realtime is down |

None of it is running. The bridge half needs `install.sh` and a wrapper relaunch on top of the
rebuild, and the two halves are independent.

## What restarting costs

Restarting the environment bridge reaps its managed workers. That is why none of this was deployed
during the round. Pick a window where the fleet is idle, or accept losing the managed sessions.

## Verifying afterwards

`aify-comms doctor --json` is the tool for it — never a bare `aify-comms`, which starts an
environment bridge, supersedes the live one, and reaps its workers. Expect `service` to go green on
the rebuild and `bridge-current` on the wrapper restart; they are independent and either can be done
without the other.
