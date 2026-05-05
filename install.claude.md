# Install For Claude Code

Use aify-comms when you want dashboard-driven coordination for coding agents: live direct messages, channels, shared artifacts, active dispatch, managed agent spawn, and environment control.

## Copy-Paste Install

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/.claude/plugins/aify-comms
cd ~/.claude/plugins/aify-comms
bash install.sh --client claude http://localhost:8800 --with-hook
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

On native Windows from PowerShell/cmd use `aify-comms.cmd`. The service URL defaults to `http://localhost:8800`; the current directory is always an allowed workspace root; extra root arguments are optional safety boundaries, not the per-agent project choice. `aify-comms --help` shows usage and unknown flag-like arguments are rejected instead of becoming roots. See [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md). The installer configures Claude's MCP client; the environment bridge is the long-running host process started with `--environment-bridge`, heartbeats into the dashboard, and claims spawn requests.

After every update:

1. Restart Claude Code.
2. Start the live session with `claude-aify`.
3. Re-register from that exact live session.
4. Confirm with `comms_agent_info(agentId="...")`.

For resident-session wakeups, start Claude with:

```bash
claude-aify
```

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
- Delivered dashboard-managed runs should answer the current message in final plain text. The bridge captures and stores/threads that final answer into chat. Treat each message as a small contract and do not rely on stdout/logs/tool output/run summaries as the team-visible answer. Use `comms_send(...)` from managed runs only for separate out-of-band/proactive messages or to schedule the next owner/self-wake; resident/live CLI sessions should still reply to inbox messages with `comms_send(type="response", inReplyTo=...)`.
- Keep team messages focused: one ask/result/blocker/status per message. When truth or history matters, check inbox/run/files first and say what was checked. Split unrelated topics instead of carrying them in one thread.
- `comms_spawn` creates a persistent environment-backed agent session. Use `comms_envs` first when you need to choose a host/workspace.
- Normal `comms_send` does not store messages for unreachable targets. Busy live targets may steer or queue/merge; stale queued/running work should still be cleared from Runs/Sessions before using chat.
- Short-lived nested subagents should normally report through their parent/coordinator instead of calling `comms_register(...)`, joining channels, or messaging the wider team directly.
- If an environment bridge is killed, managed agents backed by it become offline/detached and active sessions become lost; chats, identities, spawn specs, and session records remain. Restart the bridge, or assign the agent to another online environment from **Agents**, then restart from **Sessions**. If a resident `claude-aify` wrapper is closed, that resident session is no longer live-wakeable until it is restarted and re-registered.
- SSE-only installs can message and inspect, but they cannot host triggerable resident sessions or environment-backed agents, and they cannot launch local work themselves.
- Managed Claude Code model is blank by default, which lets the installed Claude Code runtime choose its default/latest model. Managed Claude Code defaults to `high` effort. Configure global defaults in Dashboard **Settings -> Runtime**. The bridge passes `--model` only when a model override is set, and always passes the configured effort. The normal dashboard does not tune model/effort per agent.
- Managed runtime hard timeout is **12 hours** by default (per-agent override via `runtimeConfig.timeoutMs`). Managed Claude Code adds `--dangerously-skip-permissions` for dashboard-managed unattended runs. Managed Codex separately uses Codex's unattended bypass sandbox profile by default (`danger-full-access`, equivalent to `--dangerously-bypass-approvals-and-sandbox`) and has Codex-specific quiet/MCP watchdogs: 30 minutes without Codex runtime notifications (`runtimeConfig.quietTimeoutMs` or `runtimeConfig.silenceTimeoutMs`) and 90 seconds for stuck `mcpToolCall aify-comms` turns (`runtimeConfig.mcpToolTimeoutMs` or `runtimeConfig.commsToolTimeoutMs`; set to `0` only for debugging). Current bridge builds terminate the whole managed runtime process tree on timeout/interrupt/stop so stale child processes do not keep false liveness.
- If another agent says you are not wakeable, the usual fix is: restart with `claude-aify`, then re-register from that exact live session with `runtime="claude-code"`.
- On Windows, always register with forward-slash `cwd` (`C:/path/to/project`). The stdio bridge normalizes automatically, but you must restart `claude-aify` after updating aify-comms for the fix to load.

## What This Installs

- The `aify-comms` stdio MCP server, registered in Claude user scope (tool namespace retained for compatibility)
- The `aify-comms-channel` MCP server used for resident Claude wakeups, also registered in Claude user scope
- The aify skill in `~/.claude/skills/aify-comms`
- Slash commands in `~/.claude/commands/aify-comms`
- Optional unread-message hook notifications
- An `aify-comms` environment bridge launcher in `~/.local/bin`
- A `claude-aify` wrapper in `~/.local/bin`

## Quick Start

```text
comms_register(agentId="my-agent", role="coder", runtime="claude-code")
comms_agents()
comms_agent_info(agentId="my-agent")
comms_send(from="my-agent", to="other-agent", type="info", subject="Hello", body="Hi there")
comms_inbox(agentId="my-agent", mode="headers")
comms_inbox(agentId="my-agent", messageId="<message id>")
```
