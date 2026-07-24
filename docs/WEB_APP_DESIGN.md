# Web App Design

> **HISTORICAL DESIGN INPUT.** The rebuild shipped. Use `service/new_dashboard/` for the
> current UI and `AGENTS.md` for the canonical six-state status/lifecycle contract. The
> anti-patterns and frontend-state principles below remain useful; old IA/status examples do not.

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

Primary navigation is grouped by intent:

- **Work:** Control, Chat, Work Loop
- **Runtime:** Environments, Sessions, Runs
- **Insight:** Analytics, Artifacts
- **System:** Help, Settings

Secondary surfaces:

- right inspector drawer
- command palette
- toast/event center
- advanced/debug drawer

On narrow screens, secondary surfaces become temporary overlays or drawers. Each overlay must include a visible close control inside the surface, and bottom navigation should keep the active item in view after route changes.

## Core Layout Pattern

Use a consistent three-zone layout:

```text
Left nav     List / context rail       Main pane                 Inspector
---------    -------------------       -------------------       ---------
Control      Messages / runs / envs     Operations overview      Selected item
Chat         DMs / channels            Composer/actions          Runs/logs/meta
Sessions     Runtime backings          Identity/session actions   Spawn spec
Envs         Bridge list               Environment details       Sessions/logs/caps
Runs         Run list                   Events and controls       Reply/handoff state
```

The inspector drawer prevents primary tables from growing too many columns. Tables should stay compact; details live in drawers.

Dense workflow pages should expose compact modes and collapsible secondary tools instead of stacking every control above the primary content. Chat in particular should keep the message timeline and composer dominant, with search, filters, sort, channel creation, and advanced send options tucked into tabs or collapsible panels.

Current migration path: keep the old dashboard available on `8800` while building the replacement as a separate `8801` app. The `8801` app should be a frontend boundary over the existing API: configurable API origin, no duplicated data model, no copied message/run/session semantics, and no destructive first-slice controls without existing endpoints, confirmation, and visible audit context.

## UX Principles

- **Default to action.** If an agent is offline but recoverable, show Restart. If a bridge is online, show Spawn Here.
- **Expose hierarchy.** Agent identities are persistent addresses, Environments are host bridges, Sessions are concrete runtime backings grouped under Environments, and Runs are execution audit records. The primary dashboard should manage identities through Sessions and Chat, not a top-level Agents page.
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

- **Visible agents**
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
- inferred reply expectations for work/request messages; explicit require-reply remains an advanced/debug control, not a routine chat decision

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
- global runtime model/effort policy

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
- **Target** card: environment, runtime, workspace, and the global runtime model/effort policy.
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
