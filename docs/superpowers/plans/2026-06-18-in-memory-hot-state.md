# Eliminate DB lock contention via in-memory hot-state (scalable)

> Goal: **`database is locked` → 0**, and stays 0 as agent count grows. Operator's insight:
> we use SQLite for too much — hot/ephemeral state (status cache, liveness, turn-state,
> console signals, terminal output) is high-frequency, latest-value-only, and recomputed on
> restart, so it never needed durable storage. Storing it in SQLite (single writer) is what
> creates the write storm AND keeps the WAL bloated (constant readers block checkpoints).

## Why this is the root-cause fix (not another patch)
- The 8800 service is a **single uvicorn process / single event loop**; dashboard-next proxies
  to it. So a module-global `dict` is a safe, coherent, lock-free shared cache (Python async is
  single-threaded — dict access between `await`s is atomic; no mutex needed).
- Moving hot state to memory removes most **writes** (no more per-poll status upserts, no
  heartbeat row-writes) AND most **reads** (dashboard polls served from memory). Fewer readers
  ⇒ the WAL checkpoint can finally run ⇒ WAL stays small ⇒ commits are microseconds ⇒ even the
  remaining durable writes stop contending.
- It scales: a dict is O(1) and has no single-writer bottleneck, unlike every SQLite write.

## What moves to memory (ephemeral, latest-only, recomputed on restart)
| State | Today (DB table) | After |
|-------|------------------|-------|
| Derived status cache | `agent_live_state` | process-global dict, recomputed on miss/expiry + by reconcile |
| Turn sub-state | `agent_turn_state` (turn_busy, in_turn, awaiting) | dict (event-driven; restart = no active turn) |
| Console working lease | `agent_console_signal` | dict (short TTL) |
| Claimer lease | `claimer_leases` | dict |
| Liveness | `agents.last_seen` write per heartbeat | in-memory last-beat map + **periodic** flush to DB (≤1/agent/min) for restart continuity |
| Terminal output (Stage 4) | `terminal_sessions.output` + `terminal_events` per frame | in-memory ring buffer + periodic snapshot flush (replay still works) |

## What STAYS in the DB (durable records — low write frequency)
agents (identity), messages, dispatch_runs/events/controls, channels, channel_members,
shared_artifacts, settings, environments, agent_sessions, terminal_sessions (the row/metadata,
not every output frame), spawn_specs/requests, agent_tombstones, bridge_instances.

## Stages (each shippable + lock-verified before the next)
- **Stage 1 — status cache → memory.** Replace the `agent_live_state` table on the hot path
  with `_LIVE_STATE_CACHE` dict. `_refresh_*` compute into memory (no DB write); reads
  (list_agents/get_agent + the blocked-reason + WS push) read the dict; `_invalidate_*` pops the
  dict. Biggest read-path win; kills the GET /agents write storm. Update tests that seed/assert
  the table. **Verify: GET /agents writes = 0; lock rate drops sharply.**
- **Stage 2 — turn_state + console_signal + claimer_leases → memory.** Same pattern; these feed
  status. Removes their writes. **Verify.**
- **Stage 3 — liveness → memory + periodic flush.** Heartbeat updates an in-memory map; a 30–60s
  background task flushes last_seen to `agents` in one batched txn (or skip — re-derived on
  reconnect). Kills the heartbeat write storm (the #1 write-lock source). **Verify.**
- **Stage 4 — terminal output buffering (optional, biggest volume).** Buffer frames in memory;
  flush a coalesced snapshot to `terminal_sessions.output` periodically; keep `terminal_events`
  for replay but at far lower write rate. Trickiest (console replay) — do last, carefully.
- **Stage 5 — 100% guarantee (only if any lock remains under load): single writer connection.**
  Route all durable writes through ONE long-lived aiosqlite connection fed by an asyncio queue.
  SQLite then never sees two writers ⇒ `database is locked` is impossible by construction. Reads
  stay on per-request connections (WAL concurrent). NOTE: this is NOT the reverted global-lock
  proxy — that failed because per-request connections + a lock held across checkpoint stalls
  dead-locked. A single dedicated writer task has no global lock and no cross-connection
  checkpoint contention.

## Safety / invariants
- Single process only — if the service is ever scaled to multiple workers, the in-memory cache
  needs an external store (Redis) or sticky routing. Document this loudly (uvicorn stays
  single-worker; the dashboard-next proxies, it does not open the DB).
- Cache lost on restart is fine (recomputed in one reconcile pass; that's what a cache is).
- Already shipped (keep): read endpoints serve cached/degrade on lock (581341d) — belt-and-suspenders.

## Verification each stage
`docker logs aify-comms-service --since 120s | grep -c "database is locked"` under live fleet
load AND a `docker compose restart` storm. Target 0. Full Python + node suites green.
