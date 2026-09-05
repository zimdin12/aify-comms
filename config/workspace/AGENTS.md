# Agent Operational Guidelines

## Service Context

This is `aify-comms`: a FastAPI service and dashboard for agent communication, live wake, environments, managed spawns, sessions, runs, and artifacts. It runs at port `8800` by default.

## Main Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Health check |
| `GET /ready` | Readiness check |
| `GET /info` | Service discovery |
| `GET /api/v1/dashboard` | Compatibility redirect to Dashboard Next (`:8801`) |
| `GET /api/v1/environments` | Connected environment bridges |
| `POST /api/v1/environments/heartbeat` | Bridge heartbeat |
| `POST /api/v1/spawn-requests` | Queue a managed agent spawn |
| `GET /api/v1/agent-sessions` | Managed/runtime session records |
| `GET /api/v1/messages/recent` | Recent message activity |
| `GET /api/v1/analytics` | Dashboard analytics |
| `GET /mcp/sse` | Legacy/compatibility MCP SSE endpoint |

## Files To Modify

| Path | Purpose |
|---|---|
| `service/routers/api_v2.py` | Dashboard/API behavior |
| `service/models.py`, `service/db.py` | Persistent schema and migrations |
| `service/new_dashboard/` | Dashboard Next ES-module app |
| `mcp/stdio/server.js` | MCP tools, live wake, environment bridge loops |
| `mcp/stdio/runtimes.js` | Runtime adapters |
| `.agents/skills`, `.claude/skills` | Agent-facing instructions |
| `install.*.md`, `docs/BRIDGE_SETUP.md` | Runtime setup instructions |

## Rules

- Keep messaging/channel/dispatch APIs compatible.
- Prefer live wake over inbox-only compatibility paths.
- Environment bridges execute runtime CLIs; the container stores state and exposes APIs.
- Do not make ordinary MCP client sessions advertise themselves as dashboard spawn targets. `AIFY_ENVIRONMENT_BRIDGE=1` no longer has a legitimate setter: v0.6.1 removed the launcher behaviour it enabled and v0.6.2 deleted the tier. Setting it in a test once turned that test into the environment bridge and reaped seven live gateway hosts, so leave it unset. aify-env is the host tier.
- Dashboard-spawned agents must carry environment, workspace, runtime, spawn spec, session state, and owner metadata.
