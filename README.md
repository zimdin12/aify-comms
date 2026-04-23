# aify-comms

Inter-agent communication hub for Claude Code, Codex, OpenCode, and other MCP-connected coding agents. It gives coding agents shared messaging, channels, file sharing, active dispatch, and a live dashboard through one service.

Use it when you want multiple coding agents to coordinate like teammates:
- send direct messages
- chat in shared channels
- share files and artifacts
- trigger each other to start work
- inspect resident sessions, managed workers, and queued/running/completed work from one dashboard

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
- `comms_dispatch(...)` is still a message from the sender; the difference is that the server also opens a tracked run for it
- `comms_dispatch(...)` expects an explicit reply message back by default
- triggered `comms_send(...)` only defaults to reply-required when it is a `type="request"` send; override with `requireReply=true/false` when needed
- if a required reply is still missing when the run ends, the bridge mirrors the run result back into the requester's inbox as a fallback handoff
- `comms_send(...)` wakes by default; use `silent=true` when you want a message without waking the target
- `comms_channel_send(...)` also wakes channel members by default; use `silent=true` for background-only channel updates
- `comms_send(..., steer=true)` only injects guidance mid-turn when the target already has a live steer-capable run; otherwise it falls back to normal queued dispatch
- when a steer control is accepted, the source inbox message is auto-marked read so it does not linger as unread noise
- if the target is already working, later dispatches from the same sender are merged into one pending buffered run (cap: 10 items) that starts after the current run finishes instead of stacking as many separate queued runs
- if that buffered run already holds 10 items, the next dispatch is rejected with `reason: "buffer_full"` in `notStarted`, including the recipient's current status — wait, interrupt the active run, or call `comms_agent_info` first
- inbox delivery is still immediate even when a dispatch is buffered or rejected

Communication defaults:
- keep messages concise by default: one ask, one result, or one status update
- use the subject line as the short summary
- if the full detail is long, prefer `comms_share(...)` plus a short message pointing to it
- if you see an unread notice, read it promptly with `comms_inbox(...)`

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

For Codex specifically, the reliable live-wake registration sequence inside `codex-aify` is:

```text
comms_register(agentId="my-agent", role="coder", runtime="codex", sessionHandle="$CODEX_THREAD_ID", appServerUrl="$AIFY_CODEX_APP_SERVER_URL")
```

If those live env vars are unavailable in that session, fall back to:

```text
comms_register(agentId="my-agent", role="coder", runtime="codex")
```

If bare registration still reports `message-only` from inside a `codex-aify` session, use:

```text
comms_register(agentId="my-agent", role="coder", runtime="codex", sessionHandle="$CODEX_THREAD_ID")
```

The full `sessionHandle + appServerUrl` form is also the safest option when multiple `codex-aify` sessions are open on the same machine or the wrapper was launched from a different directory than the `cwd` you register.

The backend now rejects obviously impossible live Codex bindings at registration time: for example `machineId=linux:...` with `cwd="C:/repo"` or `machineId=win32:...` with `cwd="/mnt/c/repo"` when `appServerUrl` is present. WSL/Linux live Codex sessions should register `/mnt/...` or other native Linux paths; native Windows Codex sessions should register `C:/...` with forward slashes.

Windows wrapper note:
- If you run the installer from Git Bash on Windows, `install.sh` now installs both the Bash wrappers and `claude-aify.cmd` / `codex-aify.cmd` shims, and it adds `%USERPROFILE%\\.local\\bin` to your user `PATH`.
- If you install from WSL instead, those wrappers remain WSL-local. That is correct for WSL-native sessions, but it does not create native Windows launchers.

### Using aify-comms day-to-day

**At session start** — register your live session and describe yourself:

```text
comms_register(agentId="coder", role="coder", cwd="<native-path-to-project>")
comms_describe(agentId="coder", description="Coder on project X. Focus: service layer, Postgres migrations.")
comms_agents()
```

Registering is how other agents find you. `comms_describe` gives the team human context ("what is this person working on"). `comms_agents` shows who else is online.

**During work — the four common operations:**

```text
# 1. Ask for help / hand off a task (wakes the target)
comms_send(from="coder", to="reviewer", type="request", subject="PR-42 ready for review",
           body="Branch: feat/ingest. Please check the retry logic.")

# 2. Trigger tracked work and watch it run
comms_dispatch(from="lead", to="tester-worker", type="request",
               subject="Run regression suite", body="Run it against HEAD and summarize failures.")
comms_run_status(runId="run_...")

# 3. Post to the team channel
comms_channel_send(from="coder", channel="dev", type="info",
                   subject="Migration merged", body="0042_ingest_schema is in main.")

# 4. Share a big artifact (logs, report, patch) and link to it
comms_share(from="coder", name="regression-2026-04-15.log", content="...")
comms_send(from="coder", to="lead", subject="Regression log attached",
           body="See shared artifact regression-2026-04-15.log")
```

`comms_channel_read` and `GET /api/v1/channels/{name}` return the canonical channel history only. Per-member inbox fan-out copies are delivery records, not separate channel posts.

**When idle** — check your inbox and respond:

```text
comms_inbox(agentId="coder")
# read messages, act on them, reply with comms_send(..., inReplyTo="<original message id>")
```

**Rules of thumb:**
- `comms_send` wakes by default. Use `silent=true` only for pure FYI messages.
- `comms_dispatch` expects a reply by default. Plain `comms_send` only does when it is a triggered `type="request"` send unless you override `requireReply`.
- Explicit `comms_send(..., inReplyTo=...)` is still the preferred handoff. The bridge only mirrors the run summary back when a required reply never happened.
- Keep messages short. Subject = summary. If the detail is long, `comms_share` an artifact and point at it.
- Re-register with the same `agentId` after any update or restart — the server supersedes the old bridge automatically.
- If things go sideways, the **aify-comms-debug** skill lists every known failure mode and its fix.
- For the *why* behind non-obvious choices (wake modes, dispatch buffering, re-register semantics, path formats, auto-heal), see [DECISIONS.md](DECISIONS.md).

### Client — manual install

The fast-install block above (`bash install.sh --client claude ...`) is the recommended path. If you prefer to wire things up manually — stdio MCP server, resident channel bridge, skill, slash commands, and the `claude-aify` wrapper — see [install.claude.md](install.claude.md). Codex and OpenCode have equivalents in [install.codex.md](install.codex.md) and [install.opencode.md](install.opencode.md).

### Client — other install methods

<details>
<summary>SSE (zero install, works with any MCP client)</summary>

No local files needed. Works with Claude Code, OpenCode, Cursor, or any MCP-compatible client. Example for Claude Code:
```bash
claude mcp add --scope user aify-comms --transport sse http://SERVER_IP:8800/mcp/sse
```
Use the equivalent SSE-registration flow for other clients.
Note: no skill, no triggers, no notifications — just the 24 tools.
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
| **comms_describe** | Set team-facing description: who you are, project, focus areas. Visible in `comms_agents`. Persists across re-register. |
| **comms_agent_info** | Check another agent's status, unread count, last read message |
| **comms_send** | Send message with optional `priority`. By default this also queues active dispatch; use `silent=true` for message-only sends, `steer=true` to inject guidance into a live steer-capable run, and `requireReply=` to override reply-required handoff behavior |
| **comms_dispatch** | Queue active runtime dispatch explicitly and return run IDs. Reply handoff is required by default unless `requireReply=false` |
| **comms_listen** | Wait for incoming messages when you intentionally want an inbox-driven loop |
| **comms_inbox** | Check inbox (newest first, replies include parent context) |
| **comms_unsend** | Delete a message by ID |
| **comms_search** | Search messages and shared artifacts |
| **comms_run_status** | Inspect a dispatched run and its recent events |
| **comms_run_interrupt** | Request interruption of an active dispatched run |

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
- Managed workers keep their own saved thread/session state between dispatches, but they are not permanently running terminal processes. The bridge launches the runtime for each active dispatch and exits it again when that run finishes, fails, times out, or is interrupted.
- Resident Codex sessions started with `codex-aify` become `codex-live`: the visible TUI and the aify bridge share the same local WebSocket `codex app-server`.
- Resident Codex sessions started with plain `codex` still use `thread.id`-based `codex-thread-resume` through a separate App Server worker.
- Resident Claude CLI sessions become wakeable when Claude is started through the installed `claude-aify` wrapper, which loads the local aify channel bridge.
- OpenCode supports managed workers directly, and resident OpenCode resume when `comms_register` is given a real `sessionHandle`.
- Managed workers remain the detached cross-machine execution path for long-running/background work.

## Active Dispatch

`comms_send`, `comms_channel_send`, and `comms_dispatch` queue work on the server. The target agent's owning local `stdio` MCP bridge claims the run and launches it on the correct runtime. `silent=true` on send/channel-send is the background-only exception; `comms_spawn_agent` creates a detached managed worker with its own runtime state.

Wake modes by runtime:

| Runtime | Started with | Wake mode | Visible in live TUI? |
|---------|--------------|-----------|----------------------|
| Claude Code | `claude-aify` | `claude-live` | yes (local channel bridge) |
| Codex | `codex-aify` | `codex-live` | yes (shared WebSocket app-server) |
| Codex | plain `codex` | `codex-thread-resume` | no (background app-server worker) |
| OpenCode | with bound `sessionHandle` | `opencode-session-resume` | no (background worker) |
| any | `comms_spawn_agent` | `managed-worker` | no (detached) |
| anything else | — | `message-only` | no |

Key rules:
- **Dispatch tracks handoff, not just execution.** `comms_dispatch` requires a reply by default, and triggered `comms_send(type="request")` does too unless overridden. Plain-text output still stays in the target's live session and dispatch record, but the run now also tracks whether a reply message was actually sent.
- **Explicit replies are preferred.** Agents should still call `comms_send(..., inReplyTo=...)` themselves. If a required reply is missing when the run ends, the bridge mirrors the run result back to the requester as a fallback so the lane does not silently stall.
- **One active run per agent.** Later dispatches from the same sender merge into one buffered run (cap: 10 items) that starts after the current one finishes. Past the cap, dispatches return `reason: "buffer_full"` in `notStarted` with the recipient's status — wait, `comms_run_interrupt`, or `comms_agent_info` before retrying. Inbox messages still arrive immediately.
- **Steer is message-backed, not magical.** `comms_send(..., steer=true)` still writes the inbox message first. On Codex it injects mid-turn only if a live steer-capable run exists; otherwise it falls back to normal queueing. If the only active run is stale or superseded, the server fails that run first and then queues the new work normally.
- **Active dispatch requires `stdio`.** SSE clients can message, inspect, and request dispatch, but cannot be the local launcher or host triggerable sessions.
- **Re-register after any update or restart.** Re-registering supersedes the older bridge for that agent on that machine; the server rejects claims from superseded bridges automatically.
- **Nested subagents** should normally report to their parent, not register themselves into comms.
- **Interrupt/steer support:** Claude and OpenCode support interrupt; Codex supports interrupt **and** in-flight steering.

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

This runs on the client's supported post-tool hook path (rate-limited to 10s, 3s timeout). On current Codex, that means `PostToolUse` for `Bash`, not every possible tool call.

## Dashboard

Live at `http://localhost:8800` (redirects to `/api/v1/dashboard`):
- **Dashboard** — resident sessions, direct messages, files, stats, actions
- **Workers** — managed worker inventory with owner, saved state, message/dispatch shortcuts, interrupt, reset-state, and remove actions
- **Dispatches** — dedicated run triage view at `/api/v1/dashboard/dispatches`
- completed runs with no recorded reply handoff are surfaced as `Pending Handoffs`
- direct and channel messages are marked inline with compact delivery badges: `wake` for wake-requested, `bg` for background-only
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
| `/api/v1/dashboard/dispatches` | GET | Dispatch-focused dashboard view |

## Security

- **API key** (optional): Set `API_KEY` in `.env`. Clients need `CLAUDE_MCP_API_KEY` env var or `-e AIFY_API_KEY=...`.
- **Prompt injection protection**: Message bodies wrapped in code fences with safety warnings.
- **Input validation**: Agent IDs, channel names, artifact names: alphanumeric + `.` `-` `_`, 1-128 chars.
- **Timing-safe auth**: API key comparison uses `hmac.compare_digest`.
- Leave `API_KEY` empty for no auth (local use).

## License

MIT
