---
name: aify-comms-debug
description: Use when aify-comms dispatch, wake mode, bridge health, managed/resident routing, dashboard Console, or runtime wrapper behavior is broken or confusing.
---

# aify-comms: Troubleshooting

Use this skill whenever something in aify-comms is not behaving the way the main skill says it should. Each entry in the reference files below lists the **symptom**, the **cause**, and the **fix**.

**Always diagnose first.** Before digging into any specific entry, call `comms_agent_info(agentId="target")` on the agent in question and read `wakeMode`, `sessionMode`, `machineId`, `sessionHandle`, and `dispatchState`. Most of the fixes here are just "something in that record is stale or wrong" — confirming the live record narrows the problem to one domain below before you open a reference file.

## Contents

This troubleshooting reference is split by domain. Open the file that matches your symptom:

- **[references/status.md](references/status.md)** — **CHECK THIS FIRST for any "agent is permanently `working` while idle" or "never shows `working` while clearly working" report: its bridge probably has NO `AIFY_AGENT_ID`** (launched without `--aify-agent`), which silently disables the turn detector AND every turn hook — diagnose from the PROCESS env, not the DB, and note it is NOT repairable by re-registering or by Claude Code's in-app `/resume`. Also: the proof-based 6-state status model (working / online / available / blocked / offline / stopped; idle & stale removed), `derive()` as the sole authority (no `status_engine` flag), KEEP-FRESH + KEEP-CLEARED (turn state re-asserted from process truth in both directions), `available→online` timing, derived session status, `online` with no live worker / online-but-queued ("status lied"), `online` without a console (Plan 5 C), managed claude shown `online` while thinking or flapping `online` when the Console is closed, `working` vs `online · awaiting reply`.
- **[references/lifecycle.md](references/lifecycle.md)** — lifecycle verbs (Spawn/Stop/Restart/Reset/Resume-wake, where `recover` went), resident↔managed switching (handle carry, pi/opencode managed-only, mode-switch UI missing), restarting aify-comms = clean slate, send-to-a-managed-agent-with-no-claimer, deferred-reply strand, send rejected (queued work / no online env), registration 409 / superseded or stale bridge, environment still-online or superseded, bridge "lost" the agent, re-register not taking effect, `stopped` but "Console attached".
- **[references/hermes.md](references/hermes.md)** — resident Hermes stale/no-session-evidence, `visible session not found`, wrapper-PTY-unavailable, `ECONNREFUSED`, gateway-WS-failed / port collision / TUI-drops, `mcp test` works but no live tools, `'NoneType' object is not iterable`, `hermes.exe` proliferation, hermes never/wrongly shows `working`, `Queued >180s` gateway-host-died (0.15.1 `--tui`), native ACP fallback, wrapper fell through to plain hermes, console doesn't move, inter-agent delivery cluster, FRESH session after restart.
- **[references/codex.md](references/codex.md)** — resident codex approval prompts despite bypass, `AbsolutePathBuf` / `thread/resume` failures + hard reset, not live-bound when you expected `codex-live`, closed resident codex still receiving work, native app-server fallback session.
- **[references/pi.md](references/pi.md)** — managed OMP reply `(no output)`, `Session ... is in another project`, Cursor-API-key-when-model-is-`default`, synthesized terminal stream vs real PTY, `omp-aify`/`pi-aify` refuses to start ("currently driven by aify-comms"), pi-aify wrapper exits mid-turn.
- **[references/dispatch-bridge.md](references/dispatch-bridge.md)** — dispatches never claimed / stuck `running` / orphaned runs, claude wake-mode + `Session ID already in use` + channel-route queue-forever (incl. Windows localhost/IPv6), steer stayed unread, auto-heal bridge-replaced, team stranded after restart, spawn/workspace `ENOENT` + `\home\dev` paths, machine ID `unknown-host`, install.sh on Windows, wrong-console routing, managed-run cap checks, Plan 5 B queued-managed-run, Plan 6 A stale session handle, child-owned channel-claim check, managed claude freezes on boot, general escalation.
- **[references/dashboard-console.md](references/dashboard-console.md)** — console-mode lock storm / flicker / broken statuses / parsing error / env-not-found / open-terminal, per-keystroke submit, copy-out-of-Console, second Console for an already-running wrapper.

When the right domain is unclear, `references/dispatch-bridge.md` holds the general delivery/claim/bridge-health and escalation entries.
