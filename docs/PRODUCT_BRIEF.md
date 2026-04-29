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

Build a headless agent bridge where connected environments are treated as execution capacity and agents are treated as managed, observable sessions.

Messages are the work interface. Runs, sessions, bridges, handoffs, artifacts, and environment state are operational telemetry and controls around that message flow.

The dashboard becomes the primary UI for:

- direct chat and channels
- agent spawning
- environment selection
- runtime/process/session monitoring
- dispatch/run/handoff inspection
- kill/restart/resume controls

The dashboard should feel like a real web application, not a database admin page. It should answer these questions immediately:

- What bridges/environments are connected?
- Which agents exist, and where are they running?
- Who is currently working, idle, blocked, or offline?
- What conversations need attention?
- Which runs are pending handoff?
- What can I safely spawn, stop, restart, or recover?
- Which old failures are still actionable, and which are only audit history?

Daily workflow target:

1. Start the service/dashboard container.
2. Run `aify-comms` in each execution environment, for example native Windows and WSL.
3. Open the dashboard.
4. Spawn or recover managed agents from the dashboard, selecting the exact workspace per agent.
5. Chat with agents and channels from the dashboard; keep manual resident CLI registrations as compatibility/debug bindings.

## Core User Stories

- As a user, I can connect WSL and Windows bridges and see both as spawn targets.
- As a user, I can spawn a Codex agent in WSL or a Claude agent in Windows from the dashboard.
- As a user, I can select workspace, runtime, role, optional model/profile, and initial instructions before spawn.
- As a user, I can DM a spawned agent immediately without asking it to manually register.
- As a user, I can create a channel, add agents, and send a message to that channel.
- As a user, I can see which agents are alive, idle, working, blocked, or dead.
- As a user, I can stop a managed agent process and later respawn/resume the same identity when supported by the runtime.
- As a user, I can stop a managed agent process and recover it from stored backing even when the runtime does not support native resume.
- As a user, I can inspect token/cost telemetry when the runtime exposes it.
- As a user, I can inspect enough run/session evidence to understand what happened, with richer transcript/log views added per adapter as they mature.
- As a user, I can start a clean new session from an old session using a reviewed compaction packet, including switching model, runtime, bridge, or workspace.
- As a user, I can repair old missing-handoff rows and dismiss reviewed historical failures from the Home queue without deleting audit records.

## Non-Goals For Initial Build

- Full Minecraft integration.
- Building a custom LLM runtime.
- Perfect cross-runtime feature parity.
- Replacing Claude/Codex/OpenCode auth flows.
- Running native Windows processes directly from a Linux container without a Windows bridge.
- Infinite autonomous agent loops. Budget, loop, and mention controls must exist before automatic multi-agent reply behavior becomes a default.
- Pretending every runtime has the same native session model. The product UX should be consistent, but adapters must expose real capability flags.

## Key Product Decisions

- Environments are first-class. A machine/OS bridge advertises what it can run.
- Agents are lifecycle-managed records, not just self-registered inbox owners.
- Messaging is the source of truth. Dispatch/run state remains attached to messages.
- Dashboard spawn is the normal path. Manual `comms_register` is compatibility/debug.
- Dashboard chat is live-delivery gated for offline/stale/no-wake targets. Busy targets can receive deliberate queued follow-ups, and response handoffs queue automatically so close-together teammate replies are not lost.
- Headless adapters hide CLI details. The rest of the system asks for `runtime=codex`, not for raw shell flags.
- Managed warm sessions are always backed by durable state: agent identity, spawn spec, workspace, transcript/memory, runtime handles when available, and recovery policy.
- Native CLI attach is optional. A session can be recoverable through the dashboard even when it cannot be opened in Claude Code/Codex CLI later.
- Bridges are execution owners. The container coordinates; the bridge running in Windows/WSL/Linux validates paths and starts native processes.
- Continue-from is not native resume. It creates a new session from a portable compaction packet so users can compact context or switch runtime/model/environment safely.

## Product Quality Bar

- **Zero registration ceremony for spawned agents.** If an agent was spawned from the dashboard, it should appear online without manual MCP calls.
- **No hidden ownership.** Every agent/session shows its owning bridge and workspace.
- **No path ambiguity.** The dashboard uses workspace roots advertised by the bridge. Windows paths stay Windows paths; WSL paths stay WSL paths.
- **No fake symmetry.** Runtime differences are hidden where possible but visible as capability flags where they matter.
- **No infinite loops by default.** Channel messages and agent-to-agent replies need budgets, thread limits, and clear paused states before automatic reply behavior is enabled.
- **Recoverability first.** Killing a process should not destroy the teammate identity or conversation state.
- **Compaction is user-visible.** If a new session is seeded from an old one, the handoff packet should be reviewable/editable, not hidden magic.
- **Focused team communication.** Agents should answer each other naturally, but messages must stay scoped to one ask/result/blocker, verify state before asserting, and route broad work into smaller owner-specific handoffs instead of burning context on unrelated topics.
