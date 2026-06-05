# Managed Boot-vs-Deaf Status — Investigation + Design

> **IMPLEMENTED 2026-06-05 (time-window-FREE).** Dropped the boot-grace entirely (operator: "age/time-based solutions have only bitten us"). Final signal: `_managed_console_is_booting` = no channel-sidecar last-seen at/after the live console's `created_at`. Display-only override (`available`→`online`); routing untouched. The 3 `13c4ae8` contract tests were re-modeled (deaf = sidecar after console, then stale) and 2 booting tests added (fresh + cross-restart). 383 status/deliverability/regression tests green.


**Goal:** A *booting* managed claude/hermes (console up, sidecar coming) should read `online`, while a *deaf* one (console up, sidecar registered then died — the `13c4ae8` bug) stays `available`. The hard part is telling them apart from a single state snapshot.

## Investigation findings (2026-06-05)

1. **The distinction is about the future** (will the sidecar register?), so every signal is a heuristic. Console *age* alone fails: a deaf console can also be recent (the contract tests stamp exactly that).

2. **The spawn_request lifecycle does NOT give a transient boot signal.** Flow (bridge-side, server.js:2616-2642): `queued → claimed → starting → running`. Crucially `_SPAWN_TERMINAL_STATUSES = {running, failed, cancelled}` — **`running` is TERMINAL** and is PATCHed *before* the wrapper PTY/sidecar even come up, then persists for the worker's whole life. So "spawn is running" is true for a long-lived deaf worker too → useless as a boot discriminator. (It passes the current tests only because they create no spawn_request, but it would be wrong in production.)

3. **The contract tests model "deaf" abstractly, without realistic timestamps.** `test_managed_claude_live_pty_dead_sidecar_is_available_not_online` stamps a **stale** sidecar (`fresh=False`, old `last_seen`) + a **fresh** PTY (created now). Realistically that ordering (sidecar older than the console) reads as "old sidecar from a prior session, new console booting" — so any correct boot-online rule conflicts with the test as written. The 3 failing tests must be updated to model the deaf case properly (sidecar `last_seen` AFTER the console's `created_at`).

4. **The clean production signal: "has a sidecar been seen since THIS console started?"**
   - **No** sidecar `last_seen >= console.created_at` (and no fresh sidecar) → the sidecar hasn't come up for this console yet → **booting**.
   - **Yes**, but now stale → a sidecar registered for this console then died → **deaf** → `available` (`13c4ae8` preserved).
   - Cross-restart safe: an old sidecar row from a prior session has `last_seen < new console.created_at` → still reads booting (correct).
   - Bounded by a console-age grace so a boot whose sidecar NEVER arrives (claude crashed in init) drops to `available` instead of pinning `online` forever.

## Design

- `_managed_console_is_booting(db, agent_id)` → True iff: a live console exists; its `created_at` is within `MANAGED_CONSOLE_BOOT_GRACE_SECONDS` (~180s); AND **no** `channel-sidecar` bridge row for the agent has `last_seen >= console.created_at`.
- **DISPLAY-ONLY** override in `_compute_live_status_cache` (NOT `_worker_liveness_for` — that drives delivery routing; a send during boot must still queue): when `effective_status == "available"` and `channel_managed_no_sidecar` and `_managed_console_is_booting(...)` → `effective_status = "online"`, reason "Console booting (deliverable once it claims)."
- **v2 caveat:** legacy-path only; live engine is `old`. A `status_engine=new` flip would need the same signal fed into `StatusInputs` for parity.

## Tasks
1. Add `_managed_console_is_booting` (query the live console `created_at` + the latest channel-sidecar bridge `last_seen`; compare).
2. Add the display-only override (worker-liveness/routing untouched — re-run the 30 routing tests to confirm no change).
3. Update the 3 contract tests to model the **deaf** case as "sidecar `last_seen` after the console `created_at`" (so they still assert `available` for a genuinely deaf worker), and add a NEW test for the **booting** case (no sidecar since console start → `online`).
4. Run the full status + deliverability suite; expect green with the updated contracts.

## Risk
Touches the `13c4ae8` contract tests — must keep them asserting `available` for the *genuinely deaf* case (sidecar-after-console), only allowing `online` for the *never-claimed-yet* case. The resume fix (`e741dc9`) already shrinks the boot window by removing wrong-compact stalls, so this is a polish, not a critical fix.
