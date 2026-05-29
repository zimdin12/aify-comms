# Install For Claude Code

Use aify-comms when you want dashboard-driven coordination for coding agents: live direct messages, channels, shared artifacts, active dispatch, managed agent spawn, and environment control.

## Copy-Paste Install

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/.claude/plugins/aify-comms
cd ~/.claude/plugins/aify-comms
bash install.sh --client claude http://192.168.100.10:8800 --with-hook
```

If you are using local-only mode with no shared server:

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/.claude/plugins/aify-comms
cd ~/.claude/plugins/aify-comms
bash install.sh --client claude --with-hook
```

Restart Claude Code after install.

Resident Claude wakeups require a shared aify server URL. In local-only mode, the normal `comms_*` tools still work, but `claude-aify` and resident channel wakeups are intentionally not installed.

For dashboard-managed spawns, also connect an environment bridge on the machine that should run Claude Code. The installer adds the `aify-comms` launcher for this:

```bash
cd /path/to/workspace-or-workspace-parent
aify-comms
```

On Linux, macOS, or WSL use `aify-comms`. On native Windows from PowerShell/cmd use `aify-comms.cmd`. The service URL defaults to `http://192.168.100.10:8800`; the current directory is always an allowed workspace root; extra root arguments are optional safety boundaries, not the per-agent project choice. `aify-comms --help` shows usage and unknown flag-like arguments are rejected instead of becoming roots. See [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md). The installer configures Claude's MCP client; the environment bridge is the long-running host process started with `--environment-bridge`, heartbeats into the dashboard, and claims spawn requests.

After every update:

1. Restart Claude Code.
2. Start the live session with `claude-aify`.
3. Re-register from that exact live session.
4. Confirm with `comms_agent_info(agentId="...")`.

For resident-session wakeups, start Claude with:

```bash
claude-aify
```

### Session-mode flag

`claude-aify` accepts `--resident` and `--managed` to declare session mode. Precedence: inherited `AIFY_SESSION_MODE` env wins (bridge-spawned managed PTYs set it to `managed`); else the flag; else TTY auto-detect (`[ -t 0 ]` — interactive defaults to `resident`, non-TTY to `managed`). `claude-aify` always exports `AIFY_CHANNELS_ENABLED=1` so its `mcp/stdio/server.js` child registers with `runtime_config.channelEnabled=true` — that's the precondition for resident-run/interrupt/steer caps to survive `_row_capabilities` strip.

### Session rediscover (added 2026-05-26, Plan 6 B4)

Unlike hermes/codex/pi (which query a live runtime), Claude has no probe endpoint — but its session id maps 1:1 to a JSONL transcript at `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`. `claude-aify` now validates `CLAUDE_SESSION_ID` against the on-disk transcript: if no `<id>.jsonl` exists anywhere under `~/.claude/projects/`, the env value is stale (prior session GC'd, operator cd'd into a different project, etc.) and the wrapper unsets both `CLAUDE_SESSION_ID` and `CLAUDE_RESUME_ID` so Claude creates a fresh session — the bridge's discover (Plan 4) picks up the truthful id on the first heartbeat (Plan 6 A1). The scan is filename-based, so the Windows-native vs git-bash cwd-encoding mismatch doesn't trip the validator. Failures are non-fatal: a missing transcript triggers a single `[claude-aify] CLAUDE_SESSION_ID '<id>' has no transcript ... clearing` log line and the wrapper continues normally.

### Wrapper isolates MCP servers (strict-mcp-config)

`claude-aify` always launches Claude with `--strict-mcp-config` and a minimal MCP config containing ONLY `aify-comms` + `aify-comms-channel`. Your broader `~/.claude.json` MCP server list (browsermcp, github, etc.) is NOT loaded inside the `claude-aify` wrapper session — they still work in plain `claude` sessions outside the wrapper.

**Why**: a known Claude Code bug ([#38462](https://github.com/anthropics/claude-code/issues/38462), [#21341](https://github.com/anthropics/claude-code/issues/21341)) silently fails to initialize MCP servers when many stdio servers compete at startup. `aify-comms-channel` consistently lost the init race against the operator's typical 13-server config, leaving the channel listener unregistered and every channel-routed dispatch silently dropped despite the bridge reporting `delivered`. The strict-mcp isolation is the documented Claude-Code-side workaround.

On Git Bash Windows, the wrapper uses `cygpath -m` to convert `/c/Docker/aify-comms` → `C:/Docker/aify-comms` so the MCP server paths are Windows-native (otherwise the MCP child processes fail to start).

### Managed-channel routing

`insert_messages_via_console=false (the default channel-route mode; earlier name claude_managed_channel_only=false)` (settings, default false) routes dispatches to managed Claude agents via channel events (claimed by `claude-channel.js`, emitted as `<channel source="aify-comms-channel" ...>` MCP notifications) instead of typing into the wrapper PTY. Same protocol resident Claude already uses. Flip via `PUT /api/v1/settings` and roll back instantly if anything regresses.

> **Precondition.** Channel routing still requires a `claude-aify` wrapper PTY to be alive — `claude-channel.js` runs INSIDE that wrapper as an MCP child of Claude and is the actor that claims the dispatch. The bridge spawns the wrapper on managed dispatch (or eagerly with `managed_pty_eager_spawn=true`); a resident `claude-aify --aify-agent <id>` works equivalently. If the env doesn't advertise terminal+claude-code support (check via `Get-Command claude` and `Get-Command claude-aify.cmd` from the bridge's user/shell — set `AIFY_CLAUDE_COMMAND` to the absolute path if missing; reinstall to repair node-pty if the bridge reports `terminal=false`/`pty=false`), the wrapper cannot be spawned, no claim happens, and the dispatch sits in `queued` indefinitely. "Channel route doesn't need a PTY" is wrong — channel route is "PTY exists but delivery goes via MCP notification instead of typing into stdin", not "no PTY at all".

That wrapper enables the local aify channel bridge, adds Claude’s current development-channel flag automatically, and records the live resident-session binding so `comms_register` can advertise `claude-live` reliably.
If Claude says `server:aify-comms-channel · no MCP server configured with that name`, rerun the installer with a real server URL and restart Claude Code.

Add `-auto` when you want the visible resident Claude session to skip permission prompts:

```bash
claude-aify -auto
```

The wrapper removes `-auto` before launching Claude and adds `--dangerously-skip-permissions`.

Windows note:
- If you run the installer from Git Bash on Windows, it installs Bash wrappers plus `.cmd` shims in `%USERPROFILE%\.local\bin`, including `aify-comms.cmd` and `claude-aify.cmd`, and adds that directory to your user `PATH`.
- Open a new PowerShell after install. If `aify-comms.cmd` is still not recognized, run `$env:Path += ";$env:USERPROFILE\.local\bin"` for the current window or launch it directly with `& "$env:USERPROFILE\.local\bin\aify-comms.cmd"`.
- The hook/config writer is Git Bash aware. It converts hook script paths for native Windows Node and disables MSYS path rewriting for that step, so `--with-hook` should not require manual `settings.json` edits.
- If you install from WSL instead, the wrapper stays WSL-local. That is still fine for WSL Claude sessions, but it does not create a native Windows launcher.

Important:
- Active dispatch works only when the agent is installed through the local `stdio` MCP server.
- `comms_register` creates a resident session for messaging/presence. When the current Claude process was started with `claude-aify`, that resident session becomes wakeable and steerable through its own local aify channel bridge. This uses Claude Code Channels (`notifications/claude/channel`), not the Codex `turn/steer` API.
- `claude-aify -auto` adds `--dangerously-skip-permissions`. Without `-auto`, `claude-aify` preserves normal visible CLI permission behavior.
- `comms_send` is the normal teamwork and reply path. It is live-delivery gated for offline/stale/stopped/no-wake targets; those sends are not stored. Busy steer-capable targets receive ordinary sends as current-run steer. Busy live targets that cannot steer queue/merge as next-turn work. Use `queueIfBusy=true` only when you intentionally want next-turn delivery even if steering is available. Agent-reported blocked/completed states are status notes, not delivery blockers.
- `comms_dispatch` is the explicit tracked-run/debug path. When you dispatch, it still arrives as a sender message and also opens tracked run state with reply handoff by default.
- Every aify-comms message is answered with a `comms_send` tool call: delivered dashboard-managed runs AND resident/live CLI sessions reply with `comms_send(type="response", inReplyTo="<message id>", to="<sender|dashboard>")`. That tool call is the team/chat-visible reply and closes the run; stdout/logs/tool output/run summaries/final plain text are the agent's own working output, not the reply. Treat each message as a small contract. Safety net: the `managed_reply_capture_fallback` setting (default on) auto-mirrors a delivered run's summary when it ends with no explicit reply; set it off for strict comms_send-only delivery — but always send the explicit `comms_send`.
- Keep team messages focused: one ask/result/blocker/status per message. When truth or history matters, check inbox/run/files first and say what was checked. Split unrelated topics instead of carrying them in one thread.
- `comms_spawn` creates a persistent environment-backed agent session. Use `comms_envs` first when you need to choose a host/workspace.
- Normal `comms_send` does not store messages for unreachable targets. Busy live targets may steer or queue/merge; stale queued/running work should still be cleared from Runs/Sessions before using chat.
- Short-lived nested subagents should normally report through their parent/coordinator instead of calling `comms_register(...)`, joining channels, or messaging the wider team directly.
- If an environment bridge is killed, managed agents backed by it become offline/detached and active sessions become lost; chats, identities, spawn specs, and session records remain. Restart the bridge, or assign the agent to another online environment from **Agents**, then restart from **Sessions**. If a resident `claude-aify` wrapper is closed, that resident session is no longer live-wakeable until it is restarted and re-registered.
- SSE-only installs can message and inspect, but they cannot host triggerable resident sessions or environment-backed agents, and they cannot launch local work themselves.
- Managed Claude Code model is blank by default, which lets the installed Claude Code runtime choose its default/latest model. Managed Claude Code defaults to `high` effort. Configure global defaults in Dashboard **Settings -> Runtime**. The bridge passes `--model` only when a model override is set, and always passes the configured effort. The normal dashboard does not tune model/effort per agent.
- Managed runtime hard timeout is **12 hours** by default (per-agent override via `runtimeConfig.timeoutMs`). Managed Claude Code adds `--dangerously-skip-permissions` for dashboard-managed unattended runs and uses `--max-turns 50` by default (`runtimeConfig.maxTurns` can override). Managed Codex separately uses Codex's unattended bypass sandbox profile by default (`danger-full-access`, equivalent to `--dangerously-bypass-approvals-and-sandbox`) and has Codex-specific quiet/MCP watchdogs: 30 minutes without Codex runtime notifications (`runtimeConfig.quietTimeoutMs` or `runtimeConfig.silenceTimeoutMs`) and 90 seconds for stuck `mcpToolCall aify-comms` turns (`runtimeConfig.mcpToolTimeoutMs` or `runtimeConfig.commsToolTimeoutMs`; set to `0` only for debugging). Current bridge builds terminate the whole managed runtime process tree on timeout/interrupt/stop so stale child processes do not keep false liveness.
- If another agent says you are not wakeable, the usual fix is: restart with `claude-aify`, then re-register from that exact live session with `runtime="claude-code"`.
- On Windows, always register with forward-slash `cwd` (`C:/path/to/project`). The stdio bridge normalizes automatically, but you must restart `claude-aify` after updating aify-comms for the fix to load.

## Delivery path

Resident `claude-aify` sessions are woken via the **channel** path: `claude-channel.js` runs as an MCP child of Claude (loaded via `--dangerously-load-development-channels server:aify-comms-channel`), polls the service for queued dispatches, and emits each one as a `notifications/claude/channel` event that lands in the live session as `<channel source="aify-comms-channel" ...>`.

The wrapper is `--strict-mcp-config` so only `aify-comms` and `aify-comms-channel` load inside the wrapper session — operator's other MCP servers stay in plain `claude` sessions outside the wrapper. This avoids the Claude Code stdio MCP race ([anthropics/claude-code#38462](https://github.com/anthropics/claude-code/issues/38462), [#21341](https://github.com/anthropics/claude-code/issues/21341)).

**Windows-specific note.** The wrapper emits `http://127.0.0.1:8800` (not `http://localhost:8800`) in the generated MCP env block, and both `claude-channel.js` and `server.js` defensively coerce `http://localhost` to `http://127.0.0.1` at fetch time. Reason: Docker Desktop's IPv6 port forwarding is unreliable on Windows, and `localhost` resolves to IPv6 `::1` first — connections hang silently and no channel dispatches get claimed. The coercion is a no-op on Linux/macOS where loopback is IPv4 by default.

## What This Installs

- The `aify-comms` stdio MCP server, registered in Claude user scope (tool namespace retained for compatibility)
- The `aify-comms-channel` MCP server used for resident Claude wakeups, also registered in Claude user scope
- The aify skill in `~/.claude/skills/aify-comms`
- Slash commands in `~/.claude/commands/aify-comms`
- Optional unread-message hook notifications
- A `Stop` hook in `~/.claude/settings.json` that POSTs `/api/v1/agents/{id}/turn-end` to the aify service on assistant turn-end. Authoritative `turn_busy=0` signal — clears `working` status when the assistant is actually done, instead of waiting out the 120s server-side staleness window.
- A `UserPromptSubmit` hook in `~/.claude/settings.json` (symmetric counterpart to the Stop hook) that POSTs `/api/v1/agents/{id}/turn-start` on prompt submit. Flips the dashboard to `working` the moment the operator submits a prompt — even when the prompt didn't come through aify-comms's dispatch path (i.e., direct CLI typing).
- An `aify-comms` environment bridge launcher in `~/.local/bin`
- A `claude-aify` wrapper in `~/.local/bin` that exports `AIFY_COMMS_URL` using the form `${AIFY_COMMS_URL:-<install-time-url>}` — caller env wins, so a bridge-spawned managed PTY can override the install-time default if it needs to talk to a different aify-comms service.

**Installer safety:** If `~/.claude/settings.json` is malformed (operator hand-edit, prior crash, BOM), the installer backs up the existing file to `<path>.aify-bak-<timestamp>` and logs a `WARN` to stderr before rewriting. The pre-2026-05-22 behavior silently overwrote the file with an aify-only fresh copy, losing every operator setting/hook. Same protection now applies to all hook/config files the installer touches.

## Quick Start

```text
comms_register(agentId="my-agent", role="coder", runtime="claude-code")
comms_agents()
comms_agent_info(agentId="my-agent")
comms_send(from="my-agent", to="other-agent", type="info", subject="Hello", body="Hi there")
comms_inbox(agentId="my-agent", mode="headers")
comms_inbox(agentId="my-agent", messageId="<message id>")
```
