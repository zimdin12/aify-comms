# Implementation Roadmap

## Slice 0: Product Identity And Safe Cleanup

Goal: keep the product identity as `aify-comms` while folding the dashboard/control-plane work into the existing service without breaking inherited code.

Tasks:

- update visible names in README, `.env.example`, service example config, installer docs, and dashboard title
- keep old API names for compatibility
- ensure `docker compose up -d --build` works
- leave installer docs as inherited until lifecycle APIs exist

## Slice 1: Environment Registry

Goal: the server knows which machines/bridges can spawn agents.

Backend:

- add `environments` table
- add environment heartbeat API
- expose `GET /api/v1/environments`
- make stdio bridge heartbeat environment capability data

Bridge:

- send OS, machine ID, bridge ID, runtime support, cwd roots
- update heartbeat on existing dispatch poll loop
- generate a stable environment ID per bridge/machine unless configured explicitly

Dashboard:

- add Environments page with online/offline state
- show bridge label, OS/kind, runtime capabilities, workspace roots, and last heartbeat

Tests:

- environment heartbeat upsert
- stale environment status
- capabilities render in API response

## Slice 2: Spawn Request Queue

Goal: dashboard/API can ask a specific environment to spawn an agent.

Backend:

- add `spawn_requests` table
- add `spawn_specs` table or equivalent persisted spawn-spec storage
- add create/list/claim/update APIs
- status flow: `queued -> claimed -> starting -> running|failed|cancelled`
- permissions can stay open for local dev initially

Bridge:

- poll/claim spawn requests for its environment
- no runtime launch yet; first implementation can create a fake running session for tests
- validate that requested workspace is under an advertised root

Dashboard:

- Spawn Agent form
- spawn request status list
- environment/runtime/workspace selector
- managed warm/live-wake is the only normal dashboard spawn mode

Tests:

- request targets one environment
- only matching bridge can claim
- stale bridge cannot claim
- invalid workspace is rejected by bridge/API flow

## Slice 3: Managed Agent Auto-Registration

Goal: claimed spawn creates/updates an agent identity automatically.

Backend:

- spawn request can create/update `agents`
- add `agent_sessions` table
- link spawn request to session ID
- store session capabilities
- link session to spawn spec

Bridge:

- after claim, create/update agent and session records
- populate `runtimeState.bridgeInstanceId`, workspace, runtime, process/session info
- report capability flags such as `persistent`, `nativeResume`, `bridgeResume`, `cliAttach`, `interrupt`, and `streaming`

Dashboard:

- spawned agent appears in Agents and Sessions
- open direct chat after spawn
- show owning bridge/environment and workspace

Tests:

- spawned agent requires no manual register
- re-spawn same identity creates new session, preserves identity
- spawned session has a persisted spawn spec

## Slice 4: Backed Session Store

Goal: managed warm agents have persistent backing before real runtime complexity lands.

Backend:

- add conversation backing storage for transcript refs, summaries, memory, last message cursor, artifacts, and budget state
- add recover API shape even if recovery is initially stubbed
- add session status values for `lost`, `recovering`, and `recovered`

Bridge:

- persist last processed message/run cursor
- write session heartbeat and minimal transcript events
- support fake/stub recovery for tests

Dashboard:

- show transcript/log placeholder
- show Recover when an agent has backing but no live session

Tests:

- recoverable agent survives session loss
- recovered session links to same agent and spawn spec

## Slice 5: Runtime Adapter MVP

Goal: actually run one managed runtime from a bridge.

Pick Codex in WSL first because this repo is being developed from WSL and Codex has the clearest app-server/thread model.

Bridge:

- add adapter interface
- implement Codex managed warm using app-server/thread where possible
- store thread/session handle as runtime state
- capture stdout/stderr/events/status
- write run/session result back to server
- expose capability flags honestly

Backend:

- store adapter output and runtime handles on session

Dashboard:

- show logs/output drawer
- show native resume capability where available

Tests:

- adapter command/session builder
- spawn failure surfaced in API
- managed warm session receives more than one message in same logical context

## Slice 6: Warm Managed Loop Generalization

Goal: support long-lived agents across runtimes that can receive multiple messages.

Bridge:

- implement warm loop process/session manager
- keep per-session input queue
- map incoming messages/dispatches to the running process
- stop/restart controls
- implement bridge-emulated warmth for runtimes without native sessions

Backend:

- controls for session stop/restart
- session heartbeat and telemetry events
- context compaction hooks

Dashboard:

- stop/restart buttons
- live log tail
- context reset/recover controls

## Slice 7: Compaction And Continue-From

Goal: let users start a clean new session from an old session, including cross-runtime and cross-environment continuation.

Backend:

- add `compaction_packets` table
- add compact/continue APIs
- link new sessions to `continuation_of_session_id`
- store `compaction_packet_id` on continuation spawn requests

Bridge:

- generate compaction from active session when possible
- fallback to server-side transcript/summary compaction when source is offline
- pass compaction packet as initial context to the new session

Dashboard:

- add **Continue from this session** action
- show compaction packet review/edit screen
- allow target environment/runtime/workspace/model selection
- show warnings for runtime/environment switches

Tests:

- same-agent new-session continuation
- new-agent continuation
- cross-runtime continuation creates new session, not native resume
- compaction packet is persisted and editable

## Slice 8: Real Chat UI

Goal: dashboard becomes the primary user-facing messenger.

Dashboard:

- DM/group/channel sidebar
- message timeline
- composer
- mentions
- direct messages always use live-gated send/wake semantics; strict dispatch remains an advanced API path, not a chat control
- read state, live-delivery badges, and operational run-state badges

Backend:

- groups if channels are not enough for private multi-agent chats
- mention fan-out rules
- loop/budget controls

## Slice 9: Windows Bridge Spawn

Goal: spawn native Windows Claude/Codex agents from dashboard when a Windows bridge is connected.

Bridge:

- advertise Windows workspace roots
- implement Windows shell-safe command launch
- handle path normalization explicitly

Tests:

- WSL cannot claim Windows spawn
- Windows bridge can claim Windows spawn
- service container never tries to directly launch native Windows process

## Slice 10: Claude Managed Warm

Goal: add Claude Code support without pretending it has the same native session model as Codex.

Bridge:

- test available Claude Code headless/resume behavior
- implement native resume only if reliable
- otherwise implement bridge-emulated warmth using transcript/summaries
- expose `cliAttach=false` unless verified

Dashboard:

- show capability warnings clearly
- keep UX consistent with Codex where possible

Tests:

- repeated messages preserve bridge-backed continuity
- restart recovers from stored backing
- Open in CLI is hidden unless adapter reports `cliAttach=true`
