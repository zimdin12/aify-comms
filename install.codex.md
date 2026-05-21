# Install For Codex

Use aify-comms when you want dashboard-driven coordination for coding agents: live direct messages, channels, shared artifacts, active dispatch, managed agent spawn, and environment control.

## Copy-Paste Install

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
bash install.sh --client codex http://192.168.100.10:8800 --with-hook
```

If you are using local-only mode with no shared server:

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
bash install.sh --client codex --with-hook
```

Restart Codex after install.

For dashboard-managed spawns, also connect an environment bridge on the machine that should run Codex. The installer adds the `aify-comms` launcher for this:

```bash
cd /path/to/workspace-or-workspace-parent
aify-comms
```

On native Windows from PowerShell/cmd use `aify-comms.cmd`. The service URL defaults to `http://192.168.100.10:8800`; the current directory is always an allowed workspace root; extra root arguments are optional safety boundaries, not the per-agent project choice. `aify-comms --help` shows usage and unknown flag-like arguments are rejected instead of becoming roots. See [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md). The installer configures Codex's MCP client; the environment bridge is the long-running host process started with `--environment-bridge`, heartbeats into the dashboard, and claims spawn requests.

After every update:

1. Restart Codex.
2. If you want visible live wakeups, start the session with `codex-aify`.
3. Re-register from that exact live Codex session.
4. Confirm with `comms_agent_info(agentId="...")`.

For the live-wake path, start Codex with:

```bash
codex-aify
```

That wrapper starts a local `codex app-server --listen ws://127.0.0.1:...`, launches the visible TUI with `codex --remote ...`, and records that shared app-server binding locally so aify can usually auto-discover the live thread, register the session as `codex-live`, and send resident turns back into the same visible session path.

Add `-auto` when you want the visible resident Codex session to bypass approvals/sandbox prompts:

```bash
codex-aify -auto
```

The wrapper removes `-auto` before launching Codex and adds the best permission flag supported by the installed Codex CLI.

### Session-mode flag

`codex-aify` accepts `--resident` and `--managed`. Precedence: inherited `AIFY_SESSION_MODE` env wins (bridge-spawned managed PTYs set it to `managed`); else the flag; else TTY auto-detect via `[ -t 0 ]` — interactive defaults to `resident`, non-TTY to `managed`. Use the explicit flag only when TTY detection might be wrong for your shell context (most operators never need it).

### Delivery path

Managed-codex dispatches flow through the bridge's `createCodexController` native RPC adapter — the bridge connects to the codex app-server (over WebSocket) and drives turns directly. The bridge does NOT need an aify-comms MCP server inside the codex CLI session for delivery to work. This means `codex-aify` does NOT require the `--strict-mcp-config` + minimal-MCP isolation that `claude-aify` needs to work around the [Claude Code stdio MCP race bug](https://github.com/anthropics/claude-code/issues/38462). Your codex MCP servers (whatever you have configured in `~/.codex/config.toml` or equivalent) load normally inside `codex-aify`.

Windows note:
- If you run the installer from Git Bash on Windows, it installs Bash wrappers plus `.cmd` shims in `%USERPROFILE%\.local\bin`, including `aify-comms.cmd` and `codex-aify.cmd`, and adds that directory to your user `PATH`.
- Open a new PowerShell after install. If `aify-comms.cmd` is still not recognized, run `$env:Path += ";$env:USERPROFILE\.local\bin"` for the current window or launch it directly with `& "$env:USERPROFILE\.local\bin\aify-comms.cmd"`.
- If you install from WSL instead, the wrapper stays WSL-local. That is still the right setup for WSL Codex, but it does not create a native Windows launcher.

Recommended registration from inside `codex-aify`:

```text
comms_register(agentId="my-agent", role="coder", runtime="codex", cwd="<native-path-to-project>", sessionHandle="$CODEX_THREAD_ID", appServerUrl="$AIFY_CODEX_APP_SERVER_URL")
```

Use a native path for the runtime you are actually running:
- WSL/Linux Codex: `/mnt/...` or other native Linux paths
- native Windows Codex: `C:/...` with forward slashes

Fallback order if that does not flip to `codex-live`:

1. Drop `sessionHandle` + `appServerUrl`: `comms_register(..., runtime="codex")`.
2. Re-add `sessionHandle="$CODEX_THREAD_ID"` from the same session.
3. Add back `appServerUrl` when multiple `codex-aify` sessions run on the same machine or the wrapper was launched from a different directory than the `cwd` you registered.

### Windows `cwd` trap

Codex CLI is Rust-based and its path deserializer rejects Windows backslash paths with `Invalid request: AbsolutePathBuf deserialized without a base path`, which kills every dispatched run instantly. Always register with forward slashes:

```text
cwd="C:/Users/you/project"     # correct
cwd="C:\\Users\\you\\project"  # triggers the trap
```

The stdio bridge now normalizes `\` → `/` automatically at dispatch time, but you must **restart `codex-aify` after updating aify-comms** to load the fix. If you still see the error, the bridge is running stale code.

### If things go wrong

Troubleshooting lives in the **aify-comms-debug** skill (loaded automatically alongside the main skill). It covers:

- `AbsolutePathBuf deserialized without a base path` and the full hard-reset sequence
- Stuck `running` dispatches (orphaned runs) and how to cancel them via the API
- not live-bound when you expected `codex-live`
- live-send rejections, stale bridge claims, and more

If the debug skill isn't loaded in your session, see `.claude/skills/aify-comms-debug/SKILL.md` in this repo.

## WSL Note

- If Codex CLI lives in WSL, run the installer from WSL too.
- That keeps the registered `cwd` and `codex app-server` paths in the same Linux environment.

Important:
- Active dispatch works only when the agent is installed through the local `stdio` MCP server.
- `comms_register` creates a resident session for messaging/presence and, for Codex, captures the live `thread.id` when available.
- If started with `codex-aify`, resident wakeups use the same WebSocket app-server as the visible TUI and show up as `codex-live`. The dispatched sender message and final answer both appear in the visible TUI — expected.
- `codex-aify -auto` adds `--dangerously-bypass-approvals-and-sandbox`. The wrapper does not use the older `--full-auto` alias. Without `-auto`, `codex-aify` preserves normal visible CLI permission behavior.
- `comms_send` is the normal teamwork and reply path. It is live-delivery gated for offline/stale/stopped/no-wake targets; those sends are not stored. Busy steer-capable targets receive ordinary sends as current-run steer. Busy live targets that cannot steer queue/merge as next-turn work. Use `queueIfBusy=true` only when you intentionally want next-turn delivery even if steering is available. Agent-reported blocked/completed states are status notes, not delivery blockers.
- `comms_dispatch` is the explicit tracked-run/debug path. When you dispatch, it still arrives as a sender message and also opens tracked run state with reply handoff by default.
- Delivered dashboard-managed runs should answer the current message in final plain text. The bridge captures and stores/threads that final answer into chat. Treat each message as a small contract and do not rely on stdout/logs/tool output/run summaries as the team-visible answer. Use `comms_send(...)` from managed runs only for separate out-of-band/proactive messages or to schedule the next owner/self-wake; resident/live CLI sessions should still reply to inbox messages with `comms_send(type="response", inReplyTo=...)`.
- Keep team messages focused: one ask/result/blocker/status per message. When truth or history matters, check inbox/run/files first and say what was checked. Split unrelated topics instead of carrying them in one thread.
- Plain `codex` (not `codex-aify`) falls back to `codex-thread-resume`, which resumes the stored thread through a separate hidden app-server.
- `comms_spawn` creates a persistent environment-backed agent session. Use `comms_envs` first when you need to choose a host/workspace.
- Normal `comms_send` does not store messages for unreachable targets. Busy live targets may steer or queue/merge; stale queued/running work should still be cleared from Runs/Sessions before using chat.
- Short-lived nested subagents should normally report through their parent/coordinator instead of calling `comms_register(...)`, joining channels, or messaging the wider team directly.
- If an environment bridge is killed, managed agents backed by it become offline/detached and active sessions become lost; chats, identities, spawn specs, and session records remain. Restart the bridge, or assign the agent to another online environment from **Agents**, then restart from **Sessions**. If a resident `codex-aify` wrapper is closed, that resident session is no longer live-wakeable until it is restarted and re-registered.
- SSE-only installs can message and inspect, but they cannot host triggerable resident sessions or environment-backed agents.
- Managed Codex model is blank by default, which lets the installed Codex runtime choose its default/latest model. Managed Codex defaults to `high` reasoning effort. Configure global defaults in Dashboard **Settings -> Runtime**. The normal dashboard does not tune model/effort per agent. Repo fallback lives in `mcp/stdio/runtimes.js` (`managedCodexConfigText` / `createCodexController`).
- Managed runtime hard timeout is **12 hours** by default (`runtimeConfig.timeoutMs`). Managed Codex uses Codex's unattended bypass sandbox profile by default (`danger-full-access`, equivalent to `--dangerously-bypass-approvals-and-sandbox`) so managed agents can call MCP tools without hidden approval cancellation; set `runtimeConfig.sandboxMode="workspace-write"` only for permission debugging. Managed Codex also has a quiet-stall watchdog of **30 minutes** without Codex runtime notifications/stderr after the last observed activity (`runtimeConfig.quietTimeoutMs` or `runtimeConfig.silenceTimeoutMs`). A narrower aify-comms MCP tool-call watchdog fails stuck `mcpToolCall aify-comms` turns after **90 seconds** by default (`runtimeConfig.mcpToolTimeoutMs` or `runtimeConfig.commsToolTimeoutMs`; set to `0` only for debugging). Current WSL/Linux bridge builds terminate the whole managed Codex process tree on timeout/interrupt/stop so orphan MCP tool servers do not keep stale state alive. Set the quiet timeout to `0` only for agents expected to run very long silent commands.
- If another agent says you are a resident Codex session without a bound session handle, restart Codex and re-register from the live session.

## What This Installs

- The `aify-comms` stdio MCP server for Codex (tool namespace retained for compatibility)
- The aify skill in `$CODEX_HOME/skills/aify-comms`
- Optional unread-message hook notifications via `$CODEX_HOME/hooks.json`
- An `aify-comms` environment bridge launcher in `~/.local/bin`
- A `codex-aify` wrapper in `~/.local/bin`

Current Codex CLI note:
- The installer uses the current `codex mcp add ... --env ...` syntax.
- For hooks, Codex now reads `hooks.json` and requires `features.codex_hooks = true` in `config.toml`.
- The unread hook is installed for `PostToolUse` on `Bash`, which matches the current Codex hooks runtime.
- Re-running the installer removes stale duplicate aify unread-hook entries, even if an older install used a different repo path.
- Resident triggering only works when the bridge talks to the same Codex installation/thread store that created the live session. A Windows desktop session and a WSL CLI session are different stores.
- `codex-aify` avoids the extra hidden-resume hop by pointing both the visible TUI and aify at the same local WebSocket app-server.

## Quick Start

```text
comms_register(agentId="my-agent", role="coder", runtime="codex", sessionHandle="$CODEX_THREAD_ID", appServerUrl="$AIFY_CODEX_APP_SERVER_URL")
comms_agents()
comms_agent_info(agentId="my-agent")
comms_send(from="my-agent", to="other-agent", type="info", subject="Hello", body="Hi there")
comms_inbox(agentId="my-agent", mode="headers")
comms_inbox(agentId="my-agent", messageId="<message id>")
```
