# aify-comms troubleshooting: lifecycle and ownership

## Baseline

Before changing state, read `comms_agent_info` (and `comms_run_status` for a run). Record session, native
handle, terminal, environment, bridge and live process; current activity outranks stale database ownership.

## Safe interruption: message vs run vs console

**Symptom:** a message saying `STOP` was queued/delivered but work continued, or a second
interrupt hit replacement work.

Follow the aify-comms skill's operations.md (Interruption). A resident agent has no aify-owned
console: use its run interrupt, or relaunch it from its own terminal.

## Lifecycle verbs

For a DEFINED agent (0.9) start, stop, restart, reset, kill, spawn and delete are requests its host
claims: one `agent_lifecycle_requests` row each, outcome `done`, `refused` (reason named) or `failed`.

| Verb | Meaning |
|---|---|
| Spawn / Start | New or stopped identity gets backing; a defined one from its definition. |
| Stop | Halt backing and disable wake; keep identity, definition, handle. |
| Restart / Reset | New backing resuming the stored handle / with fresh native context. |
| Resume wake / Pause for CLI (API) | Re-enable a stopped resident wake path / hand ownership to an operator terminal. |
| Switch managed/resident | Change the delivery owner; for a defined agent, edits its definition. |
| **Edit…** → *Native session handle* | Repair a known native resume target without starting work. |
| Interrupt / Steer | Control one proven active turn. |
| Remove / Forget environment | Tombstone the identity / hide an offline execution target. |

## Restart acknowledged, but no new worker appears

Restart returns ok, the agent settles at `available`, no new terminal appears, its run reads `[FAILED]`:
a regression of one of two fixed causes. The run's `claimed_at` NULL (failed ~1 s in) means the rotation
adopted the dying terminal; set (failed at 120/300 s) means the dying sidecar claimed the brief.

A new worker is not proof the restart worked: a cold start can produce one minutes later. Match the
new terminal to your restart: for a defined agent by its `agent_lifecycle_requests` row (its terminal
row has no `spawn_request_id`), for an undefined one by `spawn_request_id`. A working restart can still
record its run `failed`, because it killed the process that would reply; judge by the new backing.

## Start refused by aify-env (0.9)

The request row's `outcome`, or the status note, names it:

| Reads | Cause | Remedy |
|---|---|---|
| `misconfigured`, note "Cannot be started: … outside the folder aify-env was started in" | workspace outside aify-env's start folder | operator restarts aify-env from a folder containing it (`C:\` on Windows); `aify-env doctor` row `agents-outside-roots` |
| `automatic-agent-undefined` | no host defines the id | `aify-env agents import --write` on its host |
| `automatic-definition-unavailable` | definition has problems or names no launch | `aify-env agents list`, fix it |
| `agent-reserved` | an earlier action ended `execution-unknown` | KNOWN_ISSUES "execution-unknown keeps its agent reserved" |

## Start refused by the agent lease

One instance per agent per host (v0.6.8). Explicit starts (a terminal launch naming `--aify-agent`,
dashboard Start/Restart, `comms_restart`, a `comms_compact` handoff to itself) replace a live instance;
automatic ones (cold start, backstop, `comms_spawn`, a Herdr restore, an id from the environment or
`--resume`) exit 75.

| Console shows | Cause | Remedy |
|---|---|---|
| `[aify-agent-lease] <id> is already running (…)` | automatic start, live instance | stop it, or dashboard Restart |
| `… runs inside <id>'s own live instance` | launched from that agent's own session | use another agent id |
| `… could not read its process table … Try again` | live process, unverifiable | retry |
| hermes `Session X already has a live owner (tui, pid N)` | an older generation's gateway holds it | relaunch; kill-prior collects it |

## Managed ↔ resident ownership

- Claude Code, Codex, and Hermes support managed and resident delivery.
- Pi (deprecated) is managed-only for triggerable delivery; managed OpenCode is unsupported. Presence metadata is not a resident wake path.
- Resident→managed carries the native handle so Restart resumes the same context.
- Registration records a candidate owner; it must not silently kill or displace a live managed worker.
  A defined agent whose definition says resident adopts its resident unless a managed terminal runs (0.9.2).
- Active work blocks ownership changes unless the operator forces them after proving the target.

After switching, verify `sessionMode`, native handle, bridge identity, status, and one real send.

## Send rejected or never claimed

| Evidence | Meaning | Action |
|---|---|---|
| `available`, capable environment online | cold-startable | send once; watch claim/start |
| `offline` / `misconfigured` | no wake path / no start can reach it | bind an environment / see the table above |
| `stopped` | operator-disabled | Restart or Resume wake |
| queued, no claimant | delivery owner missing | inspect bridge, wrapper/sidecar, capabilities |
| completed, no linked reply | contract still open | obtain the real threaded result |
| `steer=false`, work already queued | ordering guard | inspect the existing contract |

Queued, claimed and delivered are not execution proof; see `dispatch-delivery.md`.

## Registration and bridge ownership

A healthy resident needs its real wrapper session, a usable native handle, a fresh wrapper heartbeat and its
runtime's wake configuration. Prefer `*-aify --aify-agent <id>`; a manual `comms_register` runs inside the
session that should own delivery. A raw `POST /api/v1/agents` writes metadata only. A 409 live-owner conflict
is a safety result: inspect both owners. Re-registering repairs no dead process: relaunch the wrapper.

## After the operator restarts aify-env or the service

Identity, chat, definition and stored handle remain. Verify health and that aify-env answers, read each
affected agent (old console rows are history), restart managed agents only when needed, relaunch residents from
their real sessions, and prove one send per delivery shape.

## Misleading console or session state

An old terminal row is history, not the owner. When UI and API disagree, correlate the API row, bridge
heartbeat, terminal output and live process; repair the owning relationship, not the badge.
