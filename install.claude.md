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

On native Windows from PowerShell/cmd use `aify-comms.cmd`. The service URL defaults to `http://localhost:8800`; the current directory is always an allowed workspace root; extra root arguments are optional. See [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md). The installer configures Claude's MCP client; the environment bridge is the long-running host process started with `--environment-bridge`, heartbeats into the dashboard, and claims spawn requests.

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

Windows note:
- If you run the installer from Git Bash on Windows, it installs Bash wrappers plus `.cmd` shims in `%USERPROFILE%\.local\bin`, including `aify-comms.cmd` and `claude-aify.cmd`, and adds that directory to your user `PATH`.
- Open a new PowerShell after install. If `aify-comms.cmd` is still not recognized, run `$env:Path += ";$env:USERPROFILE\.local\bin"` for the current window or launch it directly with `& "$env:USERPROFILE\.local\bin\aify-comms.cmd"`.
- The hook/config writer is Git Bash aware. It converts hook script paths for native Windows Node and disables MSYS path rewriting for that step, so `--with-hook` should not require manual `settings.json` edits.
- If you install from WSL instead, the wrapper stays WSL-local. That is still fine for WSL Claude sessions, but it does not create a native Windows launcher.

Important:
- Active dispatch works only when the agent is installed through the local `stdio` MCP server.
- `comms_register` creates a resident session for messaging/presence. When Claude is started with `claude-aify`, that resident session becomes wakeable through the local aify channel bridge.
- `comms_send` is the normal teamwork path and is live-delivery gated. If the target is offline, stale, stopped, already working, already has queued work, or lacks a live wake path, no message is written. Agent-reported blocked/completed states are status notes, not delivery blockers.
- `comms_dispatch` is the explicit tracked-run/debug path. When you dispatch, it still arrives as a sender message and also opens tracked run state with reply handoff by default.
- Explicit `comms_send(..., inReplyTo=...)` replies are preferred, but a reply-dispatch back to the sender also satisfies the handoff. If a required reply never arrives, the bridge mirrors the run result back to the sender as a fallback handoff.
- `comms_spawn` creates a persistent environment-backed agent session. Use `comms_envs` first when you need to choose a host/workspace.
- Normal `comms_send` does not append to future queues. Advanced dispatch/run-control APIs may still expose queued runs for already-created work; clear stale queued runs before using chat.
- Short-lived nested subagents should normally report through their parent/coordinator instead of calling `comms_register(...)`, joining channels, or messaging the wider team directly.
- If an environment bridge is killed, managed teammates backed by it become offline/detached and active sessions become lost; chats, identities, spawn specs, and session records remain. Restart the bridge, or assign the teammate to another online environment from **Team**, then recover/restart from **Sessions**. If a resident `claude-aify` wrapper is closed, that resident session is no longer live-wakeable until it is restarted and re-registered.
- SSE-only installs can message and inspect, but they cannot host triggerable resident sessions or environment-backed agents, and they cannot launch local work themselves.
- Default dispatch timeout is **2 hours** (per-agent override via `runtimeConfig.timeoutMs`). Managed Codex also has a 15-minute quiet-stall watchdog (`runtimeConfig.quietTimeoutMs` or `runtimeConfig.silenceTimeoutMs`) so silent Codex turns fail cleanly instead of sitting in `running` until the hard timeout.
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
