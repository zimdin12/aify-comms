# Managed-Claude `working` When Console Unwatched — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** A managed claude that is working reads `working` even when its dashboard Console is **closed** — today it flaps to `online` because the unwatched PTY stops emitting the spinner footer, so the console-working lease (Plan 1) goes stale.

**Root cause (confirmed live, next-manager 2026-06-05):** The console-working lease refreshes from managed-PTY output (the `✻ <verb> for <time>` footer). Claude's Ink TUI only redraws that footer when its PTY is actively attached/rendered — opening the dashboard Console sends resize/attach → claude repaints → footer streams → lease refreshes. **Unwatched, claude stops emitting the footer** (verified: `terminal_sessions.output` was 74s stale while claude was working, the lease 39s stale), so the 12s lease expires → `online`. Delivery is unaffected — this is purely the status dot.

**Architecture:** Add a periodic **repaint keepalive** in the bridge that owns the managed claude PTY (`terminal-runtime.js` / the env-bridge `TERMINAL_MANAGER`): every few seconds, nudge a claude-code managed PTY with a SIGWINCH (a `term.resize(...)`) so claude re-emits its footer on its own cadence — refreshing the spinner classification + lease whether or not the operator is watching. Align the lease TTL to the keepalive cadence so brief gaps don't drop `working`. Best-effort, claude-managed-only, minimal flicker.

**Tech Stack:** Node ESM bridge (`mcp/stdio/terminal-runtime.js`, `server.js`), FastAPI/SQLite (`service/routers/api_v2.py` — the lease TTL constant). node:test for the timer/gate logic; live verification for the actual repaint behavior (can't unit-test claude's render).

**Key uncertainty (verify in Task 2 before tuning):** does a SIGWINCH actually make claude re-emit the footer, and does a *same-size* resize send SIGWINCH at all (node-pty may treat it as a no-op)? Task 2 settles this empirically; Task 5 is the fallback if SIGWINCH doesn't work.

---

### Task 1: Periodic repaint keepalive for managed claude PTYs

**Files:**
- Modify: `mcp/stdio/terminal-runtime.js` (constructor ~138; `startPty` state ~238-263; new `_armConsoleKeepalive` + teardown in `_handleExit`)
- Test: `mcp/stdio/tests/terminal-runtime-console-keepalive.test.js`

- [ ] **Step 1: Write the failing test (gate + cadence logic, no real PTY)**

```js
#!/usr/bin/env node
import assert from "node:assert/strict";
import { TerminalProcessManager } from "../terminal-runtime.js";

// Only claude-code managed PTYs get the keepalive; it calls resize on a cadence.
const resizes = [];
const mgr = new TerminalProcessManager({ onOutput: async () => {}, consoleKeepaliveMs: 5 });
// claude managed pty: stub resize to record calls
const claude = { id: "t1", runtime: "claude-code", sessionMode: "managed", kind: "pty", cols: 100, rows: 28,
  term: { resize: (c, r) => resizes.push([c, r]) } };
mgr.terminals.set("t1", claude);
const stop = mgr._armConsoleKeepalive("t1", claude);
await new Promise((r) => setTimeout(r, 25));
stop();
assert.ok(resizes.length >= 1, "claude managed pty is poked at least once");

// non-claude or resident → no keepalive armed (returns a noop, never resizes).
const codex = { id: "t2", runtime: "codex", sessionMode: "managed", kind: "pty", term: { resize: () => assert.fail("must not poke codex") } };
mgr.terminals.set("t2", codex);
const stop2 = mgr._armConsoleKeepalive("t2", codex);
await new Promise((r) => setTimeout(r, 25));
stop2();

console.log("terminal-runtime-console-keepalive.test.js: all assertions passed");
```

- [ ] **Step 2: Run it (fails — `_armConsoleKeepalive`/`consoleKeepaliveMs` undefined)**

Run: `node mcp/stdio/tests/terminal-runtime-console-keepalive.test.js` → FAIL.

- [ ] **Step 3: Add the constructor option**

In the `TerminalProcessManager` constructor options (terminal-runtime.js ~138), add `consoleKeepaliveMs = 4000,` and after `this.autoAnswerKeyDelayMs = ...` add:

```js
    // Managed-claude repaint keepalive (2026-06-05): claude only re-emits its spinner footer
    // while its PTY is actively rendered, so an UNWATCHED working claude goes quiet on the PTY
    // and the console-working lease goes stale → `online`. Nudge the PTY so it keeps emitting.
    this.consoleKeepaliveMs = Math.max(0, Number(consoleKeepaliveMs) || 0);
```

- [ ] **Step 4: Add `_armConsoleKeepalive`**

Add a method (near `input`/`_sendAnswer`):

```js
  // Periodically SIGWINCH a managed claude PTY (via resize to the SAME dims) so claude re-emits
  // its footer even when the dashboard Console is closed — keeping the console-working lease
  // fresh. claude-managed-only; best-effort; a noop for other runtimes / when disabled.
  _armConsoleKeepalive(id, state) {
    if (!this.consoleKeepaliveMs || state.runtime !== "claude-code"
        || state.sessionMode !== "managed" || state.kind !== "pty") {
      return () => {};
    }
    const tick = () => {
      const st = this.terminals.get(id);
      if (!st || !st.term) return;
      try { st.term.resize(Math.max(20, Number(st.cols || 100)), Math.max(6, Number(st.rows || 28))); }
      catch { /* best-effort */ }
    };
    const timer = setInterval(tick, this.consoleKeepaliveMs);
    if (typeof timer.unref === "function") timer.unref();
    return () => clearInterval(timer);
  }
```

- [ ] **Step 5: Arm it on PTY start, store the stop fn, clear it on exit**

In `startPty`, after `this.terminals.set(id, state);` (and the `term.onData`/`term.onExit` wiring), add:

```js
    state.stopConsoleKeepalive = this._armConsoleKeepalive(id, state);
```

In `_handleExit`, where the terminal is finalized (after `state.finalized = true;`), add:

```js
    try { state.stopConsoleKeepalive?.(); } catch { /* best-effort */ }
```

- [ ] **Step 6: Run the test → PASS; syntax-check; commit**

```bash
node mcp/stdio/tests/terminal-runtime-console-keepalive.test.js
node --check mcp/stdio/terminal-runtime.js
git add mcp/stdio/terminal-runtime.js mcp/stdio/tests/terminal-runtime-console-keepalive.test.js
git commit -m "feat(status): repaint keepalive for managed claude PTY (keep console-working lease fresh when unwatched)"
```

---

### Task 2: Live-verify the keepalive actually makes claude stream (the make-or-break step)

**Files:** none (deploy + observe).

- [ ] **Step 1: Deploy the bridge**

```bash
bash install.sh --client claude http://localhost:8800
```
Then restart the env bridge / the managed claude wrapper so the new `TERMINAL_MANAGER` is live.

- [ ] **Step 2: With a managed claude WORKING and its Console CLOSED, watch the PTY output age + lease**

```bash
watch -n2 'docker exec aify-comms-service python3 -c "
import sqlite3,datetime as d
c=sqlite3.connect(chr(47)+chr(100)+chr(97)+chr(116)+chr(97)+chr(47)+\"aify.db\");c.row_factory=sqlite3.Row
now=d.datetime.now(d.timezone.utc)
def age(t):
 import datetime;return round((now-d.datetime.fromisoformat(t.replace(chr(90),chr(43)+chr(48)+chr(48)+chr(58)+chr(48)+chr(48)))).total_seconds())
r=c.execute(\"SELECT updated_at FROM terminal_sessions WHERE agent_id=? AND status=chr(97)... \").fetchone()
"'
```
(Simpler: `curl …/agents/<id>` and `cat` the lease row.) **Expected:** with the keepalive on, the PTY `updated_at` advances every ~4s while claude works → the lease stays fresh → status holds `working` even with the Console closed.

- [ ] **Step 3: Decide based on the observation**
  - If the footer streams + status holds `working` → keepalive works → proceed to Task 3.
  - If the PTY STILL goes stale (same-size resize is a no-op / claude ignores SIGWINCH) → keepalive insufficient → go to Task 5 (fallback) and revisit the resize shape (toggle cols ±1).

---

### Task 3: Align the lease TTL to the keepalive cadence

**Files:**
- Modify: `service/routers/api_v2.py` (`CONSOLE_WORKING_LEASE_SECONDS`)
- Test: extend `service/tests/test_console_working_lease.py`

- [ ] **Step 1: Bump the TTL to comfortably exceed the keepalive interval**

`CONSOLE_WORKING_LEASE_SECONDS = 12` → `20` (≈ 5× the 4s keepalive, so a missed poke or two never drops `working`; still self-heals quickly when claude truly stops). Update the comment to reference the keepalive.

- [ ] **Step 2: Run the lease suite + status regression in the container**

```bash
docker cp service/routers/api_v2.py aify-comms-service:/app/service/routers/api_v2.py
docker exec aify-comms-service sh -c "cd /app && pip install -q pytest pytest-asyncio; python -m pytest service/tests/test_console_working_lease.py service/tests/test_status_engine_integration.py -q"
```
Expected: green (the existing TTL tests use the constant, so they track the new value).

- [ ] **Step 3: Rebuild + commit**

```bash
docker compose up -d --build && curl -s http://localhost:8800/health
git add service/routers/api_v2.py service/tests/test_console_working_lease.py
git commit -m "tune(status): console-working lease TTL 12s→20s to span the PTY keepalive cadence"
```

---

### Task 4: Guardrails + docs

- [ ] **Step 1: Confirm no visible-console flicker** when the operator opens the Console while the keepalive runs (same-size resize should be invisible; if Task 2 switched to a ±1 toggle, verify the flicker is acceptable, else gate the keepalive to pause while a console viewer is actively attached).
- [ ] **Step 2: Document** in `DECISIONS.md` + the `aify-comms-debug` skill (both mirrors): managed-claude `working` now survives a closed Console via a PTY repaint keepalive; the lease TTL spans the keepalive cadence; it's claude-managed-only and best-effort.
- [ ] **Step 3: Commit docs.**

---

### Task 5: FALLBACK (only if Task 2 shows the keepalive can't make claude stream)

The robust non-PTY signal is the **transcript turn-detector** for managed claude. At session start (2026-06-05) the managed-claude under-report was traced to the transcript detector possibly not firing in the managed worker's `server.js` MCP child. If the keepalive fails:

- [ ] Verify whether `startClaudeTurnEndDetector` runs in the **managed** claude worker (it's gated on `AIFY_AGENT_ID && adapter.name === "claude-code" && transcriptTail` — server.js ~368) and whether `transcriptTail` resolves the right transcript (the managed worker's cwd / `AIFY_AGENT_CWD` must yield the correct `~/.claude/projects/<enc>` dir).
- [ ] If it's not firing or resolves the wrong path, fix the cwd/sid resolution so the transcript detector posts `/turn-start` during tool-use phases — covering the bulk of "working" (the thinking-only blind window remains, but tool phases dominate). Combine with the lease for the rest.

---

## Self-Review

**Spec coverage:** keepalive that forces the PTY to stream while unwatched (Task 1) ✓; the make-or-break live verification BEFORE tuning (Task 2) ✓; TTL alignment (Task 3) ✓; flicker guard + docs (Task 4) ✓; an explicit fallback if claude ignores SIGWINCH (Task 5) ✓. **Placeholder scan:** the Task-2 watch command is illustrative (the real check is "PTY updated_at advances every ~4s"); everything else is concrete code. **Type consistency:** `consoleKeepaliveMs`/`_armConsoleKeepalive`/`state.stopConsoleKeepalive` consistent across Tasks 1's steps; `CONSOLE_WORKING_LEASE_SECONDS` is the same constant the existing lease tests use.
