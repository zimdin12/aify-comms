# aify-comms troubleshooting: lifecycle and ownership

Use this file for stop/restart/reset, managed↔resident switching, registration conflicts,
send gating, duplicate owners, and safe interruption.

## Baseline

Before changing state, read `comms_agent_info`; when a dispatch run exists, read
`comms_run_status`. Record the current agent session, native handle, terminal,
environment, bridge instance, and live process family. Current activity and process
ancestry outrank stale database ownership.

## Safe interruption: message vs run vs console

**Symptom:** a message saying `STOP` was queued/delivered but work continued, or a second
interrupt hit replacement work.

1. Prove the exact live owner and current turn.
2. Use `comms_run_interrupt(runId="...")` only for that dispatch run.
3. Use `comms_interrupt(agentId="...")` for the current managed console turn, including direct-TUI work without a dispatch run.
4. Do not repeat blindly. Re-read the owner after the first control.
5. Verify the original native turn ended and run/agent/session state converged.

A resident agent has no aify-owned managed console. Use its supported run interrupt or ask
the operator to stop/relaunch the resident runtime.

## Lifecycle verbs

| Verb | Meaning |
|---|---|
| Spawn | Create a new managed identity/backing. |
| Stop | Halt backing and disable wake; preserve identity/spec/handle. |
| Restart | Recreate backing and resume the stored native handle. |
| Reset | Recreate backing with fresh native context. |
| Resume wake | Re-enable a stopped resident wake path without spawning managed backing. |
| Pause for CLI | Hand ownership to an operator-open terminal. |
| Switch managed/resident | Explicitly change the delivery owner while preserving the handle. |
| Set handle | Repair a known native resume target without starting work. |
| Interrupt / Steer | Control one proven active turn. |
| Remove | Tombstone the identity. |
| Kill bridge / Forget | Remove an execution target, not agent history. |

Removed aliases such as session `recover`/`resume` must not be reintroduced. Restart preserves
context; Reset discards it. If only the native ID is wrong, Set handle is the smaller repair.

## Restart acknowledged, but no new worker appears

**Symptom:** Restart returns ok, the old backing dies, the agent settles at `available` instead of
`online`, no new terminal is created, and the restart's own run reads `[FAILED]`.

Read the failed run's `claimed_at` first — it separates two different causes, and a receipt alone
does not:

| `claimed_at` | Cause | Expected now |
|---|---|---|
| NULL, failed ~1s after request | The rotation adopted the predecessor terminal the restart was killing, so the predecessor's death failed the replacement's queued brief | FIXED 2026-08-03 (migration bounded by the spawn request's age). Seeing this again means the bound regressed — check `terminal_sessions.created_at` against `spawn_requests.created_at` |
| set, failed at 120s/300s | The dying channel sidecar claimed the brief and took it to the grave | STILL OPEN — see KNOWN_ISSUES.md; requeue via the reconcile loop or re-issue the brief |

Do not read the replacement worker's existence as proof the restart worked. A cold-start can produce
a worker minutes later by a different path, which makes a restart that never worked look merely slow.
Correlate the new terminal's `created_at` and its `spawn_request_id` with the restart you issued.

A restart that succeeds can still record its run `failed`: success is judged on the agent replying,
and a restart kills the process that would reply. Judge by the new backing, not the receipt.

## Managed ↔ resident ownership

- Claude Code, Codex, and Hermes support managed and resident delivery.
- Pi and OpenCode are managed-only for triggerable delivery; presence metadata is not a resident wake path.
- Resident→managed carries the native handle so Restart resumes the same context.
- Registration records a candidate owner; it must not silently kill or displace a live managed worker.
- Active work blocks ownership changes unless the operator explicitly forces them after proving the target.

After switching, verify `sessionMode`, native handle, bridge identity, status, and one real send.

## Send rejected or never claimed

| Evidence | Meaning | Action |
|---|---|---|
| `available` plus online capable environment | cold-startable | send once; watch claim/start evidence |
| `offline` or no online host | no wake path | restore/bind an environment |
| `stopped` | operator-disabled | Restart or Resume wake intentionally |
| queued with no claimant | delivery owner missing/unready | inspect bridge, wrapper/sidecar, and capabilities |
| completed without linked reply | runtime ended but contract remains open | obtain the real threaded result |
| target already has queued work | ordering guard | inspect existing contract; do not duplicate it |

Queued, claimed, and delivered are not execution proof. If delivery details are unclear, move
to `dispatch-bridge.md`.

## Registration and bridge ownership

**Healthy resident registration requires all of:** the real wrapper session, usable native
handle, fresh wrapper bridge heartbeat, and runtime wake configuration.

- Prefer `*-aify --aify-agent <id>` auto-registration.
- Manual `comms_register` must run inside the exact visible session that should own delivery.
- Raw `POST /api/v1/agents` writes metadata only; it cannot create the wrapper heartbeat or claim loop.
- A 409 live-owner conflict is a safety result. Inspect both owners; do not force-register over current work.
- A superseded bridge cannot claim new work or own new terminal controls.
- Re-registering metadata does not repair a dead process. Relaunch the wrapper/bridge, then verify readback and delivery.

## Restarting aify-comms

A service/bridge restart can terminate managed backings. Identity, chat, spawn spec, and stored
native handle remain. After restart:

1. Verify service health and the current environment bridge.
2. Read each affected agent; do not assume old console/session rows are live.
3. Restart managed agents only when needed, preserving handles.
4. Relaunch resident wrappers from their real native sessions.
5. Prove one send through each affected delivery shape before declaring recovery.

## Misleading console or session state

A stopped/old terminal row is historical evidence, not the current owner. Browser Console must
attach only to the terminal bound to the current agent session. When UI and API disagree, trust
neither alone: correlate the API row, bridge heartbeat, terminal output, and live process. Repair
the owning relationship; do not delete history to make the badge look right.
