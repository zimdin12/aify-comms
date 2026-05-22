# Plan: Persistent-worker model + status taxonomy refresh

Captured from operator direction on 2026-05-22 ("but do the change"). The contract for the multi-phase work that follows. Do NOT delete after the PR ships — keep as the architectural-decision narrative; promote the key bits into DECISIONS.md when implementing the docs phase.

## Goal

Unify every managed-agent runtime under a single mental model: **one persistent worker process per agent, lifecycle controlled by the operator (or wake-on-message), visible through a synthesized terminal feed**. Replace the overloaded `active` status with a clearer split.

## The state machine

```
              env-offline ─── agent.status = "offline"
                  │
              env-online ───▶ agent.status = "available"
                                   │
                  send message ────▶  spawn agent's persistent worker
                                   │  attach synthesized terminal
                                   ▼
                              "online"  (worker alive, idle)
                                   │
                  message routed ──▶ "working"  (worker handling turn)
                                   │  per-runtime turn-end signal
                                   ▼
                              "online"  (back to idle)
                                   │
                  operator Stop ───▶ kill worker process + tear down terminal
                                   ▼
                              "available"  (back to waiting)
```

## State semantics

| Status | Meaning | Predecessor signals |
|--------|---------|----------|
| `offline` | Env not reachable. Agent cannot be started from here. | env_status not in {online, degraded} |
| `available` | Env online; agent registered; no persistent worker yet. Send a message OR explicit Start spawns the worker. | env_status in {online, degraded} AND no live worker |
| `online` | Worker alive and idle. Mentally: "ready, watching for work." | worker process alive AND turn_busy=0 AND no in-flight dispatch_run |
| `working` | Worker actively processing a turn. | (worker alive AND turn_busy=1) OR active dispatch_run claimed/running OR channel-route delivered+require_reply |
| `blocked` | Worker is awaiting operator input mid-turn (existing semantic). | Unchanged. |
| `stopped` | Operator marked agent manually stopped. | Manual status override. |

## Per-runtime worker shape

| Runtime | Today | After |
|---------|-------|-------|
| **pi managed** | Persistent `omp --mode rpc` child (Phase 1 of this branch). ✓ | Unchanged. Already the reference shape. |
| **claude-aify** | Operator-launched wrapper PTY. `claude-channel.js` claims dispatches inside it. ✓ | Unchanged in lifecycle. Add: dashboard Stop kills the wrapper. Wake-on-message: spawn wrapper if absent (eager_spawn unified). |
| **codex managed** | Per-dispatch codex app-server connection. | Persistent connection per agent — pool keyed by agentId, reuse across turns, close on idle/Stop. Mirrors PiSession. |
| **opencode managed** | Per-dispatch SDK call. | Persistent SDK session per agent. Mirrors PiSession. |
| **hermes managed** | Per-dispatch `hermes chat -Q -q`. | Per-dispatch `hermes chat -Q -q --continue <name>` for session continuity (upstream constraint: no long-lived programmatic mode). Synthesized terminal stays alive across dispatches — operator sees one continuous feed. Worker "session" is the durable `--continue` name + the synthesized terminal_session row; worker "process" is short-lived per turn but invisible to the operator. |

## Wake-on-message

A dashboard send (or any dispatch trigger) to an agent in `available` status MUST:

1. Spawn the persistent worker (or whatever the runtime's equivalent is — for hermes, materialize the synthesized terminal + bind the `--continue` session name).
2. Wait for the worker to reach `online` (worker ready signal — `ready` event for pi, app-server connected for codex, etc.).
3. Forward the dispatch to the now-online worker.
4. Status flows: `available` → spawn → `online` (brief) → `working` → (turn-end) → `online`.

If the spawn fails (executable not on PATH, env capability missing, etc.), the dispatch fails visibly and the agent stays `available` (not stuck in a bad state).

This subsumes the existing `managed_pty_eager_spawn` setting: the setting becomes implicit ("always eager-spawn on first send"), and the existing toggle is kept as a deprecation alias for one release.

## Stop

A dashboard Stop on an `online` or `working` agent MUST:

1. Gracefully shut the persistent worker (`PiSession.stop`, send SIGTERM to wrapper PTY, close codex app-server, etc.).
2. Tear down the synthesized terminal_session row (status → 'stopped').
3. Clear `runtime_state.virtualTerminalId` and any worker references.
4. Move agent to `available` (not `stopped` — `stopped` remains a manual override status).

If the worker can't be cleanly shut, force-kill after a 5s grace window. Status reflects the actual outcome.

## Data-model touch points

- `agent_live_state.status` — enum widens to include `available`, `online`. `active` retained as a one-release tombstone alias.
- `_compute_live_status_cache` rules update — new logic for the available/online/working split based on worker presence + turn_busy + dispatch_runs.
- New `worker_state` notion (might just live in `agent_live_state` or `runtime_state` JSON) tracking whether a persistent worker is currently spawned. Could be derived from existing signals (PiSession pool entry, codex/opencode pool entries, terminal_sessions row status, claude-aify wrapper liveness via bridge_instances).
- Dashboard rendering of `active` continues to work (rendered as `online`) for backward compat with bookmarked filters/saved views.

## Migration plan

Multi-commit on `feature/dashboard-console-mode` (current branch):

1. **Plan doc** — this file. Reference point for the rest.
2. **Status taxonomy** — server-side enum + derivation rules + tests.
3. **Wake-on-message** — service-side auto-spawn before delivery.
4. **Stop endpoint** — wire dashboard Stop to bridge-side worker shutdown.
5. **Codex persistent** — new CodexSession class mirroring PiSession.
6. **Opencode persistent** — new OpenCodeSession class.
7. **Hermes persistent SESSION** — `--continue` semantics + synthesized terminal that survives dispatch boundaries.
8. **Docs** — DECISIONS, README, install.*, skills.

Each commit is operator-pull-safe. Phases 5-7 are independent; can be reordered or shipped one at a time.

## Open questions

- **Hermes**: confirmed operator-acceptable to ship option (a) — per-dispatch process with `--continue` for session continuity, synthesized terminal as the "persistent session" UX surface — vs option (c) blocking on upstream Hermes adding a daemon mode? Operator said "do the change" without selecting between (a) and (c), reading as (a) ship-it-now.
- **`stopped` semantics**: current `stopped` is a manual status override that survives env changes. New `available` is dynamic (depends on env). They coexist — `stopped` wins over derivation.
- **`offline` agents**: today an env-down agent shows `offline`. Keep that — `offline` is the pre-status state.

## Status (as of save)

- [x] Phase 1 — Plan doc (this file) — `5714da8`
- [x] Phase 2 — Status taxonomy (available/online/working) — `ba45795`
- [x] Phase 3 — Wake-on-message (test-pinned; works implicitly via Phase 2 + existing per-runtime dispatch handlers) — `1904269`
- [x] Phase 4 — Stop endpoint (`POST /agents/{id}/stop-worker`) — `1904269`
- [x] Phase 5 — Codex synthesized terminal feed shipped via `a625030` (`aify://virtual-rpc/codex`, per-event frames). Full **persistent-worker pool refactor** still deferred — 3-5 day work: new CodexSession class mirroring `pi-session.js`. Today managed codex still uses per-dispatch app-server connections — UX is identical to the pool version, remaining gain is one connection per agent vs per turn.
- [x] Phase 6 — Opencode synthesized terminal feed shipped via `a625030` (`aify://virtual-rpc/opencode`, coarser frames because SDK doesn't expose granular events). Full **persistent-worker pool refactor** still deferred — same shape as Phase 5.
- [x] Phase 7 — Hermes persistent SESSION via `--continue` — `1904269`. Process is per-dispatch (upstream constraint), session+terminal are durable.
- [ ] Phase 8 — Docs — in progress.

Phases 5 + 6 are scoped but not implemented; they're efficiency wins (one spawn per agent vs per dispatch) without affecting operator UX. The architectural plumbing — taxonomy + wake + stop + synthesized terminal — is already in place for them to slot into when implemented.

Update checkboxes as commits land.
