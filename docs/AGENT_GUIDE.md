# Agent Guide

This guide is for coding agents working on `aify-comms`.

`aify-comms` is a dashboard-driven communication and control plane for coding agents. Keep the existing message, channel, dispatch, artifact, and MCP APIs stable while improving the lifecycle layer around environments, sessions, managed spawns, and live wake.

## Core Rules

- Messaging is the source of truth. A run is an execution attempt attached to a message, not a separate communication concept.
- Live wake is the normal product path. Inbox-only and SSE-compatible behavior can remain for compatibility/debugging, but it should not be the primary dashboard workflow.
- The service container stores state and exposes APIs. Host-side bridges execute runtime CLIs and claim environment spawn requests.
- A dashboard-spawned agent must be auditable: environment, workspace, runtime, spawn spec, session handle/process handle when available, lifecycle status, and owner.
- Manual `comms_register` remains useful for human-open resident CLI sessions, but it is not the normal dashboard spawn path.
- Prefer runtime adapters over hardcoded CLI assumptions. Codex, Claude Code, and OpenCode flags can change.

## Main Surfaces

| Surface | Path | Purpose |
|---|---|---|
| Backend API | `service/routers/api_v2.py` | Dashboard, environments, spawn requests, sessions, analytics, message actions |
| Data model | `service/models.py`, `service/db.py` | Persistent SQLite schema and migrations |
| Dashboard | `service/dashboard.html` | Single-page app for control, chat, team, analytics, environments, sessions, runs, artifacts, help, settings |
| Stdio bridge | `mcp/stdio/server.js` | MCP tools, resident wake, environment-backed managed sessions, environment heartbeat/control loops |
| Runtime adapters | `mcp/stdio/runtimes.js` | Runtime-specific launch/resume/interrupt behavior |
| Skills | `.agents/skills`, `.claude/skills` | Agent-facing instructions for Codex and Claude Code |
| Install docs | `install.codex.md`, `install.claude.md`, `install.opencode.md` | Runtime-specific setup |

## Development Loop

```bash
git status --short
docker compose up -d --build
curl http://localhost:8800/health
```

Backend changes under `service/`, `mcp/`, and `config/` require a container rebuild or hot-copy/restart during local iteration. Host-side bridge changes under `mcp/stdio/` require restarting the relevant wrapper/bridge process.

Useful checks:

```bash
node --check mcp/stdio/server.js
npm --prefix mcp/stdio test
python3 -m py_compile service/models.py service/db.py service/routers/api_v2.py
docker compose exec -T service python -m unittest service.tests.test_api_v2_regressions service.tests.test_main_websocket_auth -q
```

For dashboard edits, extract and syntax-check the inline script:

```bash
awk '/<script>/{flag=1;next}/<\/script>/{flag=0}flag' service/dashboard.html > /tmp/aify-dashboard-script.js
node --check /tmp/aify-dashboard-script.js
```

## Bridge Setup Model

Run the service once. Then run one environment bridge per host/OS boundary you want the dashboard to control:

```bash
cd /path/to/workspace-or-workspace-parent
aify-comms
```

On native Windows:

```powershell
cd C:\path\to\workspace-or-workspace-parent
aify-comms.cmd
```

The current directory is always an allowed workspace root. Extra root arguments are optional safety boundaries. The exact project directory is selected per spawned agent in the dashboard.

Only the `aify-comms` launcher should set `AIFY_ENVIRONMENT_BRIDGE=1`. Ordinary MCP client sessions should not advertise themselves as dashboard spawn targets.

## Dashboard Standard

The dashboard should feel like a real work console, not a raw admin table:

- Home/Control should show current activity, recent messages, live issues, and running work.
- Chat should support reading/sending as the selected identity, unread state, delete/clear actions, channels, and useful conversation inspection.
- Team should focus on active managed agents and keep manual/resident identities sorted and clearly separated.
- Environments should show only real connected spawn targets, with stop/forget controls.
- Sessions and Runs should expose lifecycle details without duplicating Chat as a second messaging UI.
- Analytics should show useful time-based views, not dense tables of counters.

Keep advanced IDs, JSON, logs, and rarely used compatibility details in inspectors/drawers or Help, not in the primary flow.

## Skill Hygiene

Keep only two skills unless the workflow clearly demands another:

- `aify-comms`: normal operating guide
- `aify-comms-debug`: failure recovery and known issues

Do not teach agents to use silent/inbox-only paths as the default. New persistent teammates should be created through `comms_spawn` or dashboard Environment spawn, not ad hoc one-off launch paths.
