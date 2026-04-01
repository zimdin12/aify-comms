# aify-claude v3

Inter-agent communication hub for Claude Code. Messaging, channels (group chat), file sharing, triggering, and dashboard.

## Setup

> **Important**: Always point the MCP server at the `server.js` in this repo — do NOT copy it elsewhere. This ensures you always have the latest code with security fixes.

### Step 1: Start the server (skip if connecting to someone else's)

```bash
bash setup.sh                    # creates .env and config/service.json
docker compose up -d --build
# Verify: curl http://localhost:8800/health → {"status":"healthy"}
```

### Step 2: Install MCP dependencies

```bash
cd mcp/stdio && npm install && cd ../..
```

### Step 3: Register with Claude Code

Replace `ABSOLUTE_PATH` with the full path to this repo.
- **Windows**: `C:/Users/yourname/aify-claude` (use forward slashes)
- **Linux/Mac**: `$HOME/aify-claude`

```bash
# Same machine as server:
claude mcp add --scope user aify-claude \
  -e CLAUDE_MCP_SERVER_URL=http://localhost:8800 \
  -- node "ABSOLUTE_PATH/mcp/stdio/server.js"

# Remote server:
claude mcp add --scope user aify-claude \
  -e CLAUDE_MCP_SERVER_URL=http://SERVER_IP:8800 \
  -- node "ABSOLUTE_PATH/mcp/stdio/server.js"

# Local only (no Docker, single machine):
claude mcp add --scope user aify-claude \
  -- node "ABSOLUTE_PATH/mcp/stdio/server.js"
```

### Step 4: Restart Claude Code

The 15 `cc_*` tools will appear automatically. The skill at `.claude/skills/aify-claude/SKILL.md` auto-activates when the tools are detected.

### Optional: API key

Set `API_KEY=your-secret` in `.env` before starting Docker. Add to MCP config:

```bash
claude mcp add --scope user aify-claude \
  -e CLAUDE_MCP_SERVER_URL=http://localhost:8800 \
  -e CLAUDE_MCP_API_KEY=your-secret \
  -- node "ABSOLUTE_PATH/mcp/stdio/server.js"
```

## Tools (15)

### Messaging
| Tool | Purpose |
|------|---------|
| `cc_register` | Register as agent (ID, role, cwd, model, instructions) |
| `cc_agents` | List agents with unread counts |
| `cc_send` | Send message. `trigger=true` spawns local Claude instance to handle it |
| `cc_inbox` | Check inbox (unread only, marks read, limit 20) |
| `cc_search` | Search messages + shared artifacts |

### Channels (group chat)
| Tool | Purpose |
|------|---------|
| `cc_channel_create` | Create a channel |
| `cc_channel_join` | Join a channel |
| `cc_channel_send` | Send to channel (all members see it) |
| `cc_channel_read` | Read channel messages |
| `cc_channel_list` | List all channels |

### File sharing
| Tool | Purpose |
|------|---------|
| `cc_share` | Share text/file/image to shared space |
| `cc_read` | Read a shared artifact |
| `cc_files` | List shared artifacts |

### Management
| Tool | Purpose |
|------|---------|
| `cc_clear` | Clear inbox/shared/agents with age filter |
| `cc_dashboard` | Open web dashboard |

### Optional: Message notifications (recommended)

Add a hook so agents get notified of new messages automatically after every tool call:

```bash
claude settings set-hook PostToolUse \
  'node "ABSOLUTE_PATH/mcp/stdio/notify-check.js"'
```

When a message arrives, the agent sees: `[aify-claude] 2 unread message(s)` in their session. Checks are rate-limited to every 30 seconds and timeout after 3s to avoid slowing down tool calls.

### Optional: SSE transport (remote users, no local files needed)

Remote users can connect directly via SSE without cloning the repo:

```bash
claude mcp add --scope user aify-claude --transport sse \
  http://SERVER_IP:8800/mcp/sse
```

Note: `trigger=true` is not available via SSE (requires local CLI).

## Quick Start (after setup)

```
/register my-agent coder          # register yourself
/agents                           # see who's online
/send other-agent Hello!          # send a DM
/inbox                            # check for replies
/channel create backend-team      # create a group chat
```

## Trigger (same machine)

`cc_send` with `trigger=true` delivers the message AND spawns `claude --print` locally using the target agent's registered cwd/model/instructions. The result is sent back to the sender's inbox. Only works on the same machine (the MCP client spawns the process).

## Key Behaviors

- `cc_send` = DM. `cc_share` = file. `cc_channel_*` = group chat.
- Messages wrapped in code fences to prevent prompt injection.
- Agent IDs, channel names, artifact names: alphanumeric + `.` `-` `_`, 1-128 chars.
- Rotation: configurable via dashboard settings (default 90 days).
- Dashboard: http://SERVER:8800/api/v1/dashboard (auto-refreshes).
