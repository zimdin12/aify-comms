# Status derivation — holistic audit (2026-06-18)

READ-ONLY audit. Question: after the 2026-06-18 fixes (proof-based `derive()`, in-memory
`_LIVE_STATE_CACHE`, `TURN_END_GRACE_SECONDS=20`), is agent STATUS correct across
{claude, codex, hermes} × {resident, managed}, and does `working` persist for the WHOLE
turn until the task is actually done?

## How status is built (shared spine)

- `derive(StatusInputs)` — `service/status_engine.py:37`. Pure, ordered rules. The in-turn
  states (`working`/`blocked`) require `in_turn AND live`, where `live = worker_present`
  (managed) or `has_live_session and not bridge_stale` (resident) — `status_engine.py:51-53`.
  So no turn signal can outlive the worker, and a long turn never falls back to
  `online`/`available` as long as `in_turn` is held.
- Two builders that MUST agree (the "byproduct-parity promise"):
  - `_gather_status_inputs` (WS-push path) — `service/routers/api_v2.py:4423`.
  - `_compute_live_status_cache` (served/poll byproduct) — `service/routers/api_v2.py:4513`.
- `in_turn` is fed from three OR'd sources, in both builders:
  1. `agent_status_state.in_turn` (set by `_apply_status_event` on turn_start/turn_end).
  2. The **turn-end grace** — `api_v2.py:4454-4457` (gather) and `4560-4570` (byproduct):
     if `in_turn=0` but the last event/clear is `turn_end` within `TURN_END_GRACE_SECONDS=20`
     (`api_v2.py:420`), keep deriving in_turn.
  3. The **console-working spinner lease** — `_console_working_lease_fresh`
     (`api_v2.py:4162`), OR'd in **only when a live worker is present**
     (`api_v2.py:4480`; byproduct folds it after liveness too). TTL = `CONSOLE_WORKING_LEASE_SECONDS=20` (`api_v2.py:385`).
  Plus a dropped-event self-heal ceiling `TURN_BUSY_BACKSTOP_SECONDS = 30*60`
  (`api_v2.py:412`, `4444-4449`).
- The in-memory cache (`_LIVE_STATE_CACHE`) is invalidated immediately on every turn/console
  event (`_invalidate_agent_live_state` in `/turn-start` `api_v2.py:16102`, `/turn-end`
  `16178`, `/console-working` `16033`, `/heartbeat` turnBusy flip `15742`), and the new status
  is WS-pushed via `_broadcast_engine_status`. So the cache HELPS every cell: a turn edge is
  reflected within ~1s, not at the ~60s poll. It never holds a stale `working` longer than the
  underlying signals (grace/lease/backstop) would.

## The signal sources per harness

- **claude**: resident Stop/UserPromptSubmit hooks → `/turn-start` `/turn-end`
  (install.sh:514, 3288-style wiring); the **bridge transcript turn-END/START detector**
  (`server.js:387-425`, 30s cadence, structural tail read, re-stamps start every 45s) as a
  hook-independent backstop; the channel sidecar `reportTurnBusy` via `/heartbeat`
  (`claude-channel.js:302`) for channel-woken delivery; the **console-working spinner lease**
  via `decideConsolePulse`→`pulseConsoleWorking` (`server.js:887-913`) gated on
  `consoleClass==="working"` (`claude-console-spinner.js:93`) — managed PTY only.
- **codex**: resident UserPromptSubmit/Stop hooks → `/turn-start` `/turn-end`
  (install.sh:3256-3258); the **rollout-tail detector** (`server.js:435-463`) as
  backstop; managed runs pulse `/heartbeat` turnBusy via `reportTurnBusy` in the
  delivery loop (`server.js:2324`, called `2974/3177/3194/3335`).
- **hermes**: managed gateway turn detector (`hermes-gateway-turn-detector.js`,
  debounced bidirectional, set on gateway `running`, clear on `DEFAULT_IDLE_DEBOUNCE_TICKS=3`
  sustained idle) + `makeInFlightProbe`→`clearTurn` in `hermes-managed-host.js`; the SAME
  gateway detector is wired for **resident** hermes (`server.js:465-` resident-hermes
  detector) — resident hermes has no native end hook, so the gateway detector is its only
  reliable turn-end.

## Per-cell table

| Harness × Mode | SETS working | CLEARS / premature-clear risk | `working` persists whole turn? | Cache+grace effect | Verdict |
|---|---|---|---|---|---|
| **claude / resident** | UserPromptSubmit `/turn-start` (install.sh:514); transcript detector start (`server.js:408`); channel sidecar turnBusy on channel-woken delivery (`claude-channel.js:302`). | Stop hook `/turn-end`; transcript detector end (`server.js:416`). Stop hook (#54360) can fire prematurely BETWEEN tool calls, but resident has NO SIGWINCH keepalive and NO spinner lease (lease is managed-PTY-only, `terminal-runtime.js:581-582`). The 20s grace absorbs a single premature Stop; the transcript detector re-stamps start within 30-45s. | YES for normal turns. The transcript-structure detector keeps in_turn through long generations / blocking tool calls (it reads tail STRUCTURE, never growth). Residual: a premature Stop whose next-tool gap exceeds 20s AND lands in the 0-30s window before the detector re-stamps could flicker `online` briefly. Low likelihood, bounded. | Both help. derive() gates on `has_live_session` so a stale start never shows working on a dead resident. | **CORRECT** (minor bounded flicker window only on premature-Stop, < detector cadence). |
| **claude / managed** | Channel sidecar / dispatch `/heartbeat` turnBusy (`claude-channel.js`); transcript detector; **console-working spinner lease** (`server.js:913`) refreshed by the SIGWINCH keepalive (`terminal-runtime.js:580`). | Stop hook `/turn-end` fires prematurely between tool calls (#54360) → turn_busy=0 briefly. | MOSTLY. Defended in DEPTH: (1) 20s turn-end grace; (2) spinner lease OR'd into in_turn; (3) transcript detector re-stamp. **RESIDUAL GAP**: a long TOOL-FREE generation stretch (>20s, no tool call, no heartbeat re-pulse) where the spinner lease ALSO goes stale → can briefly show `online`. Root cause below. | Both help; the spinner lease is the primary managed-claude defense the per-message transcript can't see. The gap is precisely when the lease lapses. | **RESIDUAL-GAP** (the known #224 residual: long tool-free generation + stale spinner lease → transient `online`). |
| **codex / resident** | UserPromptSubmit `/turn-start` (install.sh:3256); rollout-tail detector start (`server.js:446`). | Stop hook `/turn-end` (install.sh:3258); rollout detector end (`server.js:454`). Codex has no premature-Stop pathology like claude; a DROPPED Stop is the only risk, caught by the rollout detector (30s) and the 30-min backstop. | YES. The rollout detector reads process-truth tail STRUCTURE, so long turns hold. | Both help; grace is largely redundant here (no flapping source). | **CORRECT**. |
| **codex / managed** | Delivery-loop `/heartbeat` turnBusy `reportTurnBusy` (`server.js:2324`, pulsed during dispatch); rollout detector. | turnBusy=false from the owning bridge at run end (ownership-guarded, `api_v2.py:15718-15737` — a stale/superseded bridge cannot false-clear); rollout detector end. | YES. Heartbeat turnBusy is re-pulsed during the run; ownership guard prevents stale clears. | Cache reflects the heartbeat flip instantly (`api_v2.py:15742`). | **CORRECT**. |
| **hermes / resident** | Resident-hermes gateway turn detector start on gateway `running` (`server.js:465-` arm). No UserPromptSubmit hook. | Gateway detector end on 3 sustained idle ticks (`hermes-gateway-turn-detector.js:48`); debounced so mid-turn `running=False` gaps don't false-clear. The gateway-idle clear is the documented risk, but debounce + the in_turn backstop cover it. | YES for gateway-attached turns. **Conditional gap**: if NO visible TUI is attached to the gateway (active_list empty), the detector can't observe `running` → never sets working → the turn shows `online`/`available`. That is a worker-presence problem, not a derive() problem. | Grace smooths the debounced idle edge; cache reflects the gateway-detector posts instantly. | **CORRECT** when a TUI is attached; RESIDUAL-GAP only in the no-TUI-attached degenerate case (separately tracked: visible-TUI requirement). |
| **hermes / managed** | Managed gateway detector + `makeInFlightProbe` start; dispatch `/heartbeat` turnBusy. | `makeInFlightProbe`→`clearTurn` / gateway detector end (debounced, `hermes-managed-host.js`). Mid-turn gateway idle flaps are debounced (3 ticks ≈ 9s) so they don't false-clear. | YES. The debounce was specifically added to stop the working↔online flap (`hermes-gateway-turn-detector.js:5-13`). | Both help. | **CORRECT**. |

### Summary of verdicts

- 5 of 6 cells: **CORRECT** (codex×both, hermes×both modulo the no-TUI degenerate case,
  claude/resident modulo a sub-detector-cadence flicker on premature Stop).
- 1 cell carries the operator-visible **RESIDUAL-GAP**: **claude / managed** — a long
  tool-free generation stretch can briefly read `online` because BOTH the turn signal
  (premature Stop clears turn_busy) AND the console-working spinner lease lapse together.

## Root cause of the residual (claude / managed spinner-lease staleness)

The managed-claude defense-in-depth is: turn-end grace (20s) + console-working spinner lease
(20s TTL) + transcript detector (30s). During a LONG tool-free generation:

1. The Stop hook may have fired prematurely earlier, so `agent_turn_state.turn_busy=0` and the
   20s grace has long since lapsed → in_turn source #1 and #2 are both OFF.
2. The transcript detector re-stamps start only every 45s and only when the tail STRUCTURE
   shows in-flight — during a single long generation the tail's last role/stop_reason may not
   re-trip a fresh start edge.
3. So the ONLY thing holding `working` is the **console-working spinner lease**, refreshed by
   `pulseConsoleWorking` (`server.js:906`). That fires only when `onOutput` fires AND
   `consoleClass==="working"` (`server.js:1031-1038`). When the dashboard Console is closed,
   claude stops re-emitting its footer (it only repaints while actively rendered), so `onOutput`
   goes quiet → no pulse → the 20s lease expires → `derive()` drops to `online`.

The keepalive that exists to prevent exactly this is `_armConsoleKeepalive`
(`terminal-runtime.js:580`). Every `consoleKeepaliveMs` (4s) it SIGWINCHes the PTY
(shrink-1-col then restore) to force claude to re-emit its footer, which produces `onOutput`,
which re-classifies and re-pulses the lease.

**Why the lease still goes stale — the IDLE-GRACE GATE** (`terminal-runtime.js:588-601`):

```
if (st.consoleClass === "idle") st._kaIdleTicks++; else st._kaIdleTicks = 0;
if (st._kaIdleTicks > this.consoleKeepaliveIdleGraceTicks) return;   // STOP nudging
```

`consoleKeepaliveIdleGraceTicks` default = 30 (`terminal-runtime.js:150`, ~2 min at 4s). The
gate's stated safety argument (comment lines 588-595): "a working-but-quiet turn keeps
consoleClass==='working', so it is never paused." THAT ARGUMENT HAS A HOLE:

- `consoleClass` is only updated inside `_handleOutput` (`terminal-runtime.js:358`). It is a
  cached value reflecting the LAST output seen.
- During a long tool-free generation with the Console closed, claude emits NOTHING on its own.
  The keepalive's SIGWINCH is what should provoke a footer re-emit. But if, at the moment work
  began, the last classified tail was **`idle`** (e.g. the completed-thought residue of the
  prior turn, or a bypass-permissions footer that classifies idle), `st.consoleClass` is stuck
  at `"idle"`. Then:
  - The idle-tick counter climbs past 30 → the keepalive STOPS SIGWINCHing.
  - With no SIGWINCH, claude never re-emits → `onOutput` never fires → `consoleClass` is never
    refreshed off `"idle"` → the gate never re-arms.
  - This is a **self-reinforcing dead state**: the gate paused because it believed idle, and
    pausing removes the only stimulus that could prove it wrong. The lease expires (20s) and
    status drops to `online` even though claude is actively generating.

Secondary contributor: even when classification IS fresh, `classifyClaudeConsoleTail` returns
`"unknown"` for many footers; `decideConsolePulse` only pulses on exactly `"working"`
(`server.js:891`), so an `unknown`-classified working footer does not refresh the lease. The
keepalive comment treats `unknown` as "keep nudging" (correct), but the PULSE side does not
treat `unknown` as working (correct, to avoid false-working) — so during an `unknown` stretch
the SIGWINCH keeps firing but the lease still isn't refreshed unless a tick lands on a real
`working` footer. The dominant failure, though, is the idle-latch dead state above.

## Concrete bridge-side fix plan

The fix must keep the lease fresh **while real work is happening**, without manufacturing
`working` for a truly idle agent. Two coordinated bridge changes (`mcp/stdio/`):

### Fix 1 (primary): make the keepalive idle-grace gate evidence-based, not latch-based

In `_armConsoleKeepalive` (`terminal-runtime.js:585-607`), the idle-tick counter must only
accumulate on **fresh** idle evidence, and a SIGWINCH that produces no output must not be
counted as confirming idle. Concretely:

- Track the last-output timestamp (`st._lastOutputAt`, stamped in `_handleOutput`).
- In `tick()`, only increment `_kaIdleTicks` when `consoleClass === "idle"` **AND** output was
  actually observed since the previous tick (i.e. the idle classification is FRESH, provoked by
  this keepalive's own SIGWINCH). If a tick fires but no new output arrived since the last tick,
  do NOT count it toward the idle streak — the classification is stale, so treat it as
  `unknown`/keep-nudging rather than confirming idle.
- This breaks the self-reinforcing dead state: a genuinely idle console DOES re-emit its idle
  footer on each SIGWINCH (so it still accrues idle ticks and eventually pauses — zero churn
  preserved), but a working-but-quiet console whose stale `consoleClass` happens to be `idle`
  will NOT accrue ticks until it produces a fresh idle footer, so the keepalive keeps nudging
  and the next footer re-classifies it to `working`, refreshing the lease.

Lower-effort variant of the same idea: raise the pause to require a sustained streak of FRESH
idle classifications, and reset `_kaIdleTicks` whenever a SIGWINCH yields NO output within one
tick (proof the PTY isn't repainting an idle prompt — i.e. it may be mid-generation).

### Fix 2 (defense-in-depth): widen the lease refresh to `working`-or-`unknown` while in a known turn, OR lengthen the lease TTL relative to the keepalive cadence

Two non-exclusive options:

- (2a) In `decideConsolePulse` (`server.js:887`), when the bridge KNOWS a turn is in flight
  (it set turn_busy=true for this agent and hasn't posted an authoritative end), treat
  `consoleClass === "unknown"` as a lease refresh too — the ambiguity should hold `working`
  during a known turn rather than drop it. Keep the strict `working`-only rule when no turn is
  known in flight (so it never manufactures working at rest).
- (2b) Decouple the lease TTL from the keepalive cadence with more headroom: the server lease
  TTL is `CONSOLE_WORKING_LEASE_SECONDS=20` and the keepalive cadence is 4s, so ~5 missed pokes
  drop it. That ratio is fine ONCE Fix 1 stops the keepalive from pausing mid-work; without
  Fix 1, no TTL helps because the pulses stop entirely. So 2b is only a smoothing knob, not a
  fix — Fix 1 is the load-bearing change.

### Do NOT re-introduce raw terminal-output "working"

The prototype that treated any terminal output as `working` was reverted because it conflates
streaming the FINAL reply with active work (noted in the task context). The fixes above keep the
signal tied to the spinner classification / known-turn state, not raw output, so they do not
reintroduce that conflation.

### Deployment note

These changes are under `mcp/stdio/` (host-side bridge). They require **rerunning `install.sh`**
(the installer copies `mcp/stdio` + node_modules into `~/.aify-comms/`, per CLAUDE.md) **and
restarting the client wrapper** (`claude-aify`) — a wrapper restart alone is NOT enough, and no
container rebuild is needed. Validate live with a managed claude doing a long tool-free
generation while the dashboard Console is CLOSED, watching the dashboard status stays `working`.

## Bottom line

`derive()` and the two-builder parity are sound: status is correct holistically, and `working`
persists for the whole turn in 5 of 6 cells. The one operator-visible residual is
**claude / managed** during long tool-free generation, and its root cause is the **idle-grace
gate in `_armConsoleKeepalive` latching on a stale `idle` classification and pausing the
SIGWINCH that is the only thing that could disprove it** — a self-reinforcing dead state that
lets the 20s console-working lease expire. Fix 1 (evidence-based idle gate) is the load-bearing
repair; Fix 2 is defense-in-depth. Both are bridge-side and ship via `install.sh` rerun +
wrapper restart.
