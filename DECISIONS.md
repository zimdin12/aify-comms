# aify-comms: Design Decisions & Current Limits

What is decided now, and why. If you are wondering *why* the service behaves a certain way, this file
beats guessing from the code; where the two disagree, the code is right and this file is the thing to
fix. Superseded entries and dated fix batches are in
[docs/history/DECISIONS-archive.md](docs/history/DECISIONS-archive.md), kept as evidence, not
instruction.

## Three repos, and which concern each one owns (2026-08-20)

**Decision.** aify-comms owns messaging, dispatch and sessions.
[aify-wrapper](https://github.com/zimdin12/aify-wrapper) owns the launchers, and this repo consumes it
as an npm dependency pinned to a commit. [aify-env](https://github.com/zimdin12/aify-env) owns
processes and terminals on a host. The full argument is
[docs/AIFY_ENV_BOUNDARY.md](docs/AIFY_ENV_BOUNDARY.md); this entry records what was settled and what it
costs, because the decisions record had no trace of the largest structural change in v0.6.

**Why the environment tier is separate.** While spawning lived inside aify-comms, a second service on
the same host could not start an agent without building its own spawner — and this project's recorded
incidents are almost all one spawner colliding with itself. Two independent spawners on one host is
that class of bug with a repo boundary through the middle of it.

**What it costs, stated rather than discovered later.** A contract with two ends can now drift between
two release lines, and it did: the wrapper version marker meant aify-comms' release in one installer and
aify-wrapper's in the other, invisible while both files read 0.5.7. Each cross-repo contract therefore
carries a test on the PRODUCING side too — the registry fingerprint, the endpoint readers, the launcher
exports, and the service self-report `/health` answers to aify-env.

**Settled.** The Phase 8 flip was taken on 2026-08-25, on an idle fleet, and aify-env is now the only
spawner: it claims spawn requests, runs the launchers and owns the PTYs;
aify-comms spawns no managed worker. The launch travels as structured `argv` beside the command string
(`terminal_sessions.argv`), because aify-env runs a launcher file by path and never a shell string.

## One live instance per agent per host, replaced only on an explicit start (2026-09-14, built in v0.6.8)

**The operator's rule:** "we should never allow 2 of same agent to run basically (resident or managed,
doesnt matter)." Asked how, the operator chose two parts. Leftovers of a dead instance are always
stopped before a start. A live instance is replaced only on an EXPLICIT start: a dashboard
Start/Restart/spawn, an agent's `comms_restart`, a `comms_compact` handoff of an agent to itself, or a
person running the launcher in a terminal and NAMING the agent with `--aify-agent`. An identity the
launcher took from its environment or recovered from a conversation only starts: on 2026-09-15 a bare
`claude-aify` in a pane that had inherited comms-tech-lead's session environment started as that agent
and replaced it (aify-wrapper `lib/inherited-session.mjs`, README "One live instance per agent"). Even
an explicit start is refused when the live instance HOSTS another agent (a Herdr server or aify-env
started from its shell, running an agent): replacing it would end that agent too, which no start of
this one asked for. An
AUTOMATIC start (a message cold-starting the agent, the queued-run backstop, an agent's `comms_spawn`)
is refused with launcher exit 75. Replacing on every start was built once and reverted: a message woke
an idle lane and the host killed four working sessions in ten minutes on 2026-09-03.

**Every launcher enforces it**, through a lease at `~/.aify/agents/<id>.json` claimed before the runtime
starts. The launchers are the one place every runtime, resident or managed, passes through.

**Why start intent is its own field.** `spawn_requests.created_by` cannot carry it: a cold start
records the SENDER's agent id, so a message waking a lane reads exactly like that agent spawning it on
purpose. So the intent is decided where the start is asked for, stored as `spawn_requests.start_intent`,
stamped on the terminal row, and handed to the launch as `AIFY_START_INTENT`.

**A blank requester counts as START.** Only a requester of exactly `dashboard` replaces
(`service/api_core/start_intent.py`). An agent's `comms_spawn` accepts an empty `from`, and any HTTP
caller can omit it. Reading those as the dashboard would let them end a live instance. A wrong guess
toward START costs a refused start, which can be retried. A wrong guess toward REPLACE costs somebody's
working session.

**Failing open and failing closed.** If the lease helper itself fails (a bad argument, an unwritable
directory), it warns and exits 0: a broken helper costs the guarantee, never the launch. Kill decisions
fail closed. If a recorded process is still running and the process table cannot be read, the start is
refused (75, retryable), because proceeding could make a second instance. A lock another start still
holds after a minute is also a refusal.

**A terminal launch replaces, and a Herdr restore does not.** A person typing the launcher meant to
start that agent now, so a non-managed launch defaults to replace. A Herdr pane restore replays that
command without anyone asking for it just then, so it passes start intent and a live instance refuses
it. A launcher started inside the agent's own live instance is refused whatever its intent.

Design, departures from it, and the residuals deliberately left:
[docs/superpowers/plans/2026-09-14-one-live-instance-per-agent.md](docs/superpowers/plans/2026-09-14-one-live-instance-per-agent.md)
("As built"). The residuals are also listed in KNOWN_ISSUES.md.

## Pi is deprecated: support kept, tests disabled by default (2026-09-18)

The operator no longer uses Oh My Pi but may again, so its code stays. Its tests, all but one, are
kept as the proof to revive with rather than deleted: each carries `deprecated-runtime: pi` near its
top, and both runners (`mcp/stdio/tests/run-all.mjs` through `deprecated-runtimes.mjs`, and
`service/tests/conftest.py`) leave such files out unless `AIFY_TEST_DEPRECATED` names the runtime.
The bridge runner lists them by name and pytest reports them skipped, so a disabled file never counts
as a pass. The one pi test that was slow enough to matter (`pi-runtime.test.js`, about 350 of the
bridge suite's 773 summed seconds) is the exception: it was deleted outright in `3b090df0`, so
reviving pi means restoring it from that commit's parent as well as setting the flag. Tests that guard OTHER runtimes against pi
code paths, such as `virtual-terminal-input-is-pi-only.test.js`, stay enabled.

Pi's design record (a persistent `omp --mode rpc` child per agent streamed as a virtual terminal, a watchdog
mutex because OMP has no multiplexing, heal-once for a dead session) is in
[docs/history/DECISIONS-archive.md](docs/history/DECISIONS-archive.md).

## Settings are declared once, in the service, and saving one changes only that one (2026-09-19)

Every setting is declared in `service/api_core/settings_spec.py`: kind, bounds, label, help, group.
`GET /settings/schema` serves the declarations and the dashboard draws its panel from them. Until
then the dashboard hand-listed every field, and it disagreed with the service about bounds.

A PUT is checked against the declarations. A value the setting cannot hold is refused with a 400 that
names it, where before it was dropped behind a 200 that the dashboard reported as "Saved". The
dashboard now sends only the fields that changed.

Saving a managed model or effort default no longer rewrites existing agents. It used to run on every
save carrying any `managed_` key, and the dashboard sent every field on every save, so changing the
theme reset the model of every managed agent, including those spawned with a model of their own.
Existing agents change only through `POST /settings/apply-managed-defaults`, which is the button
under Workers.

Retired keys (listed in `RETIRED`) are ignored on PUT, and old rows are left out on read. They are
the auto-confirm switches, `manual_session_mode`, `idle_minutes`, `offline_minutes`,
`stale_agent_hours`, `status_engine` and `worker_idle_close_enabled`. The last one is folded into
`worker_idle_close_minutes`, where 0 means off, by a one-time migration in `service/db.py`. Only a
toggle row reading `false` folds the minutes to 0. Minutes with no toggle row are left alone, because
that is what a v0.6.16-18 host looks like after its own migration deleted the row; the first 0.6.19
version zeroed those, and hosts that already booted it keep the zero.

Message rotation runs hourly from the sweep (`service/reconcilers/message_rotation.py`), and
`POST /rotate` runs it on request. Two settings control it: `message_retention_days` and
`message_cap_per_agent`, both 0 (off) by default. Before this, nothing ever called rotation, so its
old keys (`rotation_enabled`, `retention_days`, `max_messages_per_agent`) never took effect. They are
retired rather than reused: any host whose settings page was saved holds 90 and 1000 in the table,
and scheduling rotation under those names would have started deleting messages there without anyone
choosing it.

## No source decides where something is from a path typed into it

The operator, 2026-09-16: "we should never have C:/ paths. we never know where user installs anything.
everything should be dynamic in that sense. agent who installs should fill the dynamic gaps based on the
system, mb C:/ could be as example."

A location comes from the system that has it: an environment variable the OS sets (`SystemRoot`,
`USERPROFILE`, `SystemDrive`), the root this process is running on, the file's own location, or the
configuration an operator gave. What was found when the rule was written: an environment advertising no
root was handed `cd /d C:\Docker` -- this project's own directory on the machine the dashboard was written
on -- to every host that read it; codex' spawn cwd fell back to `C:\` and its Windows system root to
`C:\Windows`; a measurement script named one checkout; and aify-wrapper built codex' hook trust key from a
literal `C:\`, which is the wrong key on a host whose Windows is elsewhere.

An example a PERSON reads is allowed and says so on its line (`example path`). Two remain, both read
rather than used: the refusal naming the cwd form codex takes, and the hint in an empty roots box.
`service/tests/test_no_source_bakes_in_a_host_path.py` enforces it over Python and JavaScript through the
repo's own comment classifier, so a comment explaining a real Windows path stays legal;
`tests/no-source-bakes-in-a-host-path.test.js` does the same for aify-wrapper.

## Container name, repo name

The repo is `zimdin12/aify-comms` and the Docker container is `aify-comms-service`. Earlier versions used `aify-claude`; the rename is cosmetic and GitHub auto-redirects old URLs. If you see `aify-claude` in a log or filesystem path on an older install, it's the same project.

## Live-status cache is in-memory, not SQLite — and the service MUST stay single-worker (2026-06-18)

The recurring `database is locked` 503s are RESOLVED (commit `97a497a`, verified live: 0 locks, down from ~18/min steady-state and 137/min in the post-restart storm). The non-obvious choices:

**The root cause was that the live-status cache was a SQLite table written on the hot READ path.** `agent_live_state` held *derived* agent status — a pure cache, recomputed from scratch on restart — yet it was refresh-WRITTEN on every dashboard poll. With a connection-per-request, single-writer SQLite, those constant status-refresh writes were the write storm that produced the lock contention; worse, the constant status READS kept the WAL from ever checkpointing, so it bloated to 41–83MB → slow commits → more lock windows. It was a cache masquerading as durable state, on the busiest path in the service.

**The fix: the live-status cache now lives in a process-global in-memory dict (`_LIVE_STATE_CACHE` in `service/reconcilers/status_cache.py`).** Reads serve from memory — ZERO DB writes on the hot read path, so a read can NEVER take SQLite's write lock — and with the read-path writes gone the WAL checkpoints normally and stays small (~5MB). The `agent_live_state` table, vestigial from then on, was dropped in 0.7.0.

**SINGLE-WORKER IS NOW A HARD REQUIREMENT.** The cache is PROCESS-GLOBAL and is only correct because the service runs as exactly ONE uvicorn process / one event loop. (The `aify-comms-dashboard-next` container only PROXIES to it — it never opens the DB.) If the service is EVER scaled to multiple workers, this in-memory cache MUST move to a shared store (Redis) or use sticky routing; otherwise different workers would serve divergent status. Do not add `--workers > 1` / multiple uvicorn processes without first relocating the cache.

**Trade-off: the cache is lost on restart — accepted, because the startup reconcile warms it before serving.** A cold process recomputes the live state on boot, so a restart re-derives rather than reads stale rows.

**Belt-and-suspenders (commit `581341d`): the read endpoints degrade gracefully under a transient lock.** `GET /agents`, `GET /agents/{id}`, and `GET /sessions` catch a transient `database is locked` and serve the cached data instead of returning a 503.

## Read GET endpoints must not run repair-WRITES on the poll path (2026-06-29)

A second, distinct source of `database is locked` + idle-CPU churn (separate from the cache write-storm above): several **`GET` list endpoints ran maintenance repairs that WROTE on every dashboard poll**. With a connected fleet (~12 bridges → ~40+ req/s, terminal output ~10/s), each such read opened a write txn (a table scan + commit) that serialized behind the terminal-output writes under WAL — the top `SLOW-REQ` offenders were all these write-on-read GETs. Diagnostic middleware in `service/main.py` (logs `SLOW-REQ`>1s / `DB-LOCK` / 5xx) surfaced this; removing the read-path writes dropped slow-requests ~197→~2 over 3 min.

**The rule: a GET is a pure read unless its repair affects the correctness of THAT response.** Apply it as follows:

- **Made pure reads (repair moved to / already in the 60s reconcile loop):** `GET /spawn-requests` (orphan/failed spawn-request cleanup → reconcile), `GET /dispatch/runs` and `GET /stats` (`_repair_unusable_active_runs` is redundant — it already runs in reconcile AND on every `GET /agents` poll). These tolerate ≤60s repair lag (spawn-failure detection; a diagnostic runs/stats list).
- **KEEP their read-path repairs (do NOT move to reconcile):** `GET /agents` (`_repair_unusable_active_runs` drives the roster run/agent status the operator watches live) and `GET /sessions` (`_repair_superseded_recovering_sessions` / `_repair_current_session_freshness` / `_repair_terminal_session_consistency` fix the console/terminal binding shown in that very response — a 60s lag would surface a just-stopped terminal as still-attached). These repairs no-op when nothing needs fixing, so steady-state polls don't write.

So before adding a repair call to any GET handler, ask whether the caller needs the corrected state *in this response* or merely *eventually*. Eventually → reconcile loop. The residual CPU after this fix is inherent fleet load (request volume scales with live-bridge count + their poll intervals: `AIFY_DISPATCH_POLL_MS` 3s, and at the time `AIFY_TERMINAL_CONTROL_POLL_MS` 800ms, deleted in 0.7.0), NOT a bug — there is no runaway loop and no pathologically slow endpoint (`/usage` + `/usage/consumption` serve from the in-memory `_USAGE_CACHE`; the new dashboard's `loadAnalytics` is throttled to 12s).

## Claim endpoints are long-poll, not short-poll (2026-06-30)

The bridges discover work by polling "is there anything for me yet?" endpoints —
`/dispatch/claim` (3s), `/terminals/controls/claim` (800ms), `/spawn-requests/claim`,
`/environments/controls/claim`. With ~12 live bridges that short-poll is the bulk of the
service's ~40 req/s (each poll opens a SQLite connection; `/dispatch/claim` takes a
`BEGIN IMMEDIATE` write lock every attempt). Heartbeats stay periodic (absence-of-signal
IS the signal); terminal-output POSTs are already event-driven; the claims were the one
class that *should* be on-demand. See the read-path-write entry above for
the related GET fix.

**Design (`service/longpoll.py`).** A lock-free per-scope notification bus + a `longpoll()`
helper. Each claim handler's body is reused verbatim as a `_*_once` per-attempt function;
the route wrapper calls it, and if the result is EMPTY and the client sent `waitMs>0`, it
awaits a `notify()` (fired on the enqueue path) and retries until work, disconnect, or the
budget elapses. **Claim semantics are unchanged** — calling `_*_once` repeatedly server-side
is identical to the bridge calling it repeatedly over HTTP; only an empty response is held.
A per-iteration fallback (= the legacy poll interval) bounds latency even if a `notify()` is
ever missed, so a missed enqueue hook can only degrade to today's behaviour, never lose or
further delay work. That property is what makes it safe to ship incrementally.

**Bridge side (`AIFY_CLAIM_WAIT_MS`, default 20000; 0 disables).** Bridges send `waitMs` +
a longer per-call HTTP timeout (must exceed the hold or the bridge aborts mid-hold and trips
its failure counter; server caps the hold at `longpoll.MAX_WAIT_S`=25s — kept BELOW the bridge's
~28s claim HTTP timeout so the server always returns first, see the contention section below). Two correctness
guards: (1) `runDispatchLoop` iterates its agents SEQUENTIALLY, so the dispatch claim
long-polls ONLY on a single-agent bridge (`soloAgentBridge`) — a multi-agent bridge
keeps short-poll so one idle agent can't delay the others; (2) only the first claim of each
drain-batch long-polls. `/dispatch/controls/claim` stays short-poll — it runs only DURING an
active run (not idle volume) and governs steer/interrupt responsiveness. Activating the
bridge half needs an `install.sh` re-run + wrapper restart (the native-copy rule). Validate
with a two-session live round-trip (see CLAUDE.md "Testing a change").

## Claim probes fast-fail; writes retry the lock before 503 (2026-07-01)

Two contention behaviours, on two different paths, both keep transient SQLite write-lock
contention from surfacing as an error.

**Claim probes fail FAST (`6eb3263`).** Every connection has `busy_timeout=5000`, so a claim's
`BEGIN IMMEDIATE` could block up to 5s waiting for the write lock. `longpoll()` only checks its
wait deadline at the TOP of the loop, so a final per-iteration attempt started near the ~20s
deadline could camp ~5s on the lock and push the whole request past the bridge's ~28s HTTP timeout
("claim timed out after 28000ms", recovering next poll — observed on a busy host). Fix: claim
probes are idempotent + retry-safe, so they open with a SHORT busy_timeout
(`db.get_db(busy_timeout_ms=...)`, `SQLITE_CLAIM_BUSY_TIMEOUT_MS=1200`) — a contended attempt
raises "locked" in ~1.2s → the existing `lock_result` empty shape (200) → retry next poll. Caps
the long-poll's worst-case overshoot to ~1.2s past the deadline. `MAX_WAIT_S` was also lowered
30→25 (below the 28s client timeout). Applies to all 5 claim `_*_once` fns.

**Other writes RETRY before 503 (`d069f51`).** A non-claim write (notably `comms_send` during a
heavy multi-agent burst) that can't grab the lock within `busy_timeout` raised "database is
locked", which `JsonApiRoute` turned into a 503 (poisoning callers/tests). The route handler now
retries on a lock/busy error (3 retries, 0.1/0.25/0.5s backoff) before surfacing 503. Safe because
a lock error is raised at `BEGIN IMMEDIATE` — before any commit — so re-running the atomic
single-transaction handlers doesn't double-write, and FastAPI caches the request body so the re-run
re-reads it. Reads never take the write lock, so they never reach this path. A burst is absorbed by
a retry; only a sustained overload past all retries still 503s (correct backpressure). NOT a
write-queue — if sustained contention ever becomes real, serialize writes through one task
(deliberate architecture change), don't bolt retries on top. **Both are server-side: a host running
its own service must `git pull && docker compose up -d --build`.**

## Coalescing terminal writes + `busy_timeout` keep the single SQLite writer alive

**Decision.** Terminal output is batched through an idle/max-latency coalescing queue before hitting SQLite, every connection sets `PRAGMA busy_timeout` (WAL is persistent at the file level), and OperationalError surfaces as a JSON 503, never an HTML 500.

**Why.** A runaway flickering console produced ~80–94 output POSTs/sec, saturating SQLite's single write lock and starving heartbeat/dispatch/spawn-claim writers — that DOS'd the control plane and produced "database is locked" 500s that the dashboard then failed to parse. Fixing the flicker removed the load source; coalescing + `busy_timeout` + a JSON error contract make the remaining contention graceful.

## Terminal output sequence is server-owned, monotonic, and streamed as deltas

**Decision.** The service assigns a strictly monotonic `output_seq` per terminal via a coalescing write queue; the bridge never assigns seq. The dashboard streams each `terminal_output` websocket frame's delta straight into the live xterm keyed by seq, and only falls back to a full render on initial mount.

**Why.** The original console flicker was a full `renderChat()` (DOM rebuild + xterm remount + full-buffer repaint) on every output chunk at ~80/sec. Monotonicity must be guaranteed in the queue, not derived from a request-time DB read — a stale `output_seq` read during an uncommitted flush could regress seq and make the dashboard silently drop fresh output. A per-terminal seq floor enforces this regardless of flush/commit timing.

**Consequence.** Output POST responses intentionally omit the (up to 64KB) buffer — the bridge only needs `outputSeq`/`status`; clients read full scrollback via `GET /terminals/{id}`.

**Broadcast ordering.** The live `terminal_output` websocket frame is emitted by the coalescing queue's flush (post-commit, one ordered batch per flush), NOT per-POST. Per-POST broadcasts ran in concurrent request coroutines, so their order did not match the seq assigned at enqueue; the dashboard's `seq <= lastSeq` dedupe then discarded an out-of-order frame, leaving a hole in the byte stream that desynced the terminal's ANSI state ("scrambled text when working"). Flushes are serialized per terminal, so flush-time broadcast is ordered and gap-free, and the dedupe is now correct (it only guards mount-overlap/replays). This also cuts websocket message volume.

## Console replay uses a server-rendered screen snapshot, not the raw byte log (2026-06-30)

Managed-agent consoles scrambled (both dashboards, persisted across refresh). The service
stores each PTY's **raw byte log** (trimmed to ~64KB) and the client replayed it into a fresh
xterm. A full-screen TUI's byte stream (claude/codex Ink UIs) is meant to drive a LIVE screen
at a FIXED size via cursor-positioning; replaying the accumulated log — which starts mid-screen
after the trim, sometimes mid-escape — overlaps every historical draw into garbage, worse at a
different width. Refresh re-replayed the same log, so it never recovered. (Hermes never had this:
its TUI renders through the gateway's own correctly-sized renderer/iframe, not a replayed log.)

**Fix (`service/terminal_snapshot.py`).** On attach/refresh the client passes its grid size;
`GET /terminals/{id}?cols=&rows=` replays the raw log through a headless VT emulator (`pyte`)
sized to that grid and returns a clean, self-contained ANSI paint of the CURRENT screen as
`snapshot`. The dashboard writes that (after `term.reset()`) instead of the raw log; **live
deltas still stream raw** to the client xterm. This also fixes the per-viewer size mismatch
(each viewer renders at its own width) and the mid-escape trim corruption.

**Refinement — never render NARROWER than the source (2026-07-01).** A *resident* wrapper mirrors
the operator's real terminal, which is often much wider than the dashboard pane and whose native
width we never store (`terminal_sessions.cols` is 0 for residents). Rendering the wide log at the
narrow pane width wrapped/mangled every full-screen-TUI line (the "gappy / bugged console"). Now
`terminal_snapshot.infer_source_width()` replays the log at a generous probe width and takes the
furthest column any cell reaches (a TUI draws a full-width frame) = the source width; the endpoint
renders at `max(viewer, inferred)` and returns `renderedCols`/`renderedRows`. The dashboard widens
its xterm to `renderedCols` and scrolls the pane horizontally (`.console-wide-mirror`) instead of
re-wrapping. **Managed** terminals are drawn at the size the dashboard set, so inferred≈viewer and
behaviour is unchanged.

**Why it's safe + performance-safe.** It runs ONLY on attach/refresh (a rare, bounded, one-shot
parse over ≤64KB), never on the per-frame streaming path — so no per-frame cost. It is offloaded
to a thread executor so the parse never blocks the single event loop. `pyte` is an OPTIONAL
dependency: `render_snapshot` returns the raw log unchanged if the import fails or the parse
throws, so the console can never break on it. Continuous server-side emulation (feeding every
frame) was rejected — `pyte` is synchronous pure-Python and full-screen TUIs are high-byte-rate,
so it would burn CPU and block the loop; lazy on-attach is both safe and sufficient.

**A console screen rebuilt from the stored tail says so (2026-09-16).** After a service restart the first chunk for an existing terminal seeds its live screen from `terminal_sessions.output`, a 64 KB tail that can start mid-escape. TUIs that redraw with relative cursor moves and never clear stay overlapping for as long as they are idle: after the 2026-09-16 rebuild three running consoles read like `ia-hHermes (all four lanes):agraftrisnenabledains`, with 0, 0 and 1 full clears in their tails. The screen is now marked `reconstructed` until the program issues `CSI 2J` or `ESC c` on the main screen. A clear inside the seed counts, because everything after it is in the seed. The replay path uses the same rule on the stored log. `GET /agents/{id}/console` returns the flag; `comms_console_tail` prints a note; `context-window` does not parse a figure from such a screen. The rule is CONSERVATIVE: a short log that really is the whole stream from byte 0 but holds no clear (claude never clears) is reported as reconstructed too, because nothing in the stored bytes says where the stream began. The screen itself is not changed and nothing is dropped; the dashboard does not show the flag yet.

## The service answers one console dialog, and never a resume menu

`service/api_core/console_prompts.py` reads a managed console's rendered screen, reached from
`_answer_console_prompt` in `service/api_core/terminal_output.py` on every output append, and decides
whether the worker is parked at a dialog the service should answer. The service decides and the host
types: the answer goes out as an `input` terminal control with `requested_by="console-prompt"`, and
the rule that fired is logged.

**It answers exactly one dialog**: the development-channels acknowledgement that
`--dangerously-load-development-channels` raises on a claude worker's first launch (rule
`dev-channels-accept`). Unanswered, that dialog leaves a worker that registers `online` and claims
nothing. The rule matches the dialog's own question line, and only while that line holds the cursor;
matching the flag name instead is how the old bridge came to press Enter into a resume menu, because
the flag appears in every worker's boot output.

**A resume menu is refused outright.** While `Resume from summary` or `Resume full session` is on
screen, no rule answers anything: the menu's highlighted default summarises (compacts) the whole
session, and a wrong keystroke there cannot be undone. So compaction and resume dialogs are not
auto-answered, and a managed claude that reaches one waits there (KNOWN_ISSUES.md). Each rule answers
once per terminal (`should_answer`), because a loop pressing keys at a screen it cannot change looks
exactly like one that is working.

There is no setting and no kill-switch. The `console_auto_confirm_claude_*` keys are in `RETIRED`,
and the bridge-era switches `AIFY_NO_AUTO_ANSWER` and `AIFY_AUTO_CONFIRM_COMPACTION` are read by
nothing.

## Dashboard console is a PTY the host tier owns; the service only relays

**Decision.** The dashboard "Console" is a real PTY on the host, owned by aify-env, not by the container and not by aify-comms, which no longer depends on `node-pty`. The service stores terminal rows, renders the screen, and relays input, resize and stop as terminal controls the host claims. `agent_sessions.owner_mode` flips to `console` while a console is attached and reverts to `managed` when the terminal reaches a terminal state.

**Why.** Operators need direct interactive CLI access to managed agents. A PTY must run where the runtime runs — the host — so the service is deliberately a relay, never a process owner. The console problems were never the PTY layer; they were the relay and render plumbing.

**Consequence.** Console behaviour on the host changes with aify-env, not with a container rebuild; the service side (screen rendering, controls, replay) changes with the container.

## Console-start reuses existing live wrapper terminal

**Decision.** `POST /api/v1/sessions/{id}/console/start` checks whether the agent_session already has a `terminal_id` pointing to a `terminal_sessions` row in `{starting, attached, running, active, idle, recovering}` before doing anything else. If so, it returns the existing terminal envelope with `reused:true` and appends a `console_attach_reused_existing` audit event — no new terminal_sessions row, no sibling wrapper PTY.

**Why.** Multiple operator clicks on Start Console (or auto-attach flows that hit the endpoint) used to spawn sibling PTYs even when a wrapper was already running for the agent. Sibling PTYs confused the dashboard ("which one is current?") and wasted host processes. The dispatch path already had the same reuse semantics via `_active_terminal_for_agent`; this brings the manual-start path to parity.

## Identifier name constraints

**Decision.** Agent IDs, channel names, and shared-artifact names must match `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$` — start with an alphanumeric, then up to 127 more alphanumerics, dots, underscores, or hyphens (max 128 total).

**Why.** These end up in URLs (`/agents/{id}/...`), filesystem paths (shared artifacts), and shell arguments. The strict regex prevents path traversal, URL escaping issues, and shell injection without having to sanitize at every call site.

## 2026-09-22 — the route gates walk fastapi's route contexts, and fastapi is held to 0.138-0.139

From fastapi 0.137.0 `include_router` leaves a lazy `_IncludedRouter` in `app.routes` instead of
flattening the child routes into it. Serving is unaffected — no product code walks `app.routes` —
but eight test files did, and every gate among them reads as a pass when it finds nothing, including
"the endpoints the fleet cannot lose". Measured on the same application: 0.136.1 builds 129 route
entries; 0.137.0 and 0.139.2 build 11, three of them `_IncludedRouter`.

**The first answer was an upper bound, and it was wrong twice.** `<0.140` (2026-09-22) resolved to
0.139.2, past the collapse, so the canary was red on every fresh install. `<0.137` would have fixed
the tests by DOWNGRADING the running service, which is on 0.139.2, at its next rebuild.

**So the gates moved instead** (2026-09-23). All eight walk through `service/tests/served_routes.py`,
which asks `fastapi.routing.iter_route_contexts(app.routes)`, added in 0.138.0. At 0.139.2 it yields
the same 129 (path, methods, name) rows as 0.136.1's flat walk, compared byte for byte, and the gates,
including the route-metadata snapshot, are green on fresh resolves of 0.138.0 and 0.139.2. A context
wraps the route, so the one thing that changes for a gate is the declared class: ask
`declared_class(route)`, never `type(route)`. Hand-recursing through `_IncludedRouter` was rejected:
a walker that gets the prefixes subtly wrong produces a WRONG inventory, which is worse than an empty
one.

The requirement is `fastapi>=0.138.0,<0.140`: the floor because nothing older has the helper, the
ceiling because 0.139.2 is what the service runs. `test_the_route_inventory_is_not_empty.py` is the
canary. With the helper returning raw `app.routes` on 0.139.2, three of its cases go red, and so do
all eight other files.

## Status is proof-based: no time-decay, no engine flag (2026-06-18)

The status system was rewritten to be **PROVEN, not time-assumed** — the conclusion of many incremental patches that had accreted into a confusing 8-state model with multiple minute thresholds. The non-obvious choices, superseding the 2026-06-04/06-17 entry now in the archive:

**The vocabulary is whatever `VALID_STATUSES` in `service/status_engine.py` declares, and one table says what each state means and what to do about it: "Status Meanings" in [`.claude/skills/aify-comms/references/operations.md`](.claude/skills/aify-comms/references/operations.md).** The 2026-06-18 rewrite set six (`working` / `online` / `available` / `blocked` / `offline` / `stopped`); `shell`, `misconfigured` and `starting` were added since. The two time-decay states were DROPPED. `idle` (an `online` worker gone quiet past a minute threshold) is gone — a long-quiet live agent simply stays `online`, which operators already rely on as "ready for queued work". `stale` (resident-only, bridge lease past ~150s) is gone — a resident whose bridge heartbeat lapsed or that has no usable wake handle (`*-missing-handle`) now reads `offline`. *Why:* both labels ASSUMED a state from elapsed wall-clock time rather than proving it, which is exactly the class of inaccuracy operators kept reporting ("shows idle but it's working", "shows working but it's done").

**The `*-aify` wrapper is the source of truth; aify-comms reflects it and only ADDS what the wrapper can't know.** The wrapper signals turn-start → `working`, turn-end → `online`, awaiting-input → `blocked`, and beats a liveness heartbeat every ~30s. aify-comms reflects those verbatim and adds only: `offline` (heartbeat gone), `available` (managed, env reachable, no live worker — boots one on send), `stopped` (operator hard-disable). `offline` ≠ `stopped` is kept deliberately: offline is "we lost the signal", stopped is "operator disabled it".

**No minute thresholds. Two liveness windows (managed vs resident), honestly.** `idle_minutes` (5) and `offline_minutes` (30) were removed from settings. Liveness uses TWO windows, intentionally split by path: **`agent_liveness_seconds` (default 90 = 3× the uniform 30s heartbeat)** governs the MANAGED offline gate (a missing heartbeat reads `offline`) and the live-state cache refresh horizon; **`resident_lease_seconds` (default 150)** governs RESIDENT bridge freshness (`_resident_bridge_is_fresh`) — a resident counts as live until its bridge lease lapses at 150s. The longer resident window is deliberate: idle residents don't beat as often as a managed worker's continuous loop, so a tighter window would flap them `offline`. `offline` is also instant on a clean wrapper disconnect (`resident-lost`), so both windows are only the fallback for an unclean drop. The `TURN_BUSY_BACKSTOP_SECONDS` in-turn ceiling is KEPT — it is the dropped-turn-end safety net (a gateway-less resident hermes has no end signal), not a time-decay state.

**`derive()` is the sole authority; the `status_engine` flag was removed.** The dual-engine machinery (`status_engine: old|new`) and the legacy per-request `agent_turn_state`/`turn_busy`-window cascade are gone. `service/status_engine.py` `derive()` over `agent_status_state` is the one served derivation. *Why no fallback flag now (the 2026-06-04 entry kept one):* the event-driven engine has been the live default and validated since the 2026-06-17 flip; carrying a dead second engine was cruft that made the system harder to reason about, which was itself part of the problem being fixed.

## 2026-07-31 — The agent status vocabulary has ONE owner per language, bound by a test

`status_engine.VALID_STATUSES` is the authority. The JS side has exactly one owner —
`new_dashboard/status.js`'s `AGENT_STATUSES` — and `service/tests/test_status_vocabulary_binding.py`
asserts the two are identical, in order.

Why this needed a decision rather than a tidy-up: the vocabulary was hand-retyped in three further
places (`SESSION_FILTER_KINDS`, `SESSION_LIVE_KINDS`, and an independent `new Set([...])` in
`chat.js`), nothing bound any copy to the source, and the vocabulary is not served by the API — so
there was no runtime path by which the client could learn it either. The drift could not announce
itself: `resolveStatus` ends `|| STATUS_KINDS.unknown`, so a seventh server-side state would render
as a muted grey "unknown" chip and filter into nothing rather than throwing.

Two consequences that must survive future edits:
- `LIVE_AGENT_STATUSES` is **derived** (`AGENT_STATUSES` minus `NON_LIVE_AGENT_STATUSES`), never
  retyped. Two independently-typed sets is the defect that was removed; a literal would restore it
  while looking tidy, so the test asserts the mechanism, not just today's values.
- Adding a status server-side REQUIRES a `STATUS_KINDS` entry. The test fails without one; that is
  deliberate, and it is the check that catches "added a state, forgot the dashboard".

## 2026-07-31 — Counts that claim reachability must DERIVE it, never read stored status

The dashboard's "Online envs" card counted `environments WHERE status = 'online'` while its tooltip
claimed "bridges reachable right now (managed agents can be spawned on these)". Nothing in this
codebase ages an environment row — every `UPDATE environments` writer is a registration, an explicit
disable, or a `last_seen` bump — so a bridge that died uncleanly kept `status='online'` forever and
was counted as reachable indefinitely.

This is the same false green `aify-doctor`'s `env-bridge` check exists to prevent (`756f3a5`), which
was fixed in the tool and never in the surface the operator actually watches. The rule, stated once:
**any surface asserting reachability derives it through `_environment_effective_status`.** The stored
column is a registration record, not a liveness signal.

## 2026-07-26 — "This environment's bridge can act" has ONE definition, and it is the effective status

**Decision:** the reconcile sweeps in `service/reconcilers/terminal_controls.py` decide reachability with
`_environment_actionable_sql()` — `status IN ('online','degraded')` **AND** `last_seen` fresh within
`environment_offline_seconds` — which mirrors `environment_effective_status(...) in
{"online","degraded"}` (`service/env_status.py`). There is no second derivation.

**Why (N7, reviewer finding).** The sweeps asked `environments.status = 'online'` while the stop-
REQUEST path (`bridge_can_claim`, `service/reconcilers/terminal_controls.py`) asks for the *effective* status in
`{online, degraded}`. Two halves of one feature, two answers to the same question. For a degraded
environment the request path therefore left the stop **pending for its bridge** and the sweep then
**failed** it — so the PTY was never killed, `stop_agent_worker` had already written the session
`'ended'`, and Start was free to spawn a SECOND worker. That is the identical worker-duplication
chain fixed in v0.1 for a changed `bridge_id`, reached instead through `degraded`.

**Degenerate `last_seen` values TRUST the stored status.** Absent, empty, malformed or non-canonical
stamps are not datable, so they do not age — exactly what `environment_effective_status` does when
`fromisoformat` raises (R3a's rule: do not invent a failure). Comparison is on the canonical 19-char
prefix so `...:00Z` and a legacy `...:00.123456Z` compare correctly (C2: never compare mixed-width
timestamps lexically).

**The setting is read, not hardcoded.** `_environment_offline_cutoff` loads
`environment_offline_seconds` from `settings` with the same `max(30, …)` floor as the other call
sites. A threshold honoured in one place and ignored in another is its own defect class.

**INVARIANT — the rule now has FOUR uses and they share one fragment:**
- the stop re-target's `SET bridge_id = (SELECT …)` subquery,
- the stop re-target's `EXISTS` guard,
- the `terminal_controls` env-currency failure sweep,
- the `environment_controls` env-currency failure sweep (same predicate, same error string, sibling
  table).

`environment_controls` was included deliberately: whether an environment's bridge is reachable must
not depend on which table the control lives in. Fixing only `terminal_controls` would have recreated
the same-rule-two-answers defect on purpose. `service/tests/test_stop_control_degraded_environment.py`
enumerates the whole `ENV_KNOWN_STATES` domain — online/degraded actionable, offline/forgotten/disabled
failed, stale-either-way failed, undatable trusted — so the set cannot be narrowed silently again.

## Session display status is derived from live truth (2026-06-03)

**Session display status is DERIVED from live truth, not served from the denormalized snapshot.** `GET /sessions` now derives each session's status (`_compute_session_display_status` / `_agent_session_dict_live`) the same way `GET /agents` derives the dot: managed keys on the live `terminal_sessions` row, resident on a fresh non-superseded bridge. The stored `agent_sessions.status`/`terminal_status` is a CACHE, never the display source — which structurally kills the recurring "Stopped/Stale but running" contradiction (the snapshot drifted and reconcilers only corrected it lazily). Supporting consolidation: one canonical `LIVE_SESSION_STATUSES` defined server-side and embedded into the dashboard bootstrap (replacing 4 divergent sets); one `_agent_liveness` predicate feeding the deriver; session mutators (`_reconcile_dead_session_status`, `_reconcile_duplicate_resident_sessions`) call `_invalidate_agent_live_state` so the dot refreshes same-pass; and `_reconcile_dead_session_status` case (a) now JOINs live `terminal_sessions` instead of the frozen `terminal_status` denorm the hygiene reaper left stale at `attached`. *Why:* every "state contradiction" symptom shared one root — agent status was live-derived but session status was raw-from-snapshot. Deriving both from the same liveness truth removes the disagreement at the source.

## Turn state is re-asserted from process truth in BOTH directions — KEEP-FRESH and KEEP-CLEARED (2026-07-13)

Edge-triggered turn events are necessary but not sufficient: turn state can also be written by paths the detector never observed (a hook, the channel sidecar), and an event can be lost. So the detector **re-asserts** what the process truth currently proves, on a cadence, in both directions: while the transcript/gateway proves IN-FLIGHT it re-POSTs `/turn-start` every 45s (KEEP-FRESH, so a server-side clear can't make a working agent read `online`); while it proves ENDED and the detector is not mid-turn it re-POSTs `/turn-end` every 45s (KEEP-CLEARED, so a stray `in_turn` the detector never saw start cannot latch `working` until the 30-min ceiling).

**This is not a time-based rule, and the distinction matters.** Nothing here infers a state from elapsed time — the *proof* (transcript tail structure / gateway session status) decides, and elapsed time only governs how often that same proof is restated. The rejected alternative was lowering the in-turn backstop, which would have been a genuine time-assumption (guessing a turn is over because it is old) and would have false-cleared long turns. Consistent with the project-wide rule: status is **PROVEN, not time-assumed**.

Safety: KEEP-CLEARED cannot false-clear a live turn (claude requires `classify(tail) === "ended"` — a live turn is never structurally *ended*; hermes requires a sustained gateway-idle read plus `!inFlight`; unknown/unreadable reads are no-ops), and `/turn-end` is idempotent and can never re-arm `working`. It does **not** rescue an agent-id-less bridge — that detector never arms at all (see "An agent session must never start without an identity").

## Turn hooks use each runtime's own events, and claude's PostToolUse re-asserts turn-start (2026-06-18)

`install.sh` wires the turn hooks on every install, not behind `--with-hook`. Each is a no-op in a
session without `AIFY_AGENT_ID` and `AIFY_COMMS_URL`.

- **claude** (`install_claude_turn_start_hook`, `install_claude_turn_end_hook`): `UserPromptSubmit`
  and `PostToolUse` post turn-start. `Stop` runs through `claude-stop-gate.js`, which drops a Stop
  fired while the transcript still shows the turn in flight. `SessionStart` with matcher `compact`
  and `StopFailure` post turn-end, and `PermissionRequest` posts blocked.
- **codex** (`install_codex_turn_hooks`): `UserPromptSubmit` starts, `Stop` and `Interrupt` end,
  `PermissionRequest` blocks and `PostToolUse` unblocks.
- **hermes** (`install_hermes_turn_hooks`): `pre_llm_call` starts, `on_session_end` ends, and
  `pre_approval_request` / `post_approval_response` bracket an approval. A turn that ends on an API
  error returns before hermes fires `on_session_end`, so it stays in-turn until the gateway turn
  detector or the 30-minute ceiling clears it.

**claude's `PostToolUse` turn-start is back, reversing the 2026-06-02 removal.** That change left
`UserPromptSubmit` as the only claude turn-start hook, on the premise that `turn_busy` stays set until
`Stop`. Two findings broke the premise: `UserPromptSubmit` does not fire for a channel-woken managed
turn, and `Stop` fires early or more than once within one logical turn (Claude Code issue 54360) and
around API retries, clearing the turn mid-work. `PostToolUse` cannot re-pin an idle agent, because it
fires only on a real tool call: a tool call after a `Stop` means the turn was not over. The bridge's
transcript detector (`claude-turn-end-detector.js`) still backstops both directions, and a missed
turn-end still self-heals at the single long ceiling. The reasoning is also in the comment inside
`install_claude_turn_start_hook`.

## Delivery gates read raw `turn_busy`, bounded by one ceiling that status shares (2026-07-26, revised 2026-08-30)

The two delivery gates, the send-time queue decision in `send_message` and the turn-busy gate in
`/dispatch/claim`, read the RAW `agent_turn_state.turn_busy` flag, not derived status and not a short
freshness window: an explicit queue must mean exactly "after this turn", and re-deriving it through
status is what made `queueIfBusy` sends land mid-turn (task #236). One helper owns the read,
`_turn_busy_holds_delivery` in `service/api_core/claim_gating.py`.

**The flag needs a ceiling, because nothing guarantees it is cleared.** The dead-bridge sweep
deliberately skips hook-owned turns (`turn_bridge_id` of `''` or `user-prompt-submit`) and turns whose
bridge is alive, so a killed harness or a missed `Stop` latches the flag. Unbounded, such an agent
reads idle while delivery holds, and a target without `steer` goes deaf to every dispatch.

**The ceiling is one rule**, `turn_is_still_live` in `service/api_core/turn_liveness_policy.py`, which
the delivery gate and the status `in_turn` clamp both call with the same two constants:

- A turn whose `turn_bridge_id` names a live, heartbeating bridge of this agent is a renewable lease:
  a re-stamp extends it, up to `TURN_LEASE_ABSOLUTE_MAX_SECONDS` (4 h).
- Any other turn (the hook marker, an empty owner, a bridge that is gone or stale) is measured from
  `turn_started_at`, which only the not-busy to busy transition writes, against
  `TURN_BUSY_BACKSTOP_SECONDS` (30 min).

The anchor is the lesson of 2026-08-30: a managed hermes whose `pre_llm_call` hook re-stamps before
every model call held every queued dispatch for 38 minutes, because the old ceiling was measured from
the column that hook kept moving. `TURN_BUSY_STALE_SECONDS` (120 s) is no delivery window; its only
reader is `_turn_busy_state` in `service/api_core/turn_state.py`. `test_turn_busy_delivery_ceiling.py`
pins the gate.

## Managed-hermes turn-start is scoped to dispatched turns; post-turn background gateway "running" is NOT a turn (2026-07-10)

The managed-hermes gateway turn detector (`hermes-gateway-turn-detector.js`) faithfully reports the gateway's own `session["running"]` truth: `working` → `/turn-start`, sustained idle → `/turn-end`. But hermes (notably on gpt-5.6-sol) runs **post-turn background model work** — self-improvement / memory update — AFTER a dispatched turn completes cleanly. That work also sets `session["running"]=True`, so the detector faithfully fired a second `/turn-start` and the agent flapped to `working` while idle-to-the-user (live-reproduced 2026-07-10). `derive()` correctly trusts `turn_busy=1` as "executing a turn," so neither component was wrong in isolation — the gap was **semantic**: background housekeeping is a real gateway turn but not user-facing dispatched work.

**Decision:** the detector's `/turn-start` (edge AND working-refresh keep-alive) is now GATED by an optional `shouldFireTurnStart` predicate. Managed hermes passes `() => inFlight.dispatchTurnOpen === true` — a credit SET on a successful delivery and REVOKED by the detector's own turn-end. Turn-END is never gated (a clear is always safe). *Why a dedicated credit rather than the existing `submittedAt`/`completed`:* those are maintained by the re-pulse probe, which STOPS at `REPULSE_WINDOW_MS` (~15min), so a >15min turn would leave them frozen and the flap would return for long turns; the detector's turn-end fires regardless of duration, so a credit it revokes tracks turn boundaries far better than the 15-min-capped fields. *Why this doesn't under-report normal real turns:* a dispatched turn shows `working` INSTANTLY via the claim-time `reportTurnBusy` + the `makeInFlightPulse` beat — the detector start was only a redundant backstop, now correctly scoped. *Two accepted residual under-reports* (benign; detailed in the KNOWN_ISSUES archive): an operator TYPING into a managed console (no delivery credit), and the resumed tail of a >15-min turn that had a ≥9s mid-turn gateway-idle gap (credit revoked at the gap, probe already stopped). Both are strictly better than the flap; gating in `derive()` was rejected because it reintroduces the flap for >15-min turns and breaks resident/typed work. *Resident residual (accepted):* the default predicate is always-fire, so a resident hermes (human-typed turns, no delivery signal) keeps the old behavior and could still flap on post-turn background — there is no local delivery signal to gate a resident on, and the managed fleet is where the flap matters. Deploy is a bridge change (`install.sh --client hermes` + env-wrapper restart).

## Managed-hermes gateway re-ensure is bounded by a reset-on-recovery crash-loop budget (2026-07-11)

`maybeReEnsureGatewayHost` (blast-radius fix, #237a) respawns a dead gateway host on every delivery poll so a single agent self-heals without a bridge restart. But it had **no ceiling**: a gateway that BINDS-THEN-DIES — a TUI-build failure (`Missing script: "build"` on hermes drift), an operator `hermes update`/`dashboard --stop`, or a hermes/GLM account with no API balance — gets respawned every `POLL_MS` forever, each respawn a `hermes.exe` the reapers must then clean (the proliferation / headless-orphan class). **Decision (surfaced by reading Traycer's `host-health-monitor`, whose `MAX_AUTO_RESPAWNS_WITHOUT_RECOVERY = 3` counter is reset by a successful probe):** cap consecutive no-recovery respawns at `MAX_REENSURE_WITHOUT_RECOVERY = 3`; a **live ws connect** (the strongest reachability proof, not just an index 200) resets the budget for a future episode; exhausting it stops the respawn and falls through to the existing `reportGatewayDeadOnce` resident-lost path (which self-corrects the agent off `available` so the dispatcher stops sending). The arithmetic is a pure `nextReEnsureBudget()` helper (unit-tested); behavior changes ONLY in the pathological binds-then-dies case — a gateway that recovers restores full budget. This is the respawn half of the guard our gateway-liveness *probe* already had on the detect half (`DEFAULT_GATEWAY_PROBE_THRESHOLD`). Bridge change — deploys on `install.sh --client hermes` + wrapper restart.

## An agent session must never start without an identity — and the command we hand out must carry one (2026-07-14)

**`AIFY_AGENT_ID` is the single point of failure for ALL turn state.** Every path that can set or clear `working` is gated on it: the bridge's turn detector (`server.js` — `if (AIFY_AGENT_ID && …)`), the `Stop` / `UserPromptSubmit` / `PostToolUse` hooks (`if [ -n "$AIFY_AGENT_ID" ]`), and the session-store capture hook (keyed by agent id). The wrapper exports it only when the agent id is passed. So a session launched without one is a session whose status is **structurally unfixable**: the channel sidecar (which carries the id in its own config) still SETS `working` on an inbound wake, and nothing left alive can ever CLEAR it → latched `working`; once the backstop ages that flag out it reads `online` and can never show `working` again. Both halves, one cause.

**The trigger was our own UI.** `resume_command` — surfaced by the dashboard as the operator's resume/takeover command — emitted `claude-aify --resume <id>` with **no `--aify-agent`**. Copying the command the product handed you produced an identity-less session. Meanwhile hermes had recovered its agent from a bare `--resume <handle>` since 2026-06-03, and its comment claimed *"same idea is wired into claude/codex"* — which was **false**, and that false comment is why nobody looked.

**Decisions.**
1. `resume_command` carries `--aify-agent <id>` for every wrapper runtime. A command we hand the operator must never be the command that breaks the agent. (It is a display-only string — no exec path — so this is safe to change.)
2. `claude-aify` mirrors hermes' recovery: on `--resume <handle>` with no agent id, ask the service which agent owns that handle (authoritative, and unlike the `/tmp` session store it survives a reboot), then fall back to the local store.
3. If the id is **still** unknown, the wrapper says so loudly (`NO AGENT ID: aify turn/status detection is DISABLED`). Anonymous sessions stay legal — a plain claude+comms session is a real use case — they just may never be *silent* again.
4. **Not fixable at runtime.** `comms_register` writes DB rows; `AIFY_AGENT_ID` is read once at bridge boot. Claude Code's in-app `/resume` picker swaps the conversation *inside* the same process and keeps its env. Only a relaunch repairs identity — `--resume` preserves the conversation, so the cost is a relaunch, not context.

**Codex has the same recovery.** `codex-aify` (aify-wrapper's `codex-aify.sh.in`) asks the service which codex agent owns a bare `--resume <handle>`, and prints `NO AGENT ID` when none does; it has no local session store to fall back to.

## 2026-07-28 — `comms_register` warns when a resident has no launch identity

**Decision:** `comms_register` appends a warning (never an error) when a RESIDENT registers from a
session whose `AIFY_AGENT_ID` is missing, or differs from the id being registered. See
`mcp/stdio/register-identity.js`.

**Diagnostic scope, corrected 2026-09-10.** Compare the sanitized bridge launch identity,
including its `AIFY_COMMS_AGENT_ID` alias. An unresolved template is unavailable, never an
identity to register under. A concrete different identity still warrants a warning.

The MCP child's environment does not establish the parent's hook environment. In particular,
Hermes can filter and configure its MCP environment separately. Registration preserves an explicit
native handle and can start registered-agent heartbeat paths independently of launch identity.
The warning therefore does not prove a missing handle, latched status, or a need to relaunch.
Check identity propagation, turn reporting and delivery binding separately before choosing a repair.
For resident Hermes, the legacy `hermes-missing-handle` wake label checks for a usable gateway URL,
not a missing native handle. This diagnostic correction changes neither predicate nor lifecycle.

**Scope:** residents only. Managed sessions get their identity from the spawner and their turn
signals from the runtime host, not shell hooks. A warning, not a refusal — anonymous
`claude` + comms sessions remain legal, they just stop being silent.

## Wake modes

Every agent registration resolves to one of these wake modes. `comms_agent_info` reports the current one:

| Wake mode | Meaning |
|-----------|---------|
| `claude-live` | Resident Claude session started via `claude-aify`; woken through the local aify channel bridge. |
| `codex-live` | Resident Codex session started via `codex-aify`; woken through the shared local WebSocket app-server that the visible TUI uses. |
| `codex-thread-resume` | Resident Codex session started with plain `codex`; woken by resuming the bound `thread.id` in a separate background app-server. |
| `hermes-live` | Resident Hermes session started via `hermes-aify`; woken through the local Hermes dashboard gateway. |
| `presence-only` | Resident Pi/OpenCode metadata is visible, but triggerable delivery must use a managed runtime session. |
| `managed-worker` | Detached managed worker created by dashboard Environment spawn or `comms_spawn`. Not visible in a live user CLI. |
| `message-only` | Legacy/no-live binding. Normal `comms_send` rejects these targets instead of storing future work; older inbox-only records may still display this mode. |
| `claude-needs-channel` | Claude agent is registered but no alive `claude-aify` wrapper exists on this machine. Fix: launch one. |

## Runtime limits

| Capability | Claude Code | Codex | Hermes | OpenCode | Oh My Pi |
|------------|-------------|-------|--------|----------|----------|
| Managed workers | yes | yes | yes | unsupported: unverified since the environment bridge was deleted (v0.6.3) | deprecated (see "Pi is deprecated") |
| Default managed backing | `claude-aify` PTY under aify-env, channel delivery | `codex-aify` wrapper PTY under aify-env | `hermes-aify` wrapper PTY under aify-env | native controller (unverified) | native OMP RPC |
| Resident visible-wake | `claude-live` | `codex-live` | `hermes-live` | presence only | presence only |
| Interrupt | yes | yes | yes | yes | yes |
| In-flight steering | channel/resident | yes | gateway steer/follow-up | no | yes |
| Active dispatch hard timeout | 12 h | 12 h | 12 h | 12 h | 12 h |

**One active dispatched run per agent.** Later dispatches from the same sender merge into a buffered pending run (see below).

**SSE clients** can message, inspect runs, and request dispatch — but they cannot host triggerable sessions or be local launchers.

## Governance: lazy-autostart, disable, and the same-mode race guard (2026-05-31)

**Lazy autostart + env auto-bind (Phase 2).** Sending to an `available` managed agent (registered, env online, no live worker) auto-starts it: if no live PTY backs it, the send cold-starts a `spawn_request` the host (aify-env) claims, auto-binding the freshest ONLINE environment that advertises the runtime when the agent has no env bound. Only when *no* online environment can host the runtime does the send reject — with a clear "no online environment can host" message, not the old misleading "wrapper PTY unavailable". A queued dispatch is always backed by a claimable `spawn_request` (no orphans). *Why:* the operator's policy — `available` means reachable/not-running and should start on first message ("100 sessions shouldn't all boot when I open the dashboard"). Previously codex/hermes/pi `available` agents hard-rejected ("cannot start live work now"); only Claude escaped because its channel branch was best-effort. Helpers: `_select_online_environment_for_runtime`, `_coldstart_spawn_request_for_dispatch`.

**Disable = explicit hard-block (Phase 3).** Operator **Stop** (`POST /agents/{id}/control` action=`stop`) sets `launch_mode='none'` + `status='stopped'`; that agent never auto-starts and refuses dispatches from *other* agents (preflight treats `stopped` as unavailable and `_agent_execution_mode` blocks `launch_mode='none'`). **Resume** reverses it. The Phase 2 cold-start respects this — a disabled agent is never auto-started. (`wakeMode='disabled'` signals it in the dashboard.)

**Same-mode resident race guard (Phase 4).** A re-register by a DIFFERENT bridge of a resident identity whose prior same-mode bridge is still LIVE (heartbeat within the resident lease) is hard-rejected with `409` instead of silently superseding it and killing the live wrapper's in-flight work. `force=true` (wrappers: `AIFY_FORCE_REGISTER=1`) is the deliberate take-over escape hatch after the operator restarted the prior wrapper. Stale prior bridges fall through and are superseded normally (self-heal). This is RESIDENT-only: managed bridges keep latest-launch-wins (zombie reaping), and the visible-TUI managed model runs a legitimate `channel-sidecar` + `managed-wrapper-child` pair concurrently — neither trips the guard. *Why:* the operator wanted an error on a genuine registration race rather than a silent collision; the 60s-grained heartbeat means the guard is gated on liveness + an explicit force override so legitimate fast restarts can still take over. Supersedes the unconditional latest-wins behavior for the fresh-resident case (the archived 2026-05-23 carve-out note describes the prior model). Helper: `_fresh_same_mode_bridge_conflict`.

## Sends to a managed agent always queue (2026-06-02)

**Sends to a managed agent ALWAYS queue — no deaf fail-fast (operator-reversed).** The original WS6 design hard-rejected ("failed fast", no run written) a send to a managed sidecar-delivery agent whose claimer lease was released/stale. That was **reversed**: in live use it lost messages to an agent merely mid-restart (lease released then re-acquired moments later). A send to a managed agent now **always queues a dispatch run**; the **queued-run backstop reaper** (`queued_run_backstop_seconds`, 180s) is the **sole safety net**, failing a queued run only after it has been genuinely undeliverable for the backstop window (then mirroring the failure to the sender). Lazy-autostart-on-send and all live-agent delivery are preserved, and an agent that never recorded a lease is still treated as cold-startable. The lease helpers and deaf-detection are retained for status/deliverability reporting only — they no longer gate a send. *Why:* the operator judged "queue + a backstop that fails only after a real undeliverable window" strictly safer than "reject immediately and risk dropping a message to a worker that is about to be back," and the backstop still prevents unbounded `buffer_full` pileups on a genuinely dead target.

## Dispatch never switches an agent between resident and managed (2026-07-06)

**resident↔managed is OPERATOR-ONLY: dispatch NEVER auto-cold-starts a managed worker for a `session_mode='resident'` agent (2026-07-06).** The send-path/backstop cold-start (`_coldstart_spawn_request_for_dispatch`) returns early — before any spawn — when the target's canonical mode is `resident`, regardless of whether its resident bridge is fresh or disconnected. Both resident and managed claude deliver via `claude-channel.js`, so a channel dispatch to a resident is claimed by the RESIDENT session's own sidecar; auto-spawning a managed worker instead forks a **duplicate identity beside the live resident** (operator-reported "2 aicm-lc-managers": a reply cold-started a managed twin). Auto-switching a *disconnected* resident is equally wrong — it SPLITS delivery, so replies land on the managed twin while the resident (on reconnect) still sends. The guard sits at the top of the shared coldstart helper, so it protects every caller (send-path `51df58c`, the queued-run backstop, and the env-recovery channel replay `#238`); a genuine `managed` recipient with no live worker still cold-starts normally. A deliberate operator **Switch to managed** flips `session_mode='managed'` BEFORE any cold-start, so it never blocks an intentional transition. *Why:* resident↔managed ownership is a manual, operator-owned decision (the switch-safety entry is in the archive); dispatch must never change an agent's ownership model behind the operator's back.

**`resident-lost` splits by `session_mode`: a MANAGED worker that loses its backing rests cold-startable `available`, only a RESIDENT goes `stopped` (2026-07-07).** The same endpoint is reused by the hermes managed-host (`reportGatewayDead`) when a managed agent's gateway port dies. The old fallback stopped ANY agent with no auto-return transition (`status='stopped'`, `launch_mode='none'`) — but `stopped` makes the send-gate reject every message ("agent status is stopped", `dispatchRuns:[]`), so a dead-gateway managed hermes could NEVER wake and the only recovery was a manual `hermes-aify` restart (whole hermes team got stuck stopped, 2026-07-06/07). A managed worker is re-spawnable, so the handler now branches on `session_mode`: `managed` → rested cold-startable (`status='active'` → derives `available`, `launch_mode='detached'`) so the next send auto-cold-starts a fresh managed worker (new gateway); `resident` → `stopped`, as before. *Why safe:* the bound-env send-preflight still gates delivery, so an offline env yields a clean "env unavailable" wait rather than a permanent stop; and a resident that lost its runtime with no managed backing is still correctly stopped.

## A retried `/messages/send` collapses through a unique index (2026-07-08)

**`/messages/send` idempotency is enforced by a DB UNIQUE index, NOT the upfront SELECT (2026-07-08, #240).** An optional `clientNonce` makes a send retriable: a retry with the same `(from_agent, client_nonce)` collapses to the original message instead of double-sending, so the bridge can safely retry a send that hit a transient socket error (previously it DROPPED such sends, stranding owed replies). The handler has an upfront SELECT-by-nonce as a fast path, but that alone is **not** sufficient — under a concurrent retry (the client aborts at ~20s and retries while the first request is still mid-handler on the single event loop) both requests SELECT-miss across the wide `await` gap and both insert. The atomicity source is therefore a **partial UNIQUE index `(from_agent, client_nonce, to_agent) WHERE client_nonce != ''`** plus `INSERT OR IGNORE`: a raced duplicate is rejected at the DB, and `rowcount == 0` for a nonce'd send means "lost the race" → return the original `messageId`, create NO dispatch runs. *Two subtleties a future edit must preserve:* the index MUST include `to_agent` (a legit multi-recipient send writes one row per recipient with the SAME nonce — keying only `(from_agent, client_nonce)` would reject all but the first recipient); and the `WHERE client_nonce != ''` clause keeps the empty-nonce default (all legacy + nonce-less sends) out of the index so they insert freely, exactly as before. Do not remove the index thinking the SELECT covers it.

A retry is a replay only when it is the same message: since `33b26d24` a reused `clientNonce` carrying a different type, subject, body, recipient or trigger is refused with 409 instead of being answered with the first message's id.

## Channel offline-replay owns a dedicated run (2026-07-08)

**Channel offline-replay inserts a DEDICATED run and never merges (2026-07-08, #238).** `_replay_undelivered_channel_messages_on_env_recovery` (60s reconcile) re-delivers a channel post that was stored-only because the member's env was offline at send time, keyed on the member's fanout `message_id`; its idempotency watermark is `NOT EXISTS(dispatch_runs WHERE message_id = fanout_id)`. It calls `_create_dispatch_runs(..., allow_merge=False)` — the merge path (`_find_mergeable_queued_run`) folds a dispatch into an existing queued run but deliberately KEEPS that run's original `message_id` ("first item"), so a merged replay would leave its own fanout id on no run, the watermark would stay true, and the sweep would **re-replay it every 60s, appending the body until the buffer cap**. The replay must own a run keyed on its own message_id. *Why `allow_merge` and not a replay-only insert:* the guard lives inside the one shared `_create_dispatch_runs` so the normal send path keeps merging (its intended pile-up avoidance) while only the replay opts out.

## A delivered reply run whose turn died is failed by reconcile (2026-07-10)

**A delivered require_reply run whose worker turn DIED is FAILED by reconcile, not left `delivered` forever (2026-07-10).** A managed hermes turn that dies to a model-429, a mid-turn interrupt, or a stall never sends its reply and never emits a clean turn-end — so the turn-end→auto-mirror close (`managed_reply_capture_fallback`, default on) never fires and the run sits `delivered, require_reply=1, result_message_id=''` indefinitely. Downstream that reads as "the agent is idle / ignoring the contract" (sc-manager live repro: sc-architect's 3 runs all model-429'd before any work; sc-tester's turn interrupted mid-work — his comms_sends that COMPLETED did land, confirmed server-side, so it's a dead-turn accounting gap, not a delivery drop). `_fail_stranded_delivered_reply_runs` (60s reconcile, before `_sweep_unmirrored_failed_handoffs`) FAILS such a run past `stranded_reply_fail_minutes` (default 45, well beyond the 10/20/30 reminder cycle) with a clear cause; the existing failed-handoff sweep then mirrors the failure to the sender. *Keyed on STALENESS, not `turn_busy`* — deliberately, because the hermes turn-status flaps, so a turn_busy-gated close would be unreliable; a 45-min-old still-`delivered` run is dead regardless. *Safety invariants a future edit must keep:* skip a run the agent is CURRENTLY working (`turn_busy=1 AND turn_run_id == this run`), re-check `status='delivered'` in the UPDATE so a concurrent reply wins, and treat `stranded_reply_fail_minutes=0` as off. This closes the accounting; it does NOT fix the upstream hermes turn deaths (429/interrupt) — those are model/runtime-side.

## A channel `require_reply` reply must be sent in the same turn (2026-06-02)

**Channel `require_reply` reply must be same-turn.** The channel wake text for `require_reply` dispatches now explicitly instructs a same-turn `comms_send(inReplyTo=...)` reply and warns the session won't be re-woken to finish a deferred reply. *Why:* a managed/channel session goes idle after its turn and is not re-woken; an agent that split read (turn 1) and reply (turn 2) stranded the reply until an unrelated dispatch happened to re-wake it (~20min). The durable platform mitigation is the instruction, not new infra (the reply threads correctly once sent).

## Dispatch tracks handoff, with explicit replies preferred

**Decision.** `comms_dispatch` requires a reply handoff by default, and `comms_send(type="request")` does too. `requireReply=false` does NOT release it for a `request`, `review` or `error`: `_contract_list_query` enrols those three by TYPE whatever the flag says, so the flag releases the handoff only for `info`, `response` and `approval`. (Corrected 2026-08-29. Whether the flag SHOULD be honoured for all six is an open question recorded in `reply_contract.py` and in the weak-points doc; this sentence describes what happens, not what should.) Agents are still expected to send their own explicit `comms_send(..., inReplyTo=...)` reply. A reply-dispatch back to the requester also satisfies the handoff. As a recovery path, a recent unthreaded direct `response`/`review`/`approval`/`error` from the worker to the requester satisfies the latest matching pending handoff for that pair. If a required reply is still missing when the run ends, the bridge mirrors the run result back to the requester as a fallback inbox handoff.

**Why.** Pure run summaries were too easy to miss in real manager/worker loops: work finished, but the requester saw an empty inbox and the lane looked dead until someone manually polled `comms_run_status`. Fully automatic replies were also too blunt because the bridge cannot reliably decide what the agent meant to report. The compromise is: require a real reply for work handoff, prefer an intentional agent-authored message, accept reply-dispatches as real handoffs too, but refuse to let the lane silently stall if that handoff never happens.

**Consequence.** Once a real reply is linked to the run, fallback mirror messages are not generated. If an older fallback mirror already exists and a late real reply is linked later, that mirror is auto-marked read so it stops polluting unread counts. The dashboard's `Pending Handoffs` repair action applies the same fallback mirroring to old terminal runs so stale "done but nobody was told" records can be forced into the requester's inbox.

**Claude resident caveat.** Claude resident notification runs that complete with `Delivered to Claude resident session` are delivery acknowledgements, not proof that Claude finished the task. Those rows are not counted as pending handoffs; the real handoff remains the message/reply flow.

**Unread caveat.** When a dispatch is claimed, the server marks the source inbox message as read for the target because the work was already injected into that runtime. Buffered `Pending updates` runs mark every included `MessageId` read on claim, not only the first message, so delivered batches do not keep resurfacing as unread work.

## Reply reminders preserve the original conversation identity (2026-07-15)

An overdue reminder is delivery machinery, not a new speaker and not a new contract. The
service therefore emits it **from the original requester's agent id**, to the original target,
with `type=info` and `in_reply_to=<original-message-id>`. Its body directs the target to send a
`type=response` message back to that requester with `inReplyTo` set to the original id. That
response records the result and completes the original run even if the original run was still
queued; otherwise the already-answered request could later be delivered again. Historical
reminders stored as `from_agent=dashboard` remain historical evidence and are not rewritten.

## Dispatch buffering (cap 10)

**Decision.** When an agent is already running a dispatch and the same sender tries to queue another, new dispatches are merged into one pending buffered run instead of stacking. The buffer caps at 10 items; past that, new dispatches are rejected with `reason: "buffer_full"` in `notStarted`.

**Why.** Without it, a sender that panic-retries (or a channel that fans out aggressively) can pile up 50+ queued runs on a stuck agent. Those runs all claim to be "queued" but there is nothing the operator can do except cancel them one by one. Merging collapses panic-retries into a single growing envelope with per-item timestamps; the cap prevents unbounded body growth.

**Why per-sender.** Different senders are different conversations; merging across senders would lose the thread. The cap is per (sender, recipient) pair.

**Why 10.** Picked to be high enough that normal bursty workflows never hit it, low enough that a buggy sender can't grow a single run body past ~100 KB.

## Steer requests are message-backed and stale-safe

**Decision.** `comms_send(..., steer=true)` still writes the inbox message first. If the target already has a live active run on a steer-capable runtime, the server appends a steer control to that run and records the source inbox message ID. When the bridge later marks the control `completed`, the inbox copy is auto-marked read. If the only active run is owned by a superseded bridge, the server waits through the same bridge-replacement grace window before failing that stale run and falling back to a normal queued dispatch instead of steering into dead state.

**Why.** Steering is advisory work-routing, not a separate message transport. The sender still expects an auditable inbox record. Before this fix, steer results could look like "queued behind active run `<same run id>`", and a steer sent while the DB still pointed at a dead bridge could disappear into a stale control queue. Recording the source message ID and treating superseded active runs as stale before steering eliminates both failure modes.

**Consequence.** A successful live steer no longer leaves an unread inbox copy behind. If the active run was stale, you may see that older run fail with an auto-heal summary while the new message queues normally for the replacement bridge.

## Stale-run cleanup has a short bridge-replacement grace window

**Decision.** The `/dispatch/claim` endpoint treats an active run owned by a different bridge as stale only after a short grace window. During that window the replacement bridge gets `blockedBy.reason = "active_run_owned_by_previous_bridge"` and does not claim more work. After the window, the server marks the orphaned run failed inline and proceeds to hand out queued work. If the active run is owned by the *same* bridge that's polling, the server still blocks as a bridge-side safety net.

**Why.** The previous behavior had a ~60-line tree of heuristics (superseded-bridge check, timestamp comparison, legacy-unowned detection) that tried to distinguish "genuinely busy" from "stale orphan" based on bridge_instances metadata. These heuristics had timing gaps: if a bridge died and a replacement registered slightly before the dead bridge's last claim, the timestamp comparison failed and the stale run permanently blocked all wake delivery for that agent.

The structural insight that eliminates the old heuristics: the bridge-side gate in `server.js` prevents a live bridge from calling `/dispatch/claim` while it has work in flight. Therefore, if a bridge IS calling claim, it has no local active run. Any DB-level "active" row for that agent owned by a *different* bridge is stale once it survives the bridge-replacement grace window. The grace window avoids the opposite race: a fresh bridge starts polling while the previous bridge is still finishing the run it just claimed.

## Channel messages land in inbox

**Decision.** `comms_channel_send` delivers the message to every member's inbox. There is no separate "channel view" the agent has to poll.

**Why.** Coding agents don't keep long-lived UI windows open on channels. If channel messages lived only in channel history, agents would miss them unless they remembered to poll. Delivering to the inbox means the normal unread-notification flow covers channel traffic automatically.

## Channel history is canonical-only

**Decision.** Channel read endpoints (`GET /channels`, `GET /channels/{name}`) count and return only canonical channel rows (`to_agent IS NULL`). Per-member inbox fan-out rows are not part of channel history.

**Why.** Channel send writes one canonical row plus one inbox delivery row per recipient. Treating both as channel history duplicated every logical post in the UI and MCP reads, inflated message counts, and made channels look noisy even when delivery worked correctly. Canonical-only reads preserve the actual conversation while leaving inbox fan-out intact for unread counts and wake delivery.

## Managed claude is delivered through its channel, not typed into its console

- **`insert_messages_via_console`, default `false`, chooses the delivery.** False: runs to managed
  claude are routed `execution_mode='channel'`, claimed by `claude-channel.js` inside the wrapper PTY
  and delivered as channel notifications. True is the legacy escape hatch that types the message into
  the PTY as a bracketed paste, scrambling anything typed at the same moment; it stays as a working
  baseline for a host whose channel delivery is misconfigured. It replaced
  `claude_managed_channel_only`, with the polarity inverted so proper delivery is the default. Channel
  delivery depends on `--dangerously-load-development-channels` and its first-launch
  acknowledgement, which the service answers (see "The service answers one console dialog, and never
  a resume menu").
- **Every run-creating path routes managed claude to the channel.** `send_message`, the spawn
  request's running transition and the auto-mirrored handoff all call
  `_apply_channel_routing_to_claude_runs` after `_create_dispatch_runs`; a managed claude run left at
  `execution_mode='managed'` has no claimer and sits queued. That is how spawn-time briefs once stuck.
- **Channel-eligible managed claude skips the `managed-run` capability check** in
  `_agent_execution_mode` (`service/api_core/execution_mode.py`), because claude has no headless run
  API; its dispatch flows to `execution_mode='channel'`.
- **A channel run settles only after its notification succeeds.** The sidecar marks a delivered run
  `delivered` when it owes a reply and `completed` when it does not, and `failed` if the notification
  throws (`markDispatchDelivered` in `mcp/stdio/channel-dispatch-receipts.mjs`). "Delivered" means
  the notification landed, not that claude did the work; the work is tracked by the reply (see
  "Dispatch tracks handoff").

## Wrapper-PTY pre-spawn at spawn-request running (managed_pty_eager_spawn)

**Both settings are internal, default ON, and must stay on.** `managed_pty_eager_spawn` and `managed_terminal_backing_enabled` are declared in `service/api_core/settings_spec.py` with `shown=False`, so the dashboard never draws them. Under aify-env every managed worker starts from its terminal row, so turning `managed_terminal_backing_enabled` off stops every managed worker. They remain settings only because the legacy regression suites switch them to exercise the pre-console delivery paths. `validate_update` still accepts both on `PUT /settings`, so the switch exists; do not use it on a live host.

**What the eager spawn does.** With both on, the spawn request's running transition launches the console for the newly registered managed agent (`managed_pty_eager_spawn` in `service/api_core/running_spawn.py`), so the console exists before the first dispatch; later dispatches and a manual Start Console reuse that terminal. A launch failure here does not fail the spawn request's running transition; the dispatch path's lazy start is the safety net.

## Orphan-managed-run reaper covers terminal-mode runs too

**Decision.** `_close_orphaned_managed_runs` (the 5-min fast reaper) drops its `dispatch_mode != 'terminal'` exclusion and ALSO catches terminal-mode runs whose `claim_bridge_id` is empty. Same 5-min `active_managed_run_stale_minutes` window. Plus: the reaper now requires positive evidence of no progress (`NOT EXISTS dispatch_events since cutoff`) to avoid false-positive reaping of slow-claim clients.

**Consequence.** Stuck wrapper-PTY-backed dispatches now clear in 5 min instead of 30, unblocking queued messages. Same `active_managed_run_stale_minutes` setting tunes it. Legitimate in-flight runs that DO emit dispatch_events (the normal case) are untouched.

## Startup reconcile closes runs nothing will ever close

**Decision.** A bounded startup pass closes `delivered` dispatch runs that are result-linked, or stale with no required reply, or — a require_reply run that is stale **and** has no active owner (no queued/claimed/running run and no live session) to ever produce the reply.

**Why.** Hundreds of `delivered` runs accumulated that no code path would ever finish, inflating "reply pending" handoff metrics and making lanes look alive forever. The orphaned-require_reply case is gated on demonstrable no-owner so a run a live session could still answer is never closed prematurely.

## 2026-09-22 — An external agent sends here without registering, and says where it is

An agent on another machine must be able to send a message to this service without appearing in its
roster. Its env and wrapper belong to its own host; a row here would claim this service knows it,
and nothing here can vouch for it. That is the operator's call, taken after an agent from a second
PC turned up in the registry with a blank machine id: **"that external should not register here. he
is agent in another pc and it would not make sense if he would register here."**

Sending already required no row and still creates none. What was missing was any way to see that a
message came from outside, or who to answer. So a send may carry an `origin` — an endpoint and a
contact in the sender's own words — stored on the message. `fromRegistered` is derived per read.
Every reader carries both through one serializer (`api_core/message_view.py`): the dashboard feed and
inbox draw an `external` chip with the origin beside it, and the `From:` line an agent reads names
the sender as external with the origin it declared. That covers `comms_inbox` on both transports, the
prompt a dispatch wakes an agent with, and console-typed delivery. The service's own voices
(`dashboard`, `operator`, `aify-comms`, channel notices) have no roster row and are not external. A
message sent *to* such a sender is stored here only, and the send answers with the origin it declared
instead of "register the target first".

**DECLARED, NEVER MEASURED, and it is labelled as a claim wherever it is drawn.** The obvious design
is to record the client IP, and this deployment cannot: every peer the service observes is
`172.27.0.1`, the Docker bridge gateway, or a sibling container, because Docker NATs everything
arriving from outside. Traffic from this host and from another PC are indistinguishable at the
socket, so an IP field would hold one constant for both and read as provenance. Only the sender
knows, so the sender says. It is attacker-controlled text from a party with no identity beyond the
shared key: trimmed, capped at 200 characters, escaped where drawn, and never a default.

**Scoped to SHOWING the origin, not relaying a reply.** Answering an unregistered sender means
POSTing to *their* service, which needs a peers table holding each peer's endpoint and credential
and a decision about mismatched tokens. That is a cross-instance feature; this fits the current
shape. Derived at read time rather than stamped at send time, so a sender removed later reads as
external from then on.

**What this does NOT close:** holding the shared key still lets a caller register any agent id from
any machine, and send as any id: `fromRegistered` says a name exists here, not that the caller is
its owner. Auth is one instance-wide secret with no notion of which host is calling, which is how
the second PC's agent landed in the roster in the first place. Per-machine credentials are a
separate design, not started. **Started and shipped 2026-09-24: see "A key per other machine" below.**

## Re-register is a full state refresh (except description)

**Decision.** `comms_register` on an existing agent overwrites `sessionHandle`, `runtime_state`, `cwd`, `role`, `runtime`, `machineId`, `runtimeConfig`, and capabilities with whatever the new request contains. The only exception is `description`: omitting it preserves the existing value; passing `""` clears it.

**Why not preserve everything.** Earlier versions preserved `sessionHandle` and `runtime_state` across re-register. That let stale Codex thread IDs survive a fresh `codex-aify` start and broke `thread/resume` with `AbsolutePathBuf` or `no rollout found`. Making re-register authoritative is simpler and matches the user's mental model: "I just re-registered, the record should reflect *this* session".

**Why keep description.** Description is human-facing team context ("I work on the NRD ingest pipeline"). It changes on a slow cadence and should survive the common "kill + restart + re-register" loop. The explicit `""` clear is there for when you genuinely want to reset it.

## A runtime session id belongs to at most one live agent (2026-05-31, extended 2026-09-16)

**Cross-agent session-id collision guard (2026-05-31).** A runtime session id must be owned by at most ONE live agent. `update_agent_session_handle` now rejects/parks a handle a DIFFERENT live agent already owns (`_session_handle_live_owner`), keeping the agent's own handle — the resident↔managed invariant. A stale/dead owner is not a collision. **`POST /agents` applies the same guard since 2026-09-16** (`service/api_core/registration_handle_collision.py`): until then a registration wrote whatever `sessionHandle` it was sent, and the bridge sends its own session id on every `comms_register`, so a second name registered from inside a live session held that conversation beside its owner. The newcomer now registers without the id, which is parked in `pending_session_id` for a Confirm. **"Live"
is a fresh HEARTBEAT, not a status**, so an agent whose window was closed a moment ago still holds its id for
the resident lease (150 s): measured 2026-09-16, an owner marked `stopped` or `offline` with a recent
`last_seen` still parks the newcomer's id. That is the safe direction -- an agent momentarily reading
offline must not lose its conversation to whoever registers next, which is the 2026-05-31 incident -- so the
operator's "close the window, register it under another name" either waits out the lease or takes the
dashboard's Confirm. **Waiting out the lease works only because the bridge offers the id again.** The first
version of this paragraph promised it before that was true: the service answers a refusal with HTTP 200 and
`state: "session-collision"`, and the session-id heartbeat recorded any 200 as delivered and never sent the id
again, so a refused agent stayed without its conversation until a Confirm or a relaunch (external review,
2026-09-16: 1 PATCH in about 15 ticks). The heartbeat now re-offers an id answered `session-collision` every
tick (60 s), and the route takes it once the owner's heartbeat is stale. Both halves are tested:
`mcp/stdio/tests/a-refused-session-id-is-offered-again.test.js` and
`service/tests/test_a_refused_session_id_is_taken_once_its_owner_is_gone.py`. It reaches an agent only once
its bridge runs the new heartbeat, so an agent still on older bridge code needs the Confirm or a relaunch. The guard does NOT clear a dead owner's id when a live agent takes it over: the id is the link to a conversation, and a takeover can be the wrong party (the 2026-09-15 pane-inheritance launch), so that leftover stays for `aify-comms doctor`'s `session-handles` row and an operator's decision.

## Same-logical-owner supersession scope

**Decision.** `bridge_instances` supersession is scoped to `(agent_id, machine_id, runtime, session_mode, session_handle)`. A new bridge that re-registers an agent with the SAME tuple supersedes prior bridge instances for that tuple only — it does NOT supersede bridges for the same agent with a different session_mode (resident vs managed) or a different session_handle.

**Why.** Earlier supersession was scoped to `(agent_id, machine_id)` only. That triggered when a managed wrapper PTY registered for an agent whose resident bridge was alive — the managed registration superseded the resident bridge and killed its in-flight runs. Scope narrowing to the full logical-owner tuple lets resident and managed sessions for the same agent coexist when that's the intent (e.g. operator runs claude-aify resident while managed claude-aify PTYs handle dashboard dispatch).

## Superseded bridges are blocked at claim time

**Decision.** When an agent re-registers, the server marks the old bridge instance as `superseded_by: <new bridge id>`. The `/dispatch/claim` endpoint rejects claims from any superseded bridge with `blockedBy: {reason: "bridge_superseded"}`. For Codex/OpenCode stdio bridges, claim also checks `runtimeState.bridgeInstanceId`; if a stale process keeps polling with an ID that is no longer current, claim returns `blockedBy: {reason: "bridge_not_current"}` before it can consume a queued run.

**Why.** Without this, an old `codex-aify` process that didn't exit cleanly would keep polling, keep claiming fresh runs, and keep failing them with its stale in-memory state — even though the code on disk had been updated and a new bridge was ready to handle the same work. Blocking old bridges at claim time makes re-register a definitive handoff. The `bridge_not_current` guard covers the edge case where the old bridge's row has disappeared or cannot be classified as superseded, but the agent's current runtime state clearly points at a newer bridge.

The old bridge stays alive and keeps polling (that's fine — polling is cheap) but can no longer steal work.

## Bridges self-heal on persistent failures

**Decision.** The stdio bridge retries transient HTTP errors up to 3 times with exponential backoff (250ms → 500ms → 1s), and auto-re-registers an agent from its cached state when either (a) the server returns `404` on `/agents/{id}` or `/dispatch/claim` for that agent, or (b) 4 consecutive claim attempts fail for any reason.

**Why.** The most common "stale bridge needs manual re-registration" symptom has two root causes: a transient network blip that the old code didn't retry, and the server legitimately forgetting about the agent (via `comms_clear`, an operator DELETE, or a DB rotation) with no way for the bridge to notice. The first is handled by retries. The second is handled by treating a 404 as "re-register from what I remember" rather than silently polling a dead `agentId`. Both paths use the `REMOTE_AGENT_STATE` cache that already existed — no new state introduced.

**Retry is method-whitelisted to prevent duplicate side effects.** `GET`, `PATCH`, and `DELETE` are always retried because they are idempotent by design. `POST` is only retried on a narrow whitelist of known-idempotent endpoints: `POST /agents` (INSERT OR REPLACE), `POST /agents/{id}/heartbeat`, and `POST /channels/{name}/join`. `/messages/send` is retried only when its body carries a `clientNonce`, which the service collapses to the original message. Other non-idempotent POSTs — `/dispatch`, `/dispatch/claim`, `/dispatch/controls/claim`, a nonce-less `/messages/send`, `/channels/{name}/send` — fail fast on the first transient error and surface the error to the caller. Without this restriction, a connection that drops mid-response after the server has already processed a `/dispatch/claim` would retry and claim a second run, leaving the first one orphaned in `claimed` state.

**Limits.** Auto-re-register only works if the bridge has a cached registration for the agent (i.e. it was registered at least once in this process). If the bridge starts up cold against a server that doesn't know about the agent, there's nothing to re-register from — the caller still has to do the first registration manually. Auto-re-register also cannot recover agents that failed their *first* registration attempt, since no cache entry exists yet.

## Bridges coerce `http://localhost` to `http://127.0.0.1` before fetching

**Decision.** Both `mcp/stdio/claude-channel.js` and `mcp/stdio/server.js` apply a `coerceLoopbackToIPv4` normalization to `AIFY_SERVER_URL` / `CLAUDE_MCP_SERVER_URL` and to every fallback URL in `SERVER_URLS`. Any `http://localhost[:port][/path]` is rewritten to `http://127.0.0.1[:port][/path]` at the point of use. Wrapper-generated MCP configs also emit `127.0.0.1` directly instead of `localhost`. The coercion is universal, not Windows-gated.

**Why.** Docker Desktop on Windows reports IPv6 port bindings (`docker port` shows both `0.0.0.0:8800` and `[::]:8800`) but its IPv6 port forwarding is unreliable in practice — connections to `::1` hang silently. Windows resolves `localhost` to IPv6 `::1` first, so node's `fetch()` and curl both hit the broken path. Every `/dispatch/claim` poll aborted at the bridge's `HTTP_TIMEOUT_MS` (20s), no run was ever claimed, no `notifications/claude/channel` was ever emitted. Symptom looked identical to a channel-registration bug, a wrong-allowlist bug, or a queue-routing bug — but the actual blocker was network-level. Confirmed live: `curl http://localhost:8800/health` from host timed out at 30s while `curl http://127.0.0.1:8800/health` returned in 30ms.

Coercion lives in the bridges rather than only in the wrapper template because operators can override `AIFY_SERVER_URL` from their shell or `~/.claude/settings.local.json`. A wrapper-only fix would miss those cases; a bridge-level fix protects against any future config that says `localhost`. Linux/macOS resolve `localhost` to IPv4 `127.0.0.1` by default so the coercion is a no-op there.

Hindsight: when channel-routed dispatches sit queued forever, time `curl --max-time 5 http://localhost:8800/health` before chasing channel-registration or allowlist hypotheses. The IPv6/loopback bug shows up first.

## Wrapper session-mode is declared, not inferred

**Decision.** Every `*-aify` wrapper (`claude-aify`, `codex-aify`, `pi-aify`/`omp-aify`, `hermes-aify`) accepts explicit `--resident` and `--managed` flags. The wrapper reads `AIFY_SESSION_MODE` from its inherited env first; if unset, the flag wins; if neither, the wrapper auto-detects via TTY presence (`[ -t 0 ]`) — interactive launches default to `resident`, non-TTY launches default to `managed`. The wrapper exports `AIFY_SESSION_MODE=resident|managed` for its child `mcp/stdio/server.js`, which puts that mode into the `/agents` register call. For a managed worker the launch answer always sets `AIFY_SESSION_MODE=managed` (`service/api_core/launch_env.py`), so the inherited env wins regardless of TTY shape.

**Why.** Earlier, session_mode was guessed from registration context (no session_handle ⇒ managed; with handle ⇒ resident). That inference broke when bridge-spawned PTYs (which run inside a node-pty allocated TTY) auto-detected as `resident` and registered as resident, then collided with the real resident bridge for the same agent. The collision triggered scope-mismatched supersession and killed in-flight runs. Making mode an explicit declaration removes the ambiguity at the wire.

**Why TTY auto-detect as a fallback.** Operator-launched wrappers (the human types `pi-aify --aify-agent ...` in a terminal) almost always want resident. Container/bridge-spawned wrappers want managed. TTY presence is the single Unix-shell signal that distinguishes those cases and works identically on Ubuntu bash, macOS, and Git Bash for Windows.

**Why claude-aify always exports `AIFY_CHANNELS_ENABLED=1`.** claude-aify is the channels-aware Claude wrapper. Server-side, `runtime_config.channelEnabled=true` is the precondition for `_row_capabilities` keeping resident-run/interrupt/steer caps; without it the strip reduces caps to just `managed-run/resume` and preflight rejects live sends. Declaring the channel-enabled flag at register time removes the manual DB patching that earlier sessions needed.

## claude-aify loads the operator's full MCP list; strict mode is opt-in (2026-05-25)

**Decision (2026-05-25).** `install_claude_wrapper` (`install_claude_wrapper` in `install.sh`) no longer forces `--strict-mcp-config` on the spawned `claude` process. Default behavior is now "let claude load the operator's full `~/.claude.json` mcpServers list" — which already contains `aify-comms` and `aify-comms-channel` because `install_claude_config` merges them in at install time. The strict two-server temp config and `--strict-mcp-config` flag are still emitted, but only when the launching shell sets `AIFY_CLAUDE_STRICT_MCP=1`.

**Why.** The legacy always-strict behavior was a workaround for upstream Claude Code MCP init race (issues #38462 / #21341): when many stdio MCP servers compete for init, slower ones (including `aify-comms-channel`) get stuck in "still connecting" state and channel notifications never deliver. The workaround shipped channel-wake reliability but cost operators visibility into every *other* MCP server inside the wrapper — `aify-project-graph`, `github`, `browsermcp`, etc. were silently missing from claude-aify sessions even though their Skill files propagated. Operator pain over weeks ("why can't I use my MCP tools in claude-aify?") outweighed the residual race risk, especially since many operator setups don't trigger the race at all.

**Residual risk.** When the init race re-bites, channel-routed dispatches stop reaching the model — they queue at the service and never surface. Symptom: `comms_send` returns success, the dashboard shows a queued turn, but the receiving claude-aify session never wakes. Recovery: set `AIFY_CLAUDE_STRICT_MCP=1` in the launching shell and relaunch the wrapper. The two-server config restores guaranteed channel wake at the cost of the other MCP servers.

**Reconsider if.** Upstream Claude Code fixes the MCP init race (#38462 / #21341 close) — at which point the env-var escape hatch becomes dead code and can be removed. Until then, keep both branches.

## Resident codex: the wrapper's app-server, an exact binding, native paths, and self-healing threads

- **Delivery uses the wrapper's own app-server, not a separate channel process.** `codex-aify` runs a
  local `codex app-server` and publishes it as `AIFY_CODEX_APP_SERVER_URL`; the bridge claims resident
  codex runs through `/dispatch/claim`, and `CodexLegacyController`
  (`mcp/stdio/controllers/codex-legacy-controller.js`) issues `turn/start` on the resident's thread over
  that WebSocket. Codex has no equivalent of `notifications/claude/channel`, so a `codex-channel.js`
  would only duplicate that client. Reconsider if codex ships a notification primitive that needs its
  own MCP server entry.
- **Registration binds an exact wrapper.** Claude falls back to any live `claude-aify` on the machine,
  because its wake is process-level; codex binds one specific app-server URL, so with live markers for
  several cwds the bridge refuses to pick one and registers `message-only` unless the caller passes
  `sessionHandle` and `appServerUrl`. Multi-tab codex therefore registers each tab with
  `sessionHandle="$CODEX_THREAD_ID"` and `appServerUrl="$AIFY_CODEX_APP_SERVER_URL"`.
- **Runtime markers are written by the long-lived bridge** (`claude-channel.js`, and `server.js` for
  codex), never by the bash wrappers: on Git Bash `$$` is an MSYS pid Windows cannot see, so a
  wrapper-written marker was deleted as dead within a second.
- **The request cwd follows the connection type** (`resolveCodexRequestCwdFor` in
  `mcp/stdio/codex-errors.js`): with an `appServerUrl` it is a native host path (`C:/...` on Windows),
  because `codex-aify` always runs a native codex; without one it keeps the launcher-derived transform,
  which gives `/mnt/c/...` for a `wsl.exe` launcher. Sending `/mnt/c/...` to a native Windows codex was
  the root cause of the `AbsolutePathBuf deserialized without a base path` failures.
- **The service refuses impossible combinations** for a resident codex registration carrying an
  `appServerUrl`: a drive-letter cwd from a `linux:` or `darwin:` machine, or a `/mnt/...` cwd from a
  `win32:` machine. Before the guard such a record looked healthy until its first dispatch failed deep
  inside codex.
- **A thread codex cannot resume is replaced, not retried** (`detectCodexResumeFailure`): on
  `AbsolutePathBuf`, `no rollout found`, or a websocket frame-limit error the bridge starts a new
  thread, reports the new handle, and runs the dispatch there. For a resident session the new thread is
  not the one in the visible TUI, so the work runs where the operator cannot see it; that was judged
  better than failing every dispatch until a manual reset.

The full reasoning for each point is in [docs/history/DECISIONS-archive.md](docs/history/DECISIONS-archive.md).

## Worker-death cleanup is keyed on terminal STATE, not on a death EVENT (2026-08-07)

**`_finalize_spawns_with_dead_terminals` asks "is this spawn's terminal in a terminal status?" rather than trusting any death path to notify it.** That is the whole point of the reconciler, and it is a correction of the shape that came before it.

`report_terminal_dead` already finalized the owning `spawn_request`, with a comment explaining exactly why that matters (a `running` spawn with empty `finished_at` reads as "worker mid-boot" for 5 minutes, so the dead worker suppresses the very respawn its death requires). But `report_terminal_dead` is **one of ~26 sites that write `terminal_sessions`**, and on 2026-08-07 it was never called: `term_1786109794427_0f32fd75` reached `stopped` at 13:37:39 carrying no `console_dead_reported` event and an empty `error`, so a different path stopped it. The spawn then sat `running` for **97 minutes** and was cleared only by an unrelated "superseded by a newer live managed session" — i.e. by its eventual replacement, not by any reaper.

`_fail_orphaned_running_spawn_requests` could never have caught it either, and is right not to: it skips any spawn whose claiming bridge is a currently-ONLINE env bridge, and the env bridge stayed online. Only the worker died.

**So: event-based cleanup is only as complete as the last developer's memory, and state-based cleanup cannot be defeated by adding a 27th way for a terminal to die.** When a cleanup must hold for *all* paths into a state, key it on the state.

The cost is that a state-based sweep can race a legitimate transition, so the guards are where the care went: it leaves the spawn alone if ANY terminal on the same session is still live (the rebind race — a respawn creates the new terminal before the session is re-pointed), it requires the death to be older than `SPAWN_DEAD_TERMINAL_GRACE_SECONDS`, and an undeterminable death time is treated as too fresh rather than as old. See `service/tests/test_spawn_dead_terminal_finalize.py`.

## A session rotation may only adopt a terminal younger than the spawn that ordered it (2026-08-03)

**A managed respawn's bridge can create the console/TUI terminal a few seconds BEFORE the `running` transition mints the new session**, leaving the live terminal bound to the about-to-be-ended session and the new session with `terminal_id=''` — dashboard reads "Console not started" over a live TUI, and the terminal row hangs off an ended session where a FK cascade could later drop a running TUI's tracking. The rotation therefore re-points the agent's freshest live, same-bridge terminal onto the new session (2026-05-31). **That adoption is now bounded: the terminal must not be older than the spawn request that ordered it.**

*Why the bound.* "A few seconds before" was the intent but was never expressed as a constraint, so the match also accepted the PREVIOUS generation's terminal — and on a **Restart** the freshest live terminal is precisely the one being killed. Live on `ef-manager` (2026-08-03): the rotation adopted a terminal 10h16m older than its spawn request; the restart's own stop landed one second later; the run-closing sweep, which keys on the CURRENT session's terminal, then failed the replacement's queued brief; the spawn died on "Initial brief failed" and the reaper killed the surviving sidecar as a headless orphan. **Every dashboard Restart destroyed the brief it existed to deliver**, and it read as intermittent only because a separate cold-start produced a worker minutes later.

*Why this layer.* The sweep is correct about what it sees — it was handed the wrong terminal. Fixing the sweep instead was tried (`0b948d2`) and made the failure faster and more certain; it was reverted (`70e03aa`). The bound is sound because both timestamps come from `_now()` on one clock and a terminal produced by a respawn cannot predate the spawn request that ordered it, and it is safe because it still admits the entire legitimate window (claim → create terminal → PATCH running); a row with no `created_at` still migrates, so the 2026-05-31 rescue is never silently disabled. `_now()` is second-resolution, which the bound tolerates: the worker a restart replaces is by construction older than the restart replacing it.

**A different bug with the same symptom, fixed 2026-08-07:** a restart's brief *claimed* by the dying sidecar was failed before recovery could requeue it. `_requeue_instead_of_failing_undelivered_claim` (`service/api_core/recovery_writes.py`) now requeues an undelivered claim, up to `UNDELIVERED_CLAIM_REQUEUE_LIMIT` times. Tell the two apart by `claimed_at`.

## 2026-07-26 — A queued terminal `stop` is exempt from the liveness sweep (and the rule lives in TWO places)

`stop_agent_worker` marks a real terminal `'stopping'` — deliberately transitional, because the stop
is only QUEUED and the host has not acknowledged it — and appends the `action='stop'` terminal
control in the same transaction. Two reconcilers then fail pending controls whose terminal is
`NOT IN ('starting','attached','running','active','idle')`, and `'stopping'` is not in that set. Both
run on timers while the bridge polls every ~3s, so whenever a sweep won the race it **cancelled the
very stop meant to kill the process**: the PTY survived a "successful" Stop worker, and 900s later
the `STUCK_STOPPING_GRACE_SECONDS` reaper wrote `'stopped'` over it — a row asserting a death that
never happened. Not theoretical; the live DB held 158 controls failed with exactly
`terminal is not active`, so the sweep fires against real traffic.

**Decision: `action='stop'` is never failed on liveness grounds.** Killing a process is idempotent
and stays desirable on a dead-looking row — `server.js` carries an orphan-pid fallback for exactly
the case where no bridge owns the PTY in memory any more. Everything else still fails fast, which is
the point of the sweep: keystrokes into a console that is gone cannot be honoured and the caller
should learn that instead of hanging.

**Rejected alternative:** adding `'stopping'` to the active set. The pre-existing VIRTUAL-terminal
path marks `'stopped'` and queues its stop together, so it had the identical exposure through a
different status. Exempting the action covers both; widening the status set covers only one.

**INVARIANT — this rule is implemented TWICE and both copies must carry the exemption:**
- `service/reconcilers/terminal_runs.py` → `_reconcile_ended_terminal_controls` (plus the
  `exclude_actions` parameter on `_fail_pending_terminal_controls`, because a terminal holding an
  input AND a stop is still selected on account of the input, and the helper would otherwise take
  the stop down as collateral);
- `service/reconcilers/terminal_controls.py` → `_reconcile_terminal_controls` (moved out of `service/db.py`, which still calls it).

They share the predicate and the error string. The first fix landed in `api_v2` only and **changed
nothing**, because the second copy (then in `db.py`) still cancelled the stop — found in self-review, not by the
suite. `service/tests/test_stop_control_survives_reconcile.py` now drives BOTH paths explicitly so
they cannot drift apart again.

Accumulation stays bounded: the env-currency sweep in `_reconcile_terminal_controls` still fails controls whose
environment/bridge is no longer current, stop included, so controls for a dead environment do not
pile up forever.

## Dashboard Next is the only dashboard (2026-07-15; supersedes 2026-06-30)

The operator explicitly retired the legacy monolith. `service/new_dashboard/` on `:8801` is the
only dashboard implementation; `service/dashboard.html` and its legacy-only tests are removed.
The API root plus `/api/v1/dashboard{,/dispatches}` remain compatibility redirects so existing
bookmarks and `comms_dashboard` calls converge on Dashboard Next rather than breaking. Shared
behavior still belongs server-side where appropriate, but client changes are made only in
`service/new_dashboard/`.

## Dashboard actions use data attributes and one dispatcher, not interpolated JavaScript

**Decision.** Dynamic dashboard buttons carry a `data-action` attribute and their values as data attributes, and one delegated click dispatcher (`service/new_dashboard/click-dispatch.mjs`) routes them. Nothing interpolates agent IDs, run IDs, subjects, or channel names into an inline `onclick` string.

**Why.** Agent IDs and message subjects can contain characters that are safe as data but unsafe inside a hand-built JavaScript string literal. The previous pattern caused broken buttons such as Follow up and Continue as when a value introduced a quote or unmatched escape. Data attributes keep dynamic values as data, never as code.

**Consequence.** Button behavior does not depend on display text, and a value containing a quote cannot break a handler.

## Home is an operations queue, not the audit log

**Decision.** The dashboard Home page highlights live blockers, pending handoff repairs, failed spawns, and failed/cancelled runs, but reviewed historical failures can be dismissed locally from Home. Runs, spawn requests, and event history remain in their dedicated audit views.

**Why.** A control-plane homepage becomes useless if old, already-understood failures permanently look urgent. Operators need a current work queue first, with audit detail one click away.

**Consequence.** Dismissal is a browser-local presentation choice. It does not delete messages, runs, spawn requests, sessions, or artifacts.

## 2026-07-28 — The Sessions list collapses an agent's superseded rows, CLIENT-side

**Decision:** the Sessions list shows at most one entry per agent — every LIVE row, or if none are
live the newest non-live row — and the collapsed count is a toggle that reveals the rest.
Implemented in `service/new_dashboard/sessions-list.mjs`, applied at the list render, NOT in
`GET /sessions`.

**Why.** Operator report: *"i see multiple sc-manager sessions… for me i know only one sc-manager.
this one identification is one specific agent / session for me… seeing 2 makes me misunderstand."*
Measured: sc-manager had 10 rows in `agent_sessions`, exactly ONE live. The server already hides pure
history (`SESSION_CLEAN_HISTORY_STATUSES`), so 8 were suppressed; what reached the dashboard was the
live row plus a `stopped` row from eight weeks earlier.

**Why not narrow the server response.** `stopped`/`failed`/`lost` are served deliberately — hiding
them once broke `comms_restart`, `comms_compact` and the drawer's Restart/Reset/Compact, because a
non-live session is exactly what those act on (see the entry on `SESSION_CLEAN_HISTORY_STATUSES`).
Checked the consumer BEFORE writing the filter: `comms_restart` resolves with
`sessions.find(live) || sessions[0]`, so it prefers the live row — which this never removes — and
falls back to newest only when nothing is live, where the filter is a no-op. Client-side also keeps
`state.sessions` complete, which `state.terminalOwners` and `sessionForAgent` depend on.

**Two live rows for one agent stay VISIBLE.** That is a duplicate-worker leak, a class this repo has
been bitten by, and collapsing it would make the dashboard hide a real fault. Only dead rows collapse.

**Nothing is hidden silently.** `countSupersededSessions` feeds a "N older sessions collapsed — show"
toggle. The first cut had a static note claiming the rows were "still on the agent's History view";
that was FALSE (`data-agent-history` opens `openCompactionHistory` — compaction history, not
sessions), and since "Delete session" is only offered on a visible row, collapsing them removed the
only way to delete an older session. The toggle exists because of that.

## 2026-07-28 — Queueing a chat message is a per-send act, never a sticky mode

**Decision:** the composer's `Queue if busy` checkbox is REMOVED. Queue is the second half of a split
Send button and passes an explicit per-message flag; Enter and Send are always an ordinary
steer-if-possible send.

**Why.** The checkbox was never reset after a send and lived inside the collapsed Options disclosure,
so one tick silently queued every LATER message. The operator hit exactly that: *"what does ordinary
pressing enter do? it should steer / ordinary send, not queue. message was queued."* A test already
asserted the checkbox was "not checked by default", commented *"Queue is an explicit operator
choice"* — the right intent, defeated by the mechanism, because a default only ever constrains the
FIRST send. A per-send choice must not have a persistent mode.

**Rejected:** surfacing the checkbox state instead of removing it. That keeps a hidden mode and adds
an indicator to compensate for it.

## The unread-notification hook is opt-in and fires after every tool call

**Decision.** `install.sh --with-hook` installs `notify-check.js` as a post-tool hook: `PostToolUse` with matcher `.*` on Claude and Codex (`install_claude_hook`, `install_codex_hook`), and `post_tool_call` with matcher `.*` on Hermes (`install_hermes_hook`). Once installed, a later `install.sh` run refreshes it even without the flag (`scripts/hook-installed.sh`). OpenCode has no hook. The script reads the inbox with `peek`, so it never marks a message read; it rate-limits itself to one inbox check per agent every 10 seconds and sends a liveness-only heartbeat; it never sets `turn_busy`.

**Why every tool, not `Bash`.** The matcher was once `Bash`, so an agent working only through Edit/Read/Write never checked its inbox. The rate limit bounds the cost of firing on every tool.

**Consequence.** An agent without the hook, or one that runs no tools, learns of unread messages only from `comms_inbox`; agents should still check it at natural points (start of a task, between major steps).

## The service key is opt-in, and the operator key is not a substitute for it (2026-08-18, revised 2026-08-30)

**Decision: `API_KEY` stays opt-in; `OPERATOR_KEY` is the privilege credential.** With no `API_KEY`,
`APIKeyMiddleware` is never installed (`service/main.py` adds it only `if config.api_key`), so every
route is open to whoever reaches the port, and `cors_origins` defaults to `*`. That is the operator's
choice for a host that is their own machine, not an oversight.

**Setting a key no longer costs the dashboard.** The first version of this entry said an `API_KEY`
would 401 the whole dashboard, because a browser cannot send `X-API-Key`. Since 2026-08-30 a
navigation to `/?api_key=<value>` is exchanged for an HttpOnly, SameSite=Lax cookie
(`APIKeyMiddleware.COOKIE`), and `install.sh --with-api-key` generates a key, writes it to `.env`, and
passes the same value to every wrapper and MCP config, reusing an existing key rather than rotating
one. Every bridge sends that one shared key, so it proves "inside the trust boundary" and can never
tell the dashboard from an agent. A browser request made from a page on another site is refused
whatever the key (`CrossSiteBrowserMiddleware`, keyed on `Sec-Fetch-Site`).

**What `OPERATOR_KEY` does and does not do.** Added for R5-H1: until 2026-08-18 an actor string of
`operator` or `dashboard` was enough to unsend any message, delete any channel and unshare any
artifact. It now requires `X-Aify-Operator-Key`. That raises the bar from "type an English word" to
"hold a secret" — which stops the casual, the confused and the prompt-injected case. It is NOT a
boundary against an agent with filesystem access: `.env` is readable on the host, and the dashboard
page carries the key to the browser. Anyone reading this should not treat it as one. Since 2026-09-24
the service generates one when `.env` sets none (see "A key per other machine" below).

**The honest summary:** authorization is *auditable and non-trivial* rather than *absent*. It is not
authentication. The deployment's real perimeter is that the host is the operator's own machine.

## 2026-09-24 — A key per other machine, and an operator key that exists without being asked for

**A key per other machine.** `EXTERNAL_KEYS=pc2:<key>,laptop:<key>` issues each other machine its
own key, named by the operator (its machine name is the obvious choice). Until now an agent on
another PC sent here with the shared `API_KEY`, which let it do anything a local agent can, send as
any local id, and left the service trusting whatever it wrote about where it was. A request carrying
an external key now:

- is **proven** to come from that machine: the service stores the key's name on the message
  (`messages.external_machine`, shown as `externalMachine`) beside the sender's declared `origin`.
  Every reader states the machine as a fact and still quotes the origin as a claim: the inbox and feed
  on both transports, the dispatch claim, a steer injected mid-turn, console delivery, the reply hint
  and the dashboard chip.
- opens **only** routes declared with `EXTERNAL_ROUTE` (today: `POST /api/v1/messages/send`). The
  allowed set is derived from that flag on the route, not listed anywhere else. Everything else answers
  403 naming whose key it is: the roster, registration, other agents' inboxes, consoles, spawns,
  deletes, `/mcp`. WebSockets accept the service key alone.
- **cannot send as anyone who lives here**, whether a registered agent or one of the service's own
  voices (`dashboard`, `aify-comms`). Names are compared case-insensitively, as the rest of the
  service compares them.
- **owns the names it uses**: the first machine to send as an id keeps it, so pc2 cannot send as the
  laptop's `lap-mgr` and take over where its replies go. The reply hint takes the declared address
  only from messages that machine's key carried. Without that, a caller holding the ordinary service
  key could redirect a proven sender's replies while the hint still called the machine "proven".
- **stays external**: a proven message is labelled external even if an agent of that name registers
  here later, because registration says a name exists here, not who sent this message. The dashboard
  draws it as `from pc2 · by key`. A claimed origin is always drawn as `external: <text>`, so no text
  a sender writes can reproduce the proven chip.

A malformed, short, duplicated or reused (`API_KEY`/`OPERATOR_KEY`) entry grants nothing, is logged
by name without its key, and never stops the service. With no `API_KEY` there is no authentication to
restrict, so the keys do nothing. The startup log says so, and `/health` reports
`externalKeys.enforced: false`, with counts and no machine names, because `/health` needs no key.

Not done, deliberately: replying to the other machine. A reply is still stored here only, and the hint
now names the machine to send it to. Relaying needs this service to hold the other one's endpoint and
key, which is a peers feature. An external message with `trigger` wakes a local agent exactly as a
local sender's does, which is the point of it.

**An operator key that exists without being asked for.** `OPERATOR_KEY` was never set by anything;
`.env.example` asked for `openssl rand -hex 32` by hand. So on most hosts the dashboard's delete
controls refused, and the service could not tell the operator sending **as** an agent from the agent.
When `.env` sets none, the service now generates one at startup into a small dedicated volume
(`operator-key` at `/keys/operator.key`, mode 0600). A fixed default was rejected because a secret
written in a public repo proves nothing. The dashboard container mounts that volume **read-only** and
injects the same key, and never creates one. The key has its own volume rather than the data volume so
the dashboard is not handed the database to read one file. Only the service writes it: it writes aside
and renames into place, and it replaces a file that is empty or too short to be a key. An adversarial
review found the first version could leave an empty file that disabled operator privilege silently
and for good. Any write failure is logged and leaves the key unset; it never stops the service.
`.env` still wins.

With a key always present, **a send carrying a valid operator key no longer counts as the agent being
present** (`_touch_agent(..., present=False)`). Sending as an agent from the dashboard's identity
picker had been hiding real absences from the away briefing, which was the open item left by PR #19.
The limit is unchanged: anything that can load the dashboard page can read the key, so it separates
the dashboard from the bridges, not from a determined local agent. **One real change on a host with no
`API_KEY`**: operator privilege used to be off there unless someone set the key by hand, and it is now
on for anyone who can load the dashboard. That is small beside what such a host already allows
(anyone who reaches the port can type into consoles), but it is a change. Setting `API_KEY` is still
the answer to "who may reach this at all".
