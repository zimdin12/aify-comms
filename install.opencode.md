# Install For OpenCode

Use aify-comms when you want dashboard-driven coordination for coding agents: live direct messages, channels, shared artifacts, active dispatch, managed agent spawn, and environment control.

## Copy-Paste Install

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
bash install.sh --client opencode http://localhost:8800
```

If you are using local-only mode with no shared server:

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
bash install.sh --client opencode
```

Restart OpenCode after install.

For dashboard-managed spawns, also connect an environment bridge on the machine that should run OpenCode. The installer adds the `aify-comms` launcher for this:

```bash
cd /path/to/workspace-or-workspace-parent
aify-comms
```

On native Windows from PowerShell/cmd use `aify-comms.cmd`. The service URL defaults to `http://localhost:8800`; the current directory is always an allowed workspace root; extra root arguments are optional safety boundaries, not the per-agent project choice. `aify-comms --help` shows usage and unknown flag-like arguments are rejected instead of becoming roots. See [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md). The installer configures OpenCode's MCP client; the environment bridge is the long-running host process started with `--environment-bridge`, heartbeats into the dashboard, and claims spawn requests.

After every update:

1. Restart OpenCode.
2. Re-register from the exact live OpenCode session you want other agents to target.
3. Confirm with `comms_agent_info(agentId="...")`.

Important:
- Active dispatch works only when the agent is installed through the local `stdio` MCP bridge.
- The installer writes the MCP config into `~/.config/opencode/opencode.json` under the `mcp` section.
- `comms_register` creates a resident session for messaging/presence. Persistent environment-backed OpenCode agents are supported through `comms_spawn`. Resident OpenCode resume also works when you register with a real `sessionHandle`.
- `comms_send` is the normal teamwork and reply path. It is live-delivery gated for offline/stale/stopped/no-wake targets; those sends are not stored. Busy steer-capable targets receive ordinary sends as current-run steer. Busy live targets that cannot steer queue/merge as next-turn work. Use `queueIfBusy=true` only when you intentionally want next-turn delivery even if steering is available. Agent-reported blocked/completed states are status notes, not delivery blockers.
- `comms_dispatch` is the explicit tracked-run/debug path. When you dispatch, it still arrives as a sender message and also opens tracked run state with reply handoff by default.
- Delivered dashboard-managed runs should answer the current message in final plain text. The bridge captures and stores/threads that final answer into chat. Treat each message as a small contract and do not rely on stdout/logs/tool output/run summaries as the team-visible answer. Use `comms_send(...)` from managed runs only for separate out-of-band/proactive messages or to schedule the next owner/self-wake; resident/live CLI sessions should still reply to inbox messages with `comms_send(type="response", inReplyTo=...)`.
- Keep team messages focused: one ask/result/blocker/status per message. When truth or history matters, check inbox/run/files first and say what was checked. Split unrelated topics instead of carrying them in one thread.
- `comms_spawn` creates a persistent environment-backed agent session. Use `comms_envs` first when you need to choose a host/workspace.
- Normal `comms_send` does not store messages for unreachable targets. Busy live targets may steer or queue/merge; stale queued/running work should still be cleared from Runs/Sessions before using chat.
- Short-lived nested subagents should normally report through their parent/coordinator instead of calling `comms_register(...)`, joining channels, or messaging the wider team directly.
- If an environment bridge is killed, managed agents backed by it become offline/detached and active sessions become lost; chats, identities, spawn specs, and session records remain. Restart the bridge, or assign the agent to another online environment from **Agents**, then restart from **Sessions**.
- SSE-only installs can message and inspect, but they cannot host triggerable resident sessions or environment-backed agents, and they cannot launch local work themselves.
- Managed runtime hard timeout is **12 hours** by default (per-agent override via `runtimeConfig.timeoutMs`). Current bridge builds terminate the whole managed runtime process tree on timeout/interrupt/stop so stale child processes do not keep false liveness. Managed Codex has additional Codex-specific watchdogs: 30 minutes without Codex runtime notifications (`runtimeConfig.quietTimeoutMs` or `runtimeConfig.silenceTimeoutMs`) and 90 seconds for stuck `mcpToolCall aify-comms` turns (`runtimeConfig.mcpToolTimeoutMs` or `runtimeConfig.commsToolTimeoutMs`; set to `0` only for debugging).
- If another agent says you are a resident OpenCode session without a bound session handle, either re-register with `sessionHandle="<session-id>"` or create a persistent agent with `comms_spawn`.

## What This Installs

- The `aify-comms` local MCP server for OpenCode (tool namespace retained for compatibility)
- A config entry in `~/.config/opencode/opencode.json`

Current OpenCode note:
- Environment-managed OpenCode sessions use the official OpenCode SDK/server flow.
- Resident OpenCode triggering currently depends on a real `sessionHandle`; it does not auto-bind arbitrary existing sessions yet.
- Interrupt is supported. Steering is not wired for OpenCode yet.
- Hook-based unread notifications are not installed yet for OpenCode.

## Quick Start

```text
comms_register(agentId="my-agent", role="coder", runtime="opencode")
comms_agents()
comms_agent_info(agentId="my-agent")
comms_send(from="my-agent", to="other-agent", type="info", subject="Hello", body="Hi there")
comms_inbox(agentId="my-agent", mode="headers")
comms_inbox(agentId="my-agent", messageId="<message id>")
```
