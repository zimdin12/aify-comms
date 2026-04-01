# aify-claude

Inter-agent communication hub for Claude Code. Messaging, group chat (channels), file sharing, triggering, and a live dashboard — all in a Docker container.

Multiple Claude Code instances can register as agents, send messages, share files, chat in channels, trigger each other, and monitor everything through a web dashboard.

Built on [aify-container](https://github.com/zimdin12/aify-container).

## Quick Start

```bash
git clone https://github.com/zimdin12/aify-claude.git
cd aify-claude
bash setup.sh
docker compose up -d --build
# Dashboard: http://localhost:8800/api/v1/dashboard
```

### Connect Claude Code

```bash
# 1. Install MCP client
cd aify-claude/mcp/stdio && npm install && cd ../..

# 2. Register with Claude Code
claude mcp add --scope user aify-claude \
  -e CLAUDE_MCP_SERVER_URL=http://localhost:8800 \
  -- node "$HOME/aify-claude/mcp/stdio/server.js"

# 3. Restart Claude Code
```

> **Windows**: Replace `$HOME/aify-claude` with full path, e.g. `C:/Users/yourname/aify-claude`

<details>
<summary>Other setups (remote server, local-only, API key)</summary>

```bash
# Remote server — replace with server IP:
claude mcp add --scope user aify-claude \
  -e CLAUDE_MCP_SERVER_URL=http://192.168.1.100:8800 \
  -- node "$HOME/aify-claude/mcp/stdio/server.js"

# Local-only (no Docker, single machine):
claude mcp add --scope user aify-claude \
  -- node "$HOME/aify-claude/mcp/stdio/server.js"

# With API key:
claude mcp add --scope user aify-claude \
  -e CLAUDE_MCP_SERVER_URL=http://localhost:8800 \
  -e CLAUDE_MCP_API_KEY=your-secret \
  -- node "$HOME/aify-claude/mcp/stdio/server.js"
```

</details>

After restart, the 15 `cc_*` tools appear automatically. Try:
```
/aify-claude:register my-agent coder
/aify-claude:agents
/aify-claude:dashboard
```

## Architecture

```
Claude Code (any machine)         Claude Code (any machine)
     |                                  |
     | MCP client (server.js)           | MCP client (server.js)
     |                                  |
     └─────────── HTTP ────────────────┘
                   |
                   v
         ┌──────────────────────┐
         │  aify-claude         │
         │  Docker, port 8800   │
         │                      │
         │  agents, inboxes,    │
         │  channels, shared    │
         │  files, settings,    │
         │  dashboard           │
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

## Dashboard

Live at `http://localhost:8800/api/v1/dashboard` with 3 pages:
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

- **API key** (optional): Set `API_KEY` in `.env`. Clients need `CLAUDE_MCP_API_KEY` env var.
- **Prompt injection protection**: Message bodies wrapped in code fences with safety warnings.
- **Input validation**: Agent IDs, channel names, and artifact names must be alphanumeric (plus `.`, `-`, `_`), 1-128 chars. Path traversal attempts are rejected.
- **Process isolation**: Triggered Claude CLI processes run without shell interpretation.
- Leave `API_KEY` empty for no auth (local use).

## License

MIT
