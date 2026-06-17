# Proof-based status — surgical rewrite plan (2026-06-18)

> Spec: `docs/superpowers/specs/2026-06-18-proof-based-status.md`. Two deep audits
> mapped every border (server status paths + wrapper/orphan mechanics). The clean
> model already exists as `status_engine.derive()` + `_gather_status_inputs`; this plan
> is mostly **removal** of time-decay + dual-engine cruft. Done phase-by-phase, each
> phase independently green (full pytest + node --test) and the matrix invariants held.

**Goal:** status is proof-driven — wrapper events + a single short liveness window —
with vocabulary `working/online/available/blocked/offline/stopped`. No idle/stale, no
minute decay.

**Key facts from audits:**
- Uniform 30s liveness beat everywhere → `agent_liveness_seconds = 90`.
- `idle` already unreachable under `new` (idle_too_long hardcoded False) — safe drop.
- `stale` IS emitted for residents (missing-handle / dead bridge) → folds to `offline` (behavioral).
- The OLD inline engine + the duplicated `StatusInputs` byproduct in `_compute_live_status_cache` are the biggest cruft; `status_engine` old|new flag removable once OLD is gone.
- `TURN_BUSY_BACKSTOP_SECONDS=1800` + in_turn clamps are pre-liveness-gate vestiges (derive() already gates in_turn on a live worker) → removable.
- `_status_with_dispatch`, `_enforce_live_worker_gate`, `_enforce_env_reachable_gate` are read-boundary patches that disappear once `refresh_after` keys on liveness not minutes.

---

## Phase 1 — Engine semantics (drop idle + stale). LOW risk.

Files: `service/status_engine.py`, `service/tests/test_status_engine.py`.

- `VALID_STATUSES` → `("working","online","available","blocked","offline","stopped")`.
- `StatusInputs`: remove `idle_too_long`. Keep `bridge_stale` (still gates resident `live`).
- `derive()`:
  - managed alive+worker → `online` (was idle/online).
  - resident alive+session+!bridge_stale → `online`.
  - resident else → `offline` (was `stale` on bridge_stale).
- Tests: delete `test_managed_idle_when_quiet_too_long`; rewrite the two resident-`stale` tests to expect `offline`; drop `idle_too_long` from `_inp` helper.
- Verify: `python -m pytest service/tests/test_status_engine.py -q`.

## Phase 2 — One liveness window, kill the minute decay. MEDIUM.

Files: `service/routers/api_v2.py`, `service/db.py` (DEFAULT_SETTINGS only).

- DEFAULT_SETTINGS: remove `idle_minutes`, `offline_minutes`; add `agent_liveness_seconds: 90`.
- `_managed_env_reachable` unbound fallback (`~3926`): use `agent_liveness_seconds` not `offline_minutes*60`.
- `_status_refresh_after`: drop idle/offline-minute candidates; key only on env/bridge/lease liveness windows (+ `agent_liveness_seconds`).
- `_gather_status_inputs`: `alive` = heartbeat within `agent_liveness_seconds`; drop the `TURN_BUSY_BACKSTOP` in_turn clamp (trust the event, gated by live worker in derive); keep console-working lease fold; keep awaiting-input.
- `_compute_agent_status` db-less fallback (`~6140`): use `agent_liveness_seconds`, emit only the 6 states.
- Tests: `test_status_taxonomy.py` signature; targeted sweeps.

## Phase 3 — Collapse to ONE engine + remove carve-outs. HIGH (served path).

Files: `service/routers/api_v2.py`, tests.

- Make `_refresh_agent_live_state` always `derive(_gather_status_inputs(...))`; delete the OLD inline cascade + the duplicated byproduct build in `_compute_live_status_cache` (keep only what `_gather_status_inputs` needs; the function becomes a thin wrapper or is merged).
- Remove `status_engine` old|new flag + disagreement logging.
- Delete `TURN_BUSY_BACKSTOP_SECONDS` + remaining clamps; `_status_with_dispatch` (working now comes from the engine's in_turn); `_enforce_live_worker_gate` + `_enforce_env_reachable_gate` (refresh_after now liveness-keyed).
- Queue-gate: keep a single busy signal via `agent_status_state.in_turn` + the short liveness window (retire `TURN_BUSY_STALE_SECONDS` if folded). Decide: keep `agent_turn_state.turn_busy` ONLY as the queue-gate bit, or migrate the gate to `agent_status_state`. (Prefer migrate → one event table.)
- Orphan→absence: managed env-reachable + dead worker stays `available` (lazy-start) BUT confirm sc-coder's dead-delivery-loop maps as the operator expects; resident dead-bridge → `offline`. Reapers keep cleaning rows; no detached loop keeps an agent online (liveness tied to owner).
- Tests: prune the two-engine/parity/byproduct tests; keep matrix invariants.

## Phase 4 — Borders (dashboards + remaining tests). MEDIUM.

- `service/new_dashboard/status.js`: drop `STATUS_KINDS.idle/.stale`; update the 8→6 contract comment + `status.test.mjs` list.
- `service/dashboard.html` (old): remove `.st-idle/.st-stale` + idle/stale from CHAT_STATUS_ORDER/LABELS/LIVE + presence/rank/bucket lists + the `['offline','stale','stopped']` checks (fold stale→offline) + help text.
- Settings UI (both dashboards): remove the idle/offline minute fields; (stale_agent_hours is rotation, not status — relabel/keep). Add `agent_liveness_seconds` if exposed.
- Sweep remaining test files asserting idle/stale/available (test_status_deliverability, test_resident_*, test_agent_status_read_gate, test_ready_status_endpoint, status_engine_integration).

## Phase 5 — Deploy + live-validate + (Phase 6 wrapper-side, deferred).

- Rebuild, live-validate SC team statuses match reality; watch for flap/false-offline.
- Phase 6 (after SC team's chunk): wrappers hold the turn flag for the whole turn + send a clean-disconnect offline beat + a `blocked`/awaiting-input producer. Eliminates the managed-claude flap at the source.

## Invariants to keep green throughout
managed reachable-env + dead-worker → available; resident bridge-gone → offline;
in-turn + live → working (blocked if awaiting-input); stopped wins first;
managed env-unreachable → offline. (Matrix tests in test_status_engine*.py.)
