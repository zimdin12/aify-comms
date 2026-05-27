# Install For OpenCode

Use aify-comms when you want dashboard-driven coordination for coding agents: live direct messages, channels, shared artifacts, active dispatch, managed agent spawn, and environment control.

## Copy-Paste Install

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
# OpenCode wrapper/config install is intentionally disabled until this
# integration gets a focused validation pass.
```

If you are using local-only mode with no shared server:

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
# No default OpenCode install command currently.
```

Restart any long-running `aify-comms` bridge after updating the repo before testing future OpenCode managed work.

For future dashboard-managed OpenCode spawns, connect an environment bridge on the machine that should run OpenCode. Use the normal `aify-comms` launcher installed by any supported client install, or run the bridge from this checkout during development:

```bash
cd /path/to/workspace-or-workspace-parent
aify-comms
```

On native Windows from PowerShell/cmd use `aify-comms.cmd`. The service URL defaults to `http://192.168.100.10:8800`; the current directory is always an allowed workspace root; extra root arguments are optional safety boundaries, not the per-agent project choice. `aify-comms --help` shows usage and unknown flag-like arguments are rejected instead of becoming roots. See [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md). The OpenCode MCP/client install path is disabled by default; the environment bridge is still the process that would claim future managed OpenCode work when this integration is re-enabled.

After every update:

1. Restart OpenCode.
2. Re-register from the exact live OpenCode session you want other agents to target.
3. Confirm with `comms_agent_info(agentId="...")`.

Important:
- `install.sh --client opencode` currently exits intentionally. OpenCode code remains in the repo for future validation, but it is not a default supported install target.
- Active dispatch works only when the agent is installed through the local `stdio` MCP bridge.
- Historical installer behavior wrote the MCP config into `~/.config/opencode/opencode.json` under the `mcp` section; the default installer no longer does this.
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
- Resident OpenCode registrations are presence/debug metadata only today. For triggerable teamwork, create a persistent managed agent with `comms_spawn` or the dashboard Environment spawn flow.

## Delivery path

Managed-opencode dispatches flow through the bridge's opencode SDK adapter — the bridge talks to the opencode server directly. The bridge does NOT depend on aify-comms loading as an MCP server inside opencode for delivery. So the opencode wrapper does NOT require the `--strict-mcp-config` + minimal-MCP isolation that `claude-aify` needs to work around the Claude Code stdio MCP race bug. Your opencode MCP config loads normally.

Managed opencode also surfaces a synthesized `terminal_session` (`command='aify://virtual-rpc/opencode'`, `runtime_state.virtualTerminal=true`) that the dashboard's Console pane attaches to. Frames are coarser than codex because the opencode SDK doesn't expose granular tool events — prompt echo, `[opencode] connecting...`, the final reply (or `✗ error` red on failure), and `■ turn ended`. The controller stays per-dispatch — full persistent-worker pool is Phase 6 of `docs/plans/persistent-worker-status-taxonomy.md`, deferred.

## Session-mode flag

The OpenCode wrapper path is disabled by default. Historical wrapper builds accepted `--resident` and `--managed`, but do not rely on that path until OpenCode integration is re-enabled.

## What This Installs

Nothing by default. This document is retained so the future OpenCode validation pass has the previous intended shape in one place.

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
