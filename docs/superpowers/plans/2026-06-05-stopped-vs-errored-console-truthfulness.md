# Stopped-vs-Errored Console Truthfulness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

> **OUTCOME (2026-06-05):** Task 1 SHIPPED (console label honesty). **Task 2 was REJECTED during implementation** — a regression test (`test_managed_codex_online_from_fresh_wrapper_child_bridge`) proved that a managed agent with a failed last session is deliberately `available` (it lazy-respawns on the next send — genuinely available-to-retry, not blocked). Changing it to `blocked` broke that contract, and a reason-only annotation was pre-empted by the existing sidecar reason and added marginal value. The user's actual symptom ("stopped · Console attached") was a transient teardown race during the hermes resume error, removed at the root by `5c1617a` (DB-validated resume) + Task 1's honest label. Net: no status-engine change; `_compute_live_status_cache` carries only an explanatory NOTE.

**Goal:** A managed agent whose worker ended via a FAILURE (resume error / crash / auth fail) should read **errored**, not a clean **stopped**, and its console label must never say "attached" once the session is in a dead state.

**Context / why narrow:** The originally-reported symptom (`next-senior-dev`: "stopped · Console attached") was a TRANSIENT teardown race during the hermes "session not found" resume error. The companion fix `5c1617a` (DB-validate the resume id → clean fresh start) removes that resume error, so the observed case no longer occurs. Live verification (2026-06-05) showed the terminal row already self-cleaned (`terminalStatus=''`) and the reaper only touches ACTIVE terminals (so it never overwrites a `failed`→`stopped`). Error-exits already mark a session `failed` (server.js onExit `status: error ? "failed" : "stopped"`). This plan closes the two residual truthfulness gaps for OTHER failure modes.

**Verify-first:** before implementing, restart the `hermes-aify` wrappers (to load `5c1617a`) and confirm `next-*`/`cms-*` now come up fresh cleanly with NO "session not found" and NO "stopped · Console attached". If the symptom is gone (expected), this plan is a durable-truthfulness improvement, not a bug fix — implement only if the failed-vs-stopped distinction is wanted.

**Tech Stack:** FastAPI + SQLite (`service/`), the legacy dashboard (`service/dashboard.html`). DO NOT touch `new_dashboard/` — confirm which dashboard the operator actually uses first (containers `aify-comms-dashboard-next` = the new one; if that's in use, the label fix belongs there and is out of scope for this repo rule).

---

### Task 1: Console label never says "attached" for a dead session

**Files:**
- Modify: `service/dashboard.html` (~line 6561, the `consoleState.textContent` assignment)

- [ ] **Step 1: Make the label honest for dead sessions**

Replace the console-label assignment so a session in a dead state shows that state, never the terminal's stale active status:

```javascript
      const deadSession = ['stopped', 'ended', 'failed', 'lost', 'cancelled', 'completed']
        .includes(String(session?.status || '').toLowerCase());
      consoleState.textContent = (session?.terminalId && !deadSession)
        ? `Console ${status || 'active'}`
        : deadSession
          ? `Console ${String(session.status).toLowerCase()}`   // e.g. "Console failed" / "Console stopped"
          : available
            ? 'Console available'
            : 'Console unavailable';
```

- [ ] **Step 2: Rebuild + eyeball**

```bash
docker compose up -d --build && curl -s http://localhost:8800/health
```
Expected: a stopped/failed agent shows "Console stopped" / "Console failed", never "Console attached".

- [ ] **Step 3: Commit**

```bash
git add service/dashboard.html
git commit -m "fix(dashboard): never show 'Console attached' for a dead session"
```

---

### Task 2: A resume/worker FAILURE surfaces as errored, not a clean stop

**Files:**
- Modify: `service/routers/api_v2.py` (`_compute_live_status_cache` — the terminal_status handling ~line 4256/4365)
- Test: `service/tests/test_console_errored_status.py`

> Design decision REQUIRED before coding: the 8-status vocabulary (DECISIONS.md) is deliberately fixed. Either (a) add a 9th status `errored`, or (b) reuse `blocked` with an "errored" reason. Recommendation: REUSE `blocked` (it already means "needs operator attention / can't proceed") with `reason="Last session failed: <classification>"` — no vocabulary change, no dashboard bucket migration. Confirm with the operator.

- [ ] **Step 1: Write the failing test**

```python
"""A managed agent whose most-recent session ended 'failed' (worker crash / auth /
resume error) surfaces as blocked-with-errored-reason, distinct from a clean 'stopped'."""
import sqlite3
from service.tests._base import FastApiTestCase


class ConsoleErroredStatusTests(FastApiTestCase):
    DB_NAME = "aify-test-errored.db"

    def _register(self, agent_id, **extra):
        payload = {"agentId": agent_id, "role": "coder"}
        payload.update(extra)
        r = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(r.status_code, 200, r.text)

    def test_failed_session_reads_errored_not_stopped(self):
        self._register("err-agent", runtime="hermes", sessionMode="managed")
        # Simulate a failed terminal-end for this agent's session (the bridge posts
        # status='failed' on an error exit). Insert a failed terminal_sessions row.
        con = sqlite3.connect(str(self._db_path))
        con.execute(
            "INSERT INTO terminal_sessions (id, agent_id, status, updated_at) VALUES (?,?,?,?)",
            ("term-err", "err-agent", "failed", "2026-06-05T06:00:00Z"),
        )
        con.commit(); con.close()
        body = self.client.get("/api/v1/agents/err-agent").json()
        self.assertIn(body.get("status"), {"blocked", "errored"})
        self.assertIn("fail", (body.get("statusReason") or "").lower())
```

> Confirm the `terminal_sessions` column names against `service/db.py` before writing the INSERT, and confirm how `/api/v1/agents/{id}` exposes status/reason (it may be `status`/`statusReason` or nested). Adjust the asserts to the real shape.

- [ ] **Step 2: Run it (fails — failed session currently reads stopped/available)**

Run: `docker exec aify-comms-service sh -c "cd /app && python -m pytest service/tests/test_console_errored_status.py -q"`
Expected: FAIL.

- [ ] **Step 3: Implement in `_compute_live_status_cache`**

Add a branch (before the final `available` fallback) that, for a managed agent whose most-recent session/terminal ended `failed` and which has no live worker, sets `effective_status = "blocked"` (or `"errored"` per the decision) with `reason = "Last session failed: <classification>"`. Read the most-recent terminal/session status the same way the existing derivation does; do NOT let it override `offline`/`stale` (those precede it). Keep it additive and behind the no-live-worker condition.

- [ ] **Step 4: Run the test + the full status suite**

```bash
docker exec aify-comms-service sh -c "cd /app && python -m pytest service/tests/test_console_errored_status.py service/tests/test_status_engine_integration.py -q"
```
Expected: all pass (no regression in the 375-test status suite).

- [ ] **Step 5: If a new `errored` status was chosen**, also update `service/status_engine.py` `derive()`, the dashboard status buckets, and DECISIONS.md (the vocabulary is now 9). If `blocked` was reused, only DECISIONS.md needs a note.

- [ ] **Step 6: Commit + deploy + docs**

```bash
git add service/routers/api_v2.py service/tests/test_console_errored_status.py DECISIONS.md
git commit -m "feat(status): a failed managed session reads errored, not a clean stop"
docker compose up -d --build && curl -s http://localhost:8800/health
```

---

## Self-Review

**Spec coverage:** console label honesty for dead sessions (Task 1); failed-end surfaces as errored not stopped (Task 2). **Open decisions flagged, not hidden:** which dashboard is in use (new vs legacy), and reuse-`blocked` vs new-`errored` status. **Verify-first** step prevents building for a trigger that `5c1617a` already removed.
