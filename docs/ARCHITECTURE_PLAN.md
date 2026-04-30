# Architecture Plan

## High-Level Shape

```text
Dashboard
  |
FastAPI control plane
  |-- messages / channels / inboxes
  |-- dispatch runs / handoffs
  |-- environments
  |-- spawn requests
  |-- agent sessions
  |-- spawn specs
  |-- conversation backing / memories
  |-- compaction packets / continuations
  |
Environment bridges
  |-- WSL Codex bridge
  |-- Windows Claude bridge
  |-- Linux/OpenCode bridge
  |-- future remote bridge
  |
Runtime adapters
  |-- claude headless/resident
  |-- codex headless/resident
  |-- opencode headless/resident
```

## Existing Pieces To Keep

- `messages`, `channels`, `read_receipts`, shared artifacts.
- `dispatch_runs`, run events, handoff tracking.
- `agents` table as the stable agent identity table.
- `mcp/stdio/server.js` as host-side bridge foundation.
- Runtime-specific code paths in `mcp/stdio/runtimes.js`.
- Dashboard pages and APIs as starting point.

## New Concepts

### Environment

An environment is a connected execution place.

Examples:

- `wsl:StevenZ-L`
- `win32:STEVENZ-L`
- `linux:buildbox-1`
- `docker:aify-comms-service`

Fields:

- `id`
- `label`
- `machine_id`
- `os`
- `kind`: `host`, `wsl`, `container`, `remote`
- `bridge_id`
- `cwd_roots`
- `runtimes`: supported runtime capabilities
- `status`: `online`, `idle`, `offline`, `blocked`
- `last_seen`
- `metadata`

An environment is backed by a bridge process. The bridge can be a CLI/daemon launched by the user, installed as a service later, or embedded in an existing visible wrapper. Environment identity should be user-renamable without changing the low-level bridge ID.

### Spawn Request

A spawn request is the dashboard/API asking an environment bridge to start or attach an agent.

Fields:

- `id`
- `created_by`
- `environment_id`
- `agent_id`
- `role`
- `runtime`
- `workspace`
- `workspace_root`
- `model`
- `profile`
- `system_prompt`
- `initial_message`
- `channel_ids`
- `mode`: `managed-warm` for dashboard-created agents. Older compatibility values may appear in existing data, but the live dashboard should not expose them as product modes.
- `resume_policy`: `native_first`, `bridge_only`, `fresh_context`
- `status`: `queued`, `claimed`, `starting`, `running`, `failed`, `cancelled`
- `claimed_by_bridge_id`
- `process_id`
- `session_handle`
- `error`
- `created_at`, `updated_at`

### Agent Session

An agent can have multiple sessions over time. This separates identity from a concrete process/thread.

Fields:

- `id`
- `agent_id`
- `environment_id`
- `runtime`
- `workspace`
- `mode`
- `process_id`
- `session_handle`
- `app_server_url`
- `spawn_spec_id`
- `capabilities`
- `started_at`
- `last_seen`
- `ended_at`
- `status`
- `telemetry`

### Spawn Spec

The spawn spec is the durable recipe for recreating a session. It must survive process and bridge restarts.

Fields:

- `id`
- `agent_id`
- `environment_id`
- `runtime`
- `workspace`
- `model`
- `profile`
- `mode`
- `system_prompt`
- `standing_instructions`
- `env_vars`
- `channel_ids`
- `budget_policy`
- `context_policy`
- `restart_policy`
- `created_at`, `updated_at`

### Conversation Backing

Conversation backing is the durable continuity layer.

Fields:

- `agent_id`
- `session_id`
- `last_message_cursor`
- `transcript_ref`
- `summary`
- `memory`
- `artifact_refs`
- `budget_state`
- `updated_at`

This is required even when the runtime has native resume. Native handles can break, move, or exceed context limits; bridge-backed recovery needs transcript and summary state.

### Compaction Packet

A compaction packet is portable continuation context generated from an old session and passed into a new session.

Fields:

- `id`
- `source_agent_id`
- `source_session_id`
- `target_agent_id`
- `target_runtime`
- `target_environment_id`
- `target_workspace`
- `summary`
- `current_goal`
- `completed_work`
- `open_tasks`
- `decisions`
- `constraints`
- `important_files`
- `artifacts`
- `recent_messages`
- `handoff_instructions`
- `risk_notes`
- `created_by`
- `created_at`

Compaction packets support:

- context reset
- model switch
- runtime switch
- environment switch
- recovery when native resume is not available

## Bridge Responsibilities

The host-side bridge should:

- heartbeat its environment record
- advertise runtime capabilities
- advertise allowed workspace roots
- claim spawn requests for its environment
- validate workspace paths locally before launch
- start runtime processes through adapters
- auto-create/update the agent record
- auto-create/update the agent session record
- stream lifecycle/log/telemetry events back to the server
- stop/restart child processes when requested
- persist runtime handles and telemetry through server APIs
- enforce one owning bridge per managed session

## Runtime Adapter Contract

Adapters should expose:

- `canRun(runtimeSpec)`
- `buildCommand(spawnSpec)`
- `start(spawnSpec)`
- `stop(session)`
- `resume(session)` when supported
- `sendInput(session, message)` when supported
- `collectTelemetry(output/event)` when supported
- `capabilities()` for native resume, bridge resume, CLI attach, streaming, interrupt, telemetry

Adapter implementations should be isolated because CLI flags and output formats change.

## Session Modes

### Managed Warm

Default dashboard-spawned teammate mode.

- bridge owns the runtime session/process
- agent identity is stable across restarts
- spawn spec and conversation backing are persisted
- dashboard messages are delivered into the same logical context
- recovery uses native resume when available, otherwise bridge-emulated resume

Managed warm is the normal mode for agents the user expects to talk to over time.

### Resident Visible

Use when a human-visible CLI session already exists:

- `codex-aify`
- `claude-aify`
- existing `aify-comms` live wake model

This is still useful, but it is not the default dashboard spawn path. It is for agents the user wants to personally use through the CLI.

### Run Once

Advanced/internal mode for short utility calls:

- `claude -p "..."`
- `codex exec "..."`
- `opencode run "..."`

Run once is not a teammate mode. It can be used internally for tests, probes, or short tasks, but the main dashboard "Spawn Agent" flow should default to managed warm.

## Capability Model

The UI and scheduler must respect runtime/session capabilities:

- `persistent`: durable backing exists
- `nativeResume`: runtime handle can be reused
- `bridgeResume`: bridge can emulate continuity from transcript/memory
- `cliAttach`: official CLI can open the same session
- `interrupt`: active turn can be interrupted
- `streaming`: partial output/events can stream
- `tokenTelemetry`: token data is available or estimated
- `costTelemetry`: cost can be estimated
- `contextReset`: runtime context can reset without losing agent identity

Do not infer these from runtime name alone. Codex, Claude Code, and OpenCode may change behavior across versions.

## Workspace Model

Workspace selection is part of spawn.

Rules:

- Bridges advertise allowed workspace roots.
- Dashboard lets the user pick or type a workspace under those roots.
- Owning bridge validates the path.
- The service container does not translate paths.
- Windows bridge launches Windows paths.
- WSL bridge launches WSL paths.
- Agent/session records store the exact native workspace string used by the bridge.

This is mandatory because prior path ambiguity caused real dispatch failures.

## Core Dashboard Backend APIs

The implementation exposes the environment, spawn, session, run, chat, artifact, and continuation APIs under `/api/v1`. Treat the exact routes in `service/routers/api_v2.py` and the dashboard client as authoritative; this architecture document describes the model those APIs should preserve.

Important API families:

- environments and bridge heartbeat/control
- spawn requests, spawn specs, and managed agent adoption
- agent sessions stop/restart/recover/pause/continue
- dispatch runs, events, handoffs, interrupt, and steer controls
- direct messages, channel messages, read state, and shared artifacts
- compaction packets and managed successors

## Compatibility Rule

`comms_spawn` and dashboard Environment spawn both create spawn requests. Existing direct `comms_register` remains supported for human-open resident sessions, but persistent managed agents should use environment-backed spawn.
