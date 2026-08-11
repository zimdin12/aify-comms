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

### Own release — extract the reconcilers

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
