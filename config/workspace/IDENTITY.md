# Service Identity

## Product

`aify-comms`

## What This Service Does

Runs a FastAPI service and dashboard for coding-agent communication and control. It stores messages, channels, artifacts, environments, spawn requests, sessions, runs, analytics, and dashboard state.

## Core Capabilities

- Direct messages, channels, unread/read state, search, and unsend/delete actions
- Live wake and tracked dispatch for triggerable resident sessions and managed workers
- Environment registry for every Windows, WSL or Linux host running aify-env
- Dashboard-managed agent spawn into a selected environment/workspace/runtime
- Agent/session lifecycle controls: stop, restart/continue, interrupt, and recovery metadata
- Shared artifacts
- Runtime adapters for Claude Code, Codex and Hermes

## Execution Model

The service container is the control plane. aify-env, the host tier, heartbeats each environment, advertises the runtimes that host can run, claims spawn requests, and runs runtime CLIs on its own host.

The service never guesses host paths or launches a native Windows/WSL/Linux runtime process itself; the aify-env for that environment does.
