# Roadmap — what shipped, what is next, and why

Written 2026-08-10, after v0.3.0. Supersedes the forward-looking half of `V0.3_SPEC.md`, which
stays as the record of how that release was scoped.

## The rule everything here is filtered through

> **Anything that cannot name the artifact it retires does not get scheduled.**

With one amendment the operator was right to force: **structural work is judged differently.** A
refactor's artifact is *future* debugging cost — diffuse, but real and already paid here. The rule
governs bug work; structural work is judged on whether it makes the next fix cheaper.

Under this rule v0.2 dropped four of ten candidates, including one of my own claims that turned out
to be false. That is the rule working, not the rule being expensive.

---

## Shipped

### v0.2.x — reliability, mostly instruments that were lying

| version | what |
|---|---|
| v0.2.0 | An agent can read why its own worker died. Dead worker stops suppressing its own respawn (state-based, not event-based). |
| v0.2.1 | Restart's second path: recovery was not losing a race, it was being **lapped** — 1 call site vs 4, one of them on a dashboard-polled read path. |
| v0.2.2 | `aify-doctor` stopped failing on an OpenAI token that refreshes itself. |
| v0.2.3 | `aify-doctor` stopped demanding rebuilds for code the service does not execute — three false reds of one class, each found by the previous fix. |

### v0.3.0 — the operator can hear the fleet, and search stops lying

- **Desktop notifications.** Off by default, operator-addressed only, quiet while focused,
  coalescing bursts. Shaped around 3,883 messages/14d — a notification per message would have been
  switched off within the hour. Channel notifications are membership-gated and **fail closed**.
- **Optional HTTPS** (`docker compose --profile https up -d`). The Notification API needs a secure
  context and the LAN address the dashboard is actually opened at is not one. Opt-in; a plain
  `up -d` is untouched.
- **`comms_search` stops lying about absence.** An agent could not find messages it had **sent**
  (52 of 101 matching messages invisible to their author), and omitting `agentId` silently searched
  artifacts only while reporting nothing — so an empty result read as proof of absence in a gate
  built to prevent duplicate work. Fixed in both directions and **both transports**.
- **Binary share corruption** (`4157299`, post-tag). Every binary upload gained a leading CRLF from
  an extra `\r\n` in our own multipart construction. The server was never at fault. 183 of 192
  stored artifacts are affected; a dry-run-first repair exists and has **not** been run.

---

## Next

### Immediately actionable

| item | why now |
|---|---|
| **Audit triage** | `comms-senior-dev` is auditing four areas: the health-surface family, SSE/stdio parity, the reconciler seam read-only, and an adversarial pass on the v0.3 code. Findings get triaged against the rule; survivors get scheduled here. |
| **Agent health surfaces cannot answer "what did this agent produce?"** | AUDIT FINDING 1, source-cited. See below — the fix is an outbound-activity field, and a DEGRADED marker alone would not retire the artifact. |
| ~~**Repair the 183 corrupted artifacts**~~ | **DONE.** 183 repaired, 0 still corrupted, 0 DB/disk size mismatches. Backups deleted only after `scripts/verify_crlf_repair.py` proved both facts below; manifest at `/data/crlf-repair-manifest.json`. |

### AUDIT FINDING 4 — adversarial pass on v0.3.0 + v0.3.1

`comms-senior-dev`, source review plus read-only DB probes. Two evidence-surface bugs, both fixed
in v0.3.2; one recommendation measured and declined; one pre-delete guard built and satisfied.

**F1 — `bridge-current` was green on no evidence. FIXED.** The check counts a live bridge that
reports no `bridgeBuild` as *unknown* and returned `ok` regardless, so `--strict` PASSED while
verifying nothing. Live-confirmed: this host's one online bridge reported no build. That is the
same false green as `env-bridge` counting registered rows (`756f3a5`) — reproduced inside the check
written to prevent that class. Split: *some* evidence with gaps stays `partial` (ok); **zero**
evidence is now `unknown-all` and fails. A check that cannot answer must not count as one that
answered yes.

**F2 — `Last produced: unknown` blamed the wrong component. FIXED.** The renderer collapsed "a
pre-v0.3.1 service omitted the field" and "a current service answered `{}`" into one line, so a
fresh agent on a current service was reported as the service not answering. `_agent_record_to_dict`
always emits the key, so key presence is the discriminator — no API change. Pinned from both sides:
the bridge test asserts the two never render identically, and a Python test asserts the service
never stops emitting the key.

**F3 — the index recommendation, DECLINED on measurement; my first phrasing of the decline was
too strong.** The reviewer measured the outbound run aggregate at 27.3 ms with a temp B-tree and
proposed a `(status, target_agent, finished_at DESC)` index. The dominant cost is the **fan-out**,
not the aggregate: same statement, 42 agents → 37 ms and a temp B-tree; a low-history single agent
→ 0.004 ms. The roster stopped calling it in `39e47ac`.

But "the surviving caller is already index-covered" was wrong, and the reviewer caught it:
`idx_dispatch_runs_target_status` does not include `finished_at`, so the single-agent cost scales
with **that agent's** history — `sc-claude` (3,109 runs) 3.84 ms, `sc-manager` (7,383 runs)
**13.19 ms**. Still declined, on the real distinction: 13 ms on a deliberately-opened detail view is
fine; 13 ms on a 2-second poll across 42 agents is the lock class. Reversal conditions are recorded
in the source — run detail returning to a hot path, or a latency target on a heavy agent's detail
view.

**F4 — do not delete the CRLF backups on the artifacts alone. SATISFIED, then deleted.** Correct:
from a stripped file you cannot distinguish injected framing from a legitimate leading CRLF.
`scripts/verify_crlf_repair.py` establishes the two facts that can:

- **Provenance** — the newest repaired artifact was shared `17:46:24Z`; the fix (`4157299`) was
  committed `18:12:44Z`. Every repaired artifact predates the fixed code by at least 26 minutes, so
  no bridge could have produced a *correct* leading CRLF. This is what retires "every stripped
  `0d0a` was framing" — the artifact itself never could.
- **Derivability** — 183/183 backups satisfied `backup == b"\r\n" + repaired` exactly, so each was
  reconstructible from the file beside it. Deleting destroyed no information rather than merely
  being an acceptable risk.

**Non-findings the reviewer cleared:** the notification coalesce map does not grow on a quiet tab
(entries are only inserted on a firing path, and pruning runs on every fire); the Caddy/HTTPS
profile has no source-level blocker; `transport-parity.test.js` declares an inventory rather than
overclaiming parity; the share multipart test guards construction only, as recorded.

### AUDIT FINDING 1 — health surfaces answer about the wrong path

`comms-senior-dev`, source-only review at `22521f6`, every claim cited.

**The trace.** During an outage `comms_agent_info` kept answering normally, so a manager told the
operator three times a lane was dead. It wasn't — the reply sat undelivered. Every field was
individually true and none was about *production*:

| field | what it actually answers |
|---|---|
| `unread` | inbound messages not yet read — **the wrong direction** |
| `last read` | last message the agent **consumed** |
| `last seen` | registration/heartbeat liveness — and `PATCH /agents/{id}` advances it, so a status-note write alone moves it |
| `status` | worker reachability / dispatch state, not outbound productivity |
| dispatch state | runs **targeting** the agent, not what it sent |

**It corrected my assumed fix.** I had taken the reporter's ask — a DEGRADED/STALE marker — as the
answer. The dev's argument is better: a STALE marker retires a *different* artifact ("delivery path
verified") and still cannot say what the agent last produced. **The required fix is an
outbound-activity field** (`lastSentMessage` / `lastCompletedRun`, from `messages.from_agent`);
DEGRADED becomes supporting, not the whole fix.

**Same family, also cited:** `comms_agents` roster (worse — no last-read at all),
`comms_status`/`PATCH /agents/{id}` (writes `last_seen`, so a status update looks like liveness),
`_delivery_failure_hint` (recommends `comms_agent_info` for exactly the diagnosis it cannot make),
and `comms_run_status` (fine for a known run, wrong as a fleet-health proxy).

**Non-finding worth keeping:** `comms_contracts` already joins runs to read receipts and result
messages and exposes sent/seen/queued/working/answered/missing_reply. The move may be for
`agent_info` to summarise or point at it rather than grow a parallel surface.

### AUDIT FINDING 2 — SSE/stdio parity: 20 duplicated tools, 9 divergences

`comms-senior-dev`, source-only, every divergence cited. Motivated by `comms_search` existing in
both transports where I fixed one and believed I was done.

**Highest wrong-belief risk:**

- **`comms_agents` is weaker in SSE** — no runtime or wake-mode context, so "online + unread 0 +
  advancing lastSeen" is *easier* to misread as a healthy lane. Same family as finding 1.
- **`comms_register` builds a different identity.** SSE takes only agentId/role/name/cwd/model, so
  "registered" over SSE can mean a coordination row, not a wakeable runtime-bound session.
- **`comms_share` cannot upload binaries over SSE at all** — which bounds any transport-integrity
  claim we make: the CRLF repair covers the stdio `filePath` path only.
- **`comms_send`/`comms_channel_send` differ on live delivery** — SSE exposes `silent`, stdio
  always triggers; stdio mints a `clientNonce` for retry safety and SSE does not.
- **`comms_dispatch` has no `priority` in SSE**; **`comms_run_status` omits started/finished and
  thread identity**; **`comms_console_tail` advertises the dead-worker fallback in stdio only.**

**Non-finding:** `comms_search` is now parity-repaired in both renderers — the motivating bug is
genuinely closed.

**Recommendation, and it matches what worked before:** do **not** consolidate the transports —
different languages, and SSE is *intentionally* reduced. Add cheap parity gates instead: a
generated tool-inventory snapshot with an allowed-reduced list, golden renderer-agreement tests for
the tools that turn API JSON into conclusions, and payload-construction parity tests that name
intentional divergences as allowed. Agreement tests over consolidation — the same call that worked
for the duplicated status predicates.

### AUDIT FINDING 3 — the reconciler seam, before the extraction

`comms-senior-dev`, read-only, with two read-only live DB probes.

**Blocker 1 — FIXED (`4317117`).** `_replay_undelivered_channel_messages_on_env_recovery` gated on
`datetime(m.timestamp)` where the column is epoch **milliseconds** — SQLite returns NULL, so the
predicate was never true and **the reconciler had never fired**. Measured: 665 channel messages,
0 matched broken, 115 matched corrected.

Its survival is the interesting part: fixing it broke two existing tests, and the *fixtures* were
wrong, not the fix. They seeded ISO via `api_v2._now()` while production is integer epoch-ms in
**29,854 of 29,854** rows. The suite had been validating a shape that does not occur.

**Blocker 2 — REJECTED.** `GET /agents` calling `_repair_unusable_active_runs` is a documented
decision (DECISIONS.md, 2026-06-29): *"KEEP their read-path repairs… drives the roster status the
operator watches live."* Not silently reversed. The adjacent point stands as an extraction
**constraint**: a read endpoint will import a mutating reconciler once these move.

**Finding 3 — ACCEPTED, pre-move.** `_repair_spawn_requests_from_initial_dispatch_failures` matches
on target + time rather than an initial-message identity, so an unrelated earlier failed dispatch
could kill a healthy spawn. Source-grounded overbreadth, not an active incident. Fix before its
slice, or explicitly exclude it from the purity claim.

**Also recorded, not bugs:** five load-bearing sweep orderings in `main.py` that are real but
undeclared — they should become named phase comments plus a test.

### v0.4 — mobile alerts (ntfy)

One outbound POST; no PWA, service worker, VAPID or subscription table. **Blocked on writing the
queue contract first**, because the reviewer correctly refused "fire-and-forget with a bounded
timeout" — that phrasing permits awaiting an HTTP call on the send path. Required shape:
enqueue-after-commit, background worker, timeout **in the worker**, coalescing, redacted URL,
failure logged without failing the message.

### v0.5 — SHIPPED. The reconcilers are out of the router

`api_v2.py` **23,681 -> 20,545**. Ten slices, ~3,800 lines into **ten** reconciler modules under
`service/reconcilers/`, plus two new leaf helpers (`service/clock.py`, `service/env_status.py`) —
twelve files, not the "eleven leaf modules" an earlier draft of this line claimed.

**Honest wording, which the reviewer required and is the accurate claim:** reconcilers extracted;
**router borrows documented**. NOT "router dependency eliminated". The leaf layer still reaches back
for one liveness family and a handful of append/normalize helpers, each through a function-scope shim
reading exactly one owner — no copies, no drift, but a real remaining edge.

Three gates held on every slice: sweep-ordering (7 load-bearing pairs), import identity (AST), route
inventory (128 routes, unchanged end to end). Every slice carried a dependency scan BEFORE the move,
a verbatim scripted extraction, an undefined-name sweep, a cycle smoke test, and a readback over the
artifacts that reconciler actually mutates.

**What the process caught, which is the reason to keep it:** a call-only dependency scan missed three
constants and cost 372 red tests (slice 2) — the scan now walks every name; the undefined-name sweep
found something on five consecutive slices; and in slices 8+9 `engine_status` resolved to a
*plausible but wrong* function (`status_engine.derive` vs the router's DB-reading wrapper) which
compiled and passed the cycle smoke test. Only reading the router caught that one.

**DEFERRED, tracked, not forgotten:** 1b (status core) and 3b (liveness family). Both are "a small
function anchored to a large unmoved cluster".

### Post-v0.5 — the consolidation the borrows are waiting for

Reviewer-specified order: **liveness family first** (`_agent_liveness`, `_agent_has_live_terminal`,
`_has_live_channel_sidecar`, `_resident_bridge_is_fresh`, `_has_live_managed_wrapper_child`,
`_has_live_terminal_session`), **then** the append/normalize helpers (`_append_terminal_event` — 36
call sites — `_append_dispatch_event`, `_normalize_runtime`), **then** constants into leaf owners.

The 3b shim in `service/reconcilers/sessions.py` has a removal gate tied to the liveness step: it
must not survive that consolidation. A shim that keeps working is exactly how a deferral becomes
permanent.

### v0.5.3 — PREPARED, NOT YET TAGGED. The router monolith is decomposed, and the debt is visible

> **Status, stated because the reviewer was right to flag it:** the version files say 0.5.3 and
> the work is reviewed, but **no tag exists and nothing is deployed**. This heading said
> "SHIPPED" while both were untrue — release wording written in advance of the release, which
> is the same wrong-but-plausible-documentation class as the other three this series found.
> Remaining and all operator actions: `install.sh` per client plus wrapper relaunch (the bridge
> version changed, so `aify-comms doctor` fails `bridge-installed` until then), the container
> rebuild, and the tag itself.

`service/routers/api_v2.py` **20,545 -> `service/control_plane.py` 6,964 + a 53-line composition
module**. 103 route handlers in the old carrier -> **0**. Routes **124 -> 124**, snapshot byte-equal.

The route domains moved out (usage, settings, contracts, stats, shared, analytics, environments,
spawn_requests, channels, sessions, terminals, meta, maintenance, plus the `dispatch_messages` and
`agents` packages), all 18 movable borrows were retired by hand one per commit, and the leftover
helper library — which by then declared no routes at all — was renamed to what it actually is.
`service/routers/api_v2.py` is now nothing but `include_router` calls, with **no compatibility
re-export**, so a stale `from service.routers.api_v2 import <helper>` fails loudly.

**TWO NUMBERS WENT THE WRONG WAY, and the release says so rather than burying them:**

| | v0.5.0 | v0.5.3 |
|---|---|---|
| borrow shims into the carrier | ~131 | **275** |
| non-test source files >1000 lines | 5 | **12** |
| non-test source lines | 66,567 | 72,554 (**+9.0%**) |

Extracting a domain that still needs control-plane helpers *creates* shims; retiring 18 removed far
less than extraction added. And splitting one 20k-line file into domains cannot avoid producing
several files over 1000 lines — the seven new ones are all extracted domains. The growth is
docstrings, gates and explicit shims, not duplicated logic, but it is growth.

**The defensible claim:** the worst single file went from 31% of the repo to 9.6%, and the thing
that made this codebase expensive — 339 units in one file, every edit touching a 20k-line module —
is gone. **NOT** "the file-size rule is satisfied". It is not.

**What the process caught, which is again the argument for keeping it:** `_ANSI_RE` declared twice
in one module with *different* patterns (runtime was never wrong; the dead declaration sat four
lines above the function it appeared to govern, and the fork gate could not see it because its scan
collapsed duplicates into a dict); a silent-422 class where a moved handler lost its model import
and FastAPI demoted the body to a query param; route snapshots that were gitignored and so could
never run from a clean clone; and three more false-pass holes in the extract-method gate, all found
by running it on real code rather than reading it.

### v0.6 — the decomposition inventory this release makes visible

Not scheduled, and deliberately not smuggled into v0.5.3 as cleanup. The reviewer's ruling is that
these are v0.6 scope:

- **12 non-test source files over 1000 lines.** `service/control_plane.py` 6,964 ·
  `mcp/stdio/server.js` 6,330 · `service/new_dashboard/app.js` 5,081 ·
  `mcp/stdio/hermes-managed-host.js` 3,016 · `service/routers/dispatch_messages/dispatch.py` 1,715 ·
  `service/routers/agents/shared.py` 1,460 · `mcp/stdio/pi-session.js` 1,299 ·
  `service/routers/dispatch_messages/messages.py` 1,223 · `service/routers/agents/identity.py` 1,157 ·
  `service/routers/dispatch_messages/shared.py` 1,114 · `service/routers/sessions.py` 1,031 ·
  `service/routers/terminals.py` 1,020.
- **275 control-plane import lines** across 50 modules. Splitting `control_plane.py` by
  responsibility is the way that number comes down; retiring shims one at a time is not.
- **`_compute_live_status_cache` (551 lines)** is the largest single function left and is explicitly
  OFF-LIMITS until a separate hot-mutator plan exists. It is the process-global, single-worker
  live-status cache.
- **Further `get_analytics` splits** only under the approved loop-only / single-return dialect.
  Eight blocks came out in v0.5.3 (**314 -> 190 lines**): three message-bucket series and five
  list-builders. What is left is the window/filter setup and the response assembly, neither of
  which has a single-live-out seam — the reply-contract block alone produces two.

### Superseded — the original extraction plan

43 functions, 3,530 lines, 15% of `api_v2.py`. **10 slices**, sized to the reviewer's bound (5–8
functions or 400–700 lines; ≥200-line functions anchor their own). Slice 1 is status-cache +
bridges deliberately: smallest, and the one touching `_LIVE_STATE_CACHE`, so the import-identity
gate is proven on a slice cheap to abandon.

Empty behaviour changelog, its own release. Six gates per slice, including a live smoke replay and
an import-identity check that no second `_LIVE_STATE_CACHE`/`_LIVE_SCREENS` instance exists.

**Precondition:** the dev's read-only bughunt of that seam. Fix behavioural bugs *before* the move,
not after — a pure-move refactor would carry them forward silently.

---

## Carried, not scheduled

- ~~**B1 — bridges report their build stamp on heartbeat.**~~ **SHIPPED in v0.3.1** as
  `bridge-current`, and it has already earned itself twice: it caught the environment bridge running
  pre-restart code after an install, and its own first version was green-by-default (fixed v0.3.2).
  This list said "carried" for a day after it shipped — a stale roadmap entry reads exactly like an
  open item.
- **`rename_agent`'s `had_live_bridge`** has no freshness predicate. Advisory note only, no state
  damage. Promote if a rename ever emits a false "live session orphaned".
- **Two dead branches** in `api_v2.py` matching receipt summaries that D2/#162 emptied. Measured:
  the exclusion subtracts 0 from 69. Redundant guards against a removed producer.
- ~~**SSE renderer has no unit test.**~~ **DONE** — ten tests now drive the real tool functions
  (search scope both ways, inbox safety header, fence escaping, empty vs id-not-found, truncation
  disclosure, error surfacing).
- ~~**`[MSG NEW]`-class markers elsewhere.**~~ **SWEPT, nothing found.** `[NEW]`/`[read]` is
  grounded (`read_at is not None`; 2,176 of 29,960 messages genuinely unread), `[FAILED]`/
  `[CANCELLED]` come from terminal status, `[CRITICAL]` from quota severity.
- **Thread-closure pilot.** Reviewed and ready, **not running** — it needs operator adoption, and
  `sc-manager` applies it, not me. Roughly half of that team's threads stop with no recorded
  outcome and we cannot tell abandoned from concluded.

## Declined, with reasons

`bridge_instances` accumulation (disproven — deliberate carve-out, bounded), `aify-gwport-*` dirs
(cosmetic), container DEGRADED state and `interval_seconds` (real, but on a subsystem with zero
containers defined). See `V0.2_SPEC.md`.
