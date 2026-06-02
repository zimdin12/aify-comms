# Test-Suite Consolidation Plan (2026-06-02)

From a read-only audit. Goal: cut wall-clock + clutter WITHOUT losing behavioral coverage. ~166 files, ~1000+ cases (JS `mcp/stdio/tests/` via `run-all.mjs`; Python `service/tests/` via pytest).

## P0 — highest value, safest (do first)
1. **GAP: orphaned controller tests.** `mcp/stdio/tests/run-all.mjs` walks only `["tests","tests/adapters"]` — `tests/controllers/*.test.js` (7 files / 21 cases) NEVER RUN. Wire `tests/controllers` into run-all (or delete if superseded by `adapters/controller-for.test.js`). Decide, don't leave silently dead. Run them; fix any real failures surfaced.
2. **Pytest per-test fixed cost (biggest wall-clock lever).** ~25 `unittest.TestCase` files run `asyncio.run(init_db())` (23 CREATE TABLE) + build FastAPI app + TestClient + a settings PUT in `setUp`, ×~600. Add `service/tests/conftest.py` with a **session-scoped schema template** (init_db once into a template DB file, `shutil.copy` per test for isolation) and build the app once per class; keep only per-test data reset. Hoist the legacy settings PUT into a shared base so only tests needing pre-Plan-4 defaults pay it. No behavior change, no test deleted.

## P1 — merges/parametrization (cut count + spawn overhead; preserve coverage)
JS:
- Merge shutdown-all trio (`codex/hermes/hermes-gateway-shutdown-all.test.js`) → 1 table-driven (3→1).
- Merge permissions trio (`managed-claude/managed-codex/opencode-permissions`) → 1 parametrized (3→1; add hermes/pi rows).
- Fold `turn-busy.test.js` → `turn-busy-heartbeat.test.js` (2→1).
- Dedupe `hermes-turn-repulse.test.js` ↔ `hermes-managed-host.test.js` (remove duplicate re-pulse assertions; keep one canonical).
- Collapse claude-channel files (`content/marker-binding/feedback-loop/parent-guard`) → one `claude-channel.test.js` with sections (4→1).
- Fold tiny codex stubs (`codex-aify-mcp-tool-item`, `codex-runtime-fatal-log`, `codex-session-terminal-marker`) into neighbors (3→0 new).

Pytest:
- Parametrize install session-rediscover cluster (`codex/pi/hermes` + `claude-validate`) → 1 module (4→1).
- Parametrize per-runtime spawn-default tests in the big file.
- **Split `test_api_v2_regressions.py` (13,014 lines / 308 tests, 26× the 500-line rule)** into ~8 right-sized files sharing the new conftest: `test_managed_lifecycle.py`, `test_status_taxonomy_gate.py`, `test_dispatch_claim.py`, `test_channels_messages.py`, `test_terminal_console.py`, `test_contracts_reminders.py`, `test_environments.py`, `test_sessions.py`. (Prereq for `pytest -n auto` to help.)
- Table-drive redundant clusters in the big file: orphaned/requeue matrix (~9→1-2), idle-prompt-closes (~8→2), triggered-send require_reply matrix (~9→2), route-delivered-shows-online trio (3→1), online-requires-live pair (2→1).

## Deletes (verify-then-remove)
- `test_synth_terminal_deprecation.py` (asserts removed feature deprecated), `test_hermes_session_resume_removed.py` (fold to one regression line), `test_default_settings_plan4.py` (fold into `test_settings_manual_mode.py`). Orphaned `controllers/*` if dup of `adapters/controller-for.test.js`.

## E2E swaps (more confidence; retire isolated units)
Add 2 real-fixture E2E using `mcp/stdio/tests/fixtures/fake-hermes-acp.mjs`: (1) happy path spawn→claim→loop-ready+claimer-acquire→deliver→turn-end clears working→queued delivers→release teardown+port-kill+marker clear; (2) gateway connect-refused → run fails actionable + resident-lost once. Retire ~6-8 isolated `runDeliveryLoop`/`makeInFlightProbe` micro-units the E2E now covers.

## DO NOT merge/delete (load-bearing — the lifecycle/status path just shipped)
- `mcp/stdio/tests/hermes-managed-host.test.js` (refactor internally OK; every distinct lifecycle behavior must survive).
- `test_claimer_lease.py`, `test_dispatch_channel_claim.py`, `test_status_deliverability.py`, `test_status_taxonomy.py`, `test_hermes_visible_tui_delivery_routing.py`, `test_lifecycle_phase7.py`, `test_session_mode_fsm.py`.
- Turn-end/turn-busy tests (`test_turn_end_event_flips_managed_hermes_off_working_immediately`, `..._releases_queued_run...`, `test_turn_busy_status_uses_long_backstop_but_claim_gate_stays_short`, `test_reply_landing_clears_turn_busy...`) — keep verbatim, relocate only.
- Managed-worker hygiene/reap tests (visible-TUI-violation guard) — keep as a group.

## Expected
Files: JS 118→~95; pytest 48→~44 + the 13k monolith → 8 files. ~120-150 redundant cases → ~30-40 parametrized (net ~-100, no coverage loss). Wall-clock: dominated by P0 #2 (conftest), then JS file-count reduction, enabled by the monolith split for `-n auto`.
