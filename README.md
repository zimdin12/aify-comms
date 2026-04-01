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

### Client (run on every machine that needs the tools)

> **Important**: Always point at `server.js` in this repo — do NOT copy it elsewhere.

**Option A: install.sh (recommended)**
```bash
git clone https://github.com/zimdin12/aify-claude.git
cd aify-claude
bash install.sh http://localhost:8800 --with-hook
# Restart Claude Code after this
```

For a remote server, replace `localhost` with the server's IP:
```bash
bash install.sh http://192.168.1.100:8800 --with-hook
```

**Option B: SSE (no clone needed, works with any MCP client)**
```bash
claude mcp add --scope user aify-claude --transport sse http://SERVER_IP:8800/mcp/sse
```
Works with Claude Code, OpenCode, Cursor, or any MCP-compatible client.

**Option C: Manual**
```bash
cd aify-claude/mcp/stdio && npm install && cd ../..
claude mcp add --scope user aify-claude \
  -e CLAUDE_MCP_SERVER_URL=http://localhost:8800 \
  -- node "/full/path/to/aify-claude/mcp/stdio/server.js"
```
On Windows use forward slashes: `C:/Users/yourname/aify-claude/mcp/stdio/server.js`

### After install

Restart Claude Code. The 15 `cc_*` tools appear automatically.

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
