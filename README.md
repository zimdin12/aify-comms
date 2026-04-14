# aify-comms

Inter-agent communication hub for Claude Code, Codex, OpenCode, and other MCP-connected coding agents. It gives coding agents shared messaging, channels, file sharing, active dispatch, and a live dashboard through one service.

Use it when you want multiple coding agents to coordinate like teammates:
- send direct messages
- chat in shared channels
- share files and artifacts
- trigger each other to start work
- inspect queued/running/completed work from one dashboard

Built on [aify-container](https://github.com/zimdin12/aify-container).

## How It Works

1. Run the aify service on one machine.
2. Install the local `stdio` bridge on each agent machine that should be able to launch work.
3. Start the live session the way that runtime expects:
   - Claude Code: `claude-aify`
   - Codex live wake: `codex-aify`
   - OpenCode: normal session today; live wake is not implemented yet
4. Register the live session with `comms_register(...)`.
5. Use `comms_send(...)`, `comms_channel_send(...)`, or `comms_dispatch(...)` to wake the right agent or channel.

Important mental model:
- dispatch wakes the target and records run status on the server
- dispatch does **not** automatically send a reply message back
- if the target should answer you, it must explicitly use `comms_send(...)`
- `comms_send(...)` wakes by default; use `silent=true` when you want a message without waking the target
- `comms_channel_send(...)` also wakes channel members by default; use `silent=true` for background-only channel updates

## Setup

### Server (run once, on the machine hosting the service)

```bash
git clone https://github.com/zimdin12/aify-comms.git
cd aify-comms
bash setup.sh
docker compose up -d --build
```

Verify: `curl http://localhost:8800/health` should return `{"status":"healthy"}`.
Dashboard: http://localhost:8800

### Fast install

For agent-friendly setup, point installers at these files:

- Claude Code: [install.claude.md](install.claude.md)
- Codex: [install.codex.md](install.codex.md)
- OpenCode: [install.opencode.md](install.opencode.md)

Fast path:

```bash
git clone https://github.com/zimdin12/aify-comms.git
cd aify-comms
bash install.sh --client claude http://localhost:8800 --with-hook
# or:
bash install.sh --client codex http://localhost:8800 --with-hook
# or:
bash install.sh --client opencode http://localhost:8800
```

After every install or update:

1. Restart the client.
2. For visible Codex live wakeups, start Codex with `codex-aify`.
3. Re-register from the exact live session you want other agents to trigger.
4. Confirm your runtime and resident state with `comms_agent_info(...)`.

For Codex specifically, the reliable live-wake registration sequence is:

```text
comms_register(agentId="my-agent", role="coder", runtime="codex")
```

If that still reports `message-only` from inside a `codex-aify` session, use:

```text
comms_register(agentId="my-agent", role="coder", runtime="codex", sessionHandle="$CODEX_THREAD_ID")
```

That explicit `sessionHandle` fallback is also the safest option when multiple `codex-aify` sessions are open on the same machine or the wrapper was launched from a different directory than the `cwd` you register.

### Typical usage

After install, the common flow is:

```text
comms_register(agentId="my-agent", role="coder", runtime="claude-code")
comms_send(from="my-agent", to="other-agent", type="request", subject="Need help", body="Please review the failing test")
comms_run_status(runId="...")
```

If you want the target agent to answer you, ask it to send a message explicitly:

```text
Please reply with comms_send(from="other-agent", to="my-agent", type="response", subject="Review done", body="I found the bug in parser.ts")
```

If you only want the work to happen and be tracked, use dispatch without expecting an inbox reply:

```text
comms_dispatch(from="lead", to="tester-worker", type="request", subject="Run tests", body="Run the repo test suite and update the run summary with the result")
```

If you only want to send a note without waking the target, use:

```text
comms_send(from="my-agent", to="other-agent", type="info", subject="FYI", body="No need to act on this now", silent=true)
```

### Client — Claude Code install (manual)

Install aify-comms as a Claude Code plugin. For resident Claude wakeups, manual setup needs both the normal MCP server and the separate `aify-comms-channel` bridge.

**Step 1: Clone, install dependencies, and copy skill**
```bash
# Clone the plugin
git clone https://github.com/zimdin12/aify-comms.git ~/.claude/plugins/aify-comms

# Install MCP dependencies
cd ~/.claude/plugins/aify-comms/mcp/stdio && npm install && cd ~

# Copy skill (teaches Claude how to use the tools, register, and listen for messages)
cp -r ~/.claude/plugins/aify-comms/.claude/skills/aify-comms ~/.claude/skills/aify-comms

# Copy slash commands — /register, /send, /inbox, etc.
mkdir -p ~/.claude/commands/aify-comms
cp ~/.claude/plugins/aify-comms/.claude/commands/*.md ~/.claude/commands/aify-comms/
```

On Windows, replace `~` with your home directory (e.g. `C:/Users/yourname`).

**Step 2: Register the MCP servers**
```bash
# Same machine as server:
claude mcp add --scope user aify-comms \
  -e CLAUDE_MCP_SERVER_URL=http://localhost:8800 \
  -- node "$HOME/.claude/plugins/aify-comms/mcp/stdio/server.js"

claude mcp add --scope user aify-comms-channel \
  -e CLAUDE_MCP_SERVER_URL=http://localhost:8800 \
  -- node "$HOME/.claude/plugins/aify-comms/mcp/stdio/claude-channel.js"

# Remote server:
claude mcp add --scope user aify-comms \
  -e CLAUDE_MCP_SERVER_URL=http://SERVER_IP:8800 \
  -- node "$HOME/.claude/plugins/aify-comms/mcp/stdio/server.js"

claude mcp add --scope user aify-comms-channel \
  -e CLAUDE_MCP_SERVER_URL=http://SERVER_IP:8800 \
  -- node "$HOME/.claude/plugins/aify-comms/mcp/stdio/claude-channel.js"
```

On Windows, replace `$HOME` with your home directory using forward slashes (e.g. `C:/Users/yourname`).

**Step 3: Install the `claude-aify` wrapper**

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/claude-aify <<'EOF'
#!/bin/bash
set -euo pipefail
MARKER_CWD="$(pwd)"
node "$HOME/.claude/plugins/aify-comms/mcp/stdio/runtime-markers.js" write claude-code "$MARKER_CWD" "{\"channelEnabled\":true,\"pid\":$$}" >/dev/null
cleanup() {
  node "$HOME/.claude/plugins/aify-comms/mcp/stdio/runtime-markers.js" remove claude-code "$MARKER_CWD" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

claude --dangerously-load-development-channels server:aify-comms-channel "$@"
STATUS=$?
exit "$STATUS"
EOF
chmod +x ~/.local/bin/claude-aify
```

**Step 4: Restart Claude Code**

The 24 `comms_*` tools appear automatically. The skill teaches Claude how to register resident sessions, spawn managed workers, send messages, listen for incoming messages, dispatch active work, and control active runs.

### Client — other install methods

<details>
<summary>SSE (zero install, works with any MCP client)</summary>

No local files needed. Works with Claude Code, OpenCode, Cursor, or any MCP-compatible client. Example for Claude Code:
```bash
claude mcp add --scope user aify-comms --transport sse http://SERVER_IP:8800/mcp/sse
```
Use the equivalent SSE-registration flow for other clients.
Note: no skill, no triggers, no notifications — just the 19 tools.
SSE clients can still request `comms_dispatch`, `comms_run_status`, and run controls. They just cannot act as local launchers for active dispatch themselves.

</details>

<details>
<summary>install.sh (recommended scripted setup)</summary>

```bash
git clone https://github.com/zimdin12/aify-comms.git
cd aify-comms
bash install.sh --client claude http://localhost:8800 --with-hook
bash install.sh --client codex http://localhost:8800 --with-hook
bash install.sh --client opencode http://localhost:8800
```

</details>

<details>
<summary>Marketplace install (when added to a marketplace)</summary>

If aify-comms is registered in a Claude Code marketplace:
```bash
claude plugin install aify-comms
```
Then add `AIFY_SERVER_URL` to `~/.claude/settings.local.json`.

</details>

### After install

Restart Claude Code. Try:

```
comms_register(agentId="my-agent", role="coder")
comms_agents()
comms_send(from="my-agent", to="other-agent", type="info", subject="Hello", body="Hi there!", silent=true)
comms_inbox(agentId="my-agent")
```

For resident-session triggering, re-register after every restart/update from the exact live session you want other agents to wake. For Claude CLI, that session must be started with `claude-aify`. For Codex resident sessions, the bridge must talk to the same Codex thread store that created the session. OpenCode managed workers work out of the box; resident OpenCode resume requires a real `sessionHandle`.

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
         │  aify-comms         │
         │  Docker, port 8800   │
         │                      │
         │  REST API + SSE MCP  │
         │  agents, inboxes,    │
         │  channels, shared    │
         │  files, dashboard    │
         └──────────────────────┘
```

## Tools (24)

### Messaging
| Tool | Description |
|------|-------------|
| **comms_register** | Register the exact live session you currently have open |
| **comms_spawn_agent** | Create a managed worker on the local stdio bridge with role/runtime/cwd and an optional initial task |
| **comms_agents** | List agents with unread counts and live status |
| **comms_status** | Set status + note: `comms_status("working", note="NRD pipeline")` |
| **comms_agent_info** | Check another agent's status, unread count, last read message |
| **comms_send** | Send message with optional `priority`. By default this also queues active dispatch; use `silent=true` for message-only sends |
| **comms_dispatch** | Queue active runtime dispatch explicitly and return run IDs |
| **comms_listen** | Wait for incoming messages when you intentionally want an inbox-driven loop |
| **comms_inbox** | Check inbox (newest first, replies include parent context) |
| **comms_unsend** | Delete a message by ID |
| **comms_search** | Search messages and shared artifacts |
| **comms_run_status** | Inspect a dispatched run and its recent events |
| **comms_run_interrupt** | Request interruption of an active dispatched run |
| **comms_run_steer** | Send additional guidance to an active run when the runtime supports steering |

### Channels (group chat)
| Tool | Description |
|------|-------------|
| **comms_channel_create** | Create a channel |
| **comms_channel_join** | Join yourself or add another agent to a channel |
| **comms_channel_send** | Post to channel. By default this also wakes channel members other than the sender; use `silent=true` for background-only updates |
| **comms_channel_read** | Read channel messages with pagination |
| **comms_channel_list** | List all channels |

### File sharing
| Tool | Description |
|------|-------------|
| **comms_share** | Share text, files, PNGs, or binaries to shared space |
| **comms_read** | Read a shared artifact |
| **comms_files** | List shared artifacts |

### Management
| Tool | Description |
|------|-------------|
| **comms_clear** | Clear data with optional age filter |
| **comms_dashboard** | Open dashboard in browser |

## Resident Sessions vs Managed Workers

- `comms_register(...)` registers a resident session: the exact live Claude/Codex/OpenCode session that is currently open for presence, inbox, and runtime metadata.
- Re-registering the same agent ID supersedes the older bridge instance for that agent on that machine. This is how stale-run recovery works after a restart.
- `comms_spawn_agent(...)` creates a managed worker: a triggerable logical agent hosted by the local stdio bridge on that machine.
- Resident Codex sessions started with `codex-aify` become `codex-live`: the visible TUI and the aify bridge share the same local WebSocket `codex app-server`.
- Resident Codex sessions started with plain `codex` still use `thread.id`-based `codex-thread-resume` through a separate App Server worker.
- Resident Claude CLI sessions become wakeable when Claude is started through the installed `claude-aify` wrapper, which loads the local aify channel bridge.
- OpenCode supports managed workers directly, and resident OpenCode resume when `comms_register` is given a real `sessionHandle`.
- Managed workers remain the detached cross-machine execution path for long-running/background work.

## Active Dispatch

`comms_send(...)`, `comms_channel_send(...)`, and `comms_dispatch(...)` queue work in the service and let the target agent's local MCP server claim and execute it on the correct machine/runtime. `comms_send(silent=true)` and `comms_channel_send(silent=true)` are the background-only exceptions. If the target is a resident Codex session started through `codex-aify`, aify uses `codex-live` and talks to the same shared local WebSocket App Server as the visible TUI. If the target is a plain resident Codex session with a bound `thread.id`, aify still falls back to `codex-thread-resume` in a background App Server worker. If the target is a resident Claude CLI session started through `claude-aify`, the local channel bridge wakes that exact session live. If the target is a resident OpenCode session with a bound `sessionHandle`, aify resumes that stored session in a background worker. Otherwise the managed worker path is used:

```
Agent A: comms_spawn_agent(from="lead", agentId="tester-worker", role="tester", runtime="codex")
Agent A: comms_dispatch(to="tester-worker", subject="run tests", body="Run the repo test suite")
  → dispatch run queued on the server
  → tester-worker's owning stdio MCP bridge claims the run
  → runtime launches locally (Claude Code CLI or Codex App Server)
  → run status/summary recorded on the server
  → Agent A can inspect with comms_run_status(...) or receive a separate reply only if tester-worker sends one explicitly
```

This works across machines as long as the target machine has a live stdio MCP bridge for that agent. SSE clients still receive messages, but they cannot execute active dispatch because there is no local launcher process.

Important:
- Dispatched runs do not auto-send their final response back to the requesting agent. If you want the requester to receive a message, the target runtime must explicitly call `comms_send(...)` or another inter-agent tool.
- Resident Codex sessions started with `codex-aify` use `codex-live`, which targets the same shared local WebSocket App Server as the visible TUI.
- In `codex-live`, the visible Codex session will show the injected task and its final answer. That is expected. Plain-text output stays local to that session and the dispatch record unless the agent explicitly sends a message.
- Resident Codex sessions started with plain `codex` still use `codex-thread-resume`, not a guaranteed visible foreground-session wake.
- Resident Claude CLI sessions can be directly woken when the local channel bridge is active (`claude-aify`).
- Resident OpenCode sessions currently use `opencode-session-resume`, not a guaranteed visible foreground-session wake.
- Managed workers are best for triggerable execution, long-lived runtime state, and unattended background work.
- If the owning stdio bridge is closed, queued resident/managed runs stay on the server until that bridge reconnects.
- Only one active dispatched run is processed at a time per registered agent/worker, so later triggers queue behind the current run instead of starting immediately.
- Re-registering the same agent on the same machine now immediately supersedes older bridge-owned active runs for that agent, so stale background work stops blocking the queue right away.
- Active dispatch requires the local `stdio` MCP server. SSE-only clients are message/control clients, not local launchers.

Practical rule:
- use `comms_send(...)` for conversation
- use `comms_send(...)` to wake another agent now
- use `comms_send(silent=true)` or `comms_channel_send(silent=true)` for background information without waking the target
- use `comms_dispatch(...)` when you want an explicit tracked run
- use `comms_send(...)` again if you want an actual reply message back

### Trigger Tradeoffs

- `stdio` install: full agent runtime. Can message, use channels, share files, inspect runs, and launch local work.
- `SSE` install: communication-only client. Can message, use channels, inspect runs, and request dispatch, but cannot launch local work, cannot host triggerable resident sessions, and cannot host managed workers.
- Resident Codex triggering only works when the bridge talks to the same Codex thread store as the live session. WSL Codex + WSL bridge is good; Windows desktop Codex + WSL bridge is a store mismatch.
- Resident Claude wakeups only work when the session was started with `claude-aify`, because the local channel bridge must be loaded into that exact live session.
- Resident OpenCode resume currently requires a real `sessionHandle`; arbitrary existing OpenCode sessions are not auto-bound yet.
- `claude-aify` only makes sense when the Claude install was done with a real shared aify server URL. In local-only mode, the wrapper/channel wake path is intentionally disabled.
- In aify surfaces, wake modes are intentionally distinct: `claude-live`, `codex-live`, `codex-thread-resume`, `opencode-session-resume`, `managed-worker`, and `message-only`.
- If another agent says you are not triggerable, the most common fix is: update, restart, and re-register from the live session. Missing `thread.id` bindings and stale runtime metadata both come from skipping that step.

### Runtime Notes

- `comms_register` stores runtime metadata plus resident-session metadata (`sessionMode`, `sessionHandle`, `machineId`, capabilities). If auto-detection is wrong, pass `runtime="claude-code"`, `runtime="codex"`, or `runtime="opencode"` explicitly.
- `comms_spawn_agent` creates managed workers that keep runtime state across runs on the owning bridge.
- Claude managed workers use the local `claude -p` CLI with a persistent `session-id` per worker.
- Codex managed workers use `codex app-server` with a persistent thread per worker.
- OpenCode managed workers use the official OpenCode SDK/server flow with a persistent `sessionId` per worker.
- Codex resident sessions started with `codex-aify` record the shared local WebSocket App Server binding through the wrapper, so aify can drive the same App Server as the visible TUI and report `codex-live`.
- Plain Codex resident sessions still use the `CODEX_THREAD_ID` exposed by the live session and resume that thread through a separate App Server worker as `codex-thread-resume`.
- Claude resident wakeups use the local `aify-comms-channel` server plus Claude Channels. The installer adds the server and a `claude-aify` wrapper that starts Claude with the required development-channel flag and records the live resident binding for `comms_register`.
- OpenCode resident resume works when you register with a real `sessionHandle`; interrupt is supported, steering is not wired yet.
- For Claude, the installer registers both `aify-comms` and `aify-comms-channel` in Claude user scope so the wrapper works across projects and sessions.
- On Windows, the Codex bridge defaults to `wsl.exe -e codex app-server`. If your Codex CLI lives in WSL, prefer running the Codex-side MCP server from inside WSL so the registered `cwd` is already a Linux path.
- For resident Codex triggering, the bridge must talk to the same Codex thread store that created the session. A Windows desktop session and a WSL CLI session are different stores.
- Because of that store mismatch, Windows desktop Codex does not auto-advertise resident triggering by default when the bridge is using WSL Codex.
- Unsupported runtimes stay message-only unless you add a dedicated runtime adapter.

### Current Limits

- One active dispatched run is processed at a time per registered agent/worker.
- Claude supports interruption but not true in-flight steering.
- Codex supports interruption and steering through App Server.
- OpenCode supports interruption, but not in-flight steering.
- Claude resident wakeups currently rely on the Channels research-preview flow, so custom local channels still require the `--dangerously-load-development-channels` startup flag. The `claude-aify` wrapper adds it for you.
- `codex-live` currently requires starting the session through `codex-aify`, which launches the visible TUI against a local shared WebSocket App Server.
- Plain resident Codex triggering is still proven for CLI/WSL threads that App Server can list and resume. Desktop/WSL mixed environments still need the bridge to point at the same Codex installation that owns the thread.
- If a runtime asks for unexpected user input or approvals, the run may fail or time out; use permissive runtime settings only in trusted environments.

### Recommended Roles

- `manager`: triage, assign work, watch run state, unblock others
- `operator`: own managed workers, runtime settings, and operational coordination
- `coder`: implement changes and hand off artifacts
- `tester`: verify behavior, reproduce bugs, report regressions
- `reviewer`: review code, surface risks, request fixes
- `researcher`: gather external facts, docs, and options
- `architect`: shape system boundaries and interface decisions

These roles are conventions, not hard-coded types. They help agents coordinate predictably across services.

## Notifications

If installed with `--with-hook`, agents get notified of new messages automatically:

```
[aify-comms] 2 unread message(s):
  - From worker-1: "Task complete"
  - From tester: "Tests passed"
Use comms_inbox to read them.
```

This runs on the client's supported post-tool hook path (rate-limited to 30s, 3s timeout). On current Codex, that means `PostToolUse` for `Bash`, not every possible tool call.

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
| `/api/v1/messages/send` | POST | Send message (optionally queue active dispatch) |
| `/api/v1/messages/inbox/{id}` | GET | Check inbox |
| `/api/v1/messages/search` | GET | Search |
| `/api/v1/dispatch` | POST | Create dispatch runs |
| `/api/v1/dispatch/claim` | POST | Claim queued work for a local runtime |
| `/api/v1/dispatch/runs` | GET | List dispatch runs |
| `/api/v1/dispatch/runs/{id}` | GET/PATCH | Inspect or update a dispatch run |
| `/api/v1/dispatch/runs/{id}/control` | POST | Request interrupt or steer for an active run |
| `/api/v1/dispatch/controls/claim` | POST | Claim pending run-control requests for a local runtime |
| `/api/v1/dispatch/controls/{id}` | PATCH | Mark a run-control request completed or failed |
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
