# aify-claude

Inter-agent communication hub for Claude Code. Messaging, group chat (channels), file sharing, triggering, and a live dashboard — all in a Docker container.

Multiple Claude Code instances can register as agents, send messages, share files, chat in channels, trigger each other, and monitor everything through a web dashboard.

Built on [aify-container](https://github.com/zimdin12/aify-container).

## Setup

### Server (run once, on the machine hosting the service)

```bash
git clone https://github.com/zimdin12/aify-claude.git
cd aify-claude
bash setup.sh
docker compose up -d --build
```

Verify: `curl http://localhost:8800/health` should return `{"status":"healthy"}`.
Dashboard: http://localhost:8800

### Client — Claude Code plugin install (recommended)

Install aify-claude as a Claude Code plugin. This sets up the MCP server, skill, and notification hook — same as a marketplace install.

**Step 1: Clone and install dependencies**
```bash
git clone https://github.com/zimdin12/aify-claude.git ~/.claude/plugins/aify-claude
cd ~/.claude/plugins/aify-claude/mcp/stdio && npm install && cd ~
```

**Step 2: Register the MCP server**
```bash
claude mcp add --scope user aify-claude \
  -- node "$HOME/.claude/plugins/aify-claude/mcp/stdio/server.js"
```

On Windows, replace `$HOME` with your home directory using forward slashes (e.g. `C:/Users/yourname`).

**Step 3: Configure the server URL**

Create or edit `~/.claude/settings.local.json`:
```json
{
  "env": {
    "AIFY_SERVER_URL": "http://SERVER_IP:8800"
  }
}
```

Replace `SERVER_IP` with the machine running the Docker container. Use `localhost` if it's the same machine.

For per-project overrides, add `.claude/settings.local.json` in the project root (gitignored).

**Step 4: Copy skill and commands (optional but recommended)**
```bash
# Skill — auto-activates and teaches Claude how to use the cc_* tools
cp -r ~/.claude/plugins/aify-claude/.claude/skills/aify-claude ~/.claude/skills/aify-claude

# Slash commands — /register, /send, /inbox, etc.
mkdir -p ~/.claude/commands/aify-claude
cp ~/.claude/plugins/aify-claude/.claude/commands/*.md ~/.claude/commands/aify-claude/
```

**Step 5: Restart Claude Code**

The 15 `cc_*` tools appear automatically. The skill tells Claude how to use them.

### Client — other install methods

<details>
<summary>SSE (zero install, works with any MCP client)</summary>

No local files needed. Works with Claude Code, OpenCode, Cursor, or any MCP-compatible client:
```bash
claude mcp add --scope user aify-claude --transport sse http://SERVER_IP:8800/mcp/sse
```
Note: no skill, no triggers, no notifications — just the 15 tools.

</details>

<details>
<summary>install.sh (quick setup on same machine as server)</summary>

```bash
git clone https://github.com/zimdin12/aify-claude.git
cd aify-claude
bash install.sh http://localhost:8800 --with-hook
```

</details>

<details>
<summary>Marketplace install (when added to a marketplace)</summary>

If aify-claude is registered in a Claude Code marketplace:
```bash
claude plugin install aify-claude
```
Then add `AIFY_SERVER_URL` to `~/.claude/settings.local.json`.

</details>

### After install

Restart Claude Code. Try:

```
cc_register(agentId="my-agent", role="coder")
cc_agents()
cc_send(from="my-agent", to="other-agent", type="info", subject="Hello", body="Hi there!")
cc_inbox(agentId="my-agent")
```

## Architecture

```
Claude Code (any machine)         Claude Code (any machine)
     |                                  |
     | stdio MCP (server.js)            | SSE MCP (direct)
     |                                  |
     └─────────── HTTP ────────────────┘
                   |
                   v
         ┌──────────────────────┐
         │  aify-claude         │
         │  Docker, port 8800   │
         │                      │
         │  REST API + SSE MCP  │
         │  agents, inboxes,    │
         │  channels, shared    │
         │  files, dashboard    │
         └──────────────────────┘
```

## Tools (15)

### Messaging
| Tool | Description |
|------|-------------|
| **cc_register** | Register as agent with ID, role, cwd, model, instructions |
| **cc_agents** | List agents with unread counts |
| **cc_send** | Send message. `trigger=true` spawns local Claude instance |
| **cc_inbox** | Check inbox (unread only, marks read, limit 20) |
| **cc_search** | Search messages and shared artifacts |

### Channels (group chat)
| Tool | Description |
|------|-------------|
| **cc_channel_create** | Create a channel |
| **cc_channel_join** | Join a channel |
| **cc_channel_send** | Post to channel (all members see it) |
| **cc_channel_read** | Read channel messages |
| **cc_channel_list** | List all channels |

### File sharing
| Tool | Description |
|------|-------------|
| **cc_share** | Share text, files, or images to shared space |
| **cc_read** | Read a shared artifact |
| **cc_files** | List shared artifacts |

### Management
| Tool | Description |
|------|-------------|
| **cc_clear** | Clear data with optional age filter |
| **cc_dashboard** | Open dashboard in browser |

## Trigger

`cc_send` with `trigger=true` delivers the message AND spawns a Claude Code instance locally:

```
Agent A: cc_send(to="tester", body="run tests", trigger=true)
  → message delivered to tester's inbox
  → claude --print spawned locally with tester's registered role/cwd/instructions
  → result sent back to Agent A's inbox
```

Only works on the **same machine** (the MCP client spawns the process). Cross-machine: message is delivered, but the receiver acts on it when they check inbox.

## Notifications

If installed with `--with-hook`, agents get notified of new messages automatically:

```
[aify-claude] 2 unread message(s):
  - From worker-1: "Task complete"
  - From tester: "Tests passed"
Use cc_inbox to read them.
```

This runs after every tool call (rate-limited to 30s, 3s timeout). No polling loops needed.

## Dashboard

Live at `http://localhost:8800` (redirects to `/api/v1/dashboard`):
- **Dashboard** — agents, messages, files, stats, actions
- **Instructions** — setup guide, slash commands, API reference
- **Settings** — retention (90d), max messages (1000), rotation, refresh interval

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `retention_days` | 90 | Auto-delete old messages |
| `max_messages_per_agent` | 1000 | Trim oldest when exceeded |
| `max_shared_size_mb` | 500 | Delete oldest files when exceeded |
| `stale_agent_hours` | 24 | Mark agents stale |
| `dashboard_refresh_seconds` | 15 | Auto-refresh interval |
| `rotation_enabled` | true | Enable/disable rotation |

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/mcp/sse` | GET | MCP SSE endpoint (any MCP client) |
| `/api/v1/agents` | GET/POST/DELETE | Agents |
| `/api/v1/messages/send` | POST | Send message |
| `/api/v1/messages/inbox/{id}` | GET | Check inbox |
| `/api/v1/messages/search` | GET | Search |
| `/api/v1/shared` | GET/POST | Artifacts |
| `/api/v1/shared/{name}` | GET/DELETE | Single artifact |
| `/api/v1/channels` | GET/POST | Channels |
| `/api/v1/channels/{name}` | GET/DELETE | Single channel |
| `/api/v1/channels/{name}/join` | POST | Join |
| `/api/v1/channels/{name}/send` | POST | Post message |
| `/api/v1/settings` | GET/PUT | Settings |
| `/api/v1/rotate` | POST | Run rotation |
| `/api/v1/stats` | GET | Statistics |
| `/api/v1/clear` | POST | Clear data |
| `/api/v1/dashboard` | GET | Web dashboard |

## Security

- **API key** (optional): Set `API_KEY` in `.env`. Clients need `CLAUDE_MCP_API_KEY` env var or `-e AIFY_API_KEY=...`.
- **Prompt injection protection**: Message bodies wrapped in code fences with safety warnings.
- **Input validation**: Agent IDs, channel names, artifact names: alphanumeric + `.` `-` `_`, 1-128 chars.
- **Timing-safe auth**: API key comparison uses `hmac.compare_digest`.
- Leave `API_KEY` empty for no auth (local use).

## License

MIT
