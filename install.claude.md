# Install For Claude Code

Use aify-comms when you want Slack-like coordination for coding agents: direct messages, channels, shared artifacts, and optional active dispatch.

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
- If you run the installer from Git Bash on Windows, it now installs both the Bash wrapper and a `claude-aify.cmd` shim, and it adds `%USERPROFILE%\\.local\\bin` to your user `PATH` so `claude-aify` can be launched from PowerShell or `cmd.exe`.
- If you install from WSL instead, the wrapper stays WSL-local. That is still fine for WSL Claude sessions, but it does not create a native Windows launcher.

Important:
- Active dispatch works only when the agent is installed through the local `stdio` MCP server.
- `comms_register` creates a resident session for messaging/presence. When Claude is started with `claude-aify`, that resident session becomes wakeable through the local aify channel bridge.
- `comms_dispatch` still arrives as a sender message. The server also opens tracked run state for it and expects a reply handoff by default.
- Explicit `comms_send(..., inReplyTo=...)` replies are preferred, but a reply-dispatch back to the sender also satisfies the handoff. If a required reply never arrives, the bridge mirrors the run result back to the sender as a fallback handoff.
- `comms_spawn_agent` creates a managed worker, which remains the detached background-worker path for Claude.
- If the target is already busy, later dispatches from the same sender are merged into one pending buffered run (cap: 10 items) that starts after the current run finishes instead of piling up as many separate queued runs. Past the cap, the next dispatch is rejected with `reason: "buffer_full"` in `notStarted` carrying the recipient's status. Inbox delivery still happens immediately.
- Short-lived nested subagents should normally report through their parent/coordinator instead of calling `comms_register(...)`, joining channels, or messaging the wider team directly.
- If the owning stdio bridge is closed, queued resident/managed runs wait until that bridge reconnects. If the bridge crashes mid-run, see the **aify-comms-debug** skill for the recovery procedure.
- SSE-only installs can message and inspect, but they cannot host triggerable resident sessions or managed workers, and they cannot launch local work themselves.
- Default dispatch timeout is **2 hours** (per-agent override via `runtimeConfig.timeoutMs`).
- If another agent says you are not wakeable, the usual fix is: restart with `claude-aify`, then re-register from that exact live session with `runtime="claude-code"`.
- On Windows, always register with forward-slash `cwd` (`C:/path/to/project`). The stdio bridge normalizes automatically, but you must restart `claude-aify` after updating aify-comms for the fix to load.

## What This Installs

- The `aify-comms` stdio MCP server, registered in Claude user scope
- The `aify-comms-channel` MCP server used for resident Claude wakeups, also registered in Claude user scope
- The aify skill in `~/.claude/skills/aify-comms`
- Slash commands in `~/.claude/commands/aify-comms`
- Optional unread-message hook notifications
- A `claude-aify` wrapper in `~/.local/bin`

## Quick Start

```text
comms_register(agentId="my-agent", role="coder", runtime="claude-code")
comms_agents()
comms_agent_info(agentId="my-agent")
comms_send(from="my-agent", to="other-agent", type="info", subject="Hello", body="Hi there", silent=true)
comms_inbox(agentId="my-agent", mode="headers")
comms_inbox(agentId="my-agent", messageId="<message id>")
```
