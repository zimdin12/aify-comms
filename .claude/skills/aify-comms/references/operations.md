# aify-comms Operations Reference

Load this file only for setup, runtime policy, bridge/session repair, or dashboard operations. For routine chat, use the main skill.

## Operator proof model

### Ownership before action

For lifecycle or cleanup work, establish the current ownership tuple:

```text
agent id + runtime session handle + agent session id + terminal id
+ environment id + bridge instance id + current process ancestry
```

`comms_agent_info` is the first read, not the final authority when records conflict.
Current bridge heartbeats, terminal ownership, runtime events, and OS process ancestry
outrank stale database rows. Terminal controls belong to both the environment and the
bridge instance that owns the terminal. A resident registration may reconcile stale,
unbacked managed metadata; it must not displace an active managed run. A runtime's own
delivery sidecar can be part of the same owner, not a competing worker.

Do not kill, restart, reap, switch, or supersede until the exact live owner is known.
If the tuple conflicts, stop and diagnose through `aify-comms-debug`.

### Interruption

1. Read `comms_agent_info` and `comms_run_status` where a dispatch run exists.
2. Confirm the live session, terminal, environment, bridge instance, and current turn.
3. Use `comms_run_interrupt` for that exact dispatch run, or `comms_interrupt` for the current managed console turn.
4. Do not repeat the control blindly; a replacement turn may now own the terminal.
5. Verify native turn end plus converged run/agent/session state.

An accepted or claimed control proves transport only. Completion requires observing the
original turn end and post-action state. A normal message containing `STOP` is not an
interrupt.

### Deployment claims

Report each state separately; later states do not follow from earlier ones:

| Claim | Minimum evidence |
|---|---|
| Source changed | exact worktree SHA/diff |
| Installed/copied | installed checksum or source provenance |
| Process refreshed | new PID/start time or explicit reload/restart evidence |
| Image built | image digest/build output |
| Container deployed | running container uses that image digest |
| Service current | `/version` or served checksum matches the intended SHA |
| Behavior proven | the real runtime/provider path produced the expected result |

A healthy endpoint is not deployment proof. A rebuilt image is not a recreated
container. A copied file is not a reloaded long-running process.

## Install Or Update

After every install/update:

1. Rerun the install command from the repo install doc.
2. Restart the affected CLI wrapper or client. Restarting aify-env is the OPERATOR's action: a second one supersedes the first and reaps its workers.
3. For resident/operator-open sessions only, re-register from the exact live session you want other agents to trigger, or launch with `--aify-agent <agentId>` so the wrapper registers it automatically. Dashboard-managed agents are registered by aify-env's aify-comms plugin and should not call `comms_register` from delivered runs.
4. Confirm with `comms_agent_info(agentId="...")`.

Never replace `comms_register` with raw `curl`/Node `POST /api/v1/agents` for
resident agents. That endpoint can update metadata, but it cannot create the
wrapper's bridge heartbeat or dispatch claim loop. A resident record without a
fresh bridge is `offline` and live sends are rejected.

Wrapper auto mode:

- `codex-aify` adds Codex's supported bypass flag by default; use `--safe`, `--no-auto`, or `--no-dangerous-permissions` to opt out for permission debugging.
- `claude-aify -auto` adds `--dangerously-skip-permissions`.
- `omp-aify` / `pi-aify` has no special `-auto` permission mode; model/thinking defaults come from Oh My Pi unless Dashboard Runtime settings or runtime config supplies model/effort.
- `claude-aify --aify-agent <agentId> --resume <session-id>`, `codex-aify --aify-agent <agentId> ...`, and `hermes-aify --aify-agent <agentId> --resume <session-id>` auto-register live resident sessions. `omp-aify` / `pi-aify` can auto-register presence/metadata for a human-open or standalone Pi terminal, but triggerable Pi delivery uses managed RPC.

## Managed Runtime Policy

- Dashboard-managed identities are registered by aify-env, which claims the spawn and runs the worker. Delivered managed runs must not call `comms_register`.
- aify-env owns managed backings, including the PTY. Browser Console attaches to that backing and is not another owner.
- Branch on advertised capabilities, not runtime names. Unsupported resident mode or interrupt must fail visibly rather than create an undeliverable session.
- Use Dashboard Settings for operator policy. Runtime flags and controller internals belong in [the architecture plan](../../../../docs/ARCHITECTURE_PLAN.md), not in an agent's routine context.

| Runtime | Normal managed delivery | Resident delivery |
|---|---|---|
| Claude Code | wrapper PTY plus channel | supported through `claude-aify` |
| Codex | wrapper PTY plus app-server | supported through `codex-aify` |
| Hermes | wrapper PTY plus gateway sidecar | supported through `hermes-aify` |
| Pi | persistent managed RPC | presence/debug only |
| OpenCode | native managed controller when enabled | unsupported |

A wrapper-backed runtime still needs its wrapper/sidecar alive to claim work. A console can
exist while its delivery owner is dead; prove both before calling the agent `online`.

## Send Gating & Delivery

**Reply-capture fallback.** `managed_reply_capture_fallback` decides what happens when a reply-owed delivered run ends with no explicit reply: `true` (default) auto-mirrors the run summary back to the sender as a safety net, `false` (strict) leaves the run reply-owed so the gap is surfaced rather than papered over. It is an operator setting, not an agent one — send the explicit `comms_send` either way.

- `available` means an online environment can cold-start the managed worker on send. Do not pre-spawn it.
- `online` means a live worker is between turns. `working`/`blocked` mean a live worker has an open turn.
- `offline` has no current wake path. `stopped` does not auto-start, and is not always the operator's doing (see the table).
- Ordinary sends use the runtime's live path and may steer a busy capable runtime. `queueIfBusy=true` deliberately waits for the next turn.
- A queued, claimed, or delivered run is transport evidence only. Read `comms_run_status`, the linked reply, runtime events, or console before claiming execution.
- Browser Console is an attachment to the managed backing. Do not use console input as a second messaging path.

## Environment Bridges

- **`aify-comms` verifies and starts nothing.** `doctor`, `--check`, `--version`, `--help`; anything
  else exits 2 and names aify-env. Before v0.6.1 a bare run started a bridge that superseded the live
  one and reaped its workers, taking down a fleet twice. Enforced now, not remembered.
- **Starting aify-env is the operator's action**, and it carries that same hazard: a second one
  supersedes the first and reaps its workers. Ask `aify-env doctor` instead of starting one.
- After install/update: `aify-comms doctor --json` for the bridge, `aify-env doctor` for whether `node-pty` loads here. Package presence proves neither.
- A newer bridge instance supersedes the older instance for its environment. Current bridge identity owns new terminal controls; stale controls must not cross that boundary.
- The current directory is an allowed workspace root; extra roots are optional boundaries.
- Killing or forgetting an environment does not delete agent identities, chat, spawn specs, or historical sessions.
- Never terminate a process from a stale bridge/session row. Confirm current process ancestry and activity first.

## CLI Ownership Transfer

1. Open the exact native session through `claude-aify`, `codex-aify`, or `hermes-aify` with `--aify-agent <id>`; preserve the real native handle when resuming.
2. Use manual `comms_register` only when the wrapper did not auto-register.
3. Verify `sessionHandle`, `sessionMode`, live bridge identity, and status with `comms_agent_info`.
4. Switch managed/resident explicitly. Registration records a candidate; it must not silently displace a live managed owner.
5. Use **Set handle** only to repair a known native ID. Use **Reset** only when fresh context is intentional.

Pi and OpenCode are managed-only for triggerable work. A plain presence registration does not
create a resident delivery path. A resident agent has no aify-owned console.

## Multi-Instance Rules

- Every visible agent needs a distinct `agentId` and one current delivery owner.
- Never register the same `agentId` from two tabs.
- Native handles must not be shared by live agents unless the runtime explicitly supports it and the operator accepts the coupling.
- Workspace paths must use the target environment's path style.

## Dashboard Semantics

- Work Loop shows reply/work contracts derived from messages and runs; hiding or reviewing an item does not delete audit history.
- Overdue reminders point back to the original contract. Reply to the original message ID, not the reminder.
- Chat **Queue** maps to `queueIfBusy=true`; unchecked sends use ordinary live delivery.
- Console attaches to the current managed backing. Opening, hiding, or copying from it must not change session ownership.
- Dashboard state is a view over the API, not independent execution proof.

## Status Meanings

Status is proof-based and derived from live inputs. Read `comms_agent_info`; diagnose conflicts
through `aify-comms-debug` rather than inventing another status.

| Status | Meaning | Normal action |
|---|---|---|
| `working` | Live worker, open turn | wait, steer, or interrupt the proven turn |
| `online` | Live worker, between turns | send normally |
| `available` | Managed and cold-startable, no worker | send normally; it auto-starts |
| `starting` | A claimed spawn is coming up; no worker YET | wait — do NOT restart or re-send; it is already on its way |
| `blocked` | Live turn awaiting operator input | inspect console, then answer the proven prompt |
| `offline` | No current wake path | restore bridge/environment or switch ownership |
| `stopped` | Operator-disabled, or a resident that closed cleanly (`resident-lost`) | restart/resume only when intended |
| `misconfigured` | Identity exists but can never start | a human must fix the config; sending will not work |

There are no live `idle` or `stale` states. A long-quiet live worker remains `online`.

`starting` vs `available` is worth reading carefully, because they look the same from the outside and
the right action differs. `available` means "nothing is running, and a send will cold-start it".
`starting` means "a spawn has been claimed and its worker has not appeared yet" — a send still
queues and is delivered when it arrives, but a RESTART at that moment kills the boot in progress.
It is bounded: past the spawn-in-flight window an agent that never produced a worker falls back to
`available`, so `starting` can never hide a broken spawn indefinitely.

## Repair Hints

- If another agent is not triggerable, inspect `comms_agent_info(agentId="target")` first.
- Codex path errors usually mean stale binding, wrong host path style, or stale bridge/app-server markers.
- Claude `Session ID ... is already in use` means another Claude process owns that native session. Pause/close/take over explicitly; do not silently recreate unless the operator requests it.
- `comms_listen` is deprecated. Do not use it for normal teamwork or managed runs.
