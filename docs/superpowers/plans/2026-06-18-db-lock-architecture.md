# DB Lock Contention — Architecture Plan (the real fix)

> Status: PLAN (not yet executed). The 2026-06-18 round shipped the low-risk mitigations
> (WAL checkpoint hygiene + bounded roster refresh); this doc captures the deeper fix that
> needs its own focused, carefully-tested round.

## Problem

`database is locked` HTTP 503s on the live fleet (~18–30/min steady, ~137/min in the
post-restart reconnection storm). Agents report it disrupts teamwork; it also makes the
periodic reconcile skip ("Periodic dispatch reconcile skipped: database is locked").

## Root cause (confirmed by live inspection 2026-06-18)

1. **Connection-per-request + SQLite single-writer.** `db.get_db()` opens a NEW aiosqlite
   connection per request. SQLite allows exactly one writer; under fleet concurrency
   (every bridge heartbeating + polling `/dispatch/claim`, both dashboards polling ~13
   endpoints each, agents' `comms_agents`, the 60s reconcile) writers collide and the loser
   waits past `busy_timeout=5s` → 503.
2. **WAL checkpoint starvation → bloated WAL.** With ~40 short read transactions/second
   across the two dashboards, there is almost never a reader-free window, so the passive
   auto-checkpoint (1000 pages) can rarely advance past the oldest reader snapshot. The
   `-wal` file grew to **61–83 MB** (observed). A bloated WAL slows every read and lengthens
   each commit → longer SQLite write-lock windows → more collisions. A manual
   `wal_checkpoint(TRUNCATE)` returns `busy=1` (can't truncate past live readers).
3. Read-path writes (GET /agents | /agents/{id} | /sessions re-derive + repair) historically
   amplified the writer count; the roster refresh is now bounded (see Shipped, below).

## What was TRIED and is DEAD — do not repeat

**In-process write serialization via one `asyncio.Lock` (the `_SerializedWriteConnection`
proxy in `db.py`).** Attempted TWICE (258cb82→reverted f5c86c6; f77e288→reverted 3e857ab),
the second time PAIRED with bounded roster refresh. Result was WORSE: GET /agents and
/heartbeat hung (>12s) and `database is locked` persisted. Cause: a single global write lock
+ aiosqlite's per-connection thread + WAL auto-checkpointing + many concurrent readers — a
commit that stalls on a checkpoint (waiting on long reader snapshots) holds the global lock
for its whole duration, so every other writer queues behind it (FIFO head-of-line stall).
Serialization converts independent fast-failing locks into one cascading global stall. The
765-test suite did not catch it (no concurrency/checkpoint load in tests). **Verdict: a
single process-global write lock is the wrong tool here.**

## Shipped this round (low-risk mitigations, deployed)

- **WAL checkpoint hygiene**: explicit `PRAGMA wal_checkpoint(TRUNCATE)` each reconcile pass
  (`service/main.py`) + `PRAGMA journal_size_limit=16MB` (`service/db.py`) so any checkpoint
  that DOES advance reclaims the file instead of leaving it bloated. Bounds WAL growth.
- **Bounded roster refresh**: `LIST_AGENTS_REFRESH_LIMIT=8` caps the per-poll live-state
  re-derive burst on GET /agents (oldest-first), with the unbounded warm-up left to the
  reconcile + warm-on-boot. (Kept from the serialization attempt — it is good on its own.)

These reduce growth/burst but do NOT eliminate the lock class.

## Recommended real fix (ranked, pick per appetite)

### Option A (recommended): cut reader pressure so the WAL can checkpoint
The lock windows are long mostly because the WAL is huge, and the WAL is huge because
continuous polling starves the checkpoint. Reduce the read pressure and the WAL stays
small → commits fast → lock collisions largely vanish, with NO write-architecture change.
- **A1. Slow + consolidate dashboard polling.** `settings` is polled ~2.75/s for near-static
  data; sessions/messages/recent/stats/contracts/agents/environments/dispatch-runs each
  ~1.5/s, ×2 dashboards. Raise intervals (settings → 30s; most panels → 3–5s) and/or add a
  single `/dashboard/snapshot` aggregate endpoint the dashboards poll once instead of ~13
  separate reads. Touch: `service/new_dashboard/app.js` (+ `service/dashboard.html` if still
  served). Measure reads/s before/after; target < ~10 reads/s so checkpoint windows open.
- **A2. Verify the WAL truncates** after A1 (manual `wal_checkpoint(TRUNCATE)` should return
  `busy=0` and shrink the file). This is the success signal.

### Option B: route writes through a dedicated single writer connection (not a lock)
Keep per-request connections for READS (WAL concurrency), but send all WRITES to ONE
long-lived aiosqlite connection owned by a single asyncio task with an in-memory queue
(producer/consumer). aiosqlite already serializes ops on one connection via its thread, so
there is no global lock held across unrelated awaits — the failure mode of the dead approach.
Each handler enqueues its write-unit (a closure taking the shared conn) and awaits its
result. Higher effort (handlers do read-modify-write on their own conn today), so scope it as
a focused refactor with a concurrency/checkpoint load test, NOT a quick patch.

### Option C: last resort — move hot, high-churn tables off the request DB
e.g. terminal output frames to a separate SQLite file / ring buffer, or Postgres for the
whole store. Largest effort; only if A+B prove insufficient.

## Acceptance / test
- A load test that mimics the live fleet (N bridges heartbeat+claim + 2 dashboards polling +
  reconcile) for 5 min: assert `database is locked` count ≈ 0 and GET /agents p95 < 1s,
  INCLUDING a `docker compose restart` mid-test (the post-restart all-expired storm — the
  exact scenario that broke serialization). This load test is the missing safety net; build
  it FIRST so any future write-architecture change is validated under real conditions.
- Full Python suite stays green (765+).

## References
- Memory: `db-lock-write-serialization.md` (the full serialization post-mortem; "do not
  attempt a third time").
- Mitigations shipped: `service/main.py` reconcile WAL checkpoint; `service/db.py`
  `journal_size_limit`; `service/routers/api_v2.py` `LIST_AGENTS_REFRESH_LIMIT`.
