# Dashboard Spec

## Navigation

Initial dashboard sections:

- **Home**: operational overview and "what needs attention".
- **Work Loop**: computed reply/work contracts, overdue reminders, self-wakes, and inbox hygiene.
- **Chat**: direct messages and channel conversations.
- **Analytics**: time-filtered communication volume, run health, spawn failures, and live capacity.
- **Environments**: connected spawn targets.
- **Sessions**: concrete runtime processes/threads grouped under Environments, with identity actions available from the row or Identity Directory.
- **Console**: embedded xterm-style access to the bridge-owned PTY for terminal-capable managed runtimes. Console attaches to the same backing process used by Messenger delivery; it should not turn the identity into `cli-takeover`.
- **Runs**: execution/delivery audit table for run events, failures, steering, interrupts, and handoff state.
- **Artifacts**: shared files and text artifacts.
- **Help**: tabbed product concepts, setup, daily-use, session/CLI, and reference guidance. It should be readable in-dashboard without turning into a long unstructured manual.
- **Settings**: grouped control-plane settings: appearance, managed runtime defaults, reply-contract policy, retention, presence thresholds, and dashboard refresh.

The dashboard should optimize for daily use first and debugging second. Admin tables are useful, but the default experience should answer "who is available, what is happening, and what do I need to do next?"

Current implementation note: dashboard-spawned managed identities are managed primarily through **Sessions** and Chat details. Manual `comms_register` rows are available in the on-demand **Identity Directory**, and offline manual rows are hidden by default because they are usually stale identity records, not running processes. Channels are managed inside **Chat**, not as a separate top-level workflow.

Product mode note: the dashboard UX is live-wake-only. Non-live/message-only compatibility can remain in MCP/API paths for older clients and migration, but normal dashboard views should hide it.

Implementation note: the legacy dashboard remains served by the main API service on `8800`. The replacement dashboard is introduced as a separate preview surface on `8801`; it must use the existing message, run, session, environment, and Work Loop APIs rather than creating forked frontend state or duplicate concepts. Early `8801` slices should prioritize Needs Attention, live status, Work Loop visibility, Chat, Runtime views, and an inspector drawer while keeping destructive controls limited to already-audited endpoints.

## Home

The home page should show:

- connected bridge count and warnings
- active agents by status
- pending handoffs
- unread direct/group/channel messages
- failed or lost sessions
- running tasks
- recent important events
- quick links to spawn/chat pages

Primary cards:

- **Needs attention**: pending handoffs, unread urgent messages, failed spawns, lost sessions, and live delivery issues. Old terminal handoffs should have a repair action; reviewed historical failures should be dismissible from the home queue without deleting run/spawn audit history. Repeated known live/session/handoff notices should be muted one by one: muted notices remain visible as yellow context but do not count as active red issues.
- **Live capacity**: bridges/environments and supported runtimes.
- **Active work**: current running sessions/runs.
- **Recent conversation**: latest DMs and channels.

## Work Loop

Work Loop is the operations view over chat obligations. It should not introduce a second message concept. It computes contracts from direct requests, reviews, errors, high/urgent messages, required handoff runs, and self-wakes.

It should show:

- open/overdue/working/queued/missing-reply counts
- route, subject, age, target read state, reminder count, last reminder time, and latest answer preview
- filters for state, category (`direct`, `channel`, `self-wake`), and free text; the daily default should be open direct contracts, with historical failures, answered items, channel fan-out, and self-wake audits opt-in
- one-click run detail, chat jump, single-contract reminder, batch due-reminder actions, and operator close/bulk-close for reviewed stale contracts
- local hide/restore for noisy historical contracts without deleting the source run or chat audit trail
- hygiene indicators for old unread fan-out, answered-but-unread source messages, self-wakes, and pending fallback handoffs

`delivered` means the bridge delivered/read the source message into the target's live or managed context. It is not a completed work contract by itself. A delivered contract remains open or overdue until a linked reply/result is recorded.

Reminder policy belongs in Settings: enabled/disabled, first overdue threshold, repeat interval, optional maximum reminders, and history window. Recent due reminders are sent by the periodic service loop and can also be previewed or sent manually from Work Loop. Automatic reminders should not inject text into a busy or blocked turn; they should defer while the target is working and retry when the agent returns idle/available. Reminders should be explicit automated messages, not hidden state changes, and reminder messages must not create new reply debt. A reminder should tell the target which original message/run to open and should instruct the agent to close the original contract rather than merely acknowledging the reminder. Maximum reminder count is an optional anti-spam cap, not resolution; the default is unlimited reminders. Work Loop should show both count and last reminder time, and unresolved contracts stay visible until answered, closed by the operator, or filtered into audit views.

Agents can inspect the same view through `comms_contracts(...)` when they need to audit outstanding work. The dashboard remains the primary place for batch repair actions.

Sessions should be grouped by Environment and expose compaction/continuation history from spawn records. A compact keeps the same agent identity while creating a fresh managed backing; operators need to see the old source session, the new session, status, and handoff subject without digging through the raw spawn queue.

## Analytics

Analytics should answer "what happened in this window?" rather than only all-time accumulation.

- Range selector: last 24 hours, last 30 days, last 12 months, and all time.
- Top cards use the selected range for message count, run count, completed runs, failed/cancelled runs, and spawn failures.
- Capacity is a separate "now" panel: live agents, online agents, working agents, online environments, and spawn request inventory are not historical metrics.
- Run Status Mix should render proportional bars plus counts for the selected range.
- All-time should still be available for long-term health checks, but recent ranges are the daily default.

## Settings

Settings should be grouped so the page does not become a long maintenance form:

- **Appearance**: dashboard title/brand and accent color scheme. Default title is `AIFY Comms`.
- **Runtime**: global managed runtime model and effort policy. These are operator defaults, not per-agent knobs in normal spawn/agent edit flows.
- **Work Loop**: reply-contract reminders and history windows.
- **Maintenance**: retention, shared file limits, refresh cadence, idle/offline thresholds, and rotation.

Every non-obvious setting should include a short hint that states the default and the operational effect.

## Chat

Chat should feel like a real team messenger:

- left sidebar: DMs and channels
- sidebar tools: Find, Filters, and Channels are tabbed/closable so the rail can become a compact conversation list during focused work
- sidebar search: filter conversations, and search loaded direct-message inbox history across identities
- conversation sorting: support activity, unread-first, name, and status sorting without changing read state
- main pane: message timeline
- timeline search: filter the selected conversation without changing the selected conversation or read state
- scroll behavior: realtime refresh should not steal composer focus or force-scroll while the operator is reading; show a bottom-jump button when the newest messages are below the viewport
- composer: body-first for normal chat; send/queue stay visible, while type, priority, subject, and artifact controls live in collapsible options
- compact mode: the chat rail can hide row metadata and narrow itself when the operator wants more timeline space
- message badges: `live`, `not sent`, `handoff pending`, `handoff done`; legacy stored-only messages may appear in history/debug views
- mention support: `@agent`, `@group`, `@channel`
- quick actions: reply/follow-up, mark read, clear DM/delete channel, share artifact
- message and run IDs shown in chat should be clickable where dashboard state can open the related message or run details
- thread drawer for run details, artifacts, and handoff state
- conversation details should use operator-readable labels: current viewing identity, unread in this conversation, live wake path, runtime session, environment, workspace, and supported controls. Raw IDs are useful only where they identify a session/resume handle.
- on mobile, the conversation rail and details inspector behave as temporary overlays with explicit in-panel close controls; controls must remain reachable after scrolling and after realtime refreshes.
- peek mode: watch a selected conversation without automatically marking incoming messages read; explicit Mark read remains available for direct messages and selected channels
- channel details: show current members and allow adding/removing known agents from the right-side Members panel; the current viewing identity uses a clear **Leave** action and can be re-added later; add selection must be stable across realtime refreshes
- artifact uploads store bytes in the aify-comms shared artifact service and inserted chat text should tell agents to use `comms_read(name="...")`
- reply expectations are inferred from message type: requests/reviews/errors should get explicit replies; routine info is non-contractual unless `requireReply` is explicitly set
- normal dashboard chat has one send path; strict dispatch remains an advanced API/debug path, not a primary composer option
- conversation context should stay focused: managed prompts should include only compact recent direct context, tell agents not to revive unrelated topics, and require evidence checks before status/history claims
- dashboard-origin direct messages are human/operator chat: managed agents answer the current delivered run in final plain text, and the bridge stores that final answer in dashboard chat
- asynchronous manager updates outside the current delivered run should use `comms_send(to="dashboard", type="info" or "response", ...)` when completing a dashboard promise; captured final output remains a backend safety net
- delivered managed agent-to-agent requests/reviews/errors should answer the current message in final plain text; the bridge captures and threads that answer into chat

The existing inbox/message tables can remain as an admin/debug view, but the default user experience should be conversational.

Message states:

- `sent`: accepted for live delivery and visible
- `not sent`: target was not currently startable; no message row was written
- `delivered`: bridge/session received it
- `read`: target consumed it
- `running`: message has an active run
- `blocked`: run/session needs user intervention or a decision; ordinary prompt/footer chrome alone is not enough
- `missing reply`: a run finished or returned to an idle terminal prompt without a threaded chat reply; it is audit debt, not `working`
- `handoff pending`: reply expected
- `closed`: handoff complete or explicitly dismissed

Dashboard-origin managed messages use final plain text as the primary chat reply path. Delivered managed agent-to-agent runs also use final plain text for the current threaded reply. The bridge captures that final output into Runs and stores/threads it into chat so managed replies do not depend on an extra MCP tool call. Later teammate-triggered manager/operator results outside the current delivered run should still be sent with `comms_send(to="dashboard")`; backend summary mirroring is a safety net.

Group chat must prevent accidental loops:

- default agent-to-agent auto-reply budget per thread
- visible "budget paused" state
- release/extend budget button
- per-group policy for whether agents may mention each other automatically
- manager summaries should route work by owner/topic and avoid broad team pings when one agent can answer
- agents reply in channels when named, responsible, asked a question, or holding useful evidence; they should avoid broad automatic acks

## Spawn Agent Flow

Button: **Spawn Agent**

Required fields:

- agent ID or generated ID
- role
- runtime: Claude, Codex, Hermes, OpenCode, Pi
- environment
- workspace
- mode: managed warm/live-wake
- initial prompt/instructions

Optional fields:

- global runtime model/effort policy from Settings
- system prompt file or inline prompt
- default group/channel memberships (future spawn-form enhancement; current channel membership is managed from Chat details)
- budget limits
- context reset policy
- idle timeout
- restart policy
- resume policy: native first, bridge only, fresh context

Result:

- dashboard creates a spawn request
- target environment bridge claims it
- bridge starts/attaches runtime
- agent identity appears in the Identity Directory and its backing appears in Sessions
- chat opens automatically to that agent

Spawn form UX:

- environment selector shows OS, bridge label, online state, supported runtimes, and workspace roots
- runtime selector only enables runtimes supported by the selected environment
- workspace picker validates against advertised roots before submit where possible
- generated agent IDs are editable
- advanced options are collapsed by default
- preview shows the exact environment, workspace, runtime, and mode that will be used

The bridge launcher advertises allowed workspace roots. Those roots are safety boundaries, not the default project choice for every agent. The exact workspace remains part of each spawn request. Operators may override roots from the dashboard; the service preserves that override across heartbeats and sends the effective root list back to the bridge for managed spawn checks. Reset returns to the roots the bridge command actually advertises.

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
- edit workspace roots
- reset to bridge-advertised roots
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

Spawn requests should show queued/claimed/starting requests and failures by default. Successful `running` spawn request rows mean the request already produced or updated a managed session; label them as **started** and hide them behind **Show successful spawn history** so they do not look like active work.

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
- set handle when the operator knows the correct native runtime ID
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

Ended/completed/cancelled sessions are debug history. The normal Sessions page should hide them by default and expose a **Show ended/debug sessions** toggle for lifecycle investigation.

Manual/resident identities may expose **Edit** and **Adopt env** when at least one environment is online. Adoption creates managed backing for future dashboard work without changing the current live CLI turn. If the resident bridge later goes stale, the next dashboard send can return the identity to managed mode automatically. If a CLI registers while a managed run is active, takeover must be deferred until the active run ends.

Current browser Console mode attaches to a bridge-owned PTY through xterm. Opening Console does not convert the identity to `cli-takeover`; Messenger remains the contract surface. For terminal-input runtimes such as Hermes, dashboard sends may start/reuse the same managed PTY. Managed Claude Code starts/reuses `claude-aify`, uses the PTY as the visible backing session, leaves development-channel auto-confirm off unless the operator enables it, submits the dashboard turn as one bracketed paste+submit control, and keeps a tracked active run while waiting for the reply only when live terminal backing still exists. Queueing waits behind real active/queued work; if an idle managed Claude/Hermes agent has no pending run, Queue still uses live PTY delivery rather than a channel-only queued row. Separate native CLI ownership still uses the explicit resident/Pause-for-CLI path and only changes ownership at turn boundaries.

## Continue From Session Flow

Button: **Compact**

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
4. User chooses target identity, role, environment, runtime, workspace, and identity mode. Model/effort comes from global runtime settings.
5. Dashboard creates a continuation spawn request.
6. New managed-warm session starts with the compaction packet as initial context.

Identity options:

- same agent, new session
- new agent from old session
- archive old session after continuation

The review screen should show:

- source session
- target bridge/environment
- target runtime and global model/effort policy
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

## Identity Directory

Identities are persistent addresses, not current processes. The directory is on demand from Sessions rather than a primary sidebar page.

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
- edit identity in a modal: change ID, environment, runtime, and workspace; destructive/advanced actions live under Actions, not as a row full of buttons
- restart from saved backing
- continue from latest session
- stop active session
- edit instructions
- edit channel memberships
- view sessions
- archive/remove identity

If an agent has no live session but has a spawn spec, show **Restart** to restore it with the saved handle. Show **Set handle** when the saved native ID needs operator repair and the correct value is known. Show **Recreate** only as the explicit fresh-context reset.

Use **Recreate** for the explicit fresh-context reset. It must be clear that messages, files, dispatch history, and the agent identity remain, but the native Claude session ID / Codex thread ID is intentionally left behind.

## Visual Design Direction

The dashboard should feel like an operations cockpit mixed with a messenger:

- persistent left navigation
- second column for chat/session lists where relevant
- main content pane with clear hierarchy
- right-side inspector drawer for selected agent/session/run
- mobile navigation keeps the active page visible after page changes, even when the bottom nav overflows horizontally
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
