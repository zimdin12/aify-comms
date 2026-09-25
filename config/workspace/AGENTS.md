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
| `GET /api/v1/environments` | Registered environments (one per aify-env host) |
| `POST /api/v1/environments/heartbeat` | aify-env's environment heartbeat |
| `POST /api/v1/spawn-requests` | Queue a managed agent spawn |
| `GET /api/v1/sessions` | Managed/runtime session records |
| `GET /api/v1/messages/recent` | Recent message activity |
| `GET /api/v1/analytics` | Dashboard analytics |
| `GET /mcp/sse` | SSE MCP transport |

## Files To Modify

| Path | Purpose |
|---|---|
| `service/routers/`, `service/api_core/`, `service/reconcilers/` | HTTP routes, the behaviour behind them, and the sweeps |
| `service/models.py`, `service/schema.py`, `service/db.py` | Request models and the SQLite schema |
| `service/new_dashboard/` | Dashboard Next ES-module app |
| `mcp/stdio/server.js` | MCP tools and live wake |
| `mcp/stdio/runtimes.js` | Runtime adapters |
| `.agents/skills`, `.claude/skills` | Agent-facing instructions |
| `install.*.md`, `docs/BRIDGE_SETUP.md` | Runtime and host setup |

## Rules

- Keep messaging/channel/dispatch APIs compatible.
- Prefer live wake over inbox-only compatibility paths.
- aify-env, the host tier, runs runtime CLIs; the container stores state and exposes APIs.
- Leave `AIFY_ENVIRONMENT_BRIDGE` unset (the service launches workers with it at `0`): setting it in a test once reaped seven live gateway hosts.
- Dashboard-spawned agents must carry environment, workspace, runtime, spawn spec, session state, and owner metadata.
