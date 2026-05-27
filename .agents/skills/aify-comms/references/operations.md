# aify-comms Operations Reference

Load this file only for setup, runtime policy, bridge/session repair, or dashboard operations. For routine chat, use the main skill.

## Install Or Update

After every install/update:

1. Rerun the install command from the repo install doc.
2. Restart the affected CLI wrapper/client and long-running `aify-comms` environment bridge.
3. For resident/operator-open sessions only, re-register from the exact live session you want other agents to trigger, or launch with `--aify-agent <agentId>` so the wrapper registers it automatically. Dashboard-managed agents are registered by the environment bridge and should not call `comms_register` from delivered runs.
4. Confirm with `comms_agent_info(agentId="...")`.

Never replace `comms_register` with raw `curl`/Node `POST /api/v1/agents` for
resident agents. That endpoint can update metadata, but it cannot create the
wrapper's bridge heartbeat or dispatch claim loop. A resident record without a
fresh bridge is `stale` and live sends are rejected.

Wrapper auto mode:

- `codex-aify` adds Codex's supported bypass flag by default; use `--safe`, `--no-auto`, or `--no-dangerous-permissions` to opt out for permission debugging.
- `claude-aify -auto` adds `--dangerously-skip-permissions`.
- `omp-aify` / `pi-aify` has no special `-auto` permission mode; model/thinking defaults come from Oh My Pi unless Dashboard Runtime settings or runtime config supplies model/effort.
- `claude-aify --aify-agent <agentId> --resume <session-id>`, `codex-aify --aify-agent <agentId> ...`, and `hermes-aify --aify-agent <agentId> --resume <session-id>` auto-register live resident sessions. `omp-aify` / `pi-aify` can auto-register presence/metadata for a human-open or standalone Pi terminal, but triggerable Pi delivery uses managed RPC.
- Claude Code's valid skip-permissions flag is `--dangerously-skip-permissions`; `--permanently-skip-permissions` is not valid.

## Managed Runtime Policy

- Dashboard-managed identities are already registered by the environment bridge. Do not call `comms_register` inside delivered dashboard-managed runs.
- Terminal-capable managed runtimes use a bridge-owned backing when possible. Dashboard Messenger sends start or reuse that backing, and browser Console attaches to the same process/stream instead of taking over the identity. Managed Claude Code uses an interactive `claude-aify` PTY/channel backing. Managed Codex and Hermes default to bridge-owned `codex-aify` / `hermes-aify` wrapper PTYs (`managed_via_wrapper=["codex","hermes"]`) whose in-process bridge claims channel/resident dispatches. Managed Pi uses a persistent native OMP RPC child and virtual terminal stream.
- Managed Codex uses Codex's unattended bypass profile by default. Managed Claude Code adds `--dangerously-skip-permissions` by default. Operators can override only for debugging.
- Managed Claude Code no longer uses `claude -p`; Claude work starts/reuses an interactive `claude-aify` PTY, confirms the channel prompt when needed, writes the dashboard turn into Claude, and submits it with a separate Enter.
- Three settings shape the managed-delivery surface: `insert_messages_via_console=false` is the default channel-route mode for managed Claude; `managed_pty_eager_spawn=true` with `managed_terminal_backing_enabled=true` proactively launches wrapper PTYs at spawn-request running where applicable; **`managed_via_wrapper=["codex","hermes"]` is the default wrapper-backed path for Codex and Hermes**. Pi is excluded from wrapper mode because OMP is single-client; its dashboard Console is the persistent native RPC virtual terminal (`aify://virtual-rpc/pi`). Flip settings via `PUT /api/v1/settings` and roll back instantly if anything regresses. See DECISIONS.md for the rationale.
- **Important precondition for channel-route managed Claude**: `claude-channel.js` runs INSIDE the `claude-aify` wrapper as an MCP child of Claude, so the wrapper PTY must be alive somewhere to claim the channel dispatch. The bridge spawns that wrapper for managed claude on dispatch (or eagerly with `managed_pty_eager_spawn=true`); a resident `claude-aify --aify-agent <id>` works equivalently. If the env doesn't advertise terminal+claude-code support (`Get-Command claude` / `Get-Command claude-aify` failing on the bridge host, or node-pty not built), the wrapper can't be spawned → no claim → run sits in `queued`. The "channel route doesn't need a PTY" framing is wrong — channel route is "PTY exists but delivery goes via MCP notification instead of typing into stdin", not "no PTY at all".
- All `*-aify` wrappers accept explicit `--resident`/`--managed` flags; precedence is `AIFY_SESSION_MODE` env > flag > TTY auto-detect (`[ -t 0 ]`). Bridge-spawned wrappers always inherit `AIFY_SESSION_MODE=managed`; operator-launched wrappers default to `resident`. `claude-aify` additionally exports `AIFY_CHANNELS_ENABLED=1` so `runtime_config.channelEnabled=true` is set at register (precondition for resident-run/interrupt/steer caps).
- `claude-aify` always passes `--strict-mcp-config` + a temp minimal MCP config (just `aify-comms` + `aify-comms-channel`) regardless of session mode. The operator's other MCP servers are NOT loaded inside the wrapper — they still work in plain `claude` sessions. This isolation works around a Claude-Code stdio MCP init race that silently kills `aify-comms-channel` when many servers compete at startup. On Windows Git Bash the wrapper uses `cygpath -m` to convert install paths to native (`C:/...`) format.
- When wrapper-backed delivery is disabled or unavailable, managed pi/codex/opencode/hermes delivery flows through the bridge's per-runtime controllers in `mcp/stdio/controllers/` (Plan 3 of the 2026-05-25 RuntimeAdapter refactor — `PiController`, `CodexController` + per-mode subclasses, `OpencodeController`, `HermesController` + per-mode subclasses). The adapter contract owns dispatcher selection (`adapter.controllerFor(opts)`). Pi uses this native controller surface today; OpenCode code is retained but install is disabled pending focused validation; Codex and Hermes use it as fallback/debug because their default path is wrapper-backed PTY. Native-controller paths surface a **synthesized terminal_session** so the dashboard's Console pane shows live dispatch activity (`runtime_state.virtualTerminal=true`, `command='aify://virtual-rpc/<runtime>'`):
  - **pi managed** — `aify://virtual-rpc/pi`. RPC child is **persistent**: spawned on the first dispatch (`PiController` + `pi-session.js` pool), reused across subsequent ones, idle-timed out at 24h (`AIFY_PI_IDLE_TIMEOUT_MS`). Each `AgentSessionEvent` (ready, message_update, tool_execution_*, agent_*, error, RpcExtensionUIRequest) is formatted into a human-readable frame. Operator console input buffers until `\r`/`\n` and dispatches a new RPC turn through the same persistent child. Soft watchdog (`GET /agents/{id}/pi-session-state`) lets `omp-aify`/`pi-aify` refuse to launch an external omp on the same session-id while the bridge owns it; `omp-aify --standalone --resume <other-id>` is the escape hatch. Plan 2 removed `pi-session-resume` and pi resident — pi now exclusively uses this managed path.
  - **hermes managed** — default wrapper-backed PTY via `hermes-aify`; the wrapper's child bridge delivers through the local Hermes dashboard gateway (`prompt.submit` / `session.steer`) and dashboard Console renders the real TUI. If wrapper-backed delivery is disabled, native controller fallback uses Hermes process/gateway controllers and synthesized terminal output.
  - **codex managed** — default wrapper-backed PTY via `codex-aify`; the wrapper's child bridge delivers through the local Codex app-server and dashboard Console renders the wrapper TUI. If wrapper-backed delivery is disabled, native controller fallback uses Codex app-server RPC and synthesized terminal output.
  - **opencode managed** — `aify://virtual-rpc/opencode`. Per-dispatch SDK call via `OpencodeController`. Coarser feed than codex because the SDK doesn't expose granular tool events — prompt echo, connecting marker, the final reply (or error), and `■ turn ended`. Full persistent-worker is Phase 6, deferred. Multi-client via `opencode serve` integration is also tracked as a follow-up.
  - Worker auto-close: `worker_idle_close_enabled=true` plus `worker_idle_close_minutes>0` reaps managed worker terminals that have been idle longer than the window AND have no in-flight `dispatch_runs`. It applies to wrapper-backed PTYs (`codex-aify`, `hermes-aify`, etc.) and native virtual RPC terminals (`aify://virtual-rpc/<runtime>`). Real PTYs are asked to stop through bridge terminal controls and go to `stopping`; virtual RPC terminals can be marked `stopped` immediately. Orphaned dispatch_runs (no `claim_bridge_id`, no `dispatch_events` since the cutoff) are reaped at `active_managed_run_stale_minutes` (default 5) by the periodic reconciler — covers both managed-mode AND terminal-mode (wrapper-PTY-backed) orphans, so stuck queued messages unblock in 5 min instead of 30. The dispatch_events evidence requirement prevents false-positive reaping of legitimate slow-claim clients.
  - Reliability: bridge-side `controller.promise.catch` retries the failure-PATCH 3× with exponential backoff; the virtual terminal sink retries POSTs to `/terminals/{id}/output` 3× on transient errors. Closes the gap where a service-restart blip silently drops dispatch failures or text_delta frames.
  - Bridge takeover (2026-05-22): a virtual rpc terminal_session created by an earlier bridge process whose UUID has since changed (every bridge restart picks a fresh `BRIDGE_INSTANCE_ID`) can be written to by a later bridge — the `/terminals/{id}/output` endpoint transfers ownership for `command IN VIRTUAL_RPC_COMMAND_SET` on bridge_id mismatch (audit event `virtual_rpc_bridge_takeover`). Real PTY terminals keep the strict ownership check. Operator-visible win: synth terminal feeds stay continuous across bridge restarts instead of going silent.
- Status taxonomy: `available` (wakeable/spawnable idle; no live worker yet) / `online` (live worker idle) / `working` (turn in progress) / `offline` / `idle` / `blocked` / `stopped`. The `working` state is driven by a layered signal:
  - **Per-runtime turn-start hooks** (claude-aify Stop hook, codex hooks.json Stop, hermes pre_llm_call shell hook) → POST `/agents/{id}/turn-start` → sets `agent_turn_state.turn_busy=1`.
  - **Channel-route dispatch claim** in `claude-channel.js` → also pulses `turn_busy=true`, refreshed every poll cycle while `LAST_DELIVERED_AT_PER_AGENT` is within a 10-min upper-cap.
  - **Cleared authoritatively** by per-runtime turn-end hooks (claude-aify Stop, codex Stop, hermes process exit signaling the controller .resolve), and by `_mark_dispatch_run_answered` when a reply lands. 120s server-side staleness is the safety valve.
  - **Queue gate**: `queueIfBusy=true` defers when ANY of `hasActiveRun`, `queuedRuns>0`, or `turn_busy=1`(fresh) is true. Without the `turn_busy` leg, `require_reply=0` dispatches that auto-complete on delivery would let the queue fire immediately while the assistant is still working — operator-reported bug fixed in commit `00e67ef`.
- Managed runtime defaults are global operator policy in Dashboard Settings, not normal per-agent fields.
- Managed Claude Code and Codex model fields are blank by default, which means runtime default/latest; both default to `high` effort/reasoning effort. Managed Claude Code uses 50 max turns by default (`runtimeConfig.maxTurns` can override). Bridge-spawned `claude-aify` applies model/effort when its PTY starts, so restart existing managed Claude PTYs after changing the global policy. Managed Pi has optional Dashboard Runtime model/effort defaults; blank or `default` model means no `--model` override, and Pi effort is passed as OMP `--thinking` when set.
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

- Prefer wrapper auto-registration when opening a managed session directly: `claude-aify --aify-agent <agentId> --resume <id>`, the dashboard-provided `codex-aify --aify-agent <agentId> ...` resume command, or `hermes-aify --aify-agent <agentId> --resume <id>`. For Pi, `omp-aify --aify-agent <agentId> --resume <id>` is presence/standalone only; use managed RPC for triggerable dashboard delivery.
- Manual `comms_register(...)` from the opened CLI remains the fallback and is still required for a new ID when the wrapper was launched without an ID.
- A resident live-wake identity needs both runtime wake config and a fresh
  wrapper bridge row. `wakeMode: hermes-live` or `claude-live` alone is not
  enough if `status: stale`; restart the visible wrapper and re-register from
  that same session.
- Ownership transfer is manual. A resident wrapper registration records a candidate for later use but does not take over a managed identity or kill a managed PTY. Operators use **Switch to resident/managed** from Sessions or Chat details; active runs block the switch unless forced. Stale resident sends fail visibly until switched or restarted.
- Dashboard **Stop wake** / session **Stop** on a resident identity asks the live resident bridge to terminate its host CLI/app process where the OS allows it.
- Fresh native handles should come from a new spawn or explicit **Recreate**. Ordinary adopt/restart should preserve the stored handle when runtime is unchanged.
- If the saved handle is wrong and the correct native ID is known, use Dashboard **Chat details -> Runtime Session -> Set handle**, **Sessions -> Actions -> Set handle**, or the `/api/v1/agents/{id}/session-handle` endpoint. This updates the saved `sessionHandle`, runtime state, and latest session record without creating a fresh context.

Browser Console is current behavior when an environment advertises terminal/PTY support for the runtime. It is an attachment to the bridge-owned PTY/stream, not a separate resident takeover. Messenger sends while Console is open are delivered through that runtime's backing path (Claude channel, Codex app-server, Hermes gateway, native RPC, or PTY controls depending runtime); Stop Console stops that terminal backing where applicable and returns the session to managed delivery. Dashboard Next hides stale managed PTY widgets while the identity is in `resident` mode, so a resident agent should show a resident attach surface or an explicit unavailable state rather than an old managed buffer. Use **Pause for CLI** only when you intentionally want a separate native terminal to own delivery.

## Multi-Instance Rules

| Runtime | Same project dir | Different project dirs |
|---|---|---|
| `claude-code` | OK with distinct `agentId`s; each `claude-aify` sidecar polls only its bound agent. | OK |
| `codex` | Register with `sessionHandle="$CODEX_THREAD_ID"` and `appServerUrl="$AIFY_CODEX_APP_SERVER_URL"` to avoid ambiguous live markers. | OK |
| `hermes` | Resident: launch `hermes-aify`, then `comms_register(agentId=..., runtime="hermes")` — the bridge auto-detects `gatewayUrl` from `AIFY_HERMES_GATEWAY_URL` exported by the wrapper, flipping wake mode to `hermes-live`. `status: stale` means the wrapper bridge is missing/expired; raw `/api/v1/agents` registration is not sufficient. If you see `hermes-missing-handle`, the wrapper is the old one or wasn't restarted after today's update. | OK |
| `opencode` | Deferred; install disabled pending focused validation. | Do not use as a default target. |
| `pi` | Triggerable delivery is managed RPC only; `omp-aify` / `pi-aify` registration is presence/standalone and must not share a session id with a bridge-owned RPC child. | OK with distinct session handles or `--standalone` |

Never register the same `agentId` from two tabs. Re-registering the same ID supersedes the previous bridge/session binding.

## Dashboard Semantics

- Home/Control is a live operations queue, not a full audit log.
- Work Loop shows reply/work contracts derived from messages and runs. Requests, reviews, and errors are contracts by default; routine `info` is not unless `requireReply` is explicitly set. Hidden contracts are dashboard-local and do not delete audit history; operator close/bulk-close marks selected contracts reviewed while keeping run/chat history.
- Muted live/session/handoff notices stay yellow but stop counting as active red issues.
- Analytics has range selectors and separates historical counts from live capacity.
- Settings is grouped into Appearance, Runtime, Work Loop, and Maintenance.
- The stable dashboard/API stays on `8800`. The replacement dashboard preview, when enabled by compose, is on `8801` and must read/write through the existing `8800` API rather than inventing duplicate message, run, session, or Work Loop state.
- Chat Peek mode lets an operator watch without marking messages read.
- Chat composer Queue is opt-in. A normal unchecked send follows ordinary live `comms_send` semantics; checking Queue sets `queueIfBusy=true`.
- Chat Console attaches to the same managed PTY used for terminal-capable Messenger delivery. Hiding panes or opening Console should not change the identity mode to `cli-takeover`.
- Channel Leave/Remove stops future fan-out for that identity but keeps history; re-add from Chat details to rejoin.
- Sessions hide ended/completed/cancelled rows by default; show ended/debug rows when investigating lifecycle history.

## Status Meanings

Status is computed by a single live-state engine (the same one the dashboard, `comms_agents`, and write paths use), not a self-reported field. Plan 4 finalized the post-`active` taxonomy. Use `comms_agent_info` for the authoritative state.

| Status | Meaning |
|---|---|
| `available` | Env online, agent registered, no live worker yet — sending wakes it (wrapper PTY auto-spawns under `managed_via_wrapper`, or PiSession/HermesSession/etc. spawns on demand). |
| `online` | Worker alive and idle. Internal bridge readiness is folded into this public state, so operators should not see a separate `ready` status. |
| `working` | An open turn: a tracked run is claimed/running, **or** a fresh bridge `turnBusy` heartbeat says the runtime is mid-turn. Plan 4's `mcp/stdio/turn-busy-heartbeat.js` keeps this fresh during long turns. Managed Claude PTY turns are tracked as running until their reply closes the run; if Claude visibly returns to an idle prompt without a chat reply, reconcile closes the turn as completed-without-reply so it becomes audit debt instead of live work. |
| `idle` | Heartbeat past the idle threshold but not yet offline; session may be paused. |
| `offline` | Heartbeat past the offline threshold, or the backing environment is down. |
| `stale` | Resident wrapper bridge heartbeat is missing/expired, or a stored resident binding points at a bridge that no longer owns delivery. Restart the visible wrapper and re-register from it, or switch the identity back to managed. |
| `blocked` | Agent-reported note state, not necessarily unreachable. |
| `stopped` | Wake/dispatch disabled until restart or re-register. |

Legacy `active` is no longer emitted by the live-state engine; old bookmarks/filters that match `active` will not match.

## Repair Hints

- If another agent is not triggerable, inspect `comms_agent_info(agentId="target")` first.
- Codex path errors usually mean stale binding, wrong host path style, or stale bridge/app-server markers.
- Claude `Session ID ... is already in use` means another Claude process owns that native session. Pause/close/take over explicitly; do not silently recreate unless the operator requests it.
- `comms_listen` is deprecated. Do not use it for normal teamwork or managed runs.
