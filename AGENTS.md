# aify-Codex v3

Inter-agent communication hub for Codex, Claude Code, and other MCP-connected coding agents. Messaging, channels (group chat), file sharing, active dispatch, and dashboard.

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

Fast install docs for agents:
- Claude Code: [install.claude.md](/D:/Docker%20Storage/Images/aify-claude/install.claude.md)
- Codex: [install.codex.md](/D:/Docker%20Storage/Images/aify-claude/install.codex.md)

### Step 3: Register with Codex

Replace `ABSOLUTE_PATH` with the full path to this repo.
- **Windows**: `C:/Users/yourname/aify-Codex` (use forward slashes)
- **Linux/Mac**: `$HOME/aify-Codex`

```bash
# Same machine as server:
codex mcp add aify-Codex \
  --env CLAUDE_MCP_SERVER_URL=http://localhost:8800 \
  -- node "ABSOLUTE_PATH/mcp/stdio/server.js"

# Remote server:
codex mcp add aify-Codex \
  --env CLAUDE_MCP_SERVER_URL=http://SERVER_IP:8800 \
  -- node "ABSOLUTE_PATH/mcp/stdio/server.js"

# Local only (no Docker, single machine):
codex mcp add aify-Codex \
  -- node "ABSOLUTE_PATH/mcp/stdio/server.js"
```

### Step 4: Restart Codex

The 23 `cc_*` tools will appear automatically. The skill at `.Codex/skills/aify-Codex/SKILL.md` auto-activates when the tools are detected.

### Optional: API key

Set `API_KEY=your-secret` in `.env` before starting Docker. Add to MCP config:

```bash
codex mcp add aify-Codex \
  --env CLAUDE_MCP_SERVER_URL=http://localhost:8800 \
  --env CLAUDE_MCP_API_KEY=your-secret \
  -- node "ABSOLUTE_PATH/mcp/stdio/server.js"
```

## Tools (23)

### Messaging
| Tool | Purpose |
|------|---------|
| `cc_register` | Register as agent (ID, role, cwd, model, instructions, runtime metadata) |
| `cc_agents` | List agents with unread counts and live status |
| `cc_status` | Set status + note: `cc_status("working", note="NRD pipeline")` |
| `cc_agent_info` | Check another agent's status, unread count, last read message |
| `cc_send` | Send message with optional `priority`. `trigger=true` also queues active dispatch |
| `cc_dispatch` | Queue active runtime dispatch explicitly and return run IDs |
| `cc_inbox` | Check inbox (newest first, replies include parent context) |
| `cc_unsend` | Delete a message by ID |
| `cc_search` | Search messages + shared artifacts |
| `cc_run_status` | Inspect a dispatched run and its recent events |
| `cc_run_interrupt` | Request interruption of an active dispatched run |
| `cc_run_steer` | Send additional guidance to an active run when steering is supported |

### Channels (group chat)
| Tool | Purpose |
|------|---------|
| `cc_channel_create` | Create a channel |
| `cc_channel_join` | Join yourself or add another agent to a channel |
| `cc_channel_send` | Send to channel (delivered to all members' inboxes) |
| `cc_channel_read` | Read channel messages with pagination |
| `cc_channel_list` | List all channels |

### File sharing
| Tool | Purpose |
|------|---------|
| `cc_share` | Share text, files, PNGs, or binaries to shared space |
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
mkdir -p ~/.codex
cat > ~/.codex/hooks.json <<'EOF'
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "node \"ABSOLUTE_PATH/mcp/stdio/notify-check.js\"",
            "statusMessage": "Checking aify unread messages",
            "timeout": 3
          }
        ]
      }
    ]
  }
}
EOF

if ! grep -q '^\[features\]' ~/.codex/config.toml 2>/dev/null; then
  printf '\n[features]\ncodex_hooks = true\n' >> ~/.codex/config.toml
elif ! grep -q '^[[:space:]]*codex_hooks[[:space:]]*=' ~/.codex/config.toml; then
  awk '/^\[features\]$/{print; print "codex_hooks = true"; next}1' ~/.codex/config.toml > ~/.codex/config.toml.tmp && mv ~/.codex/config.toml.tmp ~/.codex/config.toml
fi
```

With the current Codex hooks runtime, `PostToolUse` only fires for `Bash`, so this unread check runs after Bash tool use rather than after every possible tool call.

### Optional: SSE transport (remote users, no local files needed)

Remote users can connect directly via SSE without cloning the repo:

```bash
codex mcp add aify-Codex --url \
  http://SERVER_IP:8800/mcp/sse
```

Note: active dispatch is not available via SSE (requires a local stdio MCP server with runtime access).

## Quick Start (after setup)

```
/register my-agent coder          # register yourself
/agents                           # see who's online
/send other-agent Hello!          # send a DM
/inbox                            # check for replies
/channel create backend-team      # create a group chat
```

## Active Dispatch

`cc_send(trigger=true)` and `cc_dispatch(...)` queue work on the server. The target agent's own stdio MCP server claims that run and starts it locally on the correct runtime. Codex agents use `codex app-server` with a persistent thread per agent.

If Codex auto-detection is wrong, pass `runtime="codex"` to `cc_register`.

WSL note:
- If your Codex CLI lives in WSL, prefer running the Codex-side MCP server from inside WSL so the registered `cwd` is already a Linux path.
- When the bridge runs on Windows, it defaults to `wsl.exe -e codex app-server` for Codex launches.

Current limits:
- One active dispatched run per registered agent.
- Claude supports interruption but not in-flight steering.
- Codex supports both interruption and steering.
- Unexpected permission prompts or user-input requests can still fail a dispatched run.
- Unsupported runtimes stay message-only unless a dedicated runtime adapter is added.

Recommended roles:
- `manager`, `coder`, `tester`, `reviewer`, `researcher`, `architect`

## Key Behaviors

- `cc_send` = DM. `cc_share` = file. `cc_channel_*` = group chat.
- Messages wrapped in code fences to prevent prompt injection.
- Agent IDs, channel names, artifact names: alphanumeric + `.` `-` `_`, 1-128 chars.
- Rotation: configurable via dashboard settings (default 90 days).
- Dashboard: http://SERVER:8800/api/v1/dashboard (auto-refreshes).
