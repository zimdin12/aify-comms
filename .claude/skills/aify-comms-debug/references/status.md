# aify-comms troubleshooting: Status & status-derivation

## Contents

- [Status labels: online vs available vs idle vs working vs blocked vs stale vs offline vs stopped](#status-labels-online-vs-available-vs-idle-vs-working-vs-blocked-vs-stale-vs-offline-vs-stopped)
- [`status_engine=new` is the live event engine — managed/channel `working` is FIXED (4d52571)](#status_enginenew-is-the-live-event-engine-managedchannel-working-is-fixed-4d52571)
- [`available→online` is prompt now (and unrelated to auto-close); resident clean-exit drops `online` fast](#availableonline-is-prompt-now-and-unrelated-to-auto-close-resident-clean-exit-drops-online-fast)
- [Session status is derived now — no more "Stopped/Stale but running"](#session-status-is-derived-now-no-more-stoppedstale-but-running)
- [Agent shows `online`, but no live worker exists](#agent-shows-online-but-no-live-worker-exists)
- [Status semantics: `working` vs `online · awaiting reply` (2026-05-31)](#status-semantics-working-vs-online-awaiting-reply-2026-05-31)
- [Agent shows `online`/`Console ready` but messages stay queued (status lied)](#agent-shows-onlineconsole-ready-but-messages-stay-queued-status-lied)
- [Agent shows online without a console (Plan 5 Section C)](#agent-shows-online-without-a-console-plan-5-section-c)
- [Managed claude shows `online` while it is clearly thinking](#managed-claude-shows-online-while-it-is-clearly-thinking)
- [Managed claude flaps to `online` while working — but only when the Console is CLOSED](#managed-claude-flaps-to-online-while-working-but-only-when-the-console-is-closed)
- [Managed claude showed `blocked` mid-generation (2026-06-07)](#managed-claude-showed-blocked-mid-generation-2026-06-07)

## Status labels: online vs available vs idle vs working vs blocked vs stale vs offline vs stopped

**Question.** Operator confusion — "is `idle` the same as `stale`? is `available` the same
as `online`?" They are distinct signals. Canonical reference:

| Label | Meaning |
|-------|---------|
| `online` | Live worker, idle (no active turn). |
| `available` | Reachable but NO live worker; auto-starts a worker on the next send. |
| `idle` | An ONLINE worker quiet >5 min (only ever demoted from `online`). **NOT emitted under the live `new` engine** (`idle_too_long` is inert) — a long-quiet worker reads `online`. |
| `working` | Executing a turn / claimed run (active run or fresh `turn_busy`). **Liveness-gated (2026-06-17, WS-2):** requires a LIVE worker — a dead managed worker / stale resident bridge no longer reads `working`, it falls to `available`/`offline`/`stale` within the liveness lease. |
| `blocked` | An IN-TURN agent whose live console tail looks like it needs operator input/a decision (a prompt/question awaiting an answer, not healthy generation). Requires a live worker — gated like `working`. Emitted under the default `new` engine from the console-awaiting-input hint. |
| `stale` | RESIDENT-ONLY; the resident bridge heartbeat is past its ~150s lease (live-but-expired — NOT an old/sticky label). |
| `offline` | Bound env bridge down, or heartbeat past the ~30min window. |
| `stopped` | Operator-stopped, wake-disabled (`launch_mode='none'`), or set by `resident-lost` on clean close. |

Managed lifecycle: `available` → `working` ⇄ `online` → `idle` (+ stop/offline). Resident
adds `stale` when its bridge lease lapses, and (2026-06-03) `stopped` on clean close.
`blocked` is a sub-state of an active run (not a separate down-state): the run is live but
the terminal tail reads as awaiting operator input/a decision rather than healthy generation
(2026-06-07 — see "Managed claude showed `blocked` mid-generation"). Key
distinctions: `available` ≠ `online` (no live worker yet — it boots one on send); `idle` is
NOT a separate down-state, it's an `online` worker just gone quiet; `stale` is resident-only
and means a LIVE-but-expired bridge lease, not an old label that "stuck".

## `status_engine=new` is the DEFAULT live event engine (Phase I flip done, 2026-06-17)

**Context.** aify-comms has TWO status derivations behind the `status_engine` setting. `new`
(the event-driven `service/status_engine.py` `derive()` over `agent_status_state`) is the
DEFAULT and the authoritative served path; `old` (the legacy per-request `agent_turn_state`/
`turn_busy`-window derivation) remains only as a fallback setting. The 8-status VOCABULARY is
the same across engines, and under `new` every state is produced EXCEPT `idle` — a long-quiet
live agent stays `online` (the effective ready/idle state; operators rely on `online` as
"ready for queued work"), so `idle` is intentionally not emitted. `blocked` (in-turn +
console-awaiting-input, gated on a live worker), booting-console→`online`, and resident
`*-missing-handle`→`stale` ARE all produced under `new` (status-accuracy remediation
2026-06-17; see docs/superpowers/plans/2026-06-17-status-accuracy-remediation.md). What also
differs from `old`: `working` is a pure, **liveness-gated** `in_turn` flag (a dead worker /
stale bridge can't latch `working`), queued delivery is gated on the liveness-aware engine
status, and turn transitions PUSH to both dashboards in real time (WS-1). Most of the
turn-detector prose below (claude transcript detector, hermes gateway-status detector, the
codex rollout-tail detector, the 120s/30-min constants) describes the BRIDGE-side signals that
feed the engine.

**The fix (`4d52571`, 2026-06-05).** Under `new`, two gaps in the event engine are closed:
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
  (`claude-console-spinner.js`) treated a bare `esc to interrupt` ANYWHERE as `working`, so
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
  after reinstall — a stuck agent means its environment bridge predates the fix; restart the
  `aify-comms` wrapper.
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
  Known tolerated divergence: long-dead remote RESIDENTS read `stale` under the engine
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
relaunch the affected wrapper). If a managed/channel agent still reads `online`/`idle` mid-turn
while `status_engine=new`, the service is pre-`4d52571`.

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
`status=stopped` (or auto-returns to managed if a managed backing exists). So a cleanly-closed
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

## Agent shows `online`, but no live worker exists

**Symptom.** Dashboard or `comms_agent_info` reports a managed Codex/Hermes
agent as `online`, but its Console is gone, sends do not visibly
land in a real worker, or the session only has an old `vterm_*`/historical
terminal row.

**Cause.** Older service builds cached `agent_live_state.status` using
heartbeat freshness, not live worker presence. A wrapper PTY could exit while
another heartbeat kept the cache row fresh, so the UI kept showing
`online`. A related bug invalidated the corrected writeback
immediately, and readiness/registration changes could leave future-dated cache
rows in place.

**Fix (2026-06-01 / 2026-06-02).** Update and restart/rebuild the service. Current
builds downgrade managed wrapper-backed agents with no live `terminal_sessions` row
to `available`, persist that downgrade, invalidate live-state cache on
`PATCH /agents/{id}/ready`, and invalidate cache on registration. **`online` now
means deliverable — it requires a live CLAIMER, not just process presence.** Both
managed **claude** AND managed **hermes** are now in the channel-sidecar-delivery
gate: `online` requires a live, non-superseded channel-sidecar (the actual claimer —
`claude-channel.js` for claude, the `hermes-managed-host.js` delivery loop for
hermes) in addition to a live console PTY. A live PTY / `-aify` wrapper / virtual-rpc
row alone can no longer manufacture `online`. As of 2026-06-02 the delivery loop also
publishes an explicit claimer **lease** (acquired when it becomes a live claimer,
released on clean teardown), so a cleanly-exited loop is immediately non-deliverable
rather than waiting out a staleness window. A headless orphan (live sidecar, no
console — a visible-TUI violation and a proliferation source) reports `available` and
is reaped by `_reconcile_managed_worker_hygiene` (60s reconcile loop), which now
covers the hermes triad and also reaps ghost console rows (dead worker, stale
`attached` terminal). **As of 2026-06-07 (`8ef31a2`) the 60s reaper is the BACKSTOP, not
the primary path for lifecycle-driven teardown:** Stop/Restart/Reset/cli_takeover now
SYNCHRONOUSLY kill the live managed PTY (session-control enqueues a terminal stop;
`TERMINAL_MANAGER.stop` escalates SIGTERM→SIGKILL), so they no longer leave a headless
orphan waiting out the 60s hygiene reaper — the orphan path now only catches crash/leak
residue, not an operator Stop/Restart. Host-side defenses back this: the managed worker tree is
tree-killed when its console PTY closes, the channel-sidecar self-exits once its
parent process is gone, and the env bridge reaps console rows whose local
`process_id` is dead. After updating, restart the affected environment bridge or
wrapper so a real worker can re-register and recreate the backing terminal.

**Also (2026-06-02, `3ca464a`): a managed agent reads `offline` when its owning
environment bridge is down**, regardless of any surviving delivery-loop heartbeat —
a managed agent can only be hosted by its owning env bridge, so its effective status
is gated on that bridge. The status path resolves the STORED owning environment
(resolved id → `runtime_config.environmentId` → `machine_id`+runtime match), so even
after the worker row is gone the gate still fires. So killing `aify-comms` makes its
managed agents show `offline` immediately, not a stale `available`/`online`. Resident
agents are excluded (their liveness is the resident bridge, not the env bridge).

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
self-heals at the single 30-min ceiling (`TURN_BUSY_BACKSTOP_SECONDS`); the claim-gate
keeps the 120s (`TURN_BUSY_STALE_SECONDS`) so a queued send isn't stranded. Resident
hermes has no upstream turn-end event and relies on the 30-min ceiling
(KNOWN_ISSUES.md #172). A send to a busy channel-capable target (managed/resident
claude) now STEERS in immediately instead of deferring behind `turn_busy`, and an
`rr=0` channel/resident delivery clears the recipient's `turn_busy`.

## Agent shows `online`/`Console ready` but messages stay queued (status lied)

**Symptom.** A managed claude shows `online` with a live Console, yet dispatches
don't deliver.

**Cause.** `online` used to derive from the wrapper PTY's terminal session, but
for managed claude the PTY only RENDERS — `claude-channel.js` (the channel
sidecar) is the actual claimer. A live PTY with a dead/superseded sidecar
delivered nothing.

**Fix (2026-06-01).** Managed claude now requires BOTH a live console PTY AND a
live, non-superseded channel-sidecar to be `online`; otherwise it honestly
reports `available` (note: "No live channel sidecar heartbeat (not
deliverable)"). The inverse case is also handled: a live sidecar with no console
is a "headless orphan" (visible-TUI violation + proliferation source) — it reads
`available` and is reaped by `_reconcile_managed_worker_hygiene` (60s reconcile
loop), backed host-side by PTY-close tree-kill of the worker and channel-sidecar
self-exit when its parent claude is gone. (2026-06-07, `8ef31a2`: an operator
Stop/Restart/Reset/cli_takeover now kills the managed PTY SYNCHRONOUSLY —
session-control enqueues a terminal stop and `TERMINAL_MANAGER.stop` escalates
SIGTERM→SIGKILL — so a lifecycle action no longer strands a headless orphan for the
60s reaper; the reaper is the backstop for crash/leak residue.) If you see
`available` with a live Console, the sidecar is down — restart the wrapper (and
ensure the self-heal/per-agent-id build is deployed).

## Agent shows online without a console (Plan 5 Section C)

**Symptom.** Dashboard shows a managed agent as `online`. Clicking through to the agent never loads a Console widget; no live terminal_session attaches. The wrapper PTY exited some time ago, but the agent never downgraded.

**Detection.** Compare cached status against actual worker presence:

```bash
docker exec aify-comms-service python -c "
import sqlite3, glob
db = sorted(glob.glob('/data/*.db'))[-1]
c = sqlite3.connect(db)
aid = 'YOUR-AGENT-ID'
live = c.execute('SELECT status, updated_at, refresh_after FROM agent_live_state WHERE agent_id=?', (aid,)).fetchone()
terms = c.execute(\"SELECT id, status FROM terminal_sessions WHERE agent_id=? AND status NOT IN ('stopped','failed','exited')\", (aid,)).fetchall()
print('live:', live)
print('active terms:', terms)
"
```

If `live[0]=='online'` AND `active terms` is empty, that's the Plan 5 Section C bug — `agent_live_state` cached `online` and `refresh_after` was keyed off heartbeat freshness rather than worker presence, so a sibling/operator heartbeat kept the lie alive.

**Fix.** Rebuild the container so `_enforce_live_worker_gate` (added at `api_v2.py:352` in commits `b58142e` + `f38f57d`) is loaded. On the next `GET /api/v1/agents` or `/agents/{id}` read, the gate validates the live worker and downgrades to `available`; a cache writeback ensures subsequent reads stay consistent. No manual DB patch is needed once Plan 5 is in.

## Managed claude shows `online` while it is clearly thinking

**Symptom.** A managed claude is visibly working — the Console shows `✻ Crunched for 3m 12s
(esc to interrupt)` — but the dashboard dot reads `online`, not `working`. Often "right after
one tool call it went online but it's actually still working".

**Cause.** The transcript turn-detector is structurally blind to LIVE generation: claude's
transcript grows per *completed message*, so during a long thinking phase the tail still shows
the last ENDED message → the detector reads not-working.

**Fix (2026-06-05).** The managed PTY's spinner footer now drives a TTL "console-working
lease" (`agent_console_signal`, `POST /agents/{id}/console-working`) that derives `working`
for the spinner window. It's additive/weak: gated on a live worker, never clears a real
turn, self-expires ~20s after the spinner stops. If it still under-reports: confirm the
service was rebuilt and the bridge reinstalled, and that the spinner footer actually renders
in the Console. Disable nothing — it can't manufacture `working` for an idle/dead agent.

## Managed claude flaps to `online` while working — but only when the Console is CLOSED

**Symptom.** A managed claude reads `working` while you have its dashboard Console **open**,
but flaps to `online` (then back to `working` when you reopen the Console) while it is still
working. "It starts working again if I have the terminal open on my screen, otherwise it goes
to online." Delivery still works — it's only the status dot.

**Cause.** The console-working lease (above) refreshes from the spinner footer streaming
through the managed PTY, but claude (Ink) only re-emits that footer while its PTY is
*actively rendered*. With the Console closed, an unwatched working claude goes quiet on the
PTY, the lease expires, and the dot falls to `online`. Opening the Console sends a resize →
claude repaints → the footer streams again → the lease refreshes (hence the correlation with
"having the terminal open").

**Fix (2026-06-05).** The bridge now runs a managed-claude PTY **repaint keepalive**
(`terminal-runtime._armConsoleKeepalive`): every ~4s it SIGWINCHes the PTY so claude re-emits
its footer whether or not anyone watches, and the lease TTL was widened to 20s to span the
keepalive cadence. claude-managed-only, best-effort, no visible flicker (the resize shrinks one
column then restores synchronously, so claude redraws an identical frame). To deploy: re-run
`install.sh --client claude` (re-copies the bridge) **and** restart the env bridge / claude
wrapper, then rebuild the service for the TTL. Kill-switch `AIFY_NO_CONSOLE_KEEPALIVE=1`;
cadence override `AIFY_CONSOLE_KEEPALIVE_MS`. If it still flaps after deploy: confirm the
keepalive is armed (managed claude PTY) and the service carries the 20s TTL. *Note — not a
time-grace:* a deaf/stale console can be recent too, so no age threshold distinguishes "working
but unwatched" from "wedged"; forcing the signal to keep flowing is the only truthful fix.

## Managed claude showed `blocked` mid-generation (2026-06-07)

**Symptom.** A managed claude with a live active run flips to `blocked` while it is actually
generating — typically right after a subagent/Task dispatch whose prose contains
decision-flavored language ("which option do you want", "your call", "let me know how to
proceed"). The agent is not actually stuck at a prompt; it's still producing tokens.

**Cause.** The awaiting-input classifier read the terminal tail for prompt-/decision-shaped
phrasing to derive `blocked`. Subagent/Task PROSE that merely *discusses* a decision tripped
it, so a busy claude mid-generation got mislabeled `blocked`.

**Fix (`4ef1db3`, 2026-06-07).** A live spinner footer **short-circuits** the awaiting-input
hint: when the managed PTY tail carries a running spinner (`✻ … esc to interrupt` /
`✻ <verb> for <N>s`), claude is provably generating, so decision-flavored subagent/Task prose
no longer fires `blocked` while the spinner runs. A REAL prompt pauses the spinner (claude
stops emitting the footer when it's actually waiting on the menu/question), so a genuine
awaiting-input state still reads `blocked` — the spinner's presence/absence is the
discriminator. If a busy managed claude still flips to `blocked` after deploy, confirm the
service was rebuilt and the spinner footer actually renders in the Console.
