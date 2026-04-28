# aify-comms

Dashboard-driven communication and control plane for coding agents.

The goal is to move from "agents manually register themselves and can message each other" to "a user opens a dashboard, picks an environment, spawns agents, chats with them, groups them, monitors them, and stops/resumes them without caring about registration details."

## Product Direction

`aify-comms` keeps the original communication core:

- direct messages, channels, inboxes, dispatch runs, handoffs, and shared artifacts
- host-side bridges for Claude Code, Codex, and OpenCode
- resident session wakeups and environment-backed managed sessions
- dashboard-backed operational visibility

It now adds a first-class agent lifecycle layer:

- connected environment registry: WSL, Windows, Linux, Docker host, remote machine
- spawn from dashboard into any connected environment
- headless runtime adapters: `claude -p`, `codex exec`, `opencode run`, and later runtime-specific resident/session modes
- automatic identity/registration for spawned agents
- managed-warm sessions for long-lived agents
- runtime/session visibility, with richer token/cost telemetry added as runtimes expose it
- real chat UI with DMs, channels, mentions, artifacts, and run/handoff state near the conversation

## Target Mental Model

1. Start the service.
2. Connect one or more environment bridges.
3. Open the dashboard.
4. Click **Spawn Agent**.
5. Pick runtime, environment, workspace, model, role, prompt, and channel memberships.
6. Agent appears online automatically.
7. Talk to it in direct chat or channels, assign work through messages, inspect output, stop/restart/recover it.

Manual `comms_register(...)` should become an advanced/debug path, not the normal user workflow.

## Current State

This branch folds the dashboard and environment lifecycle work back into `aify-comms`. Existing message, channel, dispatch, artifact, and MCP APIs should keep working while the dashboard becomes the normal way to manage agents.

Important starting docs:

- [AGENTS.md](AGENTS.md) — coding-agent instructions for this repo.
- [docs/PRODUCT_BRIEF.md](docs/PRODUCT_BRIEF.md) — product goals and non-goals.
- [docs/ARCHITECTURE_PLAN.md](docs/ARCHITECTURE_PLAN.md) — proposed control-plane architecture.
- [docs/SESSION_MODEL.md](docs/SESSION_MODEL.md) — backed warm sessions, native resume, bridge-emulated resume, and CLI attach rules.
- [docs/DASHBOARD_SPEC.md](docs/DASHBOARD_SPEC.md) — first dashboard UX spec.
- [docs/WEB_APP_DESIGN.md](docs/WEB_APP_DESIGN.md) — web application UX/architecture principles.
- [docs/DASHBOARD_REVIEW.md](docs/DASHBOARD_REVIEW.md) — current dashboard critique, semantics, and design rules.
- [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md) — WSL/Linux/Windows bridge setup and launcher semantics.
- [docs/AGENT_GUIDE.md](docs/AGENT_GUIDE.md) — concise engineering guide for future coding agents.
- [docs/UNINSTALL.md](docs/UNINSTALL.md) — clean uninstall for Docker service, data, wrappers, MCP config, hooks, and skills.
- [docs/SKILLS.md](docs/SKILLS.md) — installed Codex/Claude skill inventory and relevance.
- [docs/PLAN_REVIEW.md](docs/PLAN_REVIEW.md) — pressure-test, risks, and product decisions that should not drift.
- [docs/IMPLEMENTATION_ROADMAP.md](docs/IMPLEMENTATION_ROADMAP.md) — staged engineering plan.
- [docs/FIRST_CODING_AGENT_TASK.md](docs/FIRST_CODING_AGENT_TASK.md) — exact first task for the implementation agent.

## Setup

```bash
bash setup.sh
docker compose up -d --build
curl http://localhost:8800/health
```

The default port is `8800`. Change `.env` only if another service already uses that port.

## Connect Environments

Dashboard spawns require at least one host-side environment bridge. The bridge is the process that actually runs Codex, Claude Code, or OpenCode on Windows, WSL, Linux, Docker, or a remote machine.

See [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md) for Linux/WSL and native Windows bridge commands, `AIFY_CWD_ROOTS` rules, and service URL examples.

Short version for Linux/WSL:

```bash
cd /path/to/workspace-or-workspace-parent
aify-comms
```

Short version for Windows PowerShell:

```powershell
cd C:\path\to\workspace-or-workspace-parent
aify-comms.cmd
```

The service URL defaults to `http://localhost:8800`. The current directory is always advertised as an allowed workspace root. Extra root arguments are optional safety boundaries, for example `aify-comms /mnt/c/Docker` or `aify-comms.cmd C:\Docker`. The exact project workspace is selected per agent in the dashboard spawn form.

## Design Rule

Messaging remains the source of truth. A dispatch/run is a delivery/execution attempt attached to a message, not a separate communication concept.

Managed warm agents are also always backed: the system stores identity, spawn spec, workspace, runtime state, transcript/memory, and recovery policy. Native runtime session handles are used when available; otherwise the bridge emulates continuity from stored transcript and summaries.

The container hosts the control plane. Bridges execute. The service must not try to directly launch native Windows/WSL/Linux runtime processes unless a bridge for that environment claims the spawn request.
