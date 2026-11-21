# Plan Review And Brainstorm

This document pressure-tests the plan after the initial scaffold.

Status note: this is a design-pressure document, not the current operator guide. For live setup and behavior, prefer `README.md`, `docs/BRIDGE_SETUP.md`, `docs/SESSION_MODEL.md`, `docs/COMMUNICATION_GUIDE.md`, `docs/DASHBOARD_REVIEW.md`, and the installed `aify-comms` / `aify-comms-debug` skills.

## Strongest Direction

The project should be framed as a **control plane for agent teams**, not as "a better message bus."

The strongest mental model is:

```text
Dashboard = command center
Bridge = connected execution capacity
Environment = where native processes can run
Agent = stable identity
Session = current runtime backing for that identity
Spawn spec = durable recipe to recreate the session
Conversation backing = durable memory/transcript continuity
```

If every feature fits this model, the product stays coherent.

## Product Decisions That Should Not Drift

- Dashboard-spawned agents do not require manual registration.
- Managed warm is the default agent mode.
- Run-once is advanced/internal, not the main agent model.
- Resident visible is for human-open CLI sessions with a tested multi-client injection path such as `codex-aify`, `claude-aify`, or `hermes-aify`; Pi wrapper sessions are presence/standalone because triggerable Pi delivery is managed RPC.
- Every managed warm agent is persistent/backed.
- Persistent/backed does not imply CLI-attachable.
- Handoff compaction creates a new backed session from a portable compaction packet; it is not native resume or native in-place compact.
- The container coordinates; bridges execute native processes.
- Workspaces are environment-native and bridge-validated.
- Messaging is the source of truth; dispatch is execution state attached to a message.
- Runtime differences are adapter/capability details, not user-facing complexity unless capability matters.

## Biggest Risks

### Risk: Dashboard Becomes A Database Viewer

Failure mode:

- too many columns
- raw IDs everywhere
- messages split across multiple pages
- users must understand `bridgeInstanceId`, `sessionHandle`, and MCP terms

Mitigation:

- Chat, Control, Work Loop, Environments, Sessions, Runs, Analytics, Artifacts, Help, and Settings are product views. Identity Directory is an on-demand management drawer/modal, not primary navigation.
- Runs/raw events/logs are inspector/debug views.
- Long details move to drawers.
- Primary rows show human labels and status chips.

### Risk: Fake Runtime Symmetry

Failure mode:

- Claude, Codex, Hermes, OpenCode, and Oh My Pi are treated as if they have identical session models
- UI promises "resume" or "open in CLI" where unsupported
- recovery breaks because native handles are missing

Mitigation:

- adapters expose capability flags
- UI shows capability badges
- bridge-emulated resume is allowed and explicit
- `cliAttach` is false unless tested

### Risk: Path Confusion Returns

Failure mode:

- service guesses Windows/WSL path conversion
- bridge launches runtime in wrong cwd
- session resume fails because thread store/workspace does not match

Mitigation:

- bridge advertises roots
- dashboard selects environment-native workspace
- bridge validates workspace
- records store exact native path string

### Risk: Agent Loops Burn Context

Failure mode:

- channel chat creates infinite agent-to-agent replies
- high-priority notifications loop
- users cannot tell why agents stopped or kept talking

Mitigation:

- thread budgets
- per-group auto-reply policy
- visible paused/budget state
- "release budget" button
- high priority is urgency, not unlimited budget

### Risk: Managed Sessions Become Unrecoverable

Failure mode:

- process dies
- dashboard only had PID/session handle
- no spawn spec or transcript backing exists

Mitigation:

- spawn spec is mandatory
- conversation backing is mandatory
- new session links to same agent identity
- native resume is attempted first, bridge resume second

### Risk: Compaction Hides Critical Context

Failure mode:

- old session produces a vague summary
- new runtime starts with missing decisions or wrong assumptions
- user believes "continue" is exact resume and trusts it too much

Mitigation:

- compaction packet is structured and editable
- source/target runtime/environment are visible
- packet includes risks, constraints, important files, and next action
- old session stays linked and accessible
- UI labels this as "continue from", not "resume"

### Risk: Bridge Ownership Races

Failure mode:

- two bridges think they own the same managed session
- stale bridge claims work
- user attaches CLI while bridge is writing to same session

Mitigation:

- exactly one owning bridge per managed session
- claim APIs check current bridge ownership
- CLI attach either takes a lock or is read-only/transcript-only initially

## Dashboard UX Principles

- **Home answers what matters now.** Urgent unread, pending handoffs, failed spawns, lost sessions, degraded bridges.
- **Chat is where work feels alive.** Messages, dispatch status, handoffs, artifacts, and run state appear inline.
- **Agents are stable team identities.** Agents persist beyond one process.
- **Sessions are machinery.** Sessions show runtime/process details and recovery state.
- **Environments are capacity.** Bridges show what can be spawned where.
- **Runs are diagnostics.** Useful, but not the primary workflow.
- **Settings are policy.** Budgets, profiles, roots, permissions, and pricing live there.

## UI Shape That Should Work

Use a four-panel application shell:

```text
Global nav | Context list | Main pane | Inspector drawer
```

Examples:

- Chat: nav -> DM/group list -> timeline/composer -> selected message/run details
- Identity Directory: Sessions -> directory -> identity table/cards -> session/spawn spec actions
- Environments: nav -> bridge list -> environment detail -> logs/capabilities drawer
- Sessions: nav -> session list -> live logs/telemetry -> recovery/actions drawer

This keeps primary views readable while preserving advanced detail.

## Roadmap Pressure-Test

The roadmap should stay in this order:

1. Environment registry.
2. Spawn request queue.
3. Managed agent auto-registration.
4. Backed session store.
5. One real managed-warm adapter, likely Codex in WSL.
6. Warm loop generalization.
7. Compaction and continue-from.
8. Real chat UI.
9. Windows bridge spawn.
10. Claude managed warm, with native resume only if proven.

Why not start with chat UI?

- Chat without spawn/environment/session ownership repeats the current system's ambiguity.

Why not start with Claude?

- Claude headless persistence is less certain. Codex app-server/thread model is likely easier to prove first.

Why not start with one-shot?

- One-shot is not the product. It is a utility path. Starting there would create the wrong default mental model.

## Open Questions For Implementation

- What exact Codex API path is most stable for bridge-owned warm sessions: app-server thread calls, `codex exec` with resume, or another CLI flow?
- What Claude Code headless persistence/resume behavior is actually available and reliable?
- Should bridge be installed as a persistent daemon/service, or is a long-running CLI enough for v1?
- How should secrets/auth status be represented without exposing secrets in the dashboard?
- Do private channels cover multi-agent chat well enough, or is a separate group model worth adding later?
- Should the frontend stay as one static HTML file for Slice 1-3 or move to a small SPA before real chat?

## Current Recommendation

Build Slice 1-4 in the current simple stack, but design APIs as if a richer frontend will consume them.

Do not overbuild the frontend framework before environment/session foundations exist. But when real Chat begins, stop growing `dashboard.html` indefinitely; extract a proper frontend boundary.
