---
name: aify-comms-debug
description: Use when aify-comms dispatch, wake mode, bridge health, managed/resident routing, dashboard Console, or runtime wrapper behavior is broken or confusing.
---

# aify-comms: Troubleshooting

Use this skill whenever something in aify-comms is not behaving the way the main skill says it should. Each entry in the reference files below lists the **symptom**, the **cause**, and the **fix**.

**Always diagnose first.** Start with `comms_agent_info(agentId="target")` and read
`wakeMode`, `sessionMode`, `machineId`, `sessionHandle`, and `dispatchState`. That is
the first read, not proof of live ownership when records conflict. For lifecycle,
interrupt, cleanup, or duplicate-session work, correlate the agent/session/terminal,
environment + bridge instance, runtime events, and current OS process ancestry before
acting. Never kill, restart, reap, switch, or supersede from a stale row or badge.

## Route by symptom

| Symptom | Open |
|---|---|
| Wrong or stuck `working`/`online`/`blocked`; status conflicts with the console | [status.md](references/status.md) |
| Stop/restart/reset, registration, mode switch, duplicate owner, or safe interrupt | [lifecycle.md](references/lifecycle.md) |
| Queued/claimed run stalls, bridge replacement, workspace/path failure, or no delivery | [dispatch-bridge.md](references/dispatch-bridge.md) |
| Hermes gateway, visible session, wrapper, session split, or console mismatch | [hermes.md](references/hermes.md) |
| Codex approval, resume/thread, app-server, or resident binding failure | [codex.md](references/codex.md) |
| Pi project/session, model, RPC, or wrapper failure | [pi.md](references/pi.md) |
| Dashboard Console rendering, input, copy, or attachment failure | [dashboard-console.md](references/dashboard-console.md) |

When the domain is unclear, start with `dispatch-bridge.md`.

## Diagnostic order

1. Read `comms_agent_info` and, for dispatched work, `comms_run_status`.
2. Identify the exact environment, bridge instance, agent session, terminal, runtime handle, and live process family.
3. Find the matching symptom entry and run its read-only checks before changing state.
4. Apply one recovery action, then verify native behavior and converged control-plane state.

Do not issue a second stop/restart/interrupt because the first acknowledgement looked vague.
Re-read ownership first: the replacement turn or bridge may now be the live target.
