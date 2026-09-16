# Idle cost, and updating on change instead of on a timer

Status: steps 1–3 SHIPPED in `2453faec` (v0.6.8) and deployed. Steps 4–5 are PROPOSED below and need an
operator decision before any code.

## What was measured (2026-09-16, operator's host, nothing running)

- `docker stats`: the service container sat between 0.05% and 11%, typically 1–3%. The operator saw 2–5%.
- py-spy, 90 s against the live service: 96 active samples of ~4,500, about 2.1% of one core. About half
  were inside aiosqlite's per-connection worker threads.
- tcpdump on the service's port for 60 s: 42 HTTP requests. 12 heartbeats, 7 dispatch claims, 7 control
  claims, 4 turn start/end, 2 health, and ONE dashboard bundle of 10 reads.
- That bundle came from Edge at `https://localhost:8801`, once a minute: a hidden tab, throttled.
- The request count does not explain the CPU. Two things in the code do:
  - `get_db()` opened a new connection, with its own thread and a PRAGMA script, for every request and
    every background tick, then closed it.
  - Every waiting claim long-poll re-runs its claim inside the service every 3 s (`longpoll.DEFAULT_FALLBACK_S`),
    each re-run a fresh connection and a `BEGIN IMMEDIATE`. About 14 waiters on this host.
  - The pi flip loop opened a connection every 5 s on a host with no pi agent.

## Built (steps 1–3)

| Step | Where | What changed | What stays the same |
|---|---|---|---|
| 1. Connection pool | `service/db_pool.py`, `service/db.py`, `service/main.py`, `service/reconcilers/sweep.py` | `get_db()` reuses connections while the service runs. | Every checkout is still exclusive. Transactions, `BEGIN IMMEDIATE` and busy timeouts behave as before. Without the lifespan (most tests) nothing is pooled. |
| 2. Pi flip loop | `service/pi_resident_flip.py`, `service/routers/agents/registration.py` | 5 s while a pi agent waits, 30 s when none does, woken at once by a pi resident registration. | A waiting agent is still looked at every 5 s. |
| 3. Hidden dashboard | `service/new_dashboard/refresh-visibility.mjs`, `app.js`, `extraction-proof.test.mjs` | A hidden tab skips the poll bundle and refreshes once when shown. | The realtime socket, notifications and console streaming are untouched. |

The hazards step 1 had to close, each with a test in `service/tests/test_db_pool.py`:

- **An open transaction** (a raised, cancelled or uncommitted request). The reset is queued on the
  connection's own worker thread, behind anything still running there. A cancelled request's query keeps
  running, so a check made from the event loop could miss a `BEGIN IMMEDIATE` about to execute.
- **Changed connection settings.** Anything that runs a PRAGMA, ATTACH or DETACH, sets an attribute other
  than `row_factory`, or installs a function or handler is closed on return, never pooled. The import path
  toggles `foreign_keys`.
- **Use after close.** The closed handle is detached before release, so it raises instead of running on
  another request's connection.
- **Another file or another event loop.** Tests repoint the database path. A connection is only reused for
  the path and loop it was pooled under.
- **Checkpoint starvation** (the 83 MB WAL of 2026-06-18). The sweep retires idle connections before its
  TRUNCATE checkpoint.
- **Windows file handles.** Shutdown awaits every close, so a test that deletes its database after stopping
  the service is not blocked by a worker thread.

Behaviour changes to know about:

- A pi agent put into resident mode by recovery, the mode switch or an import (not by registration) now
  flips within 30 s rather than 5 s. Those writers are deliberately not hooked; see below for why lists of
  writers are the thing to avoid.
- A dashboard tab opened in the background loads nothing until it is first shown.

Verified:

- Suites, run apart: python 5837, bridge 376, dashboard 1757.
- Mutations, each watched red: 11 on the pool, 5 on the flip loop, 4 on the gate.

Measured after the rebuild, and it did NOT move the idle number:

| | before | after |
|---|---|---|
| py-spy busy samples / 90 s | 96 | 107 |
| samples in aiosqlite's connect | part of every request | 0 |
| `docker stats` CPU%, 30 samples at 2 s: mean | 1.19 | 1.65 |
| `docker stats` CPU%, 30 samples at 2 s: median | ~0.5 | 0.76 |

The pool removed connection setup entirely, but the idle cost is the queries themselves: 67 of 107 samples
are aiosqlite workers executing statements. The before and after runs differ within noise, and the after run
followed a rebuild while agents re-registered. The likeliest remaining source is the in-service claim re-poll,
every 3 s per waiter, with `BEGIN IMMEDIATE`, a settings load and an agent read each time. That is step B below,
so it moves up in priority.

## v0.6.9: what the measurement found, and what shipped

Reading the service's own CPU counter (`/proc/1/stat`) rather than `docker stats` settled what the samplers
could not:

- **The claim re-poll is not the cost.** 3 s vs 25 s fallback, two 120 s windows each: 1.55% and 1.53% vs
  1.42% and 1.55% of a core.
- **About 70% of the service's CPU came in two bursts a minute.**
  - One is 0.45–0.54 CPU-s, one second after a hidden dashboard tab's poll bundle.
  - The other is 0.18–0.21 CPU-s from the reconcile sweep.
- **The dashboard burst is `GET /stats`,** 0.18 CPU-s per call. `/agents` is 0.05, `/contracts` 0.03, and the
  rest are near zero.
- **Indexes fixed most of it.** On a copy of the live database (38,515 messages, 23,208 runs), `/stats` went
  from 406 ms to 112 ms with four indexes, and the sweep's control settlement from 51 ms to 0 with one.
  Each query is pinned to its index by `test_the_idle_paths_do_not_scan_whole_tables.py`, and removing any of
  the five fails it.

An independent review of the release found one startup defect, fixed before tagging. The partial index on
`require_reply` was first put in the schema script, which runs before the column migrations, so a database
from before 2026-04-23 failed to start. It is now created in `_migrate_dispatch_runs_table`, with a test
that starts such a database.

Known and left open, both low:

- **A user action can finish on stale data.** If the tab is hidden between an action's POST and its awaited
  `refresh()`, the code after that await runs on the previous state. The catch-up refresh on show re-renders,
  but does not re-run that code.
- **`idx_messages_source` is now a redundant prefix of `idx_messages_source_ts`.** It costs a little on every
  message insert. Dropping an index on existing databases is its own change.

## Steps 4 and 5: are they the right solutions?

Not as first written. Tracing the code changed both.

### What the code actually does today

1. **Work is created in ten places, and one of them wakes the waiters.** `INSERT INTO dispatch_runs` or the
   control and spawn tables appears in `dispatch_runs.py`, `dispatch_start.py`, `dispatch_run_state.py`,
   `events.py`, `session_restart.py`, `session_mode_audit.py`, `superseded_bridge_stops.py`,
   `console_input_queue.py`, `routers/environments.py` and `routers/spawn_requests.py`. Only
   `routers/dispatch_messages/dispatch.py` calls `longpoll.notify`. The 3 s in-service re-poll is what makes
   the other nine arrive promptly. Raising it without fixing that would delay a restart, a requeue or a
   spawn by up to 25 s.
2. **The dashboard is already half event-driven, and the event half mostly triggers a full refetch.** The
   service broadcasts 46 event names. The dashboard patches `agent_status` in place and streams
   `terminal_output`. Almost every other event maps to `refreshSoon()`, which refetches all ten endpoints
   (`realtime-dispositions.mjs`, `refresh-cycle.mjs`).
3. **Changes caused by time are never pushed.** An agent becomes `offline` because its heartbeat got old, and
   nothing broadcasts that. The sweep refreshes expired states every 60 s without a push, so today only the
   15 s poll shows it. Removing the poll without adding this would leave dead agents looking alive.
4. **Pushes are hand-placed calls.** 46 names, broadcast from wherever someone remembered to. The record here
   is 35 of 49 events once silently dropped by the dashboard.

### Measured after the first draft

- **The pushes are on change and nearly free.** One idle minute on the dashboard socket carried 2 events and
  208 bytes, both `agent_status`.
- **The cost is the timed refetch, not the events.** On the dashboard, 3 event names are handled in place
  (`agent_status`, `terminal_output`, `terminal_started`). A handful are ignored with a reason. Every other
  event triggers the full ten-endpoint refetch.

### Rejected after review: a change log in the database

The first draft put a `change_log` table behind SQLite triggers. The operator questioned it, and it does not
survive the numbers:

- **Write amplification on the hottest paths.** 36 `UPDATE terminal_sessions` sites and 89 `last_seen` writes
  would each add a row.
- **Growth.** It needs pruning, and the WAL grows with it (the 83 MB WAL again).
- **More writer contention** on a database with one writer at a time.
- **Redis is not the answer either.** It solves durable events shared across processes, and this service is
  single-process by rule (the in-memory status cache already requires it). It would add a container and a
  dependency for a problem this service does not have.

### The shape that fits: an in-memory change bus, fed from one place

**A. A bounded in-memory bus in the service.**

- **A fixed-size ring of recent events,** each with a monotonic `seq` and the service's instance id. Nothing is
  written to disk, and memory is capped.
- **A restart loses the ring, by design.** A client that sees a new instance id refetches everything once.
- **Fed from the connection proxy step 1 already put in front of every write.**
  - The proxy notes which table an `INSERT`, `UPDATE`, `DELETE` or `REPLACE` names.
  - It publishes only what was COMMITTED, when the checkout is returned; a rollback publishes nothing.
  - No write site has to remember to announce anything.
- **Columns delivered by their own channel do not publish:** heartbeat liveness (`last_seen` and its siblings)
  and streamed terminal output. They are declared once, beside the schema.
- **A completeness test runs the route suites through the proxy** and fails when a committed change produced
  no event, which is how an unparsed statement shape would show up.

**B. Claims wake from the bus.**

- A committed change to a work table (runs, dispatch controls, terminal controls, environment controls, spawn
  requests) wakes that claim scope.
- That replaces the one hand-placed `notify` and covers the nine writers it misses.
- The in-service fallback can then rise from 3 s toward the wait itself (about 25 s): per waiting agent, one
  claim attempt every ~23 s instead of about eight.
- **Heartbeats stay** until measured again; they are cheap now that connections are pooled.

**C. Time-based status is pushed.** The status cache knows each agent's lease deadline. One timer set to the
earliest deadline recomputes the agents it passed and publishes the ones whose derived status moved.

**D. The dashboard updates component by component, and stops polling.**

- **A store keyed by entity.** Each component renders from its own slice.
- **An event refetches only the slice or entity it names,** never the whole bundle. For example, a message
  appends to messages and a run update fetches that run.
- **Recovery is a full fetch once,** on connect, reconnect, a detected `seq` gap, a new instance id, or a tab
  shown again (step 3's gate). That replaces the 15 s timer as the correctness guarantee.
- **The dispositions table becomes the proof.** An event with no slice mapping fails a test instead of falling
  back to a full refetch.

### A websocket per bridge: good or bad depending on what it carries

- **Good: a hint channel.** The socket carries "work available" for a scope and ping/pong liveness. The bridge
  still claims over today's HTTP claim, so the atomic claim, supersession and their tests are untouched. It
  removes held-open long-polls, in-service re-polls and heartbeat POSTs.
- **Bad: the delivery protocol on the socket.** Claims, acknowledgements and state over the socket mean
  rebuilding delivery guarantees, duplicate handling and reconnect semantics for every bridge kind (claude
  channel, hermes, codex, pi, aify-env's plugin).
- **Either way it needs:** ping timeouts for half-open connections (a sleeping laptop), jittered reconnect
  after a service restart, and a long-poll fallback where a socket cannot be kept.
- **Decide after B,** when the remaining idle cost is measured.

### Order, and what could regress

1. **Bus (A) with no reader.** Prove completeness with the route suites.
2. **Claims wake from it (B).** A missed wake costs latency, never a claim, because the fallback remains.
   Test: every work-creating path claims within 1 s with the fallback at 25 s.
3. **Status expiry push (C).** Coalesce per tick so a fleet-wide expiry (a host waking) is one burst, not N.
4. **Dashboard slices (D), agents first.** Test for each slice: a server write reaches the rendered component
   with the timer disabled. Remove the timer only when every slice passes.
5. **The bridge socket decision,** measured.

Each step is measured before and after with the same py-spy, tcpdump and socket counts used above.
