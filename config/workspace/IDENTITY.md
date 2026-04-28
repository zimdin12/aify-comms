# Service Identity

## Product

`aify-comms`

## What This Service Does

Runs a FastAPI service and dashboard for coding-agent communication and control. It stores messages, channels, artifacts, environments, spawn requests, sessions, runs, analytics, and dashboard state.

## Core Capabilities

- Direct messages, channels, unread/read state, search, and unsend/delete actions
- Live wake and tracked dispatch for triggerable resident sessions and managed workers
- Environment registry for Windows, WSL, Linux, Docker, and remote bridges
- Dashboard-managed agent spawn into a selected environment/workspace/runtime
- Agent/session lifecycle controls: stop, restart/continue, interrupt, and recovery metadata
- Shared artifacts
- Runtime adapters for Codex, Claude Code, and OpenCode

## Execution Model

The service container is the control plane. Host-side `aify-comms` launchers are environment bridges. Bridges heartbeat, advertise allowed workspace roots and runtime capabilities, claim spawn requests, and execute runtime CLIs on their own host.

The service should not guess host paths or directly launch native Windows/WSL/Linux runtime processes without a bridge for that environment.
