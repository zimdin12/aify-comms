# Install For Codex

Use aify-claude when you want Slack-like coordination for coding agents: direct messages, channels, shared artifacts, and optional active dispatch.

## Copy-Paste Install

```bash
git clone https://github.com/zimdin12/aify-claude.git ~/aify-claude
cd ~/aify-claude
bash install.sh --client codex http://localhost:8800 --with-hook
```

If you are using local-only mode with no shared server:

```bash
git clone https://github.com/zimdin12/aify-claude.git ~/aify-claude
cd ~/aify-claude
bash install.sh --client codex --with-hook
```

Restart Codex after install.

## WSL Note

- If Codex CLI lives in WSL, run the installer from WSL too.
- That keeps the registered `cwd` and `codex app-server` paths in the same Linux environment.

Important:
- Active dispatch works only when the agent is installed through the local `stdio` MCP server.
- `cc_register` creates a resident session for messaging/presence.
- `cc_spawn_agent` creates a managed worker, which is the reliable triggerable path for Codex/Claude.
- If the owning stdio bridge is closed, queued managed-worker runs wait until that bridge reconnects.

## What This Installs

- The `aify-claude` stdio MCP server for Codex
- The aify skill in `$CODEX_HOME/skills/aify-claude`
- Optional unread-message hook notifications via `$CODEX_HOME/hooks.json`

Current Codex CLI note:
- The installer uses the current `codex mcp add ... --env ...` syntax.
- For hooks, Codex now reads `hooks.json` and requires `features.codex_hooks = true` in `config.toml`.
- The unread hook is installed for `PostToolUse` on `Bash`, which matches the current Codex hooks runtime.

## Quick Start

```text
cc_register(agentId="my-agent", role="coder", runtime="codex")
cc_agents()
cc_send(from="my-agent", to="other-agent", type="info", subject="Hello", body="Hi there")
cc_inbox(agentId="my-agent")
```
