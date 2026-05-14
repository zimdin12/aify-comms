# Claude Messenger Steer Plan

> **For agentic workers:** Keep this as a product/architecture plan until the operator explicitly asks for implementation. If implementation starts, convert the chosen slice into a task-by-task plan and use tests before runtime behavior changes.

> **Status update (2026-05-14):** This plan is superseded for the current dashboard path. Managed Claude Code no longer uses `claude -p`, and Messenger delivery for terminal-capable runtimes now starts or reuses a bridge-owned PTY. Keep this document as historical analysis of the Agent SDK/print-mode option, not as the current implementation direction.

## Goal

Add real Claude Code **Messenger steering** for managed/headless chat runs without abusing PTY Console as a chat transport.

## Current State

Managed Claude currently runs through `claude -p --output-format text`, writes one prompt to stdin, closes stdin, and waits for process exit. That is reliable for one managed turn, but it cannot accept mid-run chat steering because there is no live input stream left open.

Resident Claude through `claude-aify` is different: an interactive Claude Code process is alive and the aify channel sidecar can deliver live notifications. That path is good for real CMD and dashboard Console ownership, but it does not solve headless managed Messenger steering.

## Product Rule

Use the right transport for the surface:

- **Messenger:** structured messages, run controls, queue/steer/interrupt, contracts, final replies.
- **Console:** PTY terminal keystrokes and terminal output.
- **Resident CMD:** user-owned terminal plus channel/live wake bridge.

Do not make Messenger pretend to type into a PTY. PTY should stay the Console transport. Managed Claude Messenger should use a Claude Agent SDK or streaming JSON input transport that is built for programmatic long-lived input.

## Recommended Direction

Prefer Claude Agent SDK V1 streaming input for managed Messenger steering.

Why:

- Official docs describe streaming input as the recommended persistent interactive mode.
- It supports queued messages, interruption, tool integration, hooks, real-time feedback, and natural multi-turn context.
- The V1 `query()` API accepts `prompt: AsyncIterable<SDKUserMessage>`, which maps well to an aify-managed input queue.
- The returned `Query` has `interrupt()`, so aify run controls can map to native interruption.
- The deprecated V2 session API has a nicer `send()`/`stream()` shape, but the docs say it is deprecated and should not be our main foundation.

Keep Claude Code Channels for resident/Console Claude only:

- `claude-aify` / dashboard Console starts an actual interactive Claude Code process.
- The channel bridge can deliver live aify messages into that running session.
- Ownership remains `resident` or `console`, not headless managed.

## Target Architecture

Add a second managed Claude adapter mode:

- `managed-claude-print`: current `claude -p` one-shot behavior.
- `managed-claude-stream`: long-lived SDK/streaming-input behavior with active steer.

Start behind a feature flag/runtime config:

- global setting: `managed_claude_transport = print | sdk_stream`
- default initially: `print`
- dashboard/runtime detail shows selected transport and steer capability.

The active managed SDK run owns:

- a session id / resume id
- an async input queue
- an abort controller
- output accumulator
- run controller with `interrupt` and `steer`

## Data Flow

### Start Managed Claude Run

1. Environment bridge claims a queued Claude dispatch run.
2. Claude adapter checks `managed_claude_transport`.
3. If `print`, use current path unchanged.
4. If `sdk_stream`, create a Claude Agent SDK `query()` with an async iterable prompt.
5. Yield the initial aify user message into the iterable.
6. Stream SDK messages into run events and final summary.
7. Persist the returned session id into `runtimeState.sessionId` / `sessionHandle` when exposed.

### Steer Active Run

1. Dashboard or `comms_send` creates a `steer` run control for the active Claude run.
2. Bridge control loop claims steer controls.
3. Claude SDK controller pushes the steer body as another `SDKUserMessage` into the async input queue.
4. Mark the steer control accepted once the queue accepts it, not after final Claude completion.
5. If several steers arrive before claim, batch them using the existing `[AIFY STEER BATCH]` envelope.

### Interrupt Active Run

1. Dashboard or `comms_send` creates an interrupt control.
2. SDK controller calls `query.interrupt()` if available.
3. If that fails or times out, abort via `AbortController`.
4. If still stuck, terminate the child process tree as the final fallback.

### Finish Run

1. SDK stream reaches a result message or terminal error.
2. Adapter returns `completed`, `cancelled`, or `failed`.
3. Bridge records final reply/handoff exactly like current managed Claude.
4. If there were accepted steers, include a short run event trail showing they were applied during the active run.

## Files To Touch

- `mcp/stdio/runtimes.js`
  - Split Claude controller into print and SDK-stream implementations.
  - Keep existing print controller as fallback.
  - Advertise Claude `steer: true` only when the SDK-stream controller is active.

- `mcp/stdio/package.json` and lockfile
  - Add `@anthropic-ai/claude-agent-sdk` if it is not already available through the installed Claude package.

- `mcp/stdio/tests/managed-claude-sdk-stream.test.js`
  - Unit-test async input queue, steer acceptance, interrupt path, and final result parsing using a fake SDK query.

- `mcp/stdio/tests/managed-message-prompts.test.js`
  - Ensure aify message wrapper/system prompt stays identical between print and SDK-stream modes.

- `service/app/*.py` or current service runtime-settings module
  - Add `managed_claude_transport` setting.
  - Expose it in `/settings` and save it.

- `service/dashboard.html`
  - Add Runtime setting for Claude transport.
  - Show “Claude steer unavailable in print transport” vs “Claude steer enabled by SDK stream” in runtime/session details.

- `service/tests/test_api_v2_regressions.py`
  - Cover setting persistence and dashboard render.

- `docs/SESSION_MODEL.md`, `.agents/skills/aify-comms/SKILL.md`, installed skill copy
  - Document that Claude managed steer depends on SDK-stream transport.

## Implementation Slices

### Slice 1: Transport Setting And Capability Gate

- Add `managed_claude_transport`.
- Keep default `print`.
- Make capabilities report Claude steer only for `sdk_stream`.
- Add service and stdio tests for capability selection.

### Slice 2: SDK Stream Harness

- Add a small internal async input queue helper.
- Add a fake SDK query test harness.
- Prove initial message, steer message, and interrupt command can be expressed without touching real Claude usage.

### Slice 3: Claude SDK Controller

- Implement `createClaudeSdkStreamController`.
- Map model, effort, permissions, cwd, max turns, system prompt, resume id, and environment variables from existing config.
- Capture SDK output into the same summary/runtimeState contract as print mode.

### Slice 4: Run-Control Integration

- Wire `controller.steer()` to queue a user message.
- Wire `controller.interrupt()` to SDK interrupt/abort/fallback kill.
- Add regression proving `comms_send(..., steer=true)` steers busy SDK-stream Claude instead of queueing.

### Slice 5: Dashboard And Docs

- Add Runtime UI for Claude transport.
- Add runtime/session explanation text.
- Update docs and skills.

### Slice 6: Live Verification

- With Claude quota available, start one short SDK-stream managed Claude run.
- Send one dashboard message while it is active and confirm it is accepted as steer.
- Confirm `queueIfBusy=true` still queues behind the active run.
- Confirm interrupt cancels an active SDK-stream run.
- Confirm print transport still behaves exactly as today.

## Risks And Guards

- **API churn:** Agent SDK docs changed naming from Claude Code SDK to Claude Agent SDK. Keep the SDK adapter isolated behind one module-level boundary.
- **Usage cost:** live tests should be short and operator-triggered. Unit tests use fake SDK query.
- **Session locking:** do not run SDK-stream and print/resident owners against the same native session handle concurrently.
- **Permission prompts:** start with the same bypass/permission settings as current managed Claude; do not invent a new permission model.
- **Partial output:** stream events are telemetry until final result/handoff. Do not convert every partial token into a chat message.

## Decision

Build SDK-stream as the managed Messenger steer path. Keep PTY for Console. Keep Channels for resident/Console Claude.

## Sources Checked

- Claude Agent SDK Streaming Input docs: `https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode`
- Claude Agent SDK TypeScript reference: `https://platform.claude.com/docs/en/agent-sdk/typescript`
- Claude TypeScript V2 session API deprecation note: `https://code.claude.com/docs/en/agent-sdk/typescript-v2-preview`
