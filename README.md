# aify-comms

Dashboard-driven communication and control plane for AI coding teams.

`aify-comms` solves the practical problem of running more than one coding agent across Windows, WSL, Linux, and remote machines without losing track of who is live, what they are doing, and how to restart or replace them. The normal workflow is: start the service, run an `aify-comms` bridge in each execution environment, open the dashboard, spawn persistent managed teammates into chosen workspaces, then coordinate through chat.

The dashboard is the product surface. Messages are the work interface; runs, sessions, bridges, and handoffs are operational telemetry around those messages.

## Product Direction

`aify-comms` keeps the original communication core:

- direct messages, channels, inboxes, dispatch runs, handoffs, and shared artifacts
- host-side bridges for Claude Code, Codex, and OpenCode
- resident session wakeups and environment-backed managed sessions
- dashboard-backed operational visibility

It now adds a first-class agent lifecycle layer:

- connected environment registry: WSL, Windows, Linux, Docker host, remote machine
- spawn from dashboard into any connected environment
- runtime adapters for Claude Code, Codex, and OpenCode managed/resident execution
- automatic identity/registration for spawned agents
- managed-warm sessions for long-lived agents
- runtime/session visibility, with token/cost telemetry shown only when runtimes expose it
- real chat UI with DMs, channels, mentions, artifacts, and run/handoff state near the conversation

## Target Mental Model

1. Start the service.
2. Connect one or more environment bridges.
3. Open the dashboard.
4. Click **Spawn Agent**.
5. Pick runtime, environment, workspace, role, optional model/profile, and initial instructions.
6. The agent identity, spawn spec, and session backing appear automatically.
7. Talk to it in direct chat or channels, assign work through messages, inspect output, stop/restart/recover it.

Manual `comms_register(...)` should become an advanced/debug path, not the normal user workflow.

Normal dashboard chat is live-delivery gated: if a target cannot currently start work, the message is not silently queued for a future run. Fix the agent/session/environment state, then resend. Required handoffs are repaired automatically when a terminal run finishes without an explicit reply, and the Home page exposes repair/dismiss actions for old issue states.

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
- [docs/IMPLEMENTATION_ROADMAP.md](docs/IMPLEMENTATION_ROADMAP.md) — historical staged plan plus current status notes.
- [docs/FIRST_CODING_AGENT_TASK.md](docs/FIRST_CODING_AGENT_TASK.md) — historical Slice 1 task, retained for context.

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

The service URL defaults to `http://localhost:8800`. The current directory is always advertised as an allowed workspace root. Extra root arguments are optional safety boundaries, for example `aify-comms /mnt/c/Docker` or `aify-comms.cmd C:\Docker`. The exact project workspace is selected per agent in the dashboard spawn form. Ended sessions and historical failures stay available for debugging, but the dashboard hides them from the normal work queue by default.

## Design Rule

Messaging remains the source of truth. A dispatch/run is a delivery/execution attempt attached to a message, not a separate communication concept.

Managed warm agents are also always backed: the system stores identity, spawn spec, workspace, runtime state, transcript/memory, and recovery policy. Native runtime session handles are used when available; otherwise the bridge emulates continuity from stored transcript and summaries.

The container hosts the control plane. Bridges execute. The service must not try to directly launch native Windows/WSL/Linux runtime processes unless a bridge for that environment claims the spawn request.
