# Runtime Delivery — Target Architecture

Status: **TARGET MODEL.** Captured from operator + comms-senior-dev-pi
conversation (2026-05-20). This is the runtime/bridge end-state the
service is converging toward. Companion to
[DASHBOARD_8801_PARITY.md](DASHBOARD_8801_PARITY.md) (correctness gate)
and [DASHBOARD_8801_UX.md](DASHBOARD_8801_UX.md) (UX direction).

## The model

- **Chat area** = dashboard UI over MCP/aify APIs. Sends/reads messages,
  work loops, runs. It is **not** the runtime.
- **Console** = operator attach/control/view into the agent's backing
  runtime terminal. Hidden consoles don't need to render; the backend
  still tracks output/state.
- **Message delivery** goes through the runtime wrapper attached to the
  agent backing process — `claude-aify`, `codex-aify`, `hermes-aify`,
  `omp/pi-aify`. **Not** ad-hoc service injection into a human console
  pane.
- **Status** is reported by the `*-aify` wrapper where possible: turn
  start/end, blocked/awaiting-input, idle prompt, fatal/error, session
  changed.
- **Backend status** aggregates wrapper signals + active runs + terminal
  liveness. It does **not** infer `working` from raw console bytes.
- **Dashboard status** displays backend truth, not client-only guesses
  (the F2 canonical resolver in the parity contract).

## Current mismatches (from pi — runtime/bridge lane)

1. **Codex/Pi/OpenCode are native-managed, not wrapper-backed.** The
   native decouple shipped (`22370e8` + the capability backfill +
   bridge self-heal); the target is wrapper-attached delivery for
   uniformity.
2. **Claude/Hermes still use service-created terminal input.** Made
   atomic/safer (the running-contract + safety nets); target is
   wrapper-owned delivery.
3. **Console/backing-terminal split is incomplete.** Today Console can
   become the same terminal used for delivery. Target: backing runtime
   exists independently; Console attaches/views/controls it.
4. **Status is partly inferred.** Some status comes from runs/terminal
   heuristics. Target: `*-aify` wrappers emit explicit turn/status
   events; backend aggregates.
5. **Hidden-console / activity-detection is partial.** Backend tracks
   terminal output, but the "console hidden, backing terminal active"
   model is not fully formalized.

## Lane split

- **Runtime/bridge convergence to this target** — comms-senior-dev-pi
  (with comms-senior-dev where it touches service). Their lane: items
  1–5 above, against `mcp/stdio/`, runtime wrappers, and the
  service-side status aggregator.
- **Dashboard adoption** — comms-tech-lead + comms-senior-dev. The
  dashboard side does NOT block on the runtime migration. As wrappers
  start emitting explicit status events, the F2 canonical resolver
  reads them through the same path; no UI redesign required. Per-flow
  assertions in the parity contract stay the bar.
- **Settings/operator-defaults touch points** (e.g. work-loop reminder
  defaults) — pi's lane while pi is on the old-dashboard/service polish
  arc, surfaced in the new dashboard via the existing Settings entity.

## Why this doc is short

This is a target reference, not a plan. The slice cadence and gates
already live in the parity contract; this doc just records the
architectural end-state so every slice can be sanity-checked against
it. If a proposed slice contradicts this model (e.g. moves us further
from wrapper-owned delivery or has the dashboard infer status from
console bytes), call it out at review.
