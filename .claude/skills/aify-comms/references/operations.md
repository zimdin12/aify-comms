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
- `omp-aify` / `pi-aify` has no special `-auto` permission mode; model/thinking defaults come from Oh My Pi unless Dashboard Runtime settings or runtime config supplies model/effort.
- `claude-aify --aify-agent <agentId> --resume <session-id>`, `codex-aify --aify-agent <agentId> ...`, and `omp-aify --aify-agent <agentId> --resume <session-id>` auto-register that live resident session.
- Claude Code's valid skip-permissions flag is `--dangerously-skip-permissions`; `--permanently-skip-permissions` is not valid.

## Managed Runtime Policy

- Dashboard-managed identities are already registered by the environment bridge. Do not call `comms_register` inside delivered dashboard-managed runs.
- Terminal-capable managed runtimes use a bridge-owned PTY when possible. Dashboard Messenger sends start or reuse that PTY, and browser Console attaches to the same backing process instead of taking over the identity. Managed Claude Code uses the PTY as its interactive `claude-aify` backing; Messenger work is written into Claude and submitted as a real terminal turn, with an active run/`working` status until the reply closes it. Hermes still uses PTY-input delivery with a delivered Work Loop contract.
- Managed Codex uses Codex's unattended bypass profile by default. Managed Claude Code adds `--dangerously-skip-permissions` by default. Operators can override only for debugging.
- Managed Claude Code no longer uses `claude -p`; Claude work starts/reuses an interactive `claude-aify` PTY, confirms the channel prompt when needed, writes the dashboard turn into Claude, and submits it with a separate Enter.
- Two opt-in settings (both default off) override the managed-delivery surface: `insert_messages_via_console=false (the default channel-route mode; earlier name claude_managed_channel_only=false)` switches managed Claude from PTY-input delivery to channel events (claimed by `claude-channel.js`, emitted as `<channel source="aify-comms-channel" ...>` MCP notifications — same protocol resident Claude already uses); `managed_pty_eager_spawn=true` (with `managed_terminal_backing_enabled=true`) proactively launches the wrapper PTY at spawn-request running for pi/codex/opencode/hermes so the console pre-exists before the first dispatch. Flip via `PUT /api/v1/settings` and roll back instantly if anything regresses. See DECISIONS.md for the rationale.
- **Important precondition for channel-route managed Claude**: `claude-channel.js` runs INSIDE the `claude-aify` wrapper as an MCP child of Claude, so the wrapper PTY must be alive somewhere to claim the channel dispatch. The bridge spawns that wrapper for managed claude on dispatch (or eagerly with `managed_pty_eager_spawn=true`); a resident `claude-aify --aify-agent <id>` works equivalently. If the env doesn't advertise terminal+claude-code support (`Get-Command claude` / `Get-Command claude-aify` failing on the bridge host, or node-pty not built), the wrapper can't be spawned → no claim → run sits in `queued`. The "channel route doesn't need a PTY" framing is wrong — channel route is "PTY exists but delivery goes via MCP notification instead of typing into stdin", not "no PTY at all".
- All `*-aify` wrappers accept explicit `--resident`/`--managed` flags; precedence is `AIFY_SESSION_MODE` env > flag > TTY auto-detect (`[ -t 0 ]`). Bridge-spawned wrappers always inherit `AIFY_SESSION_MODE=managed`; operator-launched wrappers default to `resident`. `claude-aify` additionally exports `AIFY_CHANNELS_ENABLED=1` so `runtime_config.channelEnabled=true` is set at register (precondition for resident-run/interrupt/steer caps).
- `claude-aify` always passes `--strict-mcp-config` + a temp minimal MCP config (just `aify-comms` + `aify-comms-channel`) regardless of session mode. The operator's other MCP servers are NOT loaded inside the wrapper — they still work in plain `claude` sessions. This isolation works around a Claude-Code stdio MCP init race that silently kills `aify-comms-channel` when many servers compete at startup. On Windows Git Bash the wrapper uses `cygpath -m` to convert install paths to native (`C:/...`) format.
- When `insert_messages_via_console=false` (the default), managed pi/codex/opencode delivery flows through the bridge's native RPC adapters (`createPiController`, `createCodexController`, opencode SDK) — NOT through a visible wrapper PTY. For managed **pi** the RPC child is **persistent**: spawned on the first dispatch (`createPiControllerManaged` + `pi-session.js` pool), reused across subsequent ones, idle-timed out at 24h (`AIFY_PI_IDLE_TIMEOUT_MS`). Each `AgentSessionEvent` (ready, message_update, tool_execution_*, agent_*, error, RpcExtensionUIRequest) is formatted into a human-readable frame and streamed into a **synthesized** `terminal_session` row marked with `command='aify://virtual-rpc/pi'` and `runtime_state.virtualTerminal=true` — the dashboard's Console pane shows this synthesized stream rather than a real PTY. Operator console input on this virtual terminal buffers until `\r`/`\n` and is dispatched as a new RPC turn through the persistent child. A soft watchdog (`GET /agents/{id}/pi-session-state`) lets `omp-aify`/`pi-aify` refuse to launch an external omp on the same session-id while the bridge owns it; `omp-aify --standalone --resume <other-id>` is the escape hatch. Codex/opencode still use the legacy per-dispatch native-RPC path with no synthesized terminal.
- Managed runtime defaults are global operator policy in Dashboard Settings, not normal per-agent fields.
- Managed Claude Code and Codex model fields are blank by default, which means runtime default/latest; both default to `high` effort/reasoning effort. Managed Claude Code uses 50 max turns by default (`runtimeConfig.maxTurns` can override). Managed Pi has optional Dashboard Runtime model/effort defaults; blank or `default` model means no `--model` override, and Pi effort is passed as OMP `--thinking` when set.
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

- Prefer wrapper auto-registration when opening a managed session directly: `claude-aify --aify-agent <agentId> --resume <id>`, the dashboard-provided `codex-aify --aify-agent <agentId> ...` resume command, or `omp-aify --aify-agent <agentId> --resume <id>`.
- Manual `comms_register(...)` from the opened CLI remains the fallback and is still required for a new ID when the wrapper was launched without an ID.
- Ownership transfer is turn-boundary safe. Active managed work defers resident takeover until the run ends. Closing the resident CLI lets dashboard sends return to managed backing after the resident lease expires. Reopening with `--aify-agent` switches ownership back to resident once it is safe.
- Dashboard **Stop wake** / session **Stop** on a resident identity asks the live resident bridge to terminate its host CLI/app process where the OS allows it.
- Fresh native handles should come from a new spawn or explicit **Recreate**. Ordinary adopt/restart should preserve the stored handle when runtime is unchanged.
- If the saved handle is wrong and the correct native ID is known, use Dashboard **Chat details -> Runtime Session -> Set handle**, **Sessions -> Actions -> Set handle**, or the `/api/v1/agents/{id}/session-handle` endpoint. This updates the saved `sessionHandle`, runtime state, and latest session record without creating a fresh context.

Browser Console is current behavior when an environment advertises terminal/PTY support for the runtime. It is an attachment to the bridge-owned PTY, not a separate resident takeover. Messenger sends while Console is open are forwarded into the active PTY; Stop Console stops that terminal backing and returns the session to managed delivery. Use **Pause for CLI** only when you intentionally want a separate native terminal to own delivery.

## Multi-Instance Rules

| Runtime | Same project dir | Different project dirs |
|---|---|---|
| `claude-code` | OK with distinct `agentId`s; each `claude-aify` sidecar polls only its bound agent. | OK |
| `codex` | Register with `sessionHandle="$CODEX_THREAD_ID"` and `appServerUrl="$AIFY_CODEX_APP_SERVER_URL"` to avoid ambiguous live markers. | OK |
| `hermes` | Prefer `hermes-aify --aify-agent <id> --resume <session-id>` when a resumable ID is known; dashboard-managed Hermes can run as a PTY-backed warm process while the PTY is alive. | OK |
| `opencode` | OK with explicit `sessionHandle` per session. | OK |
| `pi` | OK with explicit `sessionHandle` per session. | OK |

Never register the same `agentId` from two tabs. Re-registering the same ID supersedes the previous bridge/session binding.

## Dashboard Semantics

- Home/Control is a live operations queue, not a full audit log.
- Work Loop shows reply/work contracts derived from messages and runs. Requests, reviews, and errors are contracts by default; routine `info` is not unless `requireReply` is explicitly set. Hidden contracts are dashboard-local and do not delete audit history; operator close/bulk-close marks selected contracts reviewed while keeping run/chat history.
- Muted live/session/handoff notices stay yellow but stop counting as active red issues.
- Analytics has range selectors and separates historical counts from live capacity.
- Settings is grouped into Appearance, Runtime, Work Loop, and Maintenance.
- The stable dashboard/API stays on `8800`. The replacement dashboard preview, when enabled by compose, is on `8801` and must read/write through the existing `8800` API rather than inventing duplicate message, run, session, or Work Loop state.
- Chat Peek mode lets an operator watch without marking messages read.
- Chat Console attaches to the same managed PTY used for terminal-capable Messenger delivery. Hiding panes or opening Console should not change the identity mode to `cli-takeover`.
- Channel Leave/Remove stops future fan-out for that identity but keeps history; re-add from Chat details to rejoin.
- Sessions hide ended/completed/cancelled rows by default; show ended/debug rows when investigating lifecycle history.

## Status Meanings

Status is computed by a single live-state engine (the same one the dashboard, `comms_agents`, and write paths use), not a self-reported field. `active` means connected/heartbeating; it does not mean working. Use `comms_agent_info` for actual state.

| Status | Meaning |
|---|---|
| `active` | Bridge alive and fresh, but no open turn — connected/idle-capable, not currently doing work. |
| `working` | An open turn: a tracked run is claimed/running, **or** a fresh bridge `turnBusy` heartbeat says the runtime is mid-turn. Managed Claude PTY turns are tracked as running until their reply closes the run; if Claude visibly returns to an idle prompt without a chat reply, reconcile closes the turn as completed-without-reply so it becomes audit debt instead of live work. Attached-but-quiet consoles and delivered/awaiting-reply resident-channel contracts stay `active`. |
| `idle` | Heartbeat past the idle threshold but not yet offline; session may be paused. |
| `offline` | Heartbeat past the offline threshold, or the backing environment is down. |
| `blocked` | Agent-reported note state, not necessarily unreachable. |
| `stopped` | Wake/dispatch disabled until restart or re-register. |

## Repair Hints

- If another agent is not triggerable, inspect `comms_agent_info(agentId="target")` first.
- Codex path errors usually mean stale binding, wrong host path style, or stale bridge/app-server markers.
- Claude `Session ID ... is already in use` means another Claude process owns that native session. Pause/close/take over explicitly; do not silently recreate unless the operator requests it.
- `comms_listen` is deprecated. Do not use it for normal teamwork or managed runs.
