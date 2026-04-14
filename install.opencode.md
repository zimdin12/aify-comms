# Install For OpenCode

Use aify-claude when you want Slack-like coordination for coding agents: direct messages, channels, shared artifacts, and active dispatch.

## Copy-Paste Install

```bash
git clone https://github.com/zimdin12/aify-claude.git ~/aify-claude
cd ~/aify-claude
bash install.sh --client opencode http://localhost:8800
```

If you are using local-only mode with no shared server:

```bash
git clone https://github.com/zimdin12/aify-claude.git ~/aify-claude
cd ~/aify-claude
bash install.sh --client opencode
```

Restart OpenCode after install.

After every update:

1. Restart OpenCode.
2. Re-register from the exact live OpenCode session you want other agents to target.
3. Confirm with `cc_agent_info(agentId="...")`.

Important:
- Active dispatch works only when the agent is installed through the local `stdio` MCP bridge.
- The installer writes the MCP config into `~/.config/opencode/opencode.json` under the `mcp` section.
- `cc_register` creates a resident session for messaging/presence. OpenCode managed workers are fully supported. Resident OpenCode resume also works when you register with a real `sessionHandle`.
- `cc_spawn_agent` creates a managed worker for detached/background execution and durable session state.
- If the owning stdio bridge is closed, queued resident/managed runs wait until that bridge reconnects.
- SSE-only installs can message and inspect, but they cannot host triggerable resident sessions or managed workers, and they cannot launch local work themselves.
- If another agent says you are a resident OpenCode session without a bound session handle, either re-register with `sessionHandle="<session-id>"` or use `cc_spawn_agent` for a managed worker.

## What This Installs

- The `aify-claude` local MCP server for OpenCode
- A config entry in `~/.config/opencode/opencode.json`

Current OpenCode note:
- Managed-worker dispatch uses the official OpenCode SDK/server flow.
- Resident OpenCode triggering currently depends on a real `sessionHandle`; it does not auto-bind arbitrary existing sessions yet.
- Interrupt is supported. Steering is not wired for OpenCode yet.
- Hook-based unread notifications are not installed yet for OpenCode.

## Quick Start

```text
cc_register(agentId="my-agent", role="coder", runtime="opencode")
cc_agents()
cc_agent_info(agentId="my-agent")
cc_send(from="my-agent", to="other-agent", type="info", subject="Hello", body="Hi there")
cc_inbox(agentId="my-agent")
```
