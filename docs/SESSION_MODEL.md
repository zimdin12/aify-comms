# Session Model

## Core Rule

Every dashboard-spawned agent must be **backed**.

Backed means the system stores enough state to recover the agent after a process exit, bridge restart, service restart, or machine reboot. It does not mean the official human CLI can always attach to the same conversation.

## Identity vs Session

Agent identity is stable:

- `agent_id`
- role
- name
- standing instructions
- owner/manager
- default channel memberships (future policy field; current channel membership is explicit channel state)
- permissions/budgets

Agent sessions are replaceable runtime instances:

- environment/bridge
- runtime
- workspace
- process ID
- runtime session/thread handle if available
- app-server URL if relevant
- started/ended timestamps
- status
- telemetry

One agent can have many sessions over time. The dashboard should show both:

- **Agent**: the teammate identity.
- **Session**: the current or historical concrete runtime backing that teammate.

## Spawn Spec

The spawn spec is the durable recipe for recreating an agent session.

Fields:

- `agent_id`
- `environment_id`
- `runtime`
- `workspace`
- `mode`
- `model`
- `profile`
- `system_prompt`
- `standing_instructions`
- `env_vars`
- `cwd`
- `channel_ids`
- `budget_policy`
- `context_policy`
- `idle_timeout`
- `restart_policy`

The spawn spec must be persisted separately from the live process. Without it, dashboard respawn becomes guesswork.

## Persistent Backing Layers

A warm managed session is backed by four layers:

1. **Agent record**
   Stable identity and team membership.

2. **Spawn spec**
   How to recreate the runtime in the right environment and workspace.

3. **Runtime state**
   Native runtime handles when available: Codex thread ID, app-server state, process IDs, resume IDs, or equivalent.

4. **Conversation state**
   Transcript, summaries, memory, artifacts, handoff cursor, last processed message IDs, and budget state.

The bridge should prefer native runtime state when it is reliable, but it must still keep conversation state so the system can recover when native resume is unavailable or unsafe.

Native runtime handles must not be silently discarded. If a Claude session ID is locked or a user wants a fresh backing thread/session, that should be an explicit operator action such as **Clear resume state**. Recover should preserve the stored handle when possible; restart should use the saved spawn spec, but it still should not erase native memory unless the operator asks for a reset.

## Capability Flags

Each session should expose capability flags. The dashboard must use these flags instead of assuming every runtime behaves like Codex or Claude.

Suggested flags:

```json
{
  "persistent": true,
  "nativeResume": true,
  "bridgeResume": true,
  "cliAttach": false,
  "interrupt": true,
  "streaming": true,
  "tokenTelemetry": true,
  "costTelemetry": true,
  "contextReset": true
}
```

Meaning:

- `persistent`: session has durable backing and can be recovered in some form.
- `nativeResume`: runtime has a real session/thread handle we can reuse.
- `bridgeResume`: bridge can recreate continuity from stored transcript/memory even without native resume.
- `cliAttach`: a human can open the same session in the official CLI.
- `interrupt`: bridge can stop an active turn without killing the whole session.
- `streaming`: bridge can stream partial output/events.
- `tokenTelemetry`: runtime exposes tokens or bridge can estimate them.
- `costTelemetry`: bridge can estimate cost from token telemetry and pricing config.
- `contextReset`: bridge can start a fresh runtime context while preserving identity/memory.

## Managed Warm

Managed warm is the default dashboard-spawned teammate mode.

Behavior:

- The bridge owns the runtime session.
- The agent is reachable through dashboard chat, channels, and dispatch.
- Messages are delivered into the same logical agent context.
- The bridge stores transcript/memory and native handles.
- The dashboard can stop, restart, or respawn the session from the stored spawn spec.

Managed warm can be implemented two ways:

### Native Warm

The runtime supports a durable session/thread.

Codex target model:

- bridge starts or connects to Codex app-server
- bridge creates a thread
- server stores the thread/session handle
- each message becomes a turn in the same thread
- if the process dies, bridge resumes the thread when possible

### Bridge-Emulated Warm

The runtime is invoked repeatedly, but the bridge owns continuity.

Claude fallback model:

- bridge stores the transcript and summaries
- each turn invokes `claude -p` or another headless command with reconstructed context
- bridge appends the answer to transcript
- context is compacted when needed

Bridge-emulated warmth is still persistent because the bridge can recreate the agent from stored state. It may not be CLI-attachable.

## Resident Visible

Resident visible is for human-open CLI sessions:

- `codex-aify`
- `claude-aify`
- future visible OpenCode wrapper

Use this when the user wants to personally watch and type into the official CLI while dashboard/comms can also reach it.

Resident visible sessions are not the default for dashboard-spawned agents. They are an attach/register mode for visible humans and debugging.

## Run Once

Run once is not a teammate mode.

Use it for:

- quick checks
- short utility tasks
- smoke tests
- implementation internals

It can be exposed later as an advanced tool, but the normal **Spawn Agent** flow should default to managed warm.

## CLI Attach

CLI attach is optional.

A session can be persistent and recoverable without being attachable in the official CLI.

Examples:

- Codex session with a stored thread ID may become attachable if the same Codex installation and thread store can resume it safely.
- Claude bridge-emulated warm session is likely not attachable; dashboard can show transcript/logs, but there is no official interactive Claude conversation to open.

Dashboard rule:

- Show **Open in CLI** only when `cliAttach=true`.
- Show **View transcript/logs** for all persistent sessions.
- If the bridge owns an active managed session, attaching a human CLI must either pause bridge ownership or use explicit shared-session locking to avoid races.

## Workspace Rule

Workspace is part of the spawn spec and must be native to the environment that owns the session.

Examples:

- Windows bridge: `C:/Users/Administrator/echoes_of_the_fallen`
- WSL bridge: `/mnt/c/Users/Administrator/echoes_of_the_fallen`
- Linux bridge: `/home/steven/projects/echoes`

The service container should not translate paths on behalf of the bridge. The bridge advertises allowed workspace roots and validates requested workspaces locally.

## Recovery Flow

When a warm managed session dies:

1. Server marks session `lost` or `offline`.
2. Dashboard shows recovery options.
3. User or policy requests restart.
4. Spawn request targets the previous environment unless changed.
5. Bridge validates workspace and runtime capability.
6. Bridge tries native resume if `nativeResume=true`.
7. If native resume fails or is unavailable, bridge reports the failure or uses bridge-resume only when the runtime adapter can do so without silently discarding native memory. Fresh backing handles require an explicit reset/clear-resume action.
8. New session row is created and linked to the same agent identity.

## Continue From Previous Session

The dashboard should support starting a new session from an old session.

This is different from native resume:

- native resume tries to reopen the same runtime session/thread
- continue-from starts a new runtime session using a compacted handoff from the old one

Continue-from is required for:

- context compaction
- switching models
- switching runtimes, for example Claude -> Codex
- switching environments, for example Windows -> WSL
- recovering when native resume is unavailable or undesirable
- moving from a messy long session into a clean new one

## Compaction Packet

A compaction packet is a portable summary generated from the old session and used to seed the new session.

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

The packet should be human-readable and editable before launch. It is not just an internal blob.

## Compaction Flow

1. User clicks **Continue from this session**.
2. Dashboard asks the source session/agent to produce a compaction packet, or generates one from transcript/logs if the source is offline.
3. User chooses target runtime, bridge/environment, workspace, model/profile, and whether to keep the same agent identity.
4. Dashboard shows the generated compaction packet for review/edit.
5. Server creates a new spawn request with `continuation_of_session_id` and `compaction_packet_id`.
6. Target bridge launches a new managed-warm session.
7. New session receives the compaction packet as initial context.
8. New session links back to the old session for audit.

## Cross-Runtime Continuation

Cross-runtime continuation should be supported by design.

Example:

```text
old session:
  runtime: Claude Code
  environment: Windows
  workspace: C:/Users/Administrator/echoes_of_the_fallen

new session:
  runtime: Codex
  environment: WSL
  workspace: /mnt/c/Users/Administrator/echoes_of_the_fallen
```

The bridge must validate the target workspace in the target environment. The dashboard may help map equivalent roots, but the target bridge is the authority.

Cross-runtime continuation is bridge-resume, not native resume. It relies on the compaction packet, transcript summaries, and artifacts, not on the old runtime's native thread handle.

## Compaction Quality Bar

A good compaction packet should let a new model continue without re-reading the full transcript.

It should include:

- the actual user goal
- current repo/project state
- decisions already made
- files changed or under discussion
- commands/tests already run
- known failures and why they matter
- pending handoffs/messages
- constraints and standing instructions
- what not to redo
- exact next recommended action

It should avoid:

- raw full transcript dumps
- vague progress summaries
- hidden assumptions
- runtime-specific phrasing that another model cannot use

## Agent Identity During Continuation

The user should choose:

- **same agent identity, new session**: default for compaction/context reset
- **new agent identity from old session**: useful when changing role/model/runtime
- **archive old and replace**: useful when a session became noisy or broken

In all cases, the old session remains visible in history.

## Non-Negotiable Invariants

- A dashboard-spawned managed agent must never depend on manual `comms_register`.
- A warm managed agent must always have a stored spawn spec.
- A managed session must have exactly one owning bridge at a time.
- A workspace must be validated by the owning bridge, not guessed by the container.
- CLI attach is capability-driven, not assumed.
- Continue-from creates a new session from a compaction packet; it is not the same as native resume.
