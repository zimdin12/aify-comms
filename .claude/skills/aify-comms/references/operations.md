# aify-comms Operations Reference

## Operator proof model

### Ownership before action

For lifecycle or cleanup work, establish the current ownership tuple:

```text
agent id + runtime session handle + agent session id + terminal id
+ environment id + bridge instance id + current process ancestry
```

`comms_agent_info` is the first read, not the final authority when records conflict.
Current bridge heartbeats, terminal ownership, runtime events, and OS process ancestry
outrank stale database rows.

Kill, restart, reap, switch or supersede only once the exact live owner is known.
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
2. Relaunch the affected CLI wrapper or client.
3. For resident/operator-open sessions only, re-register from the exact live session you want other agents to trigger, or launch with `--aify-agent <agentId>` so the wrapper registers it automatically. Dashboard-managed agents are registered by aify-env's aify-comms plugin and should not call `comms_register` from delivered runs.
4. Confirm with `comms_agent_info(agentId="...")`.

Wrapper auto mode:

- Every `*-aify` wrapper bypasses its runtime's permission prompts by default (`claude --dangerously-skip-permissions`, Codex's bypass flag, `hermes --yolo`). `--safe` or `--no-auto` opts out for one launch.
- `claude-aify --aify-agent <agentId> --resume <session-id>`, `codex-aify --aify-agent <agentId> ...`, and `hermes-aify --aify-agent <agentId> --resume <session-id>` auto-register live resident sessions.
- Pi (`pi-aify`, `omp-aify`) is deprecated and `install.sh` no longer installs its wrapper.

## Managed Runtime Policy

- aify-env owns managed backings, including the PTY. Browser Console attaches to that backing and is not another owner.
- Branch on advertised capabilities, not runtime names. Unsupported resident mode or interrupt must fail visibly rather than create an undeliverable session.
- Use Dashboard Settings for operator policy. Runtime settings are described in `docs/OPERATING_MODES.md` and the internals in `docs/ARCHITECTURE.md` in the aify-comms checkout.

| Runtime | Normal managed delivery | Resident delivery |
|---|---|---|
| Claude Code | wrapper PTY plus channel | supported through `claude-aify` |
| Codex | wrapper PTY plus app-server | supported through `codex-aify` |
| Hermes | wrapper PTY plus gateway sidecar | supported through `hermes-aify` |
| Pi (deprecated) | none unless an old `pi-aify` is still on the host's PATH | presence only |
| OpenCode | unsupported | unsupported |

A wrapper-backed runtime still needs its wrapper/sidecar alive to claim work. A console can
exist while its delivery owner is dead; prove both before calling the agent `online`.

## Send Gating & Delivery

- Ordinary sends use the runtime's live path and may steer a busy capable runtime. `queueIfBusy=true` deliberately waits for the next turn.
- A queued, claimed, or delivered run is transport evidence only. Read `comms_run_status`, the linked reply, runtime events, or console before claiming execution.
- Browser Console is an attachment to the managed backing. Do not use console input as a second messaging path.

## Host tier (aify-env)

- **`aify-comms` verifies and starts nothing.** `doctor`, `--check`, `--version`, `--help`; anything
  else exits 2 and names aify-env.
- **Starting aify-env is the operator's action**: a second one supersedes the first and reaps its
  workers. Ask `aify-env doctor` instead of starting one.
- After install/update: `aify-comms doctor --json` for the bridge, `aify-env doctor` for whether this host can open a terminal.
- A workspace must sit under the directory aify-env was started in and under the environment's `roots:` in `comms_envs`.
- Killing or forgetting an environment does not delete agent identities, chat, spawn specs, or historical sessions.
- Never terminate a process from a stale bridge/session row. Confirm current process ancestry and activity first.

## CLI Ownership Transfer

1. Open the exact native session through `claude-aify`, `codex-aify`, or `hermes-aify` with `--aify-agent <id>`; preserve the real native handle when resuming.
2. Use manual `comms_register` only when the wrapper did not auto-register.
3. Verify `sessionMode`, wake mode and status with `comms_agent_info`, and `sessionHandle` with `GET /api/v1/agents/<id>`.
4. Switch managed/resident explicitly. Registration records a candidate; it must not silently displace a live managed owner.
5. Use the agent's **Edit…** → *Native session handle* field only to repair a known native ID. Use **Reset** only when fresh context is intentional.

Pi and OpenCode have no resident delivery path; a plain presence registration does not create one. A resident agent has no aify-owned console.

## Multi-Instance Rules

- Every visible agent needs a distinct `agentId` and one current delivery owner.
- Never register the same `agentId` from two tabs.
- Native handles must not be shared by live agents unless the runtime explicitly supports it and the operator accepts the coupling.
- Workspace paths must use the target environment's path style.

## Dashboard Semantics

- Work Loop shows reply/work contracts derived from messages and runs; hiding or reviewing an item does not delete audit history.
- Overdue reminders point back to the original contract. Reply to the original message ID, not the reminder.
- Chat's **Queue** button sends with `queueIfBusy=true`; **Send** uses live delivery.
- Console attaches to the current managed backing. Opening, hiding, or copying from it must not change session ownership.
- Dashboard state is a view over the API, not independent execution proof.

## Status Meanings

Read `comms_agent_info`; diagnose conflicts
through `aify-comms-debug` rather than inventing another status.

| Status | Meaning | Normal action |
|---|---|---|
| `working` | Live worker, open turn | wait, steer, or interrupt the proven turn |
| `shell` | Idle at prompt, background shells running | send normally |
| `online` | Live worker, between turns | send normally |
| `available` | Managed and cold-startable, no worker | send normally; it auto-starts |
| `starting` | A claimed spawn is coming up; no worker YET | wait — do NOT restart or re-send |
| `blocked` | Live turn awaiting operator input | inspect console, then answer the proven prompt |
| `offline` | No current wake path | resident: relaunch its `*-aify`; managed: its host's aify-env is down (operator) |
| `stopped` | Operator-disabled, or a resident that closed cleanly (`resident-lost`) | restart/resume only when intended |
| `misconfigured` | Identity exists but can never start | a human must fix the config; sending will not work |

There are no live `idle` or `stale` states. A long-quiet live worker remains `online`.

## Repair Hints

- If another agent is not triggerable, inspect `comms_agent_info(agentId="target")` first.
- Codex path errors usually mean stale binding, wrong host path style, or stale bridge/app-server markers.
