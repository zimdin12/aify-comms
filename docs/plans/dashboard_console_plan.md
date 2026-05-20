# Dashboard Console Mode Plan

> **For agentic workers:** Keep this as a product/architecture plan until the operator explicitly asks for implementation. If implementation starts, convert the chosen slice into a task-by-task plan and use tests before runtime behavior changes.

## Goal

Add a dashboard **Console** mode beside the current **Messenger** mode so an operator can attach to a real runtime terminal in the browser while preserving a clear ownership boundary between managed backing and resident/terminal control.

## Product Shape

The Chat page should expose two modes for an agent identity:

- **Messenger:** structured aify-comms chat, direct/channel messages, run audit, queue/steer/interrupt, contracts, and final plain-text replies.
- **Console:** an in-browser terminal connected to a host-side PTY owned by an environment bridge.

Console mode must not be a hidden implementation detail of Messenger. It is an explicit terminal surface with visible state, start/attach/stop controls, and audit events.

The browser is an attachment to a terminal owner, not the owner itself. If the browser disconnects, the environment bridge may still own and drive the PTY. The dashboard must make that visible, but the browser tab is not the thing that keeps work alive.

## Core Rule

Dashboard Messenger sends and Console keystrokes must drive the same active runtime handle through one bridge-owned PTY path when a terminal-capable managed runtime is active.

When Console owns an identity:

- Messenger can still show chat history and incoming messages.
- Normal Messenger sends to that identity should route into the active managed PTY when the runtime supports terminal delivery; they should not create a second managed run behind Console.
- The UI must show that terminal ownership is active and provide a clear **Return to managed dashboard** action.
- The dashboard Console should display the same terminal stream whether the terminal was started by dashboard Console or observed from a real resident wrapper when the runtime exposes a stream path.

When managed backing owns an identity:

- Console can be opened only by an explicit takeover/attach action.
- If a managed run is active, Console takeover is deferred until the run reaches a terminal state, matching current resident takeover rules.

## Ownership Model

Do not collapse `managed` and `resident` into one vague state. Add a clearer owner dimension:

- `managed`: environment bridge launches headless/managed runs from stored spawn spec.
- `resident`: a human-open external CLI owns the runtime.
- `console`: dashboard-opened PTY owns the runtime through an environment bridge.

`console` is resident-like because a live runtime process is open and can receive direct keystrokes. It differs from normal resident mode because the dashboard/environment bridge owns the PTY lifecycle.

The target invariant is:

> one agent identity, one native session handle, one active owner.

The active owner can be managed, resident, or console. A real CMD `claude-aify --resume <id>` should be able to take ownership of the same session after the current owner reaches a safe boundary. If a dashboard Console owns the session, the real CMD takeover should stop the dashboard Console or defer until it can be stopped safely. If managed backing owns the session, the real CMD takeover follows the existing pending resident takeover rules.

Suggested session fields:

- `owner_mode`: `managed | resident | console`
- `owner_bridge_id`: bridge currently allowed to drive the runtime
- `terminal_id`: active PTY id when `owner_mode=console`
- `terminal_status`: `starting | attached | detached | stopped | failed`
- `terminal_command`: wrapper/runtime command used to start the PTY
- `terminal_workspace`: cwd used for the PTY

## Runtime Strategy

### Claude Code

Console should start the wrapper, not raw Claude. Human Console uses `claude-aify --aify-agent <id>` inside the PTY; managed Claude message delivery starts/reuses `claude-aify --aify-agent <id> --auto --resume <session-id>` when a session handle exists, leaves development-channel auto-confirm off unless the operator enables it, and submits the formatted Messenger turn as one bracketed paste+submit control.

Benefits:

- Claude Code Channels are live because a real interactive Claude process is running.
- Operator can type directly.
- Messenger delivery can use the existing channel bridge.

Risks:

- Claude permission prompts and terminal UI must render correctly.
- Hidden/PTY Claude must be stopped cleanly.
- If the operator closes the browser tab, the PTY process may continue under the environment bridge. This is acceptable only when dashboard state clearly says the terminal is detached-but-running and provides reconnect/stop actions.

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

## Real Resident And Dashboard Console Symmetry

The desired UX is symmetric:

- real CMD resident: user starts `claude-aify`, `codex-aify`, or `pi-aify`
- dashboard Console: dashboard asks an environment bridge to start the same wrapper command inside a browser-attached PTY
- Messenger: dashboard sends structured messages to whichever owner is current

When a real resident wrapper starts, it should advertise:

- agent id
- runtime
- native session handle
- workspace
- bridge id / machine id
- channel/app-server/stream capability when available

The backend should treat that as an ownership claim for the same identity and native session handle. If another owner exists:

- active managed run: record pending resident takeover, apply after terminal state
- dashboard Console idle/detached: stop or detach dashboard Console and promote real resident
- dashboard Console busy: require explicit interrupt/stop or wait for a safe boundary
- conflicting native session handle: do not auto-merge; require operator confirmation

This keeps the source of terminal display separate from the identity:

- real resident terminal output comes from the real wrapper terminal when it can be streamed or mirrored
- dashboard Console terminal output comes from the bridge PTY
- Messenger history and contracts remain service-owned state

If real resident terminal output cannot be streamed, dashboard should still show owner/status and Messenger delivery state, but not pretend it has a live terminal transcript.

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

Windows terminal implementation options:

1. **`node-pty` on the Windows bridge.** This is the most direct fit for the current Node stdio bridge. On Windows it wraps the platform pseudoconsole path and avoids adding a second bridge language.
2. **`pywinpty` helper.** A Python helper can own ConPTY and speak a small JSON/WebSocket protocol to the Node bridge. This is useful if packaging `node-pty` native builds is painful.
3. **Rust helper using winpty/ConPTY crates.** A small static helper could be more robust long term, but it adds a build/release pipeline.
4. **WSL/Linux PTY first.** For early delivery, run Console through the WSL/Linux environment bridge and defer native Windows ConPTY. This avoids the hardest Windows edge cases but does not cover native Windows-only tools.
5. **No terminal, runtime API only.** Claude SDK streaming, Codex app-server, and Pi RPC are useful for managed chat/steer, but they are not substitutes for a real terminal when the operator wants to type arbitrary CLI commands.

References checked while writing this plan:

- Microsoft ConPTY/Pseudoconsole API exposes create, resize, and close operations over pipes.
- `xterm.js` provides browser terminal rendering and attach-style WebSocket integration.
- `pywinpty` supports Windows pseudoterminals through native ConPTY and fallback winpty paths.

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
- keep PTY alive under the environment bridge for a short configurable lease
- allow reconnect from dashboard
- after lease expiry, either stop or leave detached according to operator setting

Initial setting should be conservative: detach but show an obvious “Detached terminal still running” warning. This is a visibility issue, not a driver issue: the bridge owns the PTY, while the browser only attaches to it.

## Safety Rules

- Console cannot start outside environment bridge workspace roots.
- Console takeover cannot interrupt an active managed run unless the operator explicitly interrupts/stops that run first.
- Stop Console must terminate the PTY process tree where the bridge can do so.
- Messenger must not create a duplicate managed run while an active managed PTY exists; it should route input into the PTY or fail visibly.
- All terminal start/stop/attach/detach/input-control events should be audit-visible.
- Terminal output is telemetry, not automatically a chat reply. The agent still needs explicit Messenger reply behavior for contracts, or the UI must label the message as delivered-to-terminal rather than answered.
- If a real resident wrapper for the same session comes online, dashboard Console must not keep a second process alive against the same native handle.
- If Claude/Codex/Pi rejects a duplicate session handle, surface the lock conflict and offer stop-current-owner or wait-for-boundary actions instead of silently creating a fresh context.

## Implementation Slices

Current branch status: slices 1-6 are implemented on `feature/dashboard-console-mode` as an initial end-to-end Console mode. The backend owns policy/audit, environment bridges own PTYs via `node-pty`, and the dashboard exposes Messenger/Console switching with start/input/stop controls. Runtime delivery is symmetric at the dashboard contract but adapter-specific underneath: managed Claude Code, Codex, OpenCode, Pi, and Hermes start or reuse bridge-owned terminal backing by default, with native managed/steer paths retained as fallback where supported. Console attaches to the backing PTY where one exists. Real resident terminal mirroring remains a follow-up capability.

### Slice 1: Data Model And Read-Only UI

Add terminal/owner fields to session state, render Console tab disabled unless bridge advertises terminal capability, and show explanatory state.

Status: implemented.

Tests:

- session API includes terminal ownership fields
- dashboard renders Console unavailable when capability missing
- existing Messenger behavior unchanged

### Slice 2: Bridge Capability And Terminal Skeleton

Add bridge heartbeat capability fields and no-op terminal API routes that create auditable terminal records without spawning a PTY.

Status: implemented.

Tests:

- bridge capabilities persist
- terminal start rejects unsupported bridges
- terminal start rejects invalid workspace
- terminal audit records are created

### Slice 3: Local PTY Prototype

Implement PTY start/input/resize/stop for one host family first. Prefer Linux/WSL for the first slice unless Windows ConPTY is needed first for the operator’s main workflow.

Status: implemented with `node-pty` as the primary bridge PTY path and pipe fallback for constrained hosts.

Tests:

- start shell in a temp workspace
- send `pwd` / `echo`
- resize event accepted
- stop kills process tree
- reconnect receives recent buffered output

### Slice 4: Claude Console

Start interactive Claude Code inside the PTY with channel support and verify channel marker registration. Managed Claude Code no longer uses `claude -p`.

Status: implemented as PTY-first Claude Console/managed delivery.

Tests:

- console start keeps the identity managed while attaching a PTY
- Messenger send routes into the active managed PTY while Console is attached
- managed send starts a headless managed PTY when Console is closed
- stop returns identity to managed backing after lease expiry
- real `claude-aify --resume <handle>` takeover stops or defers dashboard Console ownership for the same handle

### Slice 5: Codex, Hermes, OpenCode, And Pi Console

Add runtime-specific command builders for `codex-aify`, `hermes-aify`, OpenCode, and `omp-aify` / `pi-aify`.

Status: implemented. Pi requires a saved handle for context-preserving Console start unless the operator explicitly starts fresh. Hermes can run as a PTY-backed warm process; resumable Hermes IDs are used when known.

Tests:

- Codex uses managed `CODEX_HOME` when resuming managed thread
- Pi refuses context-preserving Console start when no session handle exists unless operator chooses fresh session
- Messenger routing stays consistent across owner changes and active PTY attachment

### Slice 6: Polish And Operations

Add reconnect UI, terminal status badges, output buffer limits, idle lease settings, and docs.

Status: partially implemented for status badges, buffered output, stop/reconnect via terminal id, process-tree cleanup, and docs. Lease-expiry policy and real resident terminal mirroring are still follow-up decisions.

Tests:

- browser reconnect
- terminal lease expiry
- output truncation
- dashboard scroll/layout does not jump during terminal streaming
- Sessions page batch selection can stop live sessions and delete inactive session records created during test-spawn churn.

## Open Decisions

1. Should closing the browser tab stop the PTY by default, or detach for reconnect?
2. Should Console mode be allowed for agents with no native session handle as a fresh terminal, or should it require explicit **Recreate/Fresh Console**?
3. Should real resident terminal output be mirrored to dashboard when possible, or should dashboard only show status unless the terminal was opened through Console mode?
4. Should Console output be stored long-term, short-term buffered, or only streamed live?
5. Resolved for the current implementation: Messenger sends during active managed PTY attachment use the runtime's terminal-backed delivery contract by default. Native Codex/OpenCode/Pi adapters remain fallback paths when terminal backing is disabled or unavailable.
6. Which host should get the first PTY implementation: native Windows ConPTY or WSL/Linux PTY?

## Recommended First Decision

Start with an explicit **Console surface** and implement PTY through the environment bridge. Keep Messenger as the default. Console should attach to the same managed PTY backing rather than silently converting the identity to a resident takeover.

This preserves the resident/managed line:

- `managed` means dashboard sends create controlled runs or managed PTY input for terminal-capable runtimes.
- `resident` means an external terminal owns the runtime.
- `console` means the dashboard owns a terminal through the environment bridge.
