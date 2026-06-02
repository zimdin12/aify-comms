# Managed-Hermes Lifecycle Ownership + Restart Teardown — Design

**Date:** 2026-06-02
**Status:** DESIGN (awaiting operator approval before plan)
**Author:** investigation synthesis (4 parallel root-cause agents) + operator requirements

## Problem (one sentence)

The managed-hermes runtime is **three independently-detached host processes — gateway host (`hermes dashboard --tui --port`), delivery loop (`node hermes-managed-host.js run <agent>`), console PTY (`hermes --tui`) — with no single lifecycle owner**, and the server infers agent health from *presence* (gateway answering / heartbeat fresh) instead of *deliverability* (a live claimer + a live console). Every operator-observed symptom is a facet of that one structural fact.

## Symptom → root-defect map (all verified, file:line)

1. **Queued runs pile up, never delivered.** No reaper covers `status='queued'`. `_repair_unusable_active_runs`, `_close_orphaned_managed_runs`, `_requeue_orphaned_claimed_runs`, `_close_reconcilable_delivered_runs` all select `claimed`/`running`/`delivered` only (`service/routers/api_v2.py`; reconcile wired in `service/main.py:59-140`). A queued run whose target has no live claimer is invisible to every reaper; it accumulates (merged buffer) until `_DISPATCH_BUFFER_CAP` then hard-rejects sends (`buffer_full`). Only agent-delete drains it (`_cancel_nonterminal_runs_for_agents`, api_v2.py:7452).

2. **Fresh hermes shows `online` but is deaf (no delivery loop).** `runDeliveryLoop` does `await ensureGatewayHost` at `hermes-managed-host.js:1064` **before** `startLivenessHeartbeat` (:1117) and the first `/dispatch/claim` (:1157). A throw there → `process.exit(1)` (:1278) with **zero `bridge_instances` trace**, while the gateway host (spawned separately by the wrapper's `ensure-host`, `install.sh:1752/1412`) is up and serving the index → status `online`. The loop is spawned **fire-and-forget** (`Start-Process`/`nohup … & disown`, `install.sh:1778/1434`) and **never health-checked** by the wrapper.

3. **Orphan loops for deleted agents** (`gtest-echoes-hermes`, `graph-hermes-tl`). `/dispatch/claim` returns 410 for a tombstoned agent (`api_v2.py:13778-13782`), but the loop **swallows it** in `runPollCycle`'s catch (`hermes-managed-host.js:981-984`) and only ever exits on `claim.release===true` (:968,:1186), which the server returns **only** on a managed→resident switch (`api_v2.py:13809-13815`) — never on delete.

4. **`online` ≠ deliverable.** `_compute_live_status_cache`'s `has_live_worker` console branch (`api_v2.py:3253-3279`) sets `online` from a live console PTY / `virtual-rpc/hermes` synth-terminal / `-aify` command **presence alone**, before and independent of the hermes channel-sidecar gate, and **without channelEnabled**. Managed **claude** is forced through a both-required gate (`sidecar_live AND console_live`, :3314) because `claude-code ∈ _CHANNEL_SIDECAR_DELIVERY_RUNTIMES` (:346); **hermes is deliberately excluded**, so no live-claimer gate applies.

5. **Console frozen / can't input.** The console PTY is a node-pty child of the environment bridge; its `status='attached'` row only transitions via the owning bridge's in-memory `onExit` (`terminal-runtime.js:262,404-468`). A **bridge restart** strands the row `attached` with a dead `process_id`; **no PID-liveness reaper exists** (`process_id` is only read for the Stop-control kill-by-pid fallback, `server.js:1859-1862`). `_reconcile_managed_worker_hygiene` is scoped to `{"claude-code"}` (`api_v2.py:346,2307,2389`) so it never examines hermes rows.

6. **Proliferation across restarts.** Gateway host (`hermes-managed-host.js:267-275`, `detached+unref`), api_server daemon (`hermes-daemon.js:183-198`), and delivery loop (`install.sh:1434`, `nohup … & disown`) are **engineered to outlive the launcher**. Bridge shutdown (`server.js:401-454` `cleanupOnExit`/`shutdownWithStatus`) kills only in-memory PTYs + in-process RPC sessions; **every detached child survives, and there is no startup reap**. Kill-prior (`hermes-daemon.js:169-203`, `install.sh:1373-1378`) fires only on **re-launch of the same agent** — never on restart or on delete.

7. **Marker/DB cruft accumulates unbounded.** `aify-hermes-port-<agent>` and `aify-hermes-key-<agent>` (`hermes-endpoint.js:142,161`) are **never deleted by anything**. `aify-hermes-daemon-pid-*` leaks on SIGKILL/restart. `dispatch_runs` has **no FK cascade to agents** (`db.py:147-148`) and **no row pruner** (`_prune_terminal_history` only trims events/output, api_v2.py:14839) → every run for a deleted agent is orphaned forever. Only `runtime-markers/` self-heals (PID-keyed GC, `runtime-markers.js:100`).

### Why the team "works" while consoles look dead (operator-observed 2026-06-02)
Delivery flows through the **gateway host + delivery loop** (`prompt.submit`), not the console PTY. When the loop is alive, runs are claimed and worked and `working` is reported — with the console PTY showing nothing because it is a *separate, often-frozen* process. This is the visible-TUI requirement being violated, not a delivery failure.

## Operator requirements (the bar)

1. **Managed sessions are bridge-owned and die with it.** Restarting `aify-comms` must tear down **every** managed session it spawned (gateway hosts, delivery loops, console PTYs, daemons). Restart = clean slate, zero survivors — including after SIGKILL/crash.
2. **No orphans, ghosts, or proliferation** — no loops for deleted agents, no `attached` rows with dead PIDs, no daemon-alive-but-deaf agents, no marker pile.
3. **`online` means deliverable**, never "a heartbeat arrived / the port answers."
4. **Visible TUI that actually works** — live output *and* input, not a frozen buffer.
5. **Accurate, event-driven status** — replace the second-based flapping switches with real turn/liveness events + one long backstop.
6. **Docs + skills + harnesses/integrations** updated to match.

## Proposed fix architecture

### WS1 — Single lifecycle owner for the managed-hermes triad
- Harden `runDeliveryLoop`: start liveness-heartbeat + bridge registration **before** `await ensureGatewayHost`; move gateway bring-up into the retry loop (no one-shot pre-loop throw → no silent `process.exit(1)`).
- Delivery loop **self-exits + tears down** on terminal conditions: `/dispatch/claim` 410 (agent removed), confirmed-dead gateway, console-gone, `release`. On exit it authoritatively kills the gateway host by **port** (fix the `gatewayChild===null` teardown no-op, `hermes-managed-host.js:995`) and clears its markers.
- Wrapper **health-gates the loop**: the loop writes a ready marker after (1) gateway ok, (2) heartbeat started, (3) one successful claim; the wrapper waits (bounded) before `exec`'ing the TUI and **fails loud** if the loop never becomes a live claimer (`install.sh` bash :1407-1455 / PowerShell :1747-1797).
- Kill-prior PID-excludes the loop the current wrapper is spawning (`install.sh:1714/1378`).

### WS2 — Bridge restart teardown + startup reap (HARD requirement #1)
- **Shutdown teardown hook** in `shutdownWithStatus` (and the supersede path, `server.js:1659`): enumerate + kill every managed child this environment bridge owns — daemons via `stopDaemon`, gateway hosts via port-kill (`defaultKillByPort`), delivery loops via agent-scoped cmdline reap (new reaper mirroring `reap-managed-claude.js`), console PTYs via `process_id` tree-kill.
- **Startup survivor sweep** (environment-bridge boot, before `ensureSpawnLoop`): reap any managed survivor scoped to this env's `cwdRoots` whose owning bridge isn't fresh — catches SIGKILL/crash survivors so "restart = zero survivors" actually holds.
- **Scoping safety:** kill only managed agents within this env bridge's roots whose owning bridge is not live; **never** a resident operator session (reuse the agent-scoped / parent-`--aify-agent` safety from `reap-managed-claude.js`). Use real Windows PIDs / port→`Get-NetTCPConnection`, never MSYS `$$`.

### WS3 — Deliverability-based status + queued-run backstop
- Add `"hermes"` to `_CHANNEL_SIDECAR_DELIVERY_RUNTIMES` (`api_v2.py:346`) so the both-required gate (`sidecar_live AND console_live`) and `_reconcile_managed_worker_hygiene` cover the hermes triad; gate the `has_live_worker` console branch (:3253) so console/gateway presence alone can't manufacture `online`.
- **New queued-run backstop reaper**: fail/expire (or surface to sender) a `queued` run whose target has **no live claimer** — the single missing terminal path. Prefer refusing to queue to a non-deliverable target at send time.
- Extend the reaper to detect the hermes triad failure ("gateway alive + no delivery loop + console ghost").

### WS4 — Cruft GC
- `stopDaemon`/kill-prior/agent-remove clear `aify-hermes-port-*` + `aify-hermes-key-*` (mirror `clearDaemonPid`).
- Host-side TEMP sweep of `aify-hermes-*` for tombstoned/unknown agents.
- PID-liveness reap of `attached` console rows (host-reported, since the server can't probe a remote PID).
- `dispatch_runs` pruner for tombstoned agents / terminal-status TTL.

### WS5 — Event-driven turn signals (replace second-based switches)
- Drive `turn_busy` from real hermes turn-start/turn-end (`pre_llm_call`/`post_llm_call` / gateway WS), and liveness from delivery-loop start/exit lease + bridge/WS disconnect. Keep **one** long wall-clock backstop only. Stage carefully — this area caused prior feedback-loop regressions; behaviour-tested, incremental.

### WS6 — Docs / skills / harnesses / integrations
- `install.sh` wrapper changes (loop health-gate, kill-prior PID-exclude, marker cleanup).
- `aify-comms-debug` + `aify-comms` skills (both mirrors), `KNOWN_ISSUES.md`, `DECISIONS.md`, README managed-modes section.

## Sequencing
WS1 → WS2 → WS3 → WS4 first (they kill the symptom classes and satisfy the HARD requirement). WS5 (event-driven) staged next as its own careful pass. WS6 throughout / at close. Each workstream is TDD via subagent-driven-development, on a feature branch, deployed + live-validated at an operator checkpoint.

## Key file references
`mcp/stdio/hermes-managed-host.js` (loop lifecycle :1040-1207, teardown :995-1027, gateway host :232-282), `mcp/stdio/hermes-daemon.js` (:50-92,:169-203,:290-339), `mcp/stdio/hermes-endpoint.js` (:112-147,:161), `mcp/stdio/server.js` (shutdown :401-454, supersede :1642-1660, spawn loop :1675-2102, PTY :1810-1862), `mcp/stdio/terminal-runtime.js` (:227-523), `mcp/stdio/reap-managed-claude.js`, `install.sh` (bash :1407-1455, PowerShell :1747-1797, kill-prior :1373-1378/:1714-1718), `service/routers/api_v2.py` (:346,:2270-2459,:3152-3341,:6610,:7452,:13778-13815,:14839, reconcile constants :287/:468/:474/:731), `service/main.py:59-147`, `service/db.py:120-149,:340-360`.
