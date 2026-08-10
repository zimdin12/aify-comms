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
| **`comms_agent_info` answers about the wrong thing** | Reported with a trace: during an outage every field stayed true and none was about *production*, so a team reported a lane dead three times while a reply sat undelivered. Ask: a DEGRADED/STALE marker when the delivery path is unverified, plus an outbound-activity field. |
| **Repair the 183 corrupted artifacts** | Script written, dry-run verified, **operator's call** — 183 files is a destructive batch and the forward bug is already fixed. |

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

- **B1 — bridges report their build stamp on heartbeat.** `bridge-running` and `agent-identity`
  both SKIP on Windows, so nothing verifies a *running* wrapper executes current code. This bit me
  twice today: once recording a working fix as broken because my own bridge was stale. Cheap and
  platform-independent; unscheduled only because nothing has failed *because* of it.
- **`rename_agent`'s `had_live_bridge`** has no freshness predicate. Advisory note only, no state
  damage. Promote if a rename ever emits a false "live session orphaned".
- **Two dead branches** in `api_v2.py` matching receipt summaries that D2/#162 emptied. Measured:
  the exclusion subtracts 0 from 69. Redundant guards against a removed producer.
- **SSE renderer has no unit test.** The search bug existed in both transports; I fixed one and
  believed I was done. Reviewer's note — worth a parity check if that function stays transport-local.
- **`[MSG NEW]`-class markers elsewhere.** One was always-on because the data behind it never
  existed. Worth asking where else that pattern hides.
- **Thread-closure pilot.** Reviewed and ready, **not running** — it needs operator adoption, and
  `sc-manager` applies it, not me. Roughly half of that team's threads stop with no recorded
  outcome and we cannot tell abandoned from concluded.

## Declined, with reasons

`bridge_instances` accumulation (disproven — deliberate carve-out, bounded), `aify-gwport-*` dirs
(cosmetic), container DEGRADED state and `interval_seconds` (real, but on a subsystem with zero
containers defined). See `V0.2_SPEC.md`.
