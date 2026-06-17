# Status Accuracy & Queued-Dispatch Timing Remediation Plan

> **For agentic workers:** TDD, one fix per commit, container rebuild after `service/` changes, NEVER run opencode. Keep the status-matrix tests (`test_status_engine.py`, `test_status_taxonomy.py`) green throughout.

**Goal:** Make agent statuses real-time-accurate and correct for the priority harnesses (claude code, hermes, codex), and make queued/deferred work deliver only when the target returns to a ready state — fixing the operator's report that "statuses are delayed and often wrong, and queued stuff gets sent at wrong moments."

**Context — verified facts (deep review 2026-06-17, 7 reviewers):**
- Live deployment runs `status_engine='new'` (confirmed in container `/data/aify.db`), but `DEFAULT_SETTINGS` says `'old'`.
- Served status = `derive(cache["status_inputs"])` (`status_engine.py`).
- **Decisions (operator):** DROP `idle` (unused; "online" is the ready state — confirmed nothing branches on agent-status idle). RESTORE `blocked` (needs-attention signal; detector already exists). Priority harnesses: claude > hermes > codex.

**Root causes (all confirmed against source):**
1. Turn transitions (`/turn-start`, `/turn-end`, `/heartbeat` turnBusy flip, `/console-working`) **invalidate the live-state cache but do NOT broadcast** to the dashboard WS. Only `/status-event` broadcasts (`api_v2.py:15743`). → dashboard waits up to its ~60s poll to see a turn end. **Dominant "delayed" cause.**
2. `derive()` returns `working` whenever `in_turn` is set, checked **before** any liveness branch (`status_engine.py:44`). A dead managed worker / stale resident bridge with a stale `in_turn=1` reads `working` for up to 30 min (`TURN_BUSY_BACKSTOP_SECONDS`). **Dominant "wrong when queried" cause.**
3. The queued-dispatch claim gate (`api_v2.py:16634`) and the `queueIfBusy` send-path check (`api_v2.py:14648`) gate on a **raw `agent_turn_state.turn_busy` flag** (120s window) — not the liveness-aware engine status — and the `_has_claimable_steerable_run` bypass delivers queued runs **mid-turn**. Two windows (120s gate vs 30-min engine) disagree. **Dominant "sent at wrong moment" cause.**
4. `blocked`/`idle` unreachable under `new` (`awaiting_input`/`idle_too_long` never set). Dashboard run-status-mix dots render gray (raw status used as CSS class with no rule). Emission robustness gaps (resident codex no fast backstop; `/turn-end` not bridge-scoped). `DEFAULT_SETTINGS` flag mismatch + stale "no-op for old" comments.

---

## Workstreams (priority order)

### WS-1 — Real-time push on turn transitions (CRITICAL, highest leverage, lowest risk)
Add the flag-gated `_broadcast_engine_status(ws, db, agent_id, settings=settings)` call (the exact pattern `/status-event` already uses at `api_v2.py:15740-15743`) immediately after the existing `_invalidate_agent_live_state` + commit in: `/turn-start` (15848), `/turn-end` (15902), the `/heartbeat` turnBusy-flip block (15494), `/console-working` (15783). Collapses to-ready latency from ~60s to sub-second for all harnesses. No client change (dashboard already applies pushed status granularly).
- Test: each endpoint, under `status_engine='new'`, triggers a broadcast (assert `_broadcast_engine_status` called / WS receives the agent_status frame); under `'old'`, behavior unchanged.

### WS-2 — Liveness wins over `in_turn` in `derive()` (CRITICAL correctness)
Gate the in-turn states on liveness: `working`/`blocked` require, for managed `worker_present`, for resident `has_live_session and not bridge_stale`. A dead-worker/stale-bridge agent with a stale `in_turn` then correctly falls through to `available`/`offline`/`stale`. Keeps every existing `in_turn→working` test green (they all set `worker_present=True`).
- New tests: managed `in_turn=True, worker_present=False` → `available` (not working); resident `in_turn=True, bridge_stale=True` → `stale` (not working).
- Keep the caller's 30-min clamp (handles dropped-turn-end on a still-LIVE worker — complementary).

### WS-3 — Queue delivery gates on ready, not raw turn_busy (CRITICAL, operator #1 pain)
Replace the raw `turn_busy` read in `claim_dispatch` (`api_v2.py:16634`) and the `queueIfBusy` busy-check (`14648`) with the liveness-aware derived status: deliver a **queued** run only when the target derives `online` (ready). Unify the 120s-vs-30min windows onto the engine. **Preserve legitimate steering**: distinguish an explicit steer/inject (intentionally mid-turn) from a deferred queued turn — only the latter must wait for ready. Audit `_has_claimable_steerable_run` callers to confirm which runs are steers vs queued before narrowing the bypass.
- Tests: queued run to an in-turn (working) target is NOT delivered; same run delivered once target derives online; a steer to a working steerable target still injects.

### WS-4 — Emission robustness for the 3 harnesses (HIGH)
- `/turn-end` ownership guard: apply the same bridge-scope check the heartbeat `turnBusy:false` path uses (`api_v2.py:15476`) so a superseded bridge's detector can't false-clear a live successor's turn.
- Resident codex fast backstop: add a turn-state detector (or document best-effort in KNOWN_ISSUES) — today only hooks (possibly inert) + the 30-min clamp.
- Dropped-turn-end recovery: have the idle detector emit an authoritative end, or shorten the clamp for the live-worker case.

### WS-5 — Restore `blocked` (MEDIUM)
Wire the existing `_terminal_awaiting_input_hint` / `active_run_terminal_missing` (`api_v2.py:4541`) into `StatusInputs.awaiting_input` so the engine's `blocked` branch is reachable. Add `.status-dot.blocked` already exists; ensure dashboard renders it.
- Tests: gather-level → `blocked` when a console awaits input mid-turn.

### WS-6 — Dashboard run-status-mix dot coloring (LOW, quick win)
Route the run-status dot through `renderStatusDot`/`resolveStatus` (or add `data-status` + CSS) in `analytics.js:109` and `dashboard.html` so running/claimed/completed/cancelled/lost dots aren't all gray (they currently contradict the colored bar beside them).

### WS-7 — Flag alignment + cleanup (LOW)
Set `DEFAULT_SETTINGS["status_engine"]='new'` (align code with the live default + so new installs match). Fix the misleading "no-op for old" comments. Leave the `idle` engine branch (test-pinned, harmless) unwired per the drop-idle decision; optionally annotate it inert.

### WS-8 — Test gaps (folded into each WS via TDD)
`_reconcile_dead_session_status` (untested), disabled-via-`status='stopped'`, `unblocked` fold, env-down-mid-turn precedence, dashboard `inputEnabled`-per-status. Loosen the `status_engine=='old'` default test (pins a migrating value) and hardcoded backstop literals.

---

## Execution / verification
- Per WS: write failing test → implement → `python -m pytest <file> -q` (+ `node --test` for JS) → commit (Opus 4.8 co-author).
- After `service/` changes: `docker compose up -d --build new-dashboard` + the main service, browser hard-reload ×2, verify status pushes live.
- Full suite green (`pytest service/tests -k "not opencode"`, `node --test service/new_dashboard/*.test.mjs`) before reporting.
