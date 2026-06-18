# Status System — Follow-up Improvements Plan

> Status: PLAN. The 2026-06-18 round shipped the functional/cleanup items (dead `idle_too_long`
> field removed, dangling `status_engine` UI control removed, the removed `'stale'` writer
> removed + `stale→offline` canonicalization, doc drift reconciled). The items below are the
> deferred, higher-touch improvements the status audit surfaced. None is a correctness hole —
> `derive()` is a clean single authority and all 3 harnesses × both modes are covered.

## 1. Consolidate the two hand-synced StatusInputs builders (medium, do behind tests)
`service/routers/api_v2.py` builds `StatusInputs` in TWO places that MUST stay identical:
`_gather_status_inputs` (~4368) and the `_compute_live_status_cache` byproduct (~4909). They
agree today, kept in parity by comments ("byproduct-parity promise") — a standing maintenance
hazard. Extract ONE shared builder fed by the already-computed locals and call it from both.
The matrix-invariant tests (managed reachable+dead-worker→available; managed-claude
live-sidecar+no-console→available; hermes working-while-delivering→working; resident
stale→offline) gate the change. Risk: medium (hot path). Win: removes the most fragile seam.

## 2. Resolve the console-keepalive (the last #224 surface) (medium, needs a live repro)
`mcp/stdio/terminal-runtime.js` pauses SIGWINCH nudging after sustained idle classification
(`consoleKeepaliveIdleGraceTicks`), letting the `CONSOLE_WORKING_LEASE_SECONDS=20` spinner
lease go stale — the secondary #224 "online while thinking" mechanism. Since the transcript
detector now backstops managed-claude `working`, the spinner lease is no longer load-bearing.
Decide ONE of:
- (a) remove the console-keepalive + the `agent_console_signal` working lease entirely as
  redundant for status (preferred — least code), OR
- (b) ship the deferred KNOWN_ISSUES candidate: gate the idle-grace pause on "no active turn".
Either way, FIRST reproduce a rate-limited managed-claude turn with the Console CLOSED and
confirm the transcript detector alone holds `working` (so removal is safe). Bridge change →
needs `install.sh` rerun + wrapper restart; do it when the fleet can tolerate a restart.

## 3. Clarify `alive` vs `worker_present` in StatusInputs (low, clarity only)
`service/status_engine.py`: both build sites set `alive = worker_present`, so `derive()`'s
`if i.alive and i.worker_present` is just `worker_present` — `alive` is effectively dead.
Either give `alive` an independent meaning (the 90s heartbeat freshness, distinct from worker
presence) or drop it and use `worker_present`. Cosmetic; do it in a quiet cleanup pass.

## 4. Prune residual defensive `'stale'` branches (low, after a deploy cycle)
`api_v2.py` ~3753/6349/17287/20187 still tolerate a raw `'stale'` status. They are now
belt-and-suspenders for any legacy/foreign DB row (the proof engine never emits `stale`, the
cleanup writer was removed, and `_LEGACY_RAW_STATUS_TO_CANONICAL` maps `stale→offline`). Safe
to keep; prune only once you're confident no live DB carries a legacy `'stale'` row.

## 5. (Optional) misleading test names (cosmetic)
`service/tests/test_status_engine_integration.py` still calls `self._set("status_engine","new")`
(a settings row no live code reads) and names like `test_flag_new_serves_engine_status`. The
assertions exercise real always-on proof-engine behavior so they pass meaningfully; the setup
lines are dead and the names misleading. Rename + drop the dead `_set` lines in a test-tidy pass.
