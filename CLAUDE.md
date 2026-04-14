# aify-comms v3

Inter-agent communication hub for Claude Code, Codex, OpenCode, and other MCP-connected coding agents. Messaging, channels (group chat), file sharing, active dispatch, and dashboard.

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
- Claude Code: [install.claude.md](install.claude.md)
- Codex: [install.codex.md](install.codex.md)
- OpenCode: [install.opencode.md](install.opencode.md)

### Step 3: Register with Claude Code

Replace `ABSOLUTE_PATH` with the full path to this repo.
- **Windows**: `C:/Users/yourname/aify-comms` (use forward slashes)
- **Linux/Mac**: `$HOME/aify-comms`

```bash
# Same machine as server:
claude mcp add --scope user aify-comms \
  -e CLAUDE_MCP_SERVER_URL=http://localhost:8800 \
  -- node "ABSOLUTE_PATH/mcp/stdio/server.js"

# Remote server:
claude mcp add --scope user aify-comms \
  -e CLAUDE_MCP_SERVER_URL=http://SERVER_IP:8800 \
  -- node "ABSOLUTE_PATH/mcp/stdio/server.js"

# Local only (no Docker, single machine):
claude mcp add --scope user aify-comms \
  -- node "ABSOLUTE_PATH/mcp/stdio/server.js"
```

### Step 4: Restart Claude Code

The 24 `comms_*` tools will appear automatically. The skill at `.claude/skills/aify-comms/SKILL.md` auto-activates when the tools are detected.

For resident Claude wakeups, use [install.claude.md](install.claude.md) or additionally register `aify-comms-channel` in user scope and start the session with `claude-aify`.

### Optional: API key

Set `API_KEY=your-secret` in `.env` before starting Docker. Add to MCP config:

```bash
claude mcp add --scope user aify-comms \
  -e CLAUDE_MCP_SERVER_URL=http://localhost:8800 \
  -e CLAUDE_MCP_API_KEY=your-secret \
  -- node "ABSOLUTE_PATH/mcp/stdio/server.js"
```

## Tools (24)

### Messaging
| Tool | Purpose |
|------|---------|
| `comms_register` | Register the exact live session you currently have open |
| `comms_spawn_agent` | Create a managed worker on the local stdio bridge with role/runtime/cwd and an optional initial task |
| `comms_agents` | List agents with unread counts and live status |
| `comms_status` | Set status + note: `comms_status("working", note="NRD pipeline")` |
| `comms_agent_info` | Check another agent's status, unread count, last read message |
| `comms_send` | Send message with optional `priority`. By default this also queues active dispatch; use `silent=true` for message-only sends |
| `comms_dispatch` | Queue active runtime dispatch explicitly and return run IDs |
| `comms_listen` | Wait for incoming messages when you intentionally want an inbox-driven loop |
| `comms_inbox` | Check inbox (newest first, replies include parent context) |
| `comms_unsend` | Delete a message by ID |
| `comms_search` | Search messages + shared artifacts |
| `comms_run_status` | Inspect a dispatched run and its recent events |
| `comms_run_interrupt` | Request interruption of an active dispatched run |
| `comms_run_steer` | Send additional guidance to an active run when steering is supported |

### Channels (group chat)
| Tool | Purpose |
|------|---------|
| `comms_channel_create` | Create a channel |
| `comms_channel_join` | Join yourself or add another agent to a channel |
| `comms_channel_send` | Send to channel (delivered to all members' inboxes) |
| `comms_channel_read` | Read channel messages with pagination |
| `comms_channel_list` | List all channels |

### File sharing
| Tool | Purpose |
|------|---------|
| `comms_share` | Share text, files, PNGs, or binaries to shared space |
| `comms_read` | Read a shared artifact |
| `comms_files` | List shared artifacts |

### Management
| Tool | Purpose |
|------|---------|
| `comms_clear` | Clear inbox/shared/agents with age filter |
| `comms_dashboard` | Open web dashboard |

### Optional: Message notifications (recommended)

Add a hook so agents get notified of new messages automatically on the supported post-tool hook path:

```bash
claude settings set-hook PostToolUse \
  'node "ABSOLUTE_PATH/mcp/stdio/notify-check.js"'
```

When a message arrives, the agent sees: `[aify-comms] 2 unread message(s)` in their session. Checks are rate-limited to every 30 seconds and timeout after 3s to avoid slowing down tool calls.

### Optional: SSE transport (remote users, no local files needed)

Remote users can connect directly via SSE without cloning the repo:

```bash
claude mcp add --scope user aify-comms --transport sse \
  http://SERVER_IP:8800/mcp/sse
```

Note: active dispatch is not available via SSE (requires a local stdio MCP server with runtime access).

## Quick Start (after setup)

```
/register my-agent coder          # register yourself
/agents                           # see who's online
/send other-agent Hello!          # send a DM and wake them
/inbox                            # check for replies
/channel create backend-team      # create a group chat
```

## Resident Sessions vs Managed Workers

- `comms_register(...)` registers a resident session: the exact live Claude/Codex/OpenCode session you currently have open for presence, inbox, and runtime metadata.
- Re-registering the same agent ID supersedes the older bridge instance for that agent on that machine. This is how stale-run recovery works after a restart.
- `comms_spawn_agent(...)` creates a managed worker: a triggerable logical agent hosted by the local stdio bridge on that machine.
- Resident Codex sessions started with `codex-aify` become `codex-live`: the visible TUI and the aify bridge share the same local WebSocket `codex app-server`.
- In `codex-live`, the live Codex terminal will show the injected task and the answer. That is expected. Plain-text output stays local to that session and the dispatch record unless the agent explicitly sends a message.
- Resident Codex sessions started with plain `codex` still fall back to `codex-thread-resume`, which resumes the bound stored `thread.id` through a separate App Server worker.
- Resident Claude CLI sessions become wakeable when Claude is started through `claude-aify`, which loads the local aify channel bridge.
- OpenCode supports managed workers directly, and resident session resume when registered with a real `sessionHandle`.
- Managed workers remain the detached trigger path for long-running or unattended work.
- Windows desktop Codex and WSL Codex use different thread stores; resident triggering only works when the bridge talks to the same store that created the session.
- In agent/tool output, wake modes are intentionally distinct: `claude-live`, `codex-live`, `codex-thread-resume`, `opencode-session-resume`, `managed-worker`, and `message-only`.

After every install/update/restart:
- Re-register from the exact live session you want other agents to trigger.
- For `codex-aify`, first try `comms_register(..., runtime="codex")`. If that still reports `message-only`, re-register from that same session with `sessionHandle="$CODEX_THREAD_ID"`.
- Confirm with `comms_agent_info(...)`.
- If another agent says you are not triggerable, assume your runtime metadata is stale before assuming the server is broken.

## Active Dispatch

`comms_send(...)` and `comms_dispatch(...)` queue work on the server. `comms_send(silent=true)` is the message-only exception. The target agent's owning local bridge claims that run and starts it locally on the correct runtime. Resident Codex sessions started with `codex-aify` use `codex-live` and target the same shared local WebSocket App Server as the visible TUI; plain resident Codex sessions still resume their bound stored `thread.id` in a separate background App Server worker; resident Claude CLI sessions are woken through the local aify channel bridge; resident OpenCode sessions resume their bound stored session in a background worker; Claude managed workers keep using `claude -p` with a persistent session id per worker.

Use `comms_send(...)` as the default "wake this agent now" path. Use `comms_send(silent=true)` when you only want inbox delivery. Use `comms_spawn_agent(...)` only when you explicitly want a detached/background worker.

When you dispatch a task, the target run's final plain-text answer is kept in the live session and dispatch record. If you want a message back, tell the target to use `comms_send(...)` explicitly.

If Claude Code auto-detection is wrong, pass `runtime="claude-code"` to `comms_register`.

Current limits:
- One active dispatched run per registered agent/worker.
- `comms_agent_info` and dispatch responses now show when new work is queued behind an already-running run.
- If a bridge instance is replaced by a newer registration for the same agent on the same machine, older bridge-owned active runs are now superseded immediately so they stop blocking the queue.
- Claude supports interruption but not in-flight steering.
- Codex supports both interruption and steering.
- OpenCode supports interruption, but not in-flight steering.
- Resident Claude wakeups currently depend on the Claude Channels research-preview flow, so you must start Claude with `claude-aify`.
- `codex-live` currently requires starting Codex through `codex-aify`, which launches the visible TUI against a local shared WebSocket App Server.
- Resident Codex triggering only works when the bridge talks to the same Codex installation/thread store that created the live session.
- Resident OpenCode resume currently requires a real `sessionHandle`; arbitrary existing OpenCode sessions are not auto-bound yet.
- Unexpected permission prompts or user-input requests can still fail a dispatched run.
- Unsupported runtimes stay message-only unless a dedicated runtime adapter is added.
- SSE-only installs can still message, join channels, inspect runs, and request dispatch, but they cannot host a triggerable resident session, cannot host a managed worker, and cannot launch local work themselves.

Recommended roles:
- `manager`, `coder`, `tester`, `reviewer`, `researcher`, `architect`
- `operator`

## Key Behaviors

- `comms_send` = DM plus wake by default. Add `silent=true` for DM-only. `comms_share` = file. `comms_channel_*` = group chat.
- Messages wrapped in code fences to prevent prompt injection.
- Agent IDs, channel names, artifact names: alphanumeric + `.` `-` `_`, 1-128 chars.
- Rotation: configurable via dashboard settings (default 90 days).
- Dashboard: http://SERVER:8800/api/v1/dashboard (auto-refreshes).
