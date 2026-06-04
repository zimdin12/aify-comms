# Real-Time Status Engine — Design Spec (2026-06-04)

> Status: DRAFT for operator review. Brainstormed with the operator 2026-06-04.
> Decisions locked: cover **all runtimes × modes** in v1; **feature-flag** cutover
> (`status_engine: old | new`, default `old`); **keep the 8-status vocabulary**;
> the engine is the single status authority that **dispatch run-liveness consumes**.

## 1. Problem

Status today is **inferred** by `_compute_live_status_cache` (service/routers/api_v2.py)
from ~8 signals — `turn_busy`, multi-bridge heartbeat freshness, session rows,
terminal PTY state, channel-sidecar liveness, env reachability, resident-bridge-stale,
pending/collision — across a **runtime × mode** branch matrix, and **cached**.

Two consequences:

- **Delayed.** Turn detection is a **30 s poll** (claude transcript-tail / hermes
  gateway sample). On top: 60 s reconcile loop + 60 s dashboard poll (clamped) +
  150 s bridge lease + 30 min turn backstop. A real transition can take **up to ~90 s**
  to surface.
- **Messy / fragile.** Every status bug this cycle (managed→offline, hermes
  online-while-working, sc-manager won't-go-working, the false-failed busy run) is one
  branch of that derived matrix being wrong. The system **cannot distinguish
  "busy working a long turn" from "dead,"** because both are inferred from the same
  smeared signals.

Concrete symptom (2026-06-04): run `run_1780569850270_00f85b01` (sc-manager →
sc-architect, hermes) was claimed, the target was **legitimately busy for 30 min**
(every reminder logged "target is busy"), then the **1800 s wall-clock ceiling**
failed it with a **misleading** "bridge crashed / controller died / PATCH dropped"
reason. The handoff reply was killed before the agent could answer.

## 2. Goal & principle

> **Status is reported by the runtime as it happens (push), reduced to ONE small
> per-agent state machine, and streamed to the dashboard live. Polls are backstops
> only.**

Success criteria:
- `working` appears within ~1–2 s of a turn actually starting; `online`/`idle`
  within a few seconds of it ending.
- Status is **never wrong about alive-vs-dead** — a long turn reads `working`, not
  `offline`/`available`; a dead worker reads `offline`/`stale`, not `online`.
- The status derivation is a **single small, table-testable function**, not a
  runtime×mode branch sprawl.
- **No regression** of the just-stabilized matrix (gated by the feature flag +
  invariant tests + live disagreement logging).

## 3. Architecture

Six units, each independently testable:

1. **Event ingest.** Bridges/runtimes POST immediate events to the service
   (extend the existing `/agents/{id}/turn-start` `/turn-end` `/heartbeat`
   endpoints + lifecycle controls; add explicit `worker_up`/`worker_down` and
   `blocked` signals). Events carry `{agentId, kind, bridgeId, runtime, at, detail}`.
2. **Per-agent state machine** (`service/status_engine.py`, new). A **pure
   function** `derive(mode, liveness, in_turn, worker_present, env_reachable,
   disabled, awaiting_input) -> status`. State persisted in a new
   `agent_status_state` table (current status, in_turn flag, last_event, last_change_at).
3. **Liveness** = a **single** heartbeat per agent (the existing bridge heartbeat,
   tightened toward ~15–30 s) → an `alive` lease. Console/sidecar/gateway presence
   become `worker_present` *details*, not separate liveness gates.
4. **WS push.** On every state change the engine invalidates the cache and pushes
   `agent_status` over WebSocket **immediately** (all transitions, not only
   operator-driven). The dashboard renders on the push; its poll drops to a slow
   safety net (~5 min).
5. **Backstops.** The transcript-tail poll (claude) and gateway sample (hermes)
   stay, **demoted to backstops** that *synthesize a missing event* (e.g. a
   channel-woken turn the hook missed, or a dropped turn-end). The 60 s reconcile
   loop synthesizes `worker_down` when a heartbeat lease expires. No backstop is
   the primary transition.
6. **Feature flag.** Setting `status_engine: old | new` (default `old`). Read paths
   (`get_agents`, `list_sessions`, the WS push) branch on it. When both are
   computable, **log every disagreement** `(agent, old, new, reason)` so regressions
   surface immediately. Rollback = flip back to `old`.

### 3.1 Event vocabulary (transitions)

`turn_start` · `turn_end` · `heartbeat` · `worker_up` · `worker_down` ·
`blocked` (console awaiting input) · `stop`/`disable` · `resume`/`register` ·
`env_unreachable` / `env_reachable`. The state machine is driven by these; it never
re-derives from 8 tables on read.

### 3.2 State machine → the 8 statuses (unchanged vocabulary)

Inputs: `mode` (managed|resident), `alive` (heartbeat within lease), `in_turn`
(a `turn_start` not yet closed by `turn_end`), `worker_present`, `env_reachable`,
`disabled` (explicit stop), `awaiting_input`.

Precedence (first match wins):

| condition | status |
|---|---|
| `disabled` OR (`managed` AND NOT `env_reachable`) | **offline** |
| `resident` AND NOT `alive` | **offline** |
| `in_turn` AND `awaiting_input` | **blocked** |
| `in_turn` | **working** |
| `managed` AND `alive` AND `worker_present` AND quiet-too-long | **idle** |
| `managed` AND `alive` AND `worker_present` | **online** |
| `managed` AND `env_reachable` AND NOT `worker_present` | **available** |
| `resident` AND `alive` AND no-live-session-but-bridge-fresh | **online** |
| `resident` AND bridge-stale | **stale** |
| else | **offline** |

This *reproduces* the matrix we just fixed (managed available-not-offline, the
managed-claude "needs console + sidecar" online rule, resident stale→offline) — but
as one ordered table driven by events + one liveness signal.

### 3.3 Per-runtime event SOURCES (the only per-runtime piece)

- **claude:** `UserPromptSubmit` / first tool-use hook → `turn_start`; `Stop` hook →
  `turn_end`. The transcript-tail detector becomes the **backstop** that synthesizes
  `turn_start` for channel-woken turns (no `UserPromptSubmit`) and `turn_end` if
  `Stop` is missed. `worker_present` = live console PTY + live channel-sidecar
  (managed) / fresh resident bridge (resident).
- **hermes:** the gateway continuous detector pushes `turn_start` on `working` and
  `turn_end` on sustained idle (≈ exists; make it push on transition, sample = backstop).
  `worker_present` = gateway host reachable + a live delivery loop.
- **codex:** the app-server/controller `turn`/`completed` events → `turn_start`/`turn_end`.
  `worker_present` = fresh `managed-wrapper-child` heartbeat.

The state machine + ingest + WS push + flag are **built once**; only the SOURCE
adapters are per-runtime.

### 3.4 Dispatch run-liveness consumer (folds in the false-failed-busy-run fix)

The run-liveness/ceiling reaper and the reply-reminder loop read the engine status
instead of guessing:

- target **`working`** → the run is *not stuck*; reset the no-progress timer (a long
  turn is progress). Keep waiting.
- target **`stale`/`offline`/dead** → fail **fast**, with an **honest** reason
  (`"target <agent> went offline at <T>"`), not the catch-all "bridge crashed".
- target **`online`-idle**, claimed, no progress past the window → genuine orphan →
  ceiling fires with an honest reason.

This kills the class that failed `run_1780569850270` (busy 30 min → falsely
"crashed").

### 3.5 Data flow

```
runtime hook / gateway / app-server  ──event──▶  /agents/{id}/status-event
                                                        │
                                          status_engine.derive(...)
                                                        │
                                  persist agent_status_state + invalidate cache
                                                        │
                                        WS push agent_status  ──▶ dashboard (instant)
backstop poll / reconcile ──synthesize missing event──▲
```

## 4. Migration (no-regress)

1. Land the engine **behind `status_engine=old`** (default). Old derivation untouched.
2. The just-fixed matrix invariants become **unit test cases** the new `derive`
   must satisfy before any flip: managed reachable-env+dead-worker → `available`;
   managed-claude live-sidecar+no-console → `available`; hermes working-while-delivering
   → `working`; resident stale bridge → `stale`/`offline`; auto-confirm + agent-centric
   Sessions unaffected.
3. With both paths computable, **log disagreements**; review before flipping.
4. Flip `status_engine=new` when tests pass and the disagreement log is clean.
   Rollback = flip to `old`.
5. After a stable period, retire the old derivation (separate cleanup).

## 5. Testing

- **Unit:** table-driven test of `derive(...)` over every input combination, including
  all §4.2 invariants. (This is the regression net.)
- **Integration:** event sequences — `turn_start→working→turn_end→online`;
  heartbeat-lease-expiry → `worker_down`→`available`/`stale`; managed env-down → `offline`;
  busy-target run **waits**, dead-target run **fails fast with honest reason**.
- **Live (flag on):** watch the disagreement log + measure dashboard latency
  (`working` visible < 2 s after turn start).

## 6. Risks / non-goals

- **Hook gaps** (channel-woken claude turns fire no `UserPromptSubmit`) — mitigated by
  the transcript backstop synthesizing `turn_start`.
- **All-runtimes v1** = largest blast radius (operator's choice). Safety net = the flag
  + invariant tests + disagreement logging; default stays `old` until proven.
- **Non-goals:** changing the status vocabulary; changing dispatch semantics beyond
  run-liveness consuming status; the dashboard-v2 rewrite (separate).

## 7. File map

| File | Responsibility |
|---|---|
| `service/status_engine.py` (new) | pure `derive(...)` state machine + event→state transitions |
| `service/routers/api_v2.py` | event ingest endpoints; flag-branch in read paths; run-liveness consumes engine |
| `service/db.py` | `agent_status_state` table + `status_engine` setting default |
| `mcp/stdio/*` (claude-turn-end-detector, hermes-gateway-turn-detector, codex controller) | demote polls to backstop; push transitions as events |
| `service/dashboard.html` | render on `agent_status` WS push; slow safety-net poll |
| `service/tests/test_status_engine.py` (new) | table-driven `derive` tests + matrix invariants |
