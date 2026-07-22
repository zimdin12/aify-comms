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
2. Restart the affected CLI wrapper/client and long-running `aify-comms` environment bridge.
3. For resident/operator-open sessions only, re-register from the exact live session you want other agents to trigger, or launch with `--aify-agent <agentId>` so the wrapper registers it automatically. Dashboard-managed agents are registered by the environment bridge and should not call `comms_register` from delivered runs.
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
- Claude Code's valid skip-permissions flag is `--dangerously-skip-permissions`; `--permanently-skip-permissions` is not valid.

## Managed Runtime Policy

- Dashboard-managed identities are registered by the environment bridge. Delivered managed runs must not call `comms_register`.
- The bridge owns managed backings; Browser Console attaches to that backing and is not another owner.
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

- `available` means an online environment can cold-start the managed worker on send. Do not pre-spawn it.
- `online` means a live worker is between turns. `working`/`blocked` mean a live worker has an open turn.
- `offline` has no current wake path. `stopped` is operator-disabled and does not auto-start.
- Ordinary sends use the runtime's live path and may steer a busy capable runtime. `queueIfBusy=true` deliberately waits for the next turn.
- A queued, claimed, or delivered run is transport evidence only. Read `comms_run_status`, the linked reply, runtime events, or console before claiming execution.
- Browser Console is an attachment to the managed backing. Do not use console input as a second messaging path.

## Environment Bridges

- `aify-comms --help` shows launcher usage. The current directory is an allowed workspace root; extra roots are optional boundaries.
- After install/update, run `aify-doctor --json`. Package presence does not prove the installed `node-pty` can load or that the running bridge uses it.
- A newer bridge instance supersedes the older instance for its environment. Current bridge identity owns new terminal controls; stale controls must not cross that boundary.
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
| `blocked` | Live turn awaiting operator input | inspect console, then answer the proven prompt |
| `offline` | No current wake path | restore bridge/environment or switch ownership |
| `stopped` | Operator-disabled | restart/resume only when intended |

There are no live `idle` or `stale` states. A long-quiet live worker remains `online`.

## Repair Hints

- If another agent is not triggerable, inspect `comms_agent_info(agentId="target")` first.
- Codex path errors usually mean stale binding, wrong host path style, or stale bridge/app-server markers.
- Claude `Session ID ... is already in use` means another Claude process owns that native session. Pause/close/take over explicitly; do not silently recreate unless the operator requests it.
- `comms_listen` is deprecated. Do not use it for normal teamwork or managed runs.
