# Dashboard Spec

## Navigation

Initial dashboard sections:

- **Home**: operational overview and "what needs attention".
- **Chat**: direct messages and channel conversations.
- **Team**: identities and current status.
- **Analytics**: communication volume, agent availability, run health, and recent failure rates.
- **Environments**: connected spawn targets.
- **Sessions**: concrete runtime processes/threads.
- **Runs**: dispatch/run/handoff table.
- **Artifacts**: shared files and text artifacts.
- **Help**: product concepts and setup pointers.
- **Settings**: admin retention, cleanup, presence thresholds, and dashboard refresh.

The dashboard should optimize for daily use first and debugging second. Admin tables are useful, but the default experience should answer "who is available, what is happening, and what do I need to do next?"

Current implementation note: dashboard-spawned managed identities are the primary **Team** surface. Manual `comms_register` rows are shown in a separate Team-page section, and offline manual rows are hidden by default because they are usually stale identity records, not running processes. Channels are managed inside **Chat**, not as a separate top-level workflow.

Product mode note: the dashboard UX is live-wake-only. Non-live/message-only compatibility can remain in MCP/API paths for older clients and migration, but normal dashboard views should hide it.

## Home

The home page should show:

- connected bridge count and warnings
- active agents by status
- pending handoffs
- unread direct/group/channel messages
- failed or lost sessions
- running tasks
- recent important events
- quick spawn button
- quick chat composer

Primary cards:

- **Needs attention**: pending handoffs, unread urgent messages, failed spawns, lost sessions.
- **Live capacity**: bridges/environments and supported runtimes.
- **Active work**: current running sessions/runs.
- **Recent conversation**: latest DMs and channels.

## Chat

Chat should feel like a real team messenger:

- left sidebar: DMs and channels
- main pane: message timeline
- composer: body-first for normal chat; subject remains available for handoffs and searchable task titles
- message badges: `live`, `not sent`, `handoff pending`, `handoff done`; legacy stored-only messages may appear in history/debug views
- mention support: `@agent`, `@group`, `@channel`
- quick actions: reply/follow-up, mark read, clear DM/delete channel, share artifact
- thread drawer for run details, artifacts, and handoff state
- reply expectations are inferred from message type: requests/reviews should get explicit replies; routine info does not need a special toggle
- normal dashboard chat has one send path; strict dispatch remains an advanced API/debug path, not a primary composer option

The existing inbox/message tables can remain as an admin/debug view, but the default user experience should be conversational.

Message states:

- `sent`: accepted for live delivery and visible
- `not sent`: target was not currently startable; no message row was written
- `delivered`: bridge/session received it
- `read`: target consumed it
- `running`: message has an active run
- `blocked`: run/session needs user intervention
- `handoff pending`: reply expected
- `closed`: handoff complete or explicitly dismissed

Group chat must prevent accidental loops:

- default agent-to-agent auto-reply budget per thread
- visible "budget paused" state
- release/extend budget button
- per-group policy for whether agents may mention each other automatically

## Spawn Agent Flow

Button: **Spawn Agent**

Required fields:

- agent ID or generated ID
- role
- runtime: Claude, Codex, OpenCode
- environment
- workspace
- mode: managed warm/live-wake
- initial prompt/instructions

Optional fields:

- model/profile
- system prompt file or inline prompt
- default group/channel memberships
- budget limits
- context reset policy
- idle timeout
- restart policy
- resume policy: native first, bridge only, fresh context

Result:

- dashboard creates a spawn request
- target environment bridge claims it
- bridge starts/attaches runtime
- agent appears in Agents and Sessions
- chat opens automatically to that agent

Spawn form UX:

- environment selector shows OS, bridge label, online state, supported runtimes, and workspace roots
- runtime selector only enables runtimes supported by the selected environment
- workspace picker validates against advertised roots before submit where possible
- generated agent IDs are editable
- advanced options are collapsed by default
- preview shows the exact environment, workspace, runtime, and mode that will be used

The bridge launcher advertises allowed workspace roots. Those roots are safety boundaries, not the default project choice for every agent. The exact workspace remains part of each spawn request.

Live-wake-only product constraint: normal dashboard spawn should create managed-warm agents. Older non-live compatibility paths may remain below the UI/API for migration and debugging, but they are not primary user choices.

## Environments Page

Columns:

- label
- OS/kind
- machine ID
- bridge ID
- supported runtimes
- workspace roots
- active sessions
- last seen
- status
- actions

Actions:

- spawn here
- stop bridge
- set default workspace roots
- disable spawning
- view bridge logs
- unregister environment
- rename bridge/environment
- test runtime capability

Environment health states:

- `online`: bridge heartbeating normally
- `degraded`: bridge online but one or more advertised runtimes failed capability check
- `offline`: no heartbeat
- `disabled`: user disabled spawning
- `unknown`: seen before, no current health data

## Sessions Page

Columns:

- agent
- runtime
- environment
- workspace
- mode
- process/session handle
- status
- persistence/resume capabilities
- tokens/cost
- last output
- last seen
- actions

Actions:

- stop
- restart
- resume/attach when supported
- recover from backing
- continue from this session
- reset context
- open logs
- open chat

Capability badges:

- `native resume`
- `bridge resume`
- `CLI attach`
- `streaming`
- `interrupt`
- `telemetry`

Do not show **Open in CLI** unless `cliAttach=true`. Always show transcript/log access for backed sessions.

Do not show stop/kill-style actions for rows that only represent offline identity records. For offline manual bindings, show cleanup/removal language instead.

## Continue From Session Flow

Button: **Continue from this session**

Use when:

- current session context is too large/noisy
- user wants to switch model
- user wants to switch runtime, for example Claude to Codex
- user wants to move from Windows bridge to WSL bridge
- native resume is unsafe or unavailable

Flow:

1. User selects old session.
2. Dashboard creates a bounded message/context packet from the session identity plus the last selected number of relevant messages.
3. User reviews/edits the packet.
4. User chooses target identity, role, environment, runtime, workspace, model/profile, and identity mode.
5. Dashboard creates a continuation spawn request.
6. New managed-warm session starts with the compaction packet as initial context.

Identity options:

- same agent, new session
- new agent from old session
- archive old session after continuation

The review screen should show:

- source session
- target bridge/environment
- target runtime/model
- target workspace
- capability differences
- compaction packet text
- warnings if switching runtime or environment
- selected message count and which message sources are included

The old session remains in history and links to the new session.

Message-based continuation is useful, but it must stay bounded. The dashboard should default to a small recent window and make the packet editable so stale or noisy chat does not become the new session's system context.

## Runs Page

Keep the existing dispatch/runs concept but make it easier to scan:

- short subject/status by default
- full text on hover/click drawer
- handoff state visible
- filter by agent/channel/run status
- repair/admin actions hidden behind advanced toggle

The run table should be secondary to Chat and Sessions. It is for operational triage, not the normal way to talk to agents.

## Agents Page

Agents are teammate identities, not just current processes.

Columns:

- agent ID/name
- role
- current status
- owning bridge/environment for active session
- workspace
- current session mode
- channels
- unread/handoff counts
- last seen
- actions

Actions:

- open chat
- spawn/recover
- continue from latest session
- stop active session
- edit instructions
- edit channel memberships
- view sessions
- archive/remove identity

If an agent has no live session but has a spawn spec, show **Recover** instead of making the user re-create it manually.

Use **Clear Resume State** rather than **Reset State** when forgetting saved runtime thread/session handles. It must be clear that messages, files, dispatch history, and the agent identity remain.

## Visual Design Direction

The dashboard should feel like an operations cockpit mixed with a messenger:

- persistent left navigation
- second column for chat/session lists where relevant
- main content pane with clear hierarchy
- right-side inspector drawer for selected agent/session/run
- status colors with labels, not color-only meaning
- compact tables with truncation and hover/click detail drawers
- websocket/live updates for status, logs, runs, and message delivery
- keyboard-friendly chat and command palette later

Avoid:

- giant raw JSON blocks in primary views
- long status strings overflowing tables
- making every action look equally important
- hiding bridge/session ownership
- dashboard pages that require knowing MCP internals
- compact icon/button actions without hover titles or clear confirmation text

## Budget/Loop Protection

Because channel chat can create agent-to-agent loops:

- per-agent message budget
- per-channel relay budget
- max auto-replies per thread
- high-priority messages bypass only explicit budget rules
- dashboard shows when a budget paused a thread

Budget UI should be visible in Chat and channel views, not buried in Settings. Users need to see why agents stopped replying.
