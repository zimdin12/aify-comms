# aify-comms — Codex project notes

This repo is `aify-comms`. The product is a dashboard-driven headless agent control plane: connect Windows/WSL/Linux environment bridges, spawn persistent managed agents into selected workspaces, chat with agents and channels, monitor tracked work, and stop/restart/recover sessions without requiring manual `comms_register` from dashboard-spawned agents.

## Primary Documents

- [README.md](README.md) — repo overview.
- [docs/PRODUCT_BRIEF.md](docs/PRODUCT_BRIEF.md) — goals, non-goals, user stories.
- [docs/ARCHITECTURE_PLAN.md](docs/ARCHITECTURE_PLAN.md) — target architecture and data model.
- [docs/SESSION_MODEL.md](docs/SESSION_MODEL.md) — backed managed-warm sessions and recovery rules.
- [docs/DASHBOARD_SPEC.md](docs/DASHBOARD_SPEC.md) — dashboard UX direction.
- [docs/WEB_APP_DESIGN.md](docs/WEB_APP_DESIGN.md) — web app UX/architecture quality bar.
- [docs/AGENT_GUIDE.md](docs/AGENT_GUIDE.md) — concise engineering guide for coding agents.
- [docs/PLAN_REVIEW.md](docs/PLAN_REVIEW.md) — pressure-test and risks to keep in mind.
- [docs/IMPLEMENTATION_ROADMAP.md](docs/IMPLEMENTATION_ROADMAP.md) — historical staged plan plus current status notes.
- [docs/FIRST_CODING_AGENT_TASK.md](docs/FIRST_CODING_AGENT_TASK.md) — historical Slice 1 task; useful for context only.

Compatibility docs and APIs are still present where useful. Treat the dashboard/live-wake docs as authoritative for normal product behavior.

## Current Product Thesis

The user should be able to:

- open the dashboard
- see connected environments such as WSL, Windows, Linux host, Docker, or remote machines
- spawn Claude/Codex/OpenCode agents into a chosen environment and workspace
- have spawned agents auto-register with stable identity/session metadata
- have managed-warm agents backed by stored spawn spec, workspace, transcript/memory, runtime handles when available, and recovery policy
- message agents in direct chats, group chats, and channels
- watch run state, handoff state, bridge/session health, and available runtime output; token/cost telemetry should appear only when an adapter exposes it honestly
- stop, restart, or resume agents from the dashboard

Manual registration remains available for debugging and compatibility, but it is not the normal workflow.

## Developing

```bash
git status --short
docker compose up -d --build
curl http://localhost:8800/health
```

Backend changes under `service/`, `mcp/`, and `config/` require a container rebuild or hot-copy/restart during local iteration. Host-side bridge changes under `mcp/stdio/` require restarting the relevant wrapper/bridge process.

## Engineering Constraints

- Keep existing `aify-comms` message/channel/dispatch APIs working while adding lifecycle features.
- Do not create a second message concept. Dispatch/run state attaches to messages.
- Environment bridges are first-class. A service container cannot directly spawn native Windows processes unless a Windows bridge is connected and claims that spawn.
- Spawning must be auditable: every spawned agent needs an environment ID, workspace, runtime, command/profile, process/session handle, lifecycle status, and owner.
- Managed warm is the default teammate mode. Run-once is advanced/internal; resident-visible is for human-open CLI sessions like `codex-aify` / `claude-aify`.
- Persistent/backed does not imply CLI-attachable. Use capability flags.
- Prefer adapters over hardcoded CLI assumptions. `claude -p`, `codex exec`, and `opencode run` flags can change; encapsulate them behind runtime adapter modules and tests.
- Dashboard should be usable without reading docs: visible env selector, spawn form, agent list, chat, channels, worker/session controls, and clear run/session evidence.
- Dashboard should be a real web application, not a raw operational table. Use compact primary views plus inspectors/drawers for IDs, logs, JSON, and long text.

## Current Implementation Bias

Preserve the existing message/channel/artifact APIs and keep the dashboard as the normal control surface. New work should reduce duplicate concepts, keep environment-backed managed agents as the default teammate path, and add tests around lifecycle edge cases before changing runtime behavior.
