# Runtime Delivery — Target Architecture

Status: **TARGET MODEL — DELIVERED ACROSS ALL RUNTIMES (2026-05-25).**
Captured from operator + comms-senior-dev-pi conversation (2026-05-20).
Companion to [DASHBOARD_8801_PARITY.md](DASHBOARD_8801_PARITY.md)
(correctness gate) and [DASHBOARD_8801_UX.md](DASHBOARD_8801_UX.md)
(UX direction).

> **Current-model note (2026-06-03):** this is a target/design reference and a
> few specifics have since moved on. Hermes delivery is now the visible-TUI
> gateway-host model with a native-session-id scheme (see
> [HERMES_INTEGRATION.md](HERMES_INTEGRATION.md)): managed and resident both run
> a hidden per-agent gateway host plus a `hermes-managed-host.js` delivery loop
> that submits into the visible TUI's real session — it is not raw wrapper-PTY
> injection. Status is event-driven (turn-start → `working`, turn-end → `online`)
> with liveness heartbeats. The RuntimeAdapter / per-runtime controller split
> below shipped and is accurate; the line-count figures in "Remaining gaps" are
> point-in-time and have drifted.

**Implementation status (post Plans 1+2+3):**
- **All runtimes go through a unified `RuntimeAdapter` abstraction.** JS
  adapters at `mcp/stdio/adapters/`, Python mirror at `service/runtimes/`.
  Each adapter declares capabilities (`supports_resident`,
  `supports_managed`, `supports_steering`, etc.) and the bridge/server
  consult those instead of branching on `runtime == "..."`. Cross-language
  consistency enforced by `service/tests/test_runtime_adapter_consistency.py`.
- **Per-runtime controllers** live in `mcp/stdio/controllers/` — one file
  per runtime (plus per-mode subclasses for hermes + codex), each
  ≤400 lines. Adapter's `controllerFor(opts)` returns the right
  controller instance; `launchRuntimeRun` collapses to a single
  `adapter.controllerFor(opts).start(ctx)` call.
- **Session handles** are captured back into `agents.session_handle` via
  the bridge's 60s heartbeat (`mcp/stdio/session-handle-heartbeat.js`).
  Stable across all launch modes; closes the "missing handles all the time"
  operator pain.
- **Pi delivery flip:** `pi-session-resume` spawn-fresh-worker pattern
  removed. Pi managed delivery uses one persistent bridge-owned
  `omp --mode rpc` child per agent plus a virtual terminal stream. Pi is
  excluded from `managed_via_wrapper` because OMP is single-client and the
  dashboard Console must share the same native RPC controller. Ownership
  changes are manual through the dashboard switch controls.
- **Codex carve-out removed:** Console now resumes the stored handle for
  codex too. `codex-aify` wrapper gained a try-resume-then-fresh fallback
  for stale session files.
- **OpenCode:** still managed-only (no `opencode serve` integration yet).
  Adapter declares `preferredDeliveryMode = "managed"`. Tracked follow-up
  for full integration.
- **`channelEnabled` per-config gate** restored via `adapter.is_resident_ready(runtime_config)` —
  claude resident agents must have `channelEnabled=True` before advertising
  `resident-run`. Hermes resident requires a valid `gatewayUrl`.

## The model

- **Chat area** = dashboard UI over MCP/aify APIs. Sends/reads messages,
  work loops, runs. It is **not** the runtime.
- **Console** = operator attach/control/view into the agent's backing
  runtime terminal. Hidden consoles don't need to render; the backend
  still tracks output/state.
- **Message delivery** goes through the runtime backing process: the
  `claude-aify` wrapper PTY (Claude), the Codex app-server via the `codex-aify`
  wrapper child bridge, the per-agent Hermes gateway host + `hermes-managed-host.js`
  delivery loop (Hermes), or the persistent OMP RPC virtual terminal (Pi).
  **Not** ad-hoc service injection into a human console pane.
- **Status** is reported by the `*-aify` wrapper where possible: turn
  start/end, blocked/awaiting-input, idle prompt, fatal/error, session
  changed.
- **Backend status** aggregates wrapper signals + active runs + terminal
  liveness. It does **not** infer `working` from raw console bytes.
- **Dashboard status** displays backend truth, not client-only guesses
  (the F2 canonical resolver in the parity contract).

## Remaining gaps (post Plans 1+2+3)

Most of the original mismatches are closed by the RuntimeAdapter
refactor. Surviving items:

1. **runtimes.js still ~2200 lines** — the per-runtime controllers
   extracted out, but helper functions (codex-config, codex-live-discovery,
   executable-resolution, RPC clients) still live in the monolith. Plan 3
   follow-up tracked: split into per-concern modules so runtimes.js
   reaches the ≤500 target.
2. **`service/routers/api_v2.py` is ~18800 lines** — egregious 500-line
   rule violation. Separate plan (Plan 5 territory). Plans 1+2+3 deliberately
   did NOT add to its bulk; new code went into the runtimes/ adapter package
   and the pi-flip helpers stayed surgical.
3. **Runtime-ready event hook:** delivered. `ready` is now an internal bridge readiness bit; public agent status uses `online` for live idle workers and `available` for spawnable idle identities.
4. **Opencode multi-client wiring:** opencode supports `opencode serve` for
   ACP multi-client delivery but aify-comms hasn't integrated. Follow-up.
5. **codex-aify --remote + resume subcommand ordering:** validation needed
   live (task #118) — Plan 1 shipped the fallback shape but the exact
   `codex --remote URL ... resume HANDLE` ordering hasn't been verified
   on the operator's machine.

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
