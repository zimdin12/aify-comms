# aify-comms debug: The status model: labels, derive(), and what each state proves

## Status labels (proof-based 8-state model, 2026-06-18; `starting` + `misconfigured` added later)

**Principle.** Status is **PROVEN, not time-assumed.** The `*-aify` wrapper is the source of
truth for what the agent is doing (turn-start → `working`, turn-end → `online`,
awaiting-input → `blocked`) and beats a liveness heartbeat every ~30s. aify-comms reflects the
wrapper's signal verbatim and only ADDS the states the wrapper can't know about: `offline`
(heartbeat gone), `available` (managed, no live worker yet), `stopped` (operator hard-disable).
There are **no minute thresholds** and **no time-decay states** — the old `idle` (online-gone-
quiet) and `stale` (resident-only expired-lease) labels were removed because they ASSUMED a
state from elapsed time instead of proving it. Canonical reference:

| Label | Meaning |
|-------|---------|
| `working` | Wrapper reported a turn in progress (turn-start, not yet turn-end). Liveness-gated: a dead worker / gone heartbeat can't latch `working` — it falls to `offline`/`available`. |
| `online` | Live worker, no turn in progress (heartbeat fresh, wrapper between turns). This is the ready/idle state — operators rely on `online` as "ready for queued work". |
| `available` | Managed agent, env reachable, but NO live worker. Auto-starts a worker on the next send. `available` ≠ `online`: no worker yet, it boots one on send. |
| `blocked` | Wrapper reported the turn is awaiting operator input/a decision (a prompt/question, not healthy generation). Liveness-gated like `working`. |
| `offline` | Heartbeat gone — instant on a clean wrapper disconnect, otherwise within the no-heartbeat liveness window (`agent_liveness_seconds`, default 90s = 3× the 30s beat). Covers both managed (env bridge down) and resident (bridge lease lapsed). `offline` ≠ `stopped`: offline is "we lost the signal", stopped is "operator disabled it". |
| `stopped` | Operator hard-disabled the agent (wake-disabled, `launch_mode='none'`), or set by `resident-lost` on clean close of a **resident**. A deliberate down-state, not a lost signal. (A **managed** agent whose worker/gateway is lost is NOT stopped — it rests cold-startable → `available` and re-spawns on the next send.) |
| `starting` | A spawn has been CLAIMED and its worker has not appeared yet. No live worker, but one is on its way. **Do NOT restart or re-send** — a restart at this moment kills the boot in progress. A send still queues and is delivered when the worker arrives. Bounded: past the spawn-in-flight window an agent that never produced a worker falls back to `available`, so `starting` can never hide a broken spawn indefinitely. |
| `misconfigured` | The identity exists but can never start — the configuration itself is unusable. Not send-recoverable and not a transient: a human must fix the config. Sending will not work and re-sending will not help. |

Managed lifecycle: `available` → `starting` → `working` ⇄ `online` (+ `blocked` mid-turn, `offline`
when the heartbeat lapses, `stopped` on hard-disable, `misconfigured` when the identity can never
start at all). Resident lifecycle: `working` ⇄ `online`
(+ `blocked`, `offline` when the bridge lease lapses, `stopped` on clean close). `blocked` is
a sub-state of an in-turn agent (not a separate down-state): the turn is live but the wrapper
reports it as awaiting operator input rather than healthy generation. Key distinctions:
`available` ≠ `online` (no live worker yet — it boots one on send); `offline` ≠ `stopped`
(lost-signal vs operator-disabled — kept separate deliberately).

## `derive()` is the SOLE status authority — the `status_engine` flag is GONE (2026-06-18)

**Context.** The `status_engine: old|new` setting and the dual-engine machinery were REMOVED in
the proof-based rewrite. There is now ONE derivation: `service/status_engine.py` `derive()` over
`agent_status_state`, served from `_compute_live_status_cache`. The legacy per-request
`agent_turn_state`/`turn_busy`-window cascade no longer produces a served status (the proof-based
`derive()` output always wins). `derive()` emits the 8-state vocabulary above — it never emits
`idle` or `stale` (both removed as time-decay states): a long-quiet live agent stays `online`,
and a resident whose bridge lease lapsed reads `offline`, not `stale`. `working` is a pure,
**liveness-gated** `in_turn` flag (a dead worker / gone heartbeat can't latch `working`), queued
delivery is gated on the liveness-aware engine status, and turn transitions PUSH to Dashboard
Next in real time. Liveness uses TWO windows (no `idle_minutes`/`offline_minutes` any
more): `agent_liveness_seconds` (default 90s = 3× the uniform 30s wrapper heartbeat) governs the
managed offline gate + the live-state cache refresh horizon, and `resident_lease_seconds`
(default 150s) governs resident bridge freshness (`_resident_bridge_is_fresh`) — the longer
resident window is intentional, because idle residents don't beat as often as a managed worker's
continuous loop.
Most of the turn-detector prose below (claude transcript detector, hermes gateway-status
detector, the codex rollout-tail detector) describes the BRIDGE-side signals that feed the
engine; the historical `status_engine=new` fixes below are kept as a record of how the
event-driven path was built — read "new" as "the (now sole) `derive()` path".

**Historical record (`4d52571`, 2026-06-05 — building the event-driven path, then behind `status_engine=new`; now the sole path).** Two gaps in the event engine were closed:
- **Fix A — managed + claude channel-woken turns now show `working`.** The bridge `/heartbeat`
  `turnBusy` field is the DOMINANT turn signal for managed runtimes (hermes/codex/pi/opencode)
  and claude channel-woken turns, but it previously only wrote `agent_turn_state` (OLD engine)
  and never `agent_status_state`, so the `new` engine showed `online`/`idle` mid-turn. The
  `/heartbeat` handler now also feeds `turn_start` (on `turnBusy:true`) and `turn_end` (on
  `turnBusy:false`, inside the SAME ownership guard that gates the `turn_busy=0` write, so a
  stale/non-owning bridge can't wipe a live turn) into `_apply_status_event`, which sets
  `agent_status_state.in_turn`. Flag-agnostic write; no-op for `old`. **So "managed/channel
  agent shows online not working under the new engine" is FIXED.**
- **Fix B — `in_turn` staleness backstop.** The `new` engine had NO ceiling on `in_turn`, so a
  dropped/absent turn-END (e.g. resident hermes — start hook, no end hook) latched `working`
  forever. `_gather_status_inputs` now treats `in_turn` as ended once the row's `last_event_at`
  is older than `TURN_BUSY_BACKSTOP_SECONDS` (the SAME 30-min ceiling the old engine uses).
- **M-B — byproduct-path parity (2026-06-05).** The clamp above lived ONLY in
  `_gather_status_inputs`; the SERVED path under `new` is `_compute_live_status_cache`'s
  `StatusInputs` byproduct, which read `in_turn` RAW — so a dropped turn-end latched `working`
  on the served path past 30 min. The byproduct now reads `last_event_at` and clamps
  identically. If a managed agent shows `working` long after its turn ended, the service is
  pre this fix.
- **M-C — interrupt-hint false-positive (2026-06-05).** The console spinner classifier
  (`service/api_core/status_inputs.py`) treated a bare `esc to interrupt` ANYWHERE as `working`, so
  claude writing that phrase in PROSE manufactured a `working` lease (worsened by the 12s→20s
  lease TTL). It now requires a real spinner glyph on the SAME LINE as the hint — keeps both
  live-footer shapes, rejects prose.
- **Completed-residue false-`working` (2026-06-12, `8129b6c`).** The classifier counted
  claude's completed-thought residue (`✻ Sautéed for 21s` — spinner-shaped, NO interrupt
  hint) as a working signal, and bypass-permissions sessions never render the `? for
  shortcuts` idle hint — so an idle managed claude could never vote idle: the console lease
  re-pulsed on every repaint and the agent pinned `working` forever (sc-manager/sc-claude
  incident). Now `working` = the LIVE footer only (glyph + `esc to interrupt` on one line);
  a spinner-shaped line WITHOUT the hint is the turn-ended residue and counts as IDLE
  evidence. Bridge-side: the fix only applies to consoles hosted by a bridge process started
  after reinstall — a stuck agent means its host tier predates the fix; restart the
  `aify-comms` wrapper.
- **Pure-event decouple + claude Stop-gate (2026-06-19).** The `working`→`online`→`working`
  flicker (hermes sc-coder AND claude sc-claude) was a SHARED server-side premature clear:
  `_clear_turn_busy_if_no_open_reply_owing_run` cleared `agent_status_state.in_turn` when a
  dispatched REPLY landed — but agents reply MID-turn, so status flipped to `online` while still
  working, then the bridge re-asserted. Now that path clears ONLY `agent_turn_state.turn_busy`
  (the claim/send-queue gate), NOT `in_turn`; `in_turn` clears only on a real turn-END. **Debug:**
  if a managed/channel agent shows `online` while clearly working, compare `agent_turn_state.turn_busy`
  vs `agent_status_state.in_turn` — they are now INTENTIONALLY decoupled (`turn_busy=0` + `in_turn=1`
  = replied mid-turn, still working). The 20s turn-end grace was removed (no sub-minute time-decay).
  claude `Stop` routes through `claude-stop-gate.js` (suppresses a premature mid-turn Stop via the
  transcript classifier; FAIL-SAFE posts `/turn-end` on any doubt — never stuck-working). codex
  managed turn state also comes from the app-server `turn/started`/`turn/completed` events. The
  Stop-gate + codex wiring need a wrapper/env-bridge RESTART to take effect (server-side decouple
  is live on container rebuild).
- **2026-06-12 evening audit (`00bb544`) — four pipeline flaws.** (1) The read-path cache
  upserts ROLLED BACK on close (no commit in list/get agents) — the cache only persisted
  via the 60s reconcile and every poll re-derived expired rows; both endpoints now commit.
  (2) Wake-disabled (`launch_mode='none'`) was invisible to the engine's `disabled` input —
  parked agents served `available` under `status_engine=new`; they now serve `stopped`.
  (3) WS pushes broadcast the LEGACY derivation while polls served the new-engine value —
  push/poll flicker wherever they disagreed; the push now applies the same flag-gated
  `derive()`. (4) Environment death never expired dependent agents' cached live statuses
  (no transition event exists) — the read-boundary `_enforce_env_reachable_gate` (sibling
  of the live-worker gate) recomputes when a cached live/available status outlives its env.
  Known tolerated divergence: long-dead remote residents read `offline` under the engine (older builds surfaced `stale`, removed 2026-06-18)
  where legacy said `offline` (spec accepts either); disagreement logs are de-duped per
  (agent, old→new) transition. `agent_status_state.status` is VESTIGIAL (written/read by
  nothing — don't trust its values when debugging; the real turn state is `in_turn` +
  `last_event_at`).
- **Resident `online` while hard at work (2026-06-12, `8129b6c`).** Delivering a steered
  message INTO a running resident turn fires the server's delivery-completion turn_busy
  clear, and the transcript turn detector was edge-triggered — it never re-fired, so the
  resident read `online` until its next turn boundary. The detector now re-stamps
  `/turn-start` every 45s while the transcript stays in-flight (`workingRefreshMs`, mirrors
  the hermes gateway detector). Needs a wrapper relaunch to pick up.

**Deploy.** Service-side (rebuild the container) + bridge-side (`/heartbeat` turnBusy + the
console spinner/lease are sent by the wrappers' delivery loops, so re-run `install.sh` and
relaunch the affected wrapper). If a managed/channel agent still reads `online` mid-turn on
the sole `derive()` path, the service or bridge predates these fixes.

## `available→online` is prompt now (and unrelated to auto-close); resident clean-exit drops `online` fast

**Question / symptom.** "An agent's `available→online` flip looks spontaneous/laggy — is
auto-close doing it?" Or: "a resident I just closed still shows `online` for a while."

**`available→online` is now prompt (2026-06-03, `5070c84`).** The agent live-status cache is
invalidated the moment a channel sidecar's bridge row is FIRST inserted (the worker came
alive), so the transition surfaces on the next read instead of waiting out
`agent_live_state.refresh_after` (which is keyed on heartbeat freshness, not worker
presence). **This is NORMAL and is UNRELATED to auto-close** — auto-close only drives the
opposite edge (online→available) and only when enabled. If the flip still looks laggy,
you're on pre-`5070c84` code; rebuild/restart the service.

**Resident clean-exit drops `online` within ~1.5s (2026-06-03, `5070c84`).** The resident MCP
bridge (`mcp/stdio/server.js`) now POSTs `/agents/{id}/resident-lost` on clean exit
(best-effort, resident-only, idempotent, bounded ~1.5s); the server handler sets
`status=stopped` for a **resident**. Ownership never auto-switches to managed. A
`session_mode='managed'` agent that hits this same endpoint (e.g. its hermes gateway port died)
is instead rested **cold-startable** — stored `status='active'` (the enabled flag) → derives `available`,
`launch_mode='detached'` — so the next send auto-spawns a fresh managed worker (new gateway),
no manual `hermes-aify` restart (2026-07-07). So a cleanly-closed
resident no longer lingers `online` for the full ~150s heartbeat lease. A **crash**-closed
resident never runs that exit path, so it still self-heals at the lease — and a crash-closed
**presence-only** (opencode/pi) or channel-stripped resident can read `online` until the lease
ages out (deferred by design: a live `agent_session` ⇒ `online` per the persistent-worker
taxonomy; see KNOWN_ISSUES.md / DECISIONS.md 2026-06-03 round 2). On Windows, the resident/
managed hermes PS branch now reaps its detached delivery loop on TUI exit (try/finally
`Stop-Process`), so a closed Windows resident hermes no longer stays falsely `online` via an
orphaned loop + gateway host — relaunch from a reinstalled `install.sh` to pick this up.

## Session status is derived now — no more "Stopped/Stale but running"

**Symptom (old).** The Sessions table showed a session badge `Stopped`/`Stale` while the
agent dot was clearly `online`/`working` (or vice-versa) — the two disagreed.

**Fix (2026-06-03, `9896d5a`).** `GET /sessions` now DERIVES each session's status from live
truth (`_compute_session_display_status` / `_agent_session_dict_live`) — managed keys on the
live `terminal_sessions` row, resident on a fresh non-superseded bridge — exactly like the
agent dot. The stored `agent_sessions.status`/`terminal_status` is a cache, **never** the
display source, so the badge can't drift from reality anymore. One canonical
`LIVE_SESSION_STATUSES` (server + dashboard aligned) and one `_agent_liveness` predicate feed
both derivers; `_reconcile_dead_session_status` case (a) now JOINs live `terminal_sessions`
(it was reading the frozen `terminal_status` denorm the hygiene reaper left stale at
`attached`), and session mutators invalidate the live-state cache so the dot refreshes
same-pass. If you still see a contradiction, the service is running pre-fix code — rebuild
and restart it.

## Status semantics: `working` vs `online · awaiting reply` (2026-05-31)

**Symptom / question.** An agent that just got a dispatch shows `online` (with an
"awaiting reply" reason) instead of `working`; or a genuinely-working resident
claude "shows working only sometimes."

**Cause + current behavior (pure-event as of 2026-06-02).** `working` means
*actually running a turn* — `turn_busy` set, decided by the turn EVENT, not by a
staleness window. A turn-START event sets `working`; a turn-END event clears it
instantly. A delivered+`require_reply` run whose turn has ENDED (agent idle, owes
the reply) is `online` with an "Idle — awaiting reply" reason, NOT `working` — this
fixed the old "blink working while idle". Per-runtime turn signals: claude
`UserPromptSubmit`→`/turn-start` (START) + `Stop`→`/turn-end` (fast-path END), with a
bridge **BIDIRECTIONAL transcript turn-state detector** as the hook-independent backstop
(it both SETs working on an in-flight tail and CLEARs on an ended tail); codex hooks
+ app-server `turn/completed`; hermes `pre_llm_call`/managed delivery-loop idle event;
pi `agent_end`.

`delivered` proves only that a bridge/channel accepted the dispatch. It does not
prove the model ran or could reply. For a load-bearing delivery check, require the
linked reply and inspect the rendered console when it is absent; provider quota
errors can leave the agent correctly `online` and idle with an awaiting-reply note
even though the message visibly reached the channel.

**Note: the claude `PostToolUse` re-pulse was REMOVED (pure-event #4).** Earlier
builds re-asserted `turn_busy` on every tool call to hold `working` past a short
window. With status pure-event there is no short window to outlast — `turn_busy` is
set once at turn-start and cleared only by the turn-END event — and re-pulsing would
defeat that event. So claude turn hooks are `UserPromptSubmit` (start) + `Stop` (end)
ONLY; the installer also removes any leftover `PostToolUse` `/turn-start` hook. Rerun
`install.sh --client claude` + restart the session to pick this up. A long
tool-using or generation turn stays `working` simply because `turn_busy` stays set
until the end-event. The bridge's **BIDIRECTIONAL** transcript detector (`turn-end-detector.js`
+ `claude-turn-end-detector.js`, reading `adapters/claude.js` `transcriptTail` →
`{lastRole, lastStopReason, pendingToolUse}`; runs for resident AND managed claude, gated
on `AIFY_AGENT_ID` + the `claude-code` adapter + `transcriptTail`) now drives `turn_busy`
in BOTH directions, edge-triggered + idempotent, keyed ONLY on transcript process truth
(anti-feedback-loop): an IN-FLIGHT tail (trailing assistant `stop_reason == 'tool_use'` /
pending `tool_use`, a trailing user/tool_result, or no terminal `stop_reason`) → `/turn-start`
(SET working), and an ENDED tail (terminal `stop_reason` ∈ {`end_turn`, `stop_sequence`,
`max_tokens`}, no pending `tool_use`) → `/turn-end` (CLEAR); a null/unreadable tail → no
change. This both covers a missed `Stop` hook AND fixes the **resident under-report** — a
channel-woken or scheduled-task turn never fires `UserPromptSubmit`, so before `1d2cff9`
resident non-typed turns showed idle-while-working; the bidirectional detector is the robust
replacement for the removed `PostToolUse` re-pulse across ALL turn types (typed, channel,
scheduled), at ≤ ~30s latency. A long blocking tool call or a Task sub-agent dispatch shows
a pending `tool_use` (or a static parent transcript — sub-agents write a separate
`subagents/*.jsonl`) and correctly STAYS `working` (the earlier growth-based detector
false-cleared on those — fixed `8efbbaf`). Backstop only: a still-alive agent with both end-paths missed
self-heals at the single 30-min ceiling (`TURN_BUSY_BACKSTOP_SECONDS`). Explicit
`queueIfBusy` holds on raw `turn_busy=1` until the authoritative turn-end **or the same 30-min
ceiling** (anti-strand — see "queued work never delivers" below). Resident
hermes has no upstream turn-end HOOK, but it DOES arm the continuous gateway turn detector
(`startHermesGatewayTurnDetector`, `server.js`) whenever `AIFY_HERMES_GATEWAY_URL` is set —
same as managed — so a gateway-bound resident hermes reports turn-end normally; only a
gateway-less resident hermes leans on the 30-min ceiling (and, lacking a usable wake handle,
derives `offline`) (KNOWN_ISSUES.md #172). A send to a busy channel-capable target (managed/resident
claude) now STEERS in immediately instead of deferring behind `turn_busy`, and an
`rr=0` channel/resident delivery clears the recipient's `turn_busy`.

**KEEP-FRESH + KEEP-CLEARED — the detector re-asserts BOTH directions (2026-07-13, `a0c8ad9`).**
The edges above are necessary but not sufficient, because turn state can also be written by
paths the detector never observed (a hook, the channel sidecar). Both directions are therefore
re-asserted on a cadence while the PROCESS TRUTH still says so — this is a re-assertion of a
proven fact, **not** a time-based heuristic (nothing here decides anything from elapsed time;
elapsed time only sets how often the same proof is restated):

- **KEEP-FRESH** (existing): while the transcript/gateway proves IN-FLIGHT, re-POST `/turn-start`
  every 45s (far under `TURN_BUSY_BACKSTOP_SECONDS`, the 30-min dropped-event ceiling), so a
  server-side clear of a LIVE turn can't make a working agent read `online`. This cadence is also
  what makes the delivery gates' anti-strand ceiling safe: because a live turn keeps re-stamping
  `turn_updated_at`, only an ABANDONED `turn_busy` ever ages out (see status.md "delivery gates"
  below and DECISIONS.md "Delivery gates read raw turn_busy, bounded by one ceiling").
- **KEEP-CLEARED** (new, the symmetric mirror): while the transcript/gateway proves ENDED **and**
  the detector is not mid-turn, re-POST `/turn-end` every 45s. Before this, the CLEAR was
  edge-ONLY: a stray `in_turn` set OUTSIDE the detector (a hook/sidecar `/turn-start` whose
  end-event was lost) had no edge to fire on and latched `working` until the 30-min ceiling.

Both are gated on process truth and can never false-clear a live turn: claude requires
`classify(tail) === "ended"` (a live turn is never structurally *ended*), hermes requires a
sustained `isGatewaySessionIdle` read plus `!inFlight`; an unknown/unreadable read is a no-op.
`/turn-end` is idempotent and can never re-arm `working`. Applies to claude, codex (same loop)
and hermes. **This does NOT rescue an agent-id-less bridge** — the detector never arms at all
there; see *CHECK THIS FIRST* at the top.
