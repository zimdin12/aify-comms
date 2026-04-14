# Install For Claude Code

Use aify-claude when you want Slack-like coordination for coding agents: direct messages, channels, shared artifacts, and optional active dispatch.

## Copy-Paste Install

```bash
git clone https://github.com/zimdin12/aify-claude.git ~/.claude/plugins/aify-claude
cd ~/.claude/plugins/aify-claude
bash install.sh --client claude http://localhost:8800 --with-hook
```

If you are using local-only mode with no shared server:

```bash
git clone https://github.com/zimdin12/aify-claude.git ~/.claude/plugins/aify-claude
cd ~/.claude/plugins/aify-claude
bash install.sh --client claude --with-hook
```

Restart Claude Code after install.

Important:
- Active dispatch works only when the agent is installed through the local `stdio` MCP server.
- `cc_register` creates a resident session for messaging/presence.
- `cc_spawn_agent` creates a managed worker, which is the reliable triggerable path for Codex/Claude.
- If the owning stdio bridge is closed, queued managed-worker runs wait until that bridge reconnects.

## What This Installs

- The `aify-claude` stdio MCP server
- The aify skill in `~/.claude/skills/aify-claude`
- Slash commands in `~/.claude/commands/aify-claude`
- Optional unread-message hook notifications

## Quick Start

```text
cc_register(agentId="my-agent", role="coder")
cc_agents()
cc_send(from="my-agent", to="other-agent", type="info", subject="Hello", body="Hi there")
cc_inbox(agentId="my-agent")
```
