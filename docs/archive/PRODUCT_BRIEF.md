# Product Brief

## Problem

`aify-comms` is the control plane for AI coding teams. It lets agents message each other and trigger work, but the product only becomes useful when the user does not have to manually reason about live sessions, registration, wake modes, stale bridges, session IDs, and which OS a bridge belongs to.

For a multi-agent workflow, the user needs a control room:

- spawn an agent from the dashboard
- choose where it runs
- talk to it immediately
- add it to channels
- keep it warm when needed
- see whether it is actually running
- stop or resume it without CLI surgery
- separate live issues from reviewed historical failures

## Product Goal

Build a control plane where connected environments are treated as execution capacity and agent identities are backed by managed, observable sessions.

Messages are the work interface. Runs, sessions, bridges, handoffs, artifacts, and environment state are operational telemetry and controls around that message flow.

The dashboard becomes the primary UI for:

- direct chat and channels
- agent spawning
- environment selection
- runtime/process/session monitoring
- run/handoff audit inspection
- kill/restart/resume controls

The dashboard should feel like a real web application, not a database admin page. It should answer these questions immediately:

- What bridges/environments are connected?
- Which agents exist, and where are they running?
- Who is currently working, online/available, blocked, or offline?
- What conversations need attention?
- Which runs are pending handoff?
- Which reply/work contracts are overdue, answered, stale, or only historical noise?
- What can I safely spawn, stop, restart, or reset?
- Which old failures are still actionable, and which are only audit history?

Daily workflow target:

1. Start the service/dashboard container.
2. Run `aify-env` in each execution environment, for example native Windows and WSL. It hosts
   the processes and, once it holds a credential for this service, claims its spawns. (This
   said `aify-comms` until 2026-09-02: the environment bridge was the thing you ran per
   machine, and it is being retired -- `docs/TARGET_ARCHITECTURE.md` names four commands and
   that is not one of them.)
3. Open the dashboard.
4. Spawn or restart managed identities from the dashboard, selecting the exact workspace per agent.
5. Chat with agents and channels from the dashboard; keep manual resident CLI registrations as compatibility/debug bindings.

## Core User Stories

- As a user, I can connect WSL and Windows bridges and see both as spawn targets.
- As a user, I can spawn a Codex agent in WSL or a Claude agent in Windows from the dashboard.
- As a user, I can select workspace, runtime, role, and initial instructions before spawn. Managed model/effort policy is configured globally in dashboard settings.
- As a user, I can DM a spawned agent immediately without asking it to manually register.
- As a user, I can create a channel, add agents, and send a message to that channel.
- As a user, I can see which agents are online/available, working, blocked, offline, or stopped.
- As a user, I can stop a managed agent process and later respawn/resume the same identity when supported by the runtime.
- As a user, I can stop a managed agent process and restart it from stored backing even when the runtime does not support native resume.
- As a user, I can inspect token/cost telemetry when the runtime exposes it.
- As a user, I can inspect enough run/session evidence to understand what happened, with richer transcript/log views added per adapter as they mature.
- As a user, I can start a clean new session from an old session using a reviewed compaction packet, including switching model, runtime, bridge, or workspace.
- As a user, I can repair old missing-handoff rows and dismiss reviewed historical failures from the Home queue without deleting audit records.
- As a user, I can open Work Loop to see who owes whom a reply, send due reminders, and repair old delivered-read/handoff bookkeeping without reading raw database tables.
- As a future user, I can open a managed or resident session in an in-browser terminal when the environment bridge supports PTY/CLI attachment, with the same explicit ownership rules as native **Pause for CLI** so dashboard chat and the terminal do not race the same session.

## Non-Goals For Initial Build

- Full Minecraft integration.
- Building a custom LLM runtime.
- Perfect cross-runtime feature parity.
- Replacing Claude/Codex/Hermes/OpenCode/Oh My Pi auth flows.
- Running native Windows processes directly from a Linux container without a Windows bridge.
- Infinite autonomous agent loops. Budget, loop, and mention controls must exist before automatic multi-agent reply behavior becomes a default.
- Pretending every runtime has the same native session model. The product UX should be consistent, but adapters must expose real capability flags.

## Key Product Decisions

- Environments are first-class. A machine/OS bridge advertises what it can run.
- Agent identities are lifecycle-managed records, not just self-registered inbox owners.
- Messaging is the source of truth. Dispatch/run state remains attached to messages.
- Dashboard spawn is the normal path. Manual `comms_register` is compatibility/debug.
- Dashboard chat is live-delivery gated for offline/stopped/no-wake targets; those messages are not stored for future delivery. Busy steer-capable targets receive normal sends as steer into the active run between tool calls; busy non-steer targets queue or merge as next-turn work. Queue is an explicit next-turn action even when steer is available.
- Headless adapters hide CLI details. The rest of the system asks for `runtime=codex`, not for raw shell flags.
- Managed warm sessions are always backed by durable state: agent identity, spawn spec, workspace, transcript/memory, runtime handles when available, and recovery policy.
- Native CLI attach is optional. A session can be recoverable through the dashboard even when it cannot be opened in the native runtime CLI later.
- Browser Console is an implemented terminal surface for bridge-owned PTYs. It is not a replacement for chat: Messenger remains the contract surface, and Console attaches to the same managed PTY used for terminal-capable delivery instead of pausing dashboard delivery by default.
- Bridges are execution owners. The container coordinates; the bridge running in Windows/WSL/Linux validates paths and starts native processes.
- Handoff compaction is not native resume. It creates a new session from a portable compaction packet so users can compact context or switch runtime/model/environment safely. It should keep the same agent ID by default unless the operator intentionally creates a separate successor identity.
- Work contracts are computed from messages and runs. They are not a second messaging system; they expose obligations created by direct requests, reviews, errors, explicit `requireReply` messages, self-wakes, and required handoffs. Priority alone does not create reply debt.

## Product Quality Bar

- **Zero registration ceremony for spawned agents.** If an agent was spawned from the dashboard, it should appear online without manual MCP calls.
- **No hidden ownership.** Every agent/session shows its owning bridge and workspace.
- **No path ambiguity.** The dashboard uses workspace roots advertised by the bridge. Windows paths stay Windows paths; WSL paths stay WSL paths.
- **No fake symmetry.** Runtime differences are hidden where possible but visible as capability flags where they matter.
- **No infinite loops by default.** Channel messages and agent-to-agent replies need budgets, thread limits, and clear paused states before automatic reply behavior is enabled.
- **Recoverability first.** Killing a process should not destroy the agent identity or conversation state.
- **Compaction is user-visible.** If a new session is seeded from an old one, the handoff packet should be reviewable/editable, not hidden magic.
- **Focused team communication.** Agents should answer each other naturally, but messages must stay scoped to one ask/result/blocker, verify state before asserting, and route broad work into smaller owner-specific handoffs instead of burning context on unrelated topics.
- **Visible obligations.** The dashboard should distinguish “agent is doing work”, “agent owes a reply”, “agent already answered but read state is stale”, and “old audit history” so operators do not manage the team from misleading unread counts.
