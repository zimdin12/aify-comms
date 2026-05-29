# Managed/Resident Session Lifecycle — Design

Date: 2026-05-29
Status: approved (design), pending implementation plan

## Problem

Managed agent sessions leak OS processes and accumulate duplicates, and the
dashboard status does not reflect reality. Concretely observed on `hermes-test`:

- The dashboard showed the agent **available** while **two** `hermes-aify
  --aify-agent hermes-test` process trees (each with its own gateway) were
  still running. `terminal_sessions` rows were `stopped`/`failed` (so status =
  available) but the OS processes lived on.
- Killing the processes did not stick: **4 `spawn_requests` stuck in
  `running`** (managed-warm) caused the bridge to respawn a worker.
- A managed dispatch failed with "visible session not found" because a stale
  `session_handle` was replayed as `hermes --tui --resume <dead session>`, and
  because two gateways existed the agent's `runtime_config.gatewayUrl` pointed
  at the wrong one.

A five-agent bug hunt (2026-05-29) traced these to systemic root causes below.
Every file:line reference is from that hunt and is the anchor for the fix.

### Root cause A — service marks terminals dead without killing the process
`service/routers/api_v2.py`:
- `_repair_terminal_session_consistency` (3040, runs on every `GET /sessions`)
  flips `terminal_sessions.status` to stopped/failed + clears the console
  binding but **never enqueues a `stop` terminal control**.
- `_release_stale_terminal_owner` (4430) and the managed-dispatch Console
  takeover (4391) mark rows `failed` with no stop control.
- `_reconcile_stale_managed_terminals_for_resident_agents` (1715, startup)
  assumes the bridge already died and force-stops rows even if a host bridge
  survived a service-only restart.
- `POST /terminals/{id}/stop` (8179) self-completes the stop control when the
  bridge "can't claim right now" (transient env-status blip / bridge re-register
  id change) — swallowing the stop while the PTY lives.
- `_fail_pending_terminal_controls` (2631) marks **all** pending controls
  failed, including a legitimately-queued `stop`, cancelling it before the
  bridge acts.
- `session_control` (8496) writes DB state optimistically and only broadcasts a
  WS event; if the bridge misses it, the row goes non-live while the process
  survives.

### Root cause B — bridge kills are too weak / not wired
`mcp/stdio/`:
- `terminal-runtime.js` PTY kill sites (303, 330, 457) call only
  `term.kill()` = **SIGHUP** to the wrapper bash. The wrapper traps
  `EXIT INT TERM` (not HUP) so bash dies without running cleanup, and the
  `hermes dashboard --tui` child is spawned via `setsid` (install.sh:1496) in
  its **own session**, so it never receives the signal. → gateway orphaned.
- `hermes-managed-gateway-session.js` `stop()` (180, 391) uses bare
  `_proc.kill()` not `terminateProcessTree`, orphaning the dashboard's child
  processes.
- **No `shutdownAll*` for codex or hermes wired into the bridge SIGTERM path**
  (`server.js` `shutdownWithStatus` 330-348 only stops terminals + pi). codex
  app-servers are spawned **detached** (`runtimes.js:51`) → guaranteed to
  survive bridge death. Only pi + claude (wrapper PTY) are reaped today.

### Root cause C — duplicate workers per agent
- `terminal_sessions` has **no uniqueness constraint on `agent_id`**
  (`service/db.py`). `_ensure_managed_pty_for_dispatch` (4526) does a
  read-then-INSERT with no `BEGIN IMMEDIATE`/guard, so two concurrent sends, or
  the registration-eager-spawn (7198) racing the dispatch-eager-spawn (10485),
  both spawn. `_release_stale_terminal_owner` (after a bridge re-register with a
  new `BRIDGE_INSTANCE_ID`) marks the old row failed and spawns a replacement
  **without killing the old PTY**.

### Root cause D — managed-warm persistence
- Managed agents keep a `spawn_request` in `running` (managed-warm) and a 24h
  idle timeout (`hermes-managed-gateway-session.js:29`,
  `codex-session.js:55`, `pi-session.js:27`). The bridge keeps a worker alive
  to satisfy the spec — the opposite of "0 running when idle."

### Root cause E — status has no liveness truth; resident leaks to "available"
`service/routers/api_v2.py`:
- `_compute_live_status_cache` (2331) infers liveness only from DB rows; there
  is **zero OS-process liveness checking**. A managed agent with no live
  `terminal_sessions` row falls straight to `available` (2469) even if a
  process is alive.
- The resident path can also reach the shared `available` fallback (2469) when
  its session row isn't live and it has no env binding, so a resident that must
  be manually resumed is advertised as auto-wakeable "available."
- `_enforce_live_worker_gate` (374) only downgrades online→available and is
  scoped out for claude-code and pi.

### Root cause F — stale wake-handle replay
- `HermesAdapter.console_command` (`service/runtimes/hermes.py:33`) appends
  `--resume <handle>` whenever a handle is stored; managed hermes does **not
  need** a handle (the gateway resolves `session.most_recent`). `PATCH
  /agents/{id}/session-mode` (9759) and re-register preserve the leaving-mode
  `session_handle`/`runtime_config` on both the `agents` and `agent_sessions`
  rows, so a dead handle survives a mode flip and is replayed.

## Desired model (authoritative)

- Managed worker lifecycle is **owned by the environment bridge**. Closing
  `aify-comms` (env bridge exits) **kills all of its managed runners**.
- In the dashboard: writing to an `available` managed agent, or explicitly
  starting its terminal, spawns the worker → **online**.
- Stopping the session / stopping the terminal **reaps the process** → the
  managed agent returns to **available** (a resident returns to **offline**).
- Workers stay **online as long as needed** — not force-killed after each
  message.
- **Configurable idle auto-stop**: a Settings-page checkbox (enable/disable) +
  integer minutes. When enabled, a managed worker idle for X minutes is
  auto-stopped → available. When disabled, it stays online indefinitely.
- **Never duplicate workers per agent.**
- Status reflects reality: managed is `available` (no worker) or `online`
  (worker running); resident is `online` or `offline` — never `available`.

## Design

### 1. Ownership & state machine
- Managed: `available` ⇄ `online`. Spawn triggers: message-send,
  explicit start-terminal. Reap triggers: stop control, idle auto-stop,
  bridge shutdown. Resident: `online`/`offline` only.
- The environment bridge is the lifecycle owner for managed workers; the
  service requests start/stop via controls, the bridge executes and acks.

### 2. One worker per agent (never duplicates)
- Add a **partial unique index** on `terminal_sessions(agent_id)` covering live
  statuses (`starting`, `attached`, `running`, `active`, `idle`,
  `recovering`).
- Wrap the reuse-check + INSERT in `_ensure_managed_pty_for_dispatch` in
  `BEGIN IMMEDIATE` (mirroring `/spawn-requests/claim`), with a guarded
  `INSERT ... WHERE NOT EXISTS (live terminal for agent)`.
- Make the registration-eager-spawn (`PATCH /spawn-requests/{id}` running
  transition) and the dispatch-eager-spawn share the same serialized path.
- `_release_stale_terminal_owner` enqueues a `stop` control for the old PTY
  (addressed to the old `bridge_id`) **before** any replacement spawns.

### 3. Stop = real reap (single authority)
- Every DB path that marks a terminal dead
  (`_repair_terminal_session_consistency`, `_release_stale_terminal_owner`,
  Console takeover, `/terminals/{id}/stop`,
  `_reconcile_stale_managed_terminals_for_resident_agents`, `session_control`)
  **enqueues a `stop` terminal control** when the owning bridge is online;
  it force-writes the dead status only when the bridge is provably gone.
- `_fail_pending_terminal_controls` **excludes `action='stop'`** from its
  fail-sweep so a queued stop survives until the bridge acks it.
- `/terminals/{id}/stop` leaves the control `pending` (not self-completed) when
  the bridge can't claim right now, so it is delivered on the next poll.

### 4. Bridge reaping
- `terminal-runtime.js`: all PTY kill sites use `terminateProcessTree`
  (process group + descendant sweep + SIGKILL escalation), not `term.kill()`.
- Wrapper reaping: either add `HUP` to the wrapper traps **and** have
  `cleanup_aify_dashboard` reap the `setsid`'d gateway, or stop
  `setsid`-detaching the gateway so it dies with the wrapper's group.
  (install.sh hermes + codex wrapper heredocs.)
- Add `shutdownAllCodexSessions`, `shutdownAllHermesSessions`,
  `shutdownAllHermesGatewaySessions` mirroring `shutdownAllPiSessions`; wire all
  into `server.js` `shutdownWithStatus`/`cleanupOnExit`.
- `hermes-managed-gateway-session.js` `stop()`/idle/failure paths use
  `terminateProcessTree`.
- Reconsider `detached` spawn for codex app-server so it does not survive
  bridge death (or ensure the shutdownAll path always kills it).

### 5. Configurable idle auto-stop
- New settings keys (DEFAULT_SETTINGS + persisted): `managed_idle_stop_enabled`
  (bool, default true) and `managed_idle_stop_minutes` (int, default e.g. 15).
- A periodic reaper (service-side sweep, or bridge-side using the setting)
  stops managed workers whose last activity exceeds the configured minutes,
  via the same stop-control path. Replaces the hardcoded 24h idle timers.
- Dashboard Settings page: checkbox bound to `managed_idle_stop_enabled` +
  number input bound to `managed_idle_stop_minutes`.

### 6. Status = reality
- `_compute_live_status_cache`: managed `available` requires positive evidence
  of **no live worker** (no live terminal row AND no recently-claimed/active
  run); absence of a row while activity is recent resolves to `online`/`unknown`
  pending reconciliation, never `available`.
- Resident branch added before the `available` fallback: a resident with no
  live worker/session resolves to `offline` (or `stale`), never `available`,
  independent of the `resident-run` capability gate.
- The bridge heartbeats actual live worker identifiers so the service can
  reconcile orphaned/stale states without waiting for the 30-minute heartbeat
  window; extend the read-path liveness gate to cover claude-code and pi.

### 7. No stale-handle replay
- Managed hermes/codex do not pass `--resume <stored handle>`; the gateway
  (hermes) / app-server (codex) resolves the live session. Update
  `service/runtimes/hermes.py` (and codex.py) `console_command` to omit resume
  for managed launches (or gate on a known-live flag).
- `PATCH /agents/{id}/session-mode` and re-register clear the leaving-mode wake
  state (`session_handle`, `runtime_config.gatewayUrl`/`appServerUrl`) on both
  the `agents` row and the `agent_sessions` row.

## Data model changes
- `terminal_sessions`: partial unique index on `agent_id` for live statuses.
- `settings`: `managed_idle_stop_enabled` (bool), `managed_idle_stop_minutes`
  (int).

## Testing strategy
- Service unit tests (mirror existing `service/tests/test_api_v2_regressions.py`
  patterns): stop-control enqueued on every dead-path; queued stop survives
  fail-sweep; duplicate-spawn prevented under concurrent sends; resident never
  computes `available`; managed `available` requires no-live-worker; idle
  auto-stop honors the setting.
- Bridge unit tests (`mcp/stdio/tests/`): `terminateProcessTree` used at all
  kill sites; `shutdownAll*` reaps each runtime; wrapper trap reaps the setsid
  gateway (fixture-based).
- End-to-end (manual + scripted): available→online→stop→available per runtime;
  close `aify-comms` → all managed runners die; no duplicates under rapid
  repeated sends; idle auto-stop after configured minutes; dashboard status
  matches `ps`.

## Phasing
1. Bridge reaping + ownership (root causes B; `terminateProcessTree`,
   `shutdownAll*`, wrapper traps).
2. One-worker-per-agent (root cause C; index + serialized spawn + stale-owner
   kill).
3. Stop-authority (root cause A; enqueue-stop everywhere, never cancel stop).
4. Configurable idle auto-stop + settings UI (root cause D).
5. Status truth (root cause E).
6. No stale-handle replay (root cause F).

Each phase is independently testable and shippable; 1–3 close the confirmed
leak and duplicate, 4 gives the operator control, 5–6 make status/switching
honest.
