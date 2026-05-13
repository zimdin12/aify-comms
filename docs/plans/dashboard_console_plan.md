# Dashboard Console Mode Plan

> **For agentic workers:** Keep this as a product/architecture plan until the operator explicitly asks for implementation. If implementation starts, convert the chosen slice into a task-by-task plan and use tests before runtime behavior changes.

## Goal

Add a dashboard **Console** mode beside the current **Messenger** mode so an operator can attach to a real runtime terminal in the browser while preserving a clear ownership boundary between managed backing and resident/terminal control.

## Product Shape

The Chat page should expose two modes for an agent identity:

- **Messenger:** structured aify-comms chat, direct/channel messages, run audit, queue/steer/interrupt, contracts, and final plain-text replies.
- **Console:** an in-browser terminal connected to a host-side PTY owned by an environment bridge.

Console mode must not be a hidden implementation detail of Messenger. It is an explicit terminal ownership mode with visible state, start/attach/stop controls, and audit events.

## Core Rule

Dashboard Messenger sends and Console keystrokes must not drive the same active runtime handle concurrently.

When Console owns an identity:

- Messenger can still show chat history and incoming messages.
- Normal Messenger sends to that identity should route through the resident/console delivery path if the runtime supports it, or queue behind Console ownership.
- The UI must show that terminal ownership is active and provide a clear **Return to managed dashboard** action.

When managed backing owns an identity:

- Console can be opened only by an explicit takeover/attach action.
- If a managed run is active, Console takeover is deferred until the run reaches a terminal state, matching current resident takeover rules.

## Ownership Model

Do not collapse `managed` and `resident` into one vague state. Add a clearer owner dimension:

- `managed`: environment bridge launches headless/managed runs from stored spawn spec.
- `resident`: a human-open external CLI owns the runtime.
- `console`: dashboard-opened PTY owns the runtime through an environment bridge.

`console` is resident-like because a live runtime process is open and can receive direct keystrokes. It differs from normal resident mode because the dashboard/environment bridge owns the PTY lifecycle.

Suggested session fields:

- `owner_mode`: `managed | resident | console`
- `owner_bridge_id`: bridge currently allowed to drive the runtime
- `terminal_id`: active PTY id when `owner_mode=console`
- `terminal_status`: `starting | attached | detached | stopped | failed`
- `terminal_command`: wrapper/runtime command used to start the PTY
- `terminal_workspace`: cwd used for the PTY

## Runtime Strategy

### Claude Code

Console should start `claude-aify --aify-agent <id> --resume <session-id>` inside the PTY when a session handle exists.

Benefits:

- Claude Code Channels are live because a real interactive Claude process is running.
- Operator can type directly.
- Messenger delivery can use the existing channel bridge.

Risks:

- Claude permission prompts and terminal UI must render correctly.
- Hidden/PTY Claude must be stopped cleanly.
- If the operator closes the browser tab, the PTY process may continue unless explicitly stopped or detached.

### Codex

Console should start `codex-aify` with the correct managed `CODEX_HOME` and resume command when a thread id exists.

Benefits:

- Aligns browser terminal with native CLI continuation.
- Existing app-server/resident binding can keep Messenger delivery live.

Risks:

- Need to preserve managed `CODEX_HOME` for managed-backed sessions.
- Must avoid opening a second owner against the same thread while managed Codex is running.

### Oh My Pi

Console should start `omp-aify` / `pi-aify --resume <session-id>` when a handle exists.

Benefits:

- OMP already has native session handles and active steer support.
- Terminal takeover can share the same resume path as manual CLI.

Risks:

- Old Pi agents may lack a recorded handle; Console should explain that a fresh terminal would not preserve context unless a handle is set.

### OpenCode

Treat as later unless a stable resident attach/resume path is verified.

## Architecture

### Backend Service

Add APIs for terminal lifecycle and audit:

- `POST /api/v1/sessions/{session_id}/console/start`
- `POST /api/v1/sessions/{session_id}/console/attach`
- `POST /api/v1/terminals/{terminal_id}/input`
- `POST /api/v1/terminals/{terminal_id}/resize`
- `POST /api/v1/terminals/{terminal_id}/detach`
- `POST /api/v1/terminals/{terminal_id}/stop`
- `GET /api/v1/terminals/{terminal_id}`

Use WebSocket for output/input streaming:

- browser -> service: input, resize, attach/detach control
- service -> browser: terminal output, status, exit, audit notices
- service -> environment bridge: terminal start/input/resize/stop commands

The service should remain the policy/audit layer. The environment bridge owns actual host process creation.

### Environment Bridge

Add PTY support behind a bridge capability:

```json
{
  "terminal": true,
  "pty": true,
  "terminalRuntimes": ["claude-code", "codex", "pi"]
}
```

Bridge responsibilities:

- start PTY in requested workspace
- validate workspace against advertised roots
- spawn runtime wrapper command
- stream PTY output to service
- receive stdin/resize/stop controls
- heartbeat terminal status
- clean up terminal processes on explicit stop or bridge shutdown

On Linux/WSL, use a Node PTY library or a small native helper. On native Windows, choose a ConPTY-compatible implementation.

### Dashboard UI

Chat header gains a mode switch:

- `Messenger`
- `Console`

Console view contains:

- terminal panel
- attach/start/stop/detach controls
- runtime/session handle summary
- ownership warning when takeover is pending or blocked
- “Return to managed dashboard” action

Use a real terminal renderer such as xterm.js. Do not build a fake textarea terminal.

## Lifecycle Flows

### Start Console From Managed Session

1. Operator opens Chat -> Console for an agent.
2. Dashboard requests console start for the current session.
3. Backend checks no active managed run is running.
4. If active run exists, backend records pending console takeover.
5. Environment bridge starts PTY with the runtime wrapper.
6. Wrapper auto-registers the same agent as `owner_mode=console`.
7. Backend marks session console-attached and broadcasts state.
8. Messenger sends now use resident/console delivery policy.

### Return Console To Managed

1. Operator clicks **Return to managed dashboard**.
2. Backend asks bridge to stop or detach PTY.
3. If stopped, backend marks console terminal stopped.
4. If the identity has managed backing, future Messenger sends return to managed mode after the console lease expires.
5. Audit records the ownership change.

### Browser Disconnect

Browser disconnect should not automatically kill the runtime.

Default behavior:

- mark UI attachment detached
- keep PTY alive for a short configurable lease
- allow reconnect from dashboard
- after lease expiry, either stop or leave detached according to operator setting

Initial setting should be conservative: detach but show an obvious “Detached terminal still running” warning.

## Safety Rules

- Console cannot start outside environment bridge workspace roots.
- Console takeover cannot interrupt an active managed run unless the operator explicitly interrupts/stops that run first.
- Stop Console must terminate the PTY process tree where the bridge can do so.
- Messenger must not send a managed run into an identity while Console owns the same runtime handle.
- All terminal start/stop/attach/detach/input-control events should be audit-visible.
- Terminal output is telemetry, not a chat reply. The agent still needs explicit Messenger reply behavior for contracts.

## Implementation Slices

### Slice 1: Data Model And Read-Only UI

Add terminal/owner fields to session state, render Console tab disabled unless bridge advertises terminal capability, and show explanatory state.

Tests:

- session API includes terminal ownership fields
- dashboard renders Console unavailable when capability missing
- existing Messenger behavior unchanged

### Slice 2: Bridge Capability And Terminal Skeleton

Add bridge heartbeat capability fields and no-op terminal API routes that create auditable terminal records without spawning a PTY.

Tests:

- bridge capabilities persist
- terminal start rejects unsupported bridges
- terminal start rejects invalid workspace
- terminal audit records are created

### Slice 3: Local PTY Prototype

Implement PTY start/input/resize/stop for one host family first. Prefer Linux/WSL for the first slice unless Windows ConPTY is needed first for the operator’s main workflow.

Tests:

- start shell in a temp workspace
- send `pwd` / `echo`
- resize event accepted
- stop kills process tree
- reconnect receives recent buffered output

### Slice 4: Claude Console

Start `claude-aify --aify-agent <id> --resume <handle>` inside the PTY and verify channel marker registration.

Tests:

- console start creates resident/console ownership
- Messenger send routes through channel delivery while Console owns Claude
- managed send is blocked or queued when Console owns the runtime
- stop returns identity to managed backing after lease expiry

### Slice 5: Codex And Pi Console

Add runtime-specific command builders for `codex-aify` and `omp-aify` / `pi-aify`.

Tests:

- Codex uses managed `CODEX_HOME` when resuming managed thread
- Pi refuses context-preserving Console start when no session handle exists unless operator chooses fresh session
- Messenger routing stays consistent across owner changes

### Slice 6: Polish And Operations

Add reconnect UI, terminal status badges, output buffer limits, idle lease settings, and docs.

Tests:

- browser reconnect
- terminal lease expiry
- output truncation
- dashboard scroll/layout does not jump during terminal streaming

## Open Decisions

1. Should closing the browser tab stop the PTY by default, or detach for reconnect?
2. Should Console mode be allowed for agents with no native session handle as a fresh terminal, or should it require explicit **Recreate/Fresh Console**?
3. Should Console output be stored long-term, short-term buffered, or only streamed live?
4. Should Messenger sends during Console ownership be delivered immediately through runtime-specific resident channels, or should the operator choose between **Send to terminal** and **Queue for managed**?
5. Which host should get the first PTY implementation: Windows ConPTY or WSL/Linux PTY?

## Recommended First Decision

Start with an explicit **Console ownership mode** and implement WSL/Linux PTY first unless native Windows is the operator’s main target. Keep Messenger as the default. Let Console take ownership only after a visible operator action and never while a managed run is active.

This preserves the resident/managed line:

- `managed` means dashboard sends create controlled headless runs.
- `resident` means an external terminal owns the runtime.
- `console` means the dashboard owns a terminal through the environment bridge.

