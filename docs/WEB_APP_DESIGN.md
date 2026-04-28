# Web App Design

## Goal

The dashboard is the product. It should not feel like a debug page bolted onto an MCP server.

The user should be able to run a multi-agent team from the browser:

- see connected bridges
- spawn agents
- chat with agents and channels
- watch work progress
- recover broken sessions
- inspect logs/telemetry when needed

## Information Architecture

Primary navigation:

- Home
- Chat
- Team
- Analytics
- Environments
- Sessions
- Runs
- Artifacts
- Settings

Secondary surfaces:

- right inspector drawer
- command palette
- toast/event center
- advanced/debug drawer

## Core Layout Pattern

Use a consistent three-zone layout:

```text
Left nav     List / context rail       Main pane                 Inspector
---------    -------------------       -------------------       ---------
Home         DMs / agents / envs       Conversation/table/detail Selected item
Chat         DMs / channels            Composer/actions          Runs/logs/meta
Agents       Filtered agent list       Agent profile/session     Spawn spec
Envs         Bridge list               Environment details       Logs/caps
```

The inspector drawer prevents primary tables from growing too many columns. Tables should stay compact; details live in drawers.

## UX Principles

- **Default to action.** If an agent is offline but recoverable, show Recover. If a bridge is online, show Spawn Here.
- **Expose ownership.** Every active agent/session shows its bridge and workspace.
- **Hide runtime weirdness until needed.** Show capability badges and clear warnings, not implementation internals.
- **Prefer conversation over forms.** Chat is the main workflow; forms are for spawn/settings.
- **Keep debug paths available.** Advanced users still need raw run events, logs, IDs, and repair actions.
- **Make failure states legible.** "Lost session, recoverable from bridge summary" is better than "failed".
- **Live wake is the norm.** The product should make live delivery and failures visible without forcing users to choose internal dispatch modes for routine messages.
- **No ambiguous paths.** Workspace picker should show environment-native paths only.

## Visual System

Recommended direction:

- calm dark-neutral or warm-light base, not generic purple SaaS
- strong status chips with text labels
- monospace only for IDs/logs/paths, not whole UI
- readable dense tables with row hover and detail drawer
- message bubbles/cards optimized for code/log snippets
- active session cards with live pulse, runtime icon, bridge label, workspace

Status colors:

- `online`: green
- `working`: blue
- `needs attention`: amber
- `blocked/failed`: red
- `offline`: gray
- `disabled`: muted gray

Always pair color with text/icon.

## Home Page Detail

Home should be the operational answer page.

Sections:

- **Needs attention**
  - urgent unread messages
  - pending handoffs
  - failed spawns
  - lost sessions
  - degraded bridges

- **Live team**
  - agents grouped by working/idle/offline
  - active bridge labels
  - quick chat buttons

- **Capacity**
  - bridges/environments
  - supported runtimes
  - available workspace roots

- **Recent work**
  - running work
  - completed handoffs
  - recent artifacts

## Chat Detail

Chat should support:

- DMs
- channels
- mentions
- threads/replies
- one normal send path from chat; strict dispatch stays in advanced/API surfaces
- attached artifacts
- run state inline
- handoff state inline
- unread/read state
- require-reply send

Group chat controls:

- agent auto-reply budget
- max replies per agent per thread
- "pause agents in this thread"
- "release budget"
- visible reason when paused

## Spawn Flow Detail

Spawn should be a guided drawer or modal, not a raw API form.

Step 1: Agent identity

- generated ID
- display name
- role
- standing instructions

Step 2: Runtime target

- environment/bridge
- runtime
- workspace
- model/profile

Step 3: Behavior

- managed warm/live-wake default
- channel/group membership
- budget policy
- context policy
- restart policy

Step 4: Review

- exact bridge
- exact workspace
- exact runtime
- capability warnings
- create button

## Environment Page Detail

An environment is a bridge-backed execution target.

Show:

- friendly name
- machine ID
- OS/kind
- bridge ID
- bridge version
- health
- supported runtimes
- runtime capability checks
- workspace roots
- active sessions
- last heartbeat
- logs

Actions:

- rename
- disable spawning
- test runtime
- spawn here
- edit roots
- unregister

## Session Page Detail

A session is a live or historical runtime backing for an agent.

Show:

- agent identity
- session ID
- environment
- runtime
- workspace
- mode
- capabilities
- native handle if available
- process ID if available
- transcript/log links
- token/cost telemetry
- recovery state

Actions:

- open chat
- stop
- interrupt
- restart
- recover
- continue from this session
- reset context
- open in CLI if supported

## Continue UX

Continue-from should feel like "start a cleaner successor session", not like a hidden technical resume.

Recommended UI:

- **Source** card: old agent/session/runtime/workspace.
- **Target** card: environment, runtime, workspace, model/profile.
- **Compaction editor**: generated handoff packet with editable sections.
- **Warnings**: capability differences, path changes, runtime switch notes.
- **Launch**: creates new managed-warm session and opens chat.

Compaction editor sections:

- Goal
- Current state
- Completed work
- Open tasks
- Decisions
- Constraints
- Important files/artifacts
- Recent handoffs/messages
- Risks
- Next action

The user should be able to regenerate, edit, save, or launch from the packet.

## Web Architecture

Initial implementation can remain server-rendered/static HTML plus API calls if that is fastest, but the target dashboard should have a real frontend boundary.

Recommended staged path:

1. Keep current `service/dashboard.html` and add pages cleanly.
2. Extract API client and shared UI utilities.
3. Move toward a small SPA when chat/session live updates become too complex for one HTML file.
4. Use WebSocket/SSE for live status, messages, runs, and logs.

Frontend state rules:

- server is source of truth
- optimistic UI only for low-risk chat sends
- live events update cached lists
- every destructive action has confirmation or undo
- long IDs are copyable but truncated by default

## Accessibility And Usability

- keyboard send and newline behavior must be predictable
- all status chips have text
- tables support filtering/search
- timestamps can show relative + absolute on hover
- paths and IDs are copyable
- errors include next action
- dashboard remains usable on laptop width

## Anti-Patterns To Avoid

- Recreating the old dashboard as more tables.
- Showing unread counts without making messages easy to read.
- Letting long run subjects overflow table layout.
- Making users understand `sessionHandle`, `bridgeInstanceId`, or MCP before they can spawn an agent.
- Exposing non-live compatibility modes as normal dashboard choices.
- Creating separate concepts for "chat message" and "dispatch message".
- Hiding the bridge that owns a session.
- Calling cross-runtime continuation "resume". It is a new session seeded by a compaction packet.
