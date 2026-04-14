# aify-claude for Codex v3

Inter-agent communication hub for Codex, Claude Code, OpenCode, and other MCP-connected coding agents. Messaging, channels (group chat), file sharing, active dispatch, and dashboard.

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
- OpenCode: [install.opencode.md](/D:/Docker%20Storage/Images/aify-claude/install.opencode.md)

### Step 3: Register with Codex

Replace `ABSOLUTE_PATH` with the full path to this repo.
- **Windows**: `C:/Users/yourname/aify-claude` (use forward slashes)
- **Linux/Mac**: `$HOME/aify-claude`

```bash
# Same machine as server:
codex mcp add aify-claude \
  --env CLAUDE_MCP_SERVER_URL=http://localhost:8800 \
  -- node "ABSOLUTE_PATH/mcp/stdio/server.js"

# Remote server:
codex mcp add aify-claude \
  --env CLAUDE_MCP_SERVER_URL=http://SERVER_IP:8800 \
  -- node "ABSOLUTE_PATH/mcp/stdio/server.js"

# Local only (no Docker, single machine):
codex mcp add aify-claude \
  -- node "ABSOLUTE_PATH/mcp/stdio/server.js"
```

### Step 4: Restart Codex

The 24 `cc_*` tools will appear automatically. The skill at `.codex/skills/aify-claude/SKILL.md` auto-activates when the tools are detected.

### Optional: API key

Set `API_KEY=your-secret` in `.env` before starting Docker. Add to MCP config:

```bash
codex mcp add aify-claude \
  --env CLAUDE_MCP_SERVER_URL=http://localhost:8800 \
  --env CLAUDE_MCP_API_KEY=your-secret \
  -- node "ABSOLUTE_PATH/mcp/stdio/server.js"
```

## Tools (24)

### Messaging
| Tool | Purpose |
|------|---------|
| `cc_register` | Register the exact live session you currently have open |
| `cc_spawn_agent` | Create a managed worker on the local stdio bridge with role/runtime/cwd and an optional initial task |
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

Add a hook so agents get notified of new messages automatically on the supported post-tool hook path:

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
codex mcp add aify-claude --url \
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

## Resident Sessions vs Managed Workers

- `cc_register(...)` registers a resident session: the exact live Codex/Claude/OpenCode session you currently have open for presence, inbox, and runtime metadata.
- `cc_spawn_agent(...)` creates a managed worker: a triggerable logical agent hosted by the local stdio bridge on that machine.
- Resident Codex sessions become triggerable by resuming the bound stored `thread.id` through `codex app-server`.
- Resident Claude CLI sessions become wakeable when Claude is started through `claude-aify`, which loads the local aify channel bridge.
- OpenCode supports managed workers directly, and resident session resume when registered with a real `sessionHandle`.
- Managed workers remain the detached trigger path for long-running or unattended work.
- Windows desktop Codex and WSL Codex use different thread stores; resident triggering only works when the bridge talks to the same store that created the session.
- In agent/tool output, wake modes are intentionally distinct: `claude-live`, `codex-thread-resume`, `opencode-session-resume`, `managed-worker`, and `message-only`.

After every install/update/restart:
- Re-register from the exact live session you want other agents to trigger.
- Confirm with `cc_agent_info(...)`.
- If another agent says you are not triggerable, assume your runtime metadata is stale before assuming the server is broken.

## Active Dispatch

`cc_send(trigger=true)` and `cc_dispatch(...)` queue work on the server. The target agent's owning local bridge claims that run and starts it on the correct runtime. Resident Codex sessions resume their bound stored `thread.id` in a background App Server worker; resident Claude CLI sessions are woken through the local aify channel bridge; resident OpenCode sessions resume their bound stored session in a background worker; managed workers keep using their own persistent runtime state.

Use `cc_send(trigger=true)` as the default "wake this agent now" path. Use `cc_spawn_agent(...)` only when you explicitly want a detached/background worker.

If Codex auto-detection is wrong, pass `runtime="codex"` to `cc_register`.

WSL note:
- If your Codex CLI lives in WSL, prefer running the Codex-side MCP server from inside WSL so the registered `cwd` is already a Linux path.
- When the bridge runs on Windows, it defaults to `wsl.exe -e codex app-server` for Codex launches.

Current limits:
- One active dispatched run per registered agent/worker.
- `cc_agent_info` and dispatch responses now show when new work is queued behind an already-running run.
- Claude supports interruption but not in-flight steering.
- Codex supports both interruption and steering.
- OpenCode supports interruption, but not in-flight steering.
- Resident Claude wakeups currently depend on the Claude Channels research-preview flow, so you must start Claude with `claude-aify`.
- Resident Codex triggering only works when the bridge talks to the same Codex installation/thread store that created the live session.
- Resident OpenCode resume currently requires a real `sessionHandle`; arbitrary existing OpenCode sessions are not auto-bound yet.
- Unexpected permission prompts or user-input requests can still fail a dispatched run.
- Unsupported runtimes stay message-only unless a dedicated runtime adapter is added.
- SSE-only installs can still message, join channels, inspect runs, and request dispatch, but they cannot host a triggerable resident session, cannot host a managed worker, and cannot launch local work themselves.

Recommended roles:
- `manager`, `coder`, `tester`, `reviewer`, `researcher`, `architect`
- `operator`

## Key Behaviors

- `cc_send` = DM. `cc_share` = file. `cc_channel_*` = group chat.
- Messages wrapped in code fences to prevent prompt injection.
- Agent IDs, channel names, artifact names: alphanumeric + `.` `-` `_`, 1-128 chars.
- Rotation: configurable via dashboard settings (default 90 days).
- Dashboard: http://SERVER:8800/api/v1/dashboard (auto-refreshes).
