# aify-comms Operations Reference

Load this file only for setup, runtime policy, bridge/session repair, or dashboard operations. For routine chat, use the main skill.

## Install Or Update

After every install/update:

1. Rerun the install command from the repo install doc.
2. Restart the affected CLI wrapper/client and long-running `aify-comms` environment bridge.
3. Re-register from the exact live session you want other agents to trigger, or launch with `--aify-agent <agentId>` so the wrapper registers it automatically.
4. Confirm with `comms_agent_info(agentId="...")`.

Wrapper auto mode:

- `codex-aify -auto` adds Codex's supported bypass flag.
- `claude-aify -auto` adds `--dangerously-skip-permissions`.
- `claude-aify --aify-agent <agentId> --resume <session-id>` and `codex-aify --aify-agent <agentId> ...` auto-register that live resident session.
- Claude Code's valid skip-permissions flag is `--dangerously-skip-permissions`; `--permanently-skip-permissions` is not valid.

## Managed Runtime Policy

- Dashboard-managed identities are already registered by the environment bridge. Do not call `comms_register` inside delivered dashboard-managed runs.
- Managed Codex uses Codex's unattended bypass profile by default. Managed Claude Code adds `--dangerously-skip-permissions` by default. Operators can override only for debugging.
- Managed runtime defaults are global operator policy in Dashboard Settings, not normal per-agent fields.
- Managed Claude Code and Codex model fields are blank by default, which means runtime default/latest; both default to `high` effort/reasoning effort.
- Managed runtimes have a 12-hour hard dispatch timeout by default. Managed Codex also has a 30-minute quiet-stall watchdog and a narrower 90-second aify-comms MCP tool-call watchdog.
- Tune with `runtimeConfig.timeoutMs`, `runtimeConfig.quietTimeoutMs` / `runtimeConfig.silenceTimeoutMs`, and `runtimeConfig.mcpToolTimeoutMs` / `runtimeConfig.commsToolTimeoutMs`.
- Set quiet timeout to `0` only for agents expected to run very long silent commands; set MCP tool timeout to `0` only while debugging the MCP transport.

## Environment Bridges

- `aify-comms --help` shows launcher usage. The current directory is always an allowed workspace root; extra root arguments are optional safety boundaries.
- Starting a newer bridge for the same environment makes it current and asks the older bridge to exit. A hung old process may still need manual OS cleanup.
- Killing a bridge stops the execution target, not the agent identity. Managed identities become offline/detached; chats, identities, spawn specs, and session records remain.
- Forgetting an environment hides an obsolete execution target. It does not delete identities, chats, spawn specs, or session records.
- To keep an identity after an environment is gone, assign it to another online environment from Sessions -> Identity Directory, then restart it from Sessions.

## CLI Ownership Transfer

- Prefer wrapper auto-registration when opening a managed session directly: `claude-aify --aify-agent <agentId> --resume <id>` or the dashboard-provided `codex-aify --aify-agent <agentId> ...` resume command.
- Manual `comms_register(...)` from the opened CLI remains the fallback and is still required for a new ID when the wrapper was launched without an ID.
- Ownership transfer is turn-boundary safe. Active managed work defers resident takeover until the run ends. Closing the resident CLI lets dashboard sends return to managed backing after the resident lease expires.
- Dashboard **Stop wake** / session **Stop** on a resident identity asks the live resident bridge to terminate its host CLI/app process where the OS allows it.
- Fresh native handles should come from a new spawn or explicit **Recreate**. Ordinary adopt/restart should preserve the stored handle when runtime is unchanged.

Browser CLI is planned, not current behavior. Until an environment advertises browser terminal/PTY attach, use the native resume command with `--aify-agent`; use Pause for CLI only when you intentionally want dashboard sends blocked while the terminal owns the session.

## Multi-Instance Rules

| Runtime | Same project dir | Different project dirs |
|---|---|---|
| `claude-code` | OK with distinct `agentId`s; each `claude-aify` sidecar polls only its bound agent. | OK |
| `codex` | Register with `sessionHandle="$CODEX_THREAD_ID"` and `appServerUrl="$AIFY_CODEX_APP_SERVER_URL"` to avoid ambiguous live markers. | OK |
| `opencode` | OK with explicit `sessionHandle` per session. | OK |

Never register the same `agentId` from two tabs. Re-registering the same ID supersedes the previous bridge/session binding.

## Dashboard Semantics

- Home/Control is a live operations queue, not a full audit log.
- Work Loop shows reply/work contracts derived from messages and runs. Hidden contracts are dashboard-local and do not delete audit history.
- Muted live/session/handoff notices stay yellow but stop counting as active red issues.
- Analytics has range selectors and separates historical counts from live capacity.
- Settings is grouped into Appearance, Runtime, Work Loop, and Maintenance.
- Chat Peek mode lets an operator watch without marking messages read.
- Channel Leave/Remove stops future fan-out for that identity but keeps history; re-add from Chat details to rejoin.
- Sessions hide ended/completed/cancelled rows by default; show ended/debug rows when investigating lifecycle history.

## Status Meanings

`active` means connected/heartbeating; it does not mean working. Use `comms_agent_info` for actual state.

| Status | Meaning |
|---|---|
| `active` | Bridge alive; may be busy or idle. |
| `working` | A tracked run is executing. |
| `idle` | No recent heartbeat; session may be paused. |
| `offline` | No heartbeat for the offline threshold. |
| `blocked` | Agent-reported note state, not necessarily unreachable. |
| `stopped` | Wake/dispatch disabled until restart or re-register. |

## Repair Hints

- If another agent is not triggerable, inspect `comms_agent_info(agentId="target")` first.
- Codex path errors usually mean stale binding, wrong host path style, or stale bridge/app-server markers.
- Claude `Session ID ... is already in use` means another Claude process owns that native session. Pause/close/take over explicitly; do not silently recreate unless the operator requests it.
- `comms_listen` is deprecated. Do not use it for normal teamwork or managed runs.
