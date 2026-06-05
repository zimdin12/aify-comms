# Managed-Claude Console Working-Signal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop managed claude from showing `online` while it is actively thinking/generating, by deriving `working` from the claude TUI's own spinner line in the console PTY tail.

**Architecture:** The transcript turn-detector is structurally blind to live generation (claude's transcript only grows per *completed message*, so a long "✻ Crunched for 14m 58s" thinking phase isn't in the transcript yet — the tail still shows the last *ended* message). The one signal that tracks live generation is the TUI working footer streaming through the managed PTY. We add a pure classifier (`claude-console-spinner.js`) that recognizes the working footer, gate it into the host-side `_handleOutput` PTY loop (`terminal-runtime.js`), and replace the crude any-output `pulseTerminalTurnBusy` (server.js:643 — the existing "shitty version" that pumps the shared `turn_busy` field and fights the transcript `/turn-end` clear) with a dedicated, TTL-bounded **console-working lease** that is OR'd into the server's derived `working`. The lease never clobbers `turn_busy`, so it can't fight the authoritative clear path; it only ADDS `working` coverage during the spinner window and self-expires when the spinner stops.

**Tech Stack:** Node.js ESM (host bridge, `mcp/stdio/`), FastAPI + SQLite (`service/`), node:test for bridge unit tests, pytest for service tests.

**Signal strength (design intent, per operator):** the spinner footer (`✻ <verb> for <time>` / `esc to interrupt`) is a STRONG, specific "claude is working" signal and is what drives the lease. Raw byte-activity (the old `pulseTerminalTurnBusy` trigger) is the WEAK signal being removed for claude. Only POSITIVE classifications act; anything unrecognized is `unknown` and changes nothing.

**Dependency:** none. (The follow-up plan `2026-06-05-claude-console-interaction-rules.md` builds on the `stripAnsi` helper created here in Task 1.)

---

### Task 1: Pure console classifier (`claude-console-spinner.js`)

**Files:**
- Create: `mcp/stdio/claude-console-spinner.js`
- Test: `mcp/stdio/tests/claude-console-spinner.test.js`
- Fixtures: `mcp/stdio/tests/fixtures/claude-console/working-spinner.txt`, `.../idle-prompt.txt`

- [ ] **Step 1: Capture two real console frames as fixtures**

Capture a genuine managed-claude working frame and an idle frame so the regexes are validated against real ANSI output, not invented text. With a managed claude agent live (e.g. `mp-manager`):

```bash
mkdir -p mcp/stdio/tests/fixtures/claude-console
# While the agent is mid-turn (spinner visible):
node -e 'import("./mcp/stdio/server.js")' >/dev/null 2>&1 || true
# Use the console tail tool / dashboard "copy console" to dump the raw tail. If you
# have the MCP tool wired in this session, run comms_console_tail(agentId="mp-manager")
# and paste its raw output (including escape codes) into the file below:
$EDITOR mcp/stdio/tests/fixtures/claude-console/working-spinner.txt
# Repeat at the idle prompt (no turn running):
$EDITOR mcp/stdio/tests/fixtures/claude-console/idle-prompt.txt
```

Expected: `working-spinner.txt` contains a line like `✻ Crunched for 3m 12s (esc to interrupt)`; `idle-prompt.txt` contains the bare input box with `? for shortcuts` and no `esc to interrupt`.

- [ ] **Step 2: Write the failing test**

```js
#!/usr/bin/env node
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { stripAnsi, classifyClaudeConsoleTail } from "../claude-console-spinner.js";

const here = dirname(fileURLToPath(import.meta.url));
const fx = (n) => readFileSync(join(here, "fixtures/claude-console", n), "utf8");

// stripAnsi removes CSI/OSC sequences but keeps visible text.
assert.equal(stripAnsi("\x1b[31m✻ Baked for 3m 55s\x1b[0m"), "✻ Baked for 3m 55s");

// Real captured frames classify correctly.
assert.equal(classifyClaudeConsoleTail(fx("working-spinner.txt")), "working");
assert.equal(classifyClaudeConsoleTail(fx("idle-prompt.txt")), "idle");

// Synthetic invariants.
assert.equal(classifyClaudeConsoleTail("✻ Crunched for 14m 58s (esc to interrupt)"), "working");
assert.equal(classifyClaudeConsoleTail("✶ Wibbling for 5s"), "working");
assert.equal(classifyClaudeConsoleTail("esc to interrupt"), "working");
assert.equal(classifyClaudeConsoleTail("│ > │\n  ? for shortcuts"), "idle");
// A stale 'esc to interrupt' far up in scrollback must NOT pin working when the
// live footer is the idle prompt.
assert.equal(
  classifyClaudeConsoleTail("esc to interrupt\n" + "x\n".repeat(2000) + "│ > │\n  ? for shortcuts"),
  "idle",
);
// Unrecognized text never flips state.
assert.equal(classifyClaudeConsoleTail("just some build log output\n"), "unknown");
assert.equal(classifyClaudeConsoleTail(""), "unknown");

console.log("claude-console-spinner.test.js: all assertions passed");
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `node mcp/stdio/tests/claude-console-spinner.test.js`
Expected: FAIL — `Cannot find module '../claude-console-spinner.js'`.

- [ ] **Step 4: Write the classifier**

```js
// Pure matching rules for the managed-claude TUI console tail. The spinner footer
// is a STRONG "claude is working" signal (the one thing that tracks LIVE generation,
// which the per-completed-message transcript cannot see). WEAK-by-default contract:
// only POSITIVE matches classify; anything unrecognized is "unknown" and never flips
// status — so this only ADDS `working` during the spinner window and never fights the
// authoritative transcript/Stop clear.

// CSI (\x1b[ ... letter), OSC (\x1b] ... BEL/ST), and 2-byte ESC sequences.
const ANSI_RE = /\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]/g;

export function stripAnsi(s = "") {
  return String(s || "").replace(ANSI_RE, "");
}

// Spinner footer: a spinner glyph + a verb + "for <N><unit>". Verbs are claude's
// rotating gerunds/past-tense ("Crunched", "Baked", "Wibbling", ...), so we match
// "<glyph> <word> for <number><h|m|s>" rather than enumerating verbs.
const SPINNER_RE = /[✱✶✽✺✹✷✵✳✢*·]\s+\S+\s+for\s+\d+\s*(?:h|m|s)\b/i;
// The interrupt hint rides with every in-progress claude turn.
const INTERRUPT_RE = /esc to interrupt/i;
// The idle prompt renders the shortcuts hint and no interrupt hint.
const IDLE_HINT_RE = /\?\s*for shortcuts/i;

// Classify the visible console tail. Returns "working" | "idle" | "unknown".
// Only the last ~1.5KB of visible text (the live footer region) is considered, so
// an old interrupt hint in scrollback cannot pin `working`.
export function classifyClaudeConsoleTail(rawTail = "") {
  const visible = stripAnsi(rawTail).slice(-1500);
  if (INTERRUPT_RE.test(visible) || SPINNER_RE.test(visible)) return "working";
  if (IDLE_HINT_RE.test(visible)) return "idle";
  return "unknown";
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `node mcp/stdio/tests/claude-console-spinner.test.js`
Expected: PASS — `all assertions passed`. If the real-fixture asserts fail, tune `SPINNER_RE`/`IDLE_HINT_RE` glyphs/strings to the captured bytes (the fixtures are ground truth), then re-run.

- [ ] **Step 6: Syntax-check and commit**

```bash
node --check mcp/stdio/claude-console-spinner.js
git add mcp/stdio/claude-console-spinner.js mcp/stdio/tests/claude-console-spinner.test.js mcp/stdio/tests/fixtures/claude-console/
git commit -m "feat(status): pure claude TUI console spinner classifier"
```

---

### Task 2: Classify the tail inside the PTY output loop (`terminal-runtime.js`)

**Files:**
- Modify: `mcp/stdio/terminal-runtime.js` (import at top; `_handleOutput` ~line 325; `stateFor` ~line 160)
- Test: `mcp/stdio/tests/terminal-runtime-console-class.test.js`

- [ ] **Step 1: Write the failing test**

```js
#!/usr/bin/env node
import assert from "node:assert/strict";
import { TerminalProcessManager } from "../terminal-runtime.js";

// Drive _handleOutput directly (no real PTY) and assert it tags state.consoleClass
// only for claude-code, from the spinner footer.
const mgr = new TerminalProcessManager({ onOutput: async () => {} });

const claude = { id: "t1", runtime: "claude-code", agentId: "a1", outputTail: "" };
mgr.terminals.set("t1", claude);
await mgr._handleOutput("t1", claude, "✻ Crunched for 2m 3s (esc to interrupt)");
assert.equal(mgr.stateFor("t1").consoleClass, "working");

await mgr._handleOutput("t1", claude, "\r\n│ > │\n  ? for shortcuts\n");
assert.equal(mgr.stateFor("t1").consoleClass, "idle");

// Non-claude runtimes are never classified (null).
const codex = { id: "t2", runtime: "codex", agentId: "a2", outputTail: "" };
mgr.terminals.set("t2", codex);
await mgr._handleOutput("t2", codex, "✻ Crunched for 2m 3s (esc to interrupt)");
assert.equal(mgr.stateFor("t2").consoleClass, null);

console.log("terminal-runtime-console-class.test.js: all assertions passed");
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node mcp/stdio/tests/terminal-runtime-console-class.test.js`
Expected: FAIL — `consoleClass` is `undefined` (the import name `TerminalProcessManager` must already be exported; if the assert reads `undefined !== 'working'`, that's the expected failure).

- [ ] **Step 3: Add the import at the top of `terminal-runtime.js`**

After the existing `reapPriorManagedClaude` import (line 5), add:

```js
import { classifyClaudeConsoleTail } from "./claude-console-spinner.js";
```

- [ ] **Step 4: Tag `state.consoleClass` in `_handleOutput`**

In `_handleOutput` (line 325), immediately after `state.outputTail = appendTail(state.outputTail, text);` (line 327), insert:

```js
    // Console working-signal (claude only): classify the visible TUI footer so the
    // host can drive a spinner-gated working lease. Non-claude runtimes keep their
    // own native turn detectors and are never classified here.
    state.consoleClass =
      state.runtime === "claude-code" ? classifyClaudeConsoleTail(state.outputTail) : null;
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `node mcp/stdio/tests/terminal-runtime-console-class.test.js`
Expected: PASS — `all assertions passed`.

- [ ] **Step 6: Syntax-check and commit**

```bash
node --check mcp/stdio/terminal-runtime.js
git add mcp/stdio/terminal-runtime.js mcp/stdio/tests/terminal-runtime-console-class.test.js
git commit -m "feat(status): tag claude console class on each PTY output frame"
```

---

### Task 3: Server endpoint + schema for the console-working lease

**Files:**
- Modify: `service/db.py` (after `agent_turn_state` table, line ~403)
- Modify: `service/routers/api_v2.py` (new endpoint near `/turn-start` ~line 14985; new constant near `TURN_BUSY_STALE_SECONDS` ~line 363)
- Test: `service/tests/test_console_working_lease.py`

- [ ] **Step 1: Write the failing test**

```python
import time
import pytest
from httpx import ASGITransport, AsyncClient
from service.main import app
from service import db as dbmod


@pytest.mark.asyncio
async def test_console_working_endpoint_stamps_lease(tmp_path, monkeypatch):
    monkeypatch.setenv("AIFY_DB_PATH", str(tmp_path / "t.db"))
    await dbmod.init_db()
    async with dbmod.get_db() as db:
        await db.execute(
            "INSERT INTO agents (id, role, runtime) VALUES (?,?,?)",
            ("a1", "coder", "claude-code"),
        )
        await db.commit()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/v1/agents/a1/console-working", json={})
        assert r.status_code == 200
    async with dbmod.get_db() as db:
        cur = await db.execute(
            "SELECT working_at FROM agent_console_signal WHERE agent_id = ?", ("a1",)
        )
        row = await cur.fetchone()
        assert row is not None and row["working_at"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest service/tests/test_console_working_lease.py -v`
Expected: FAIL — 404 (route missing) or `no such table: agent_console_signal`.

- [ ] **Step 3: Add the schema table in `service/db.py`**

After the `agent_turn_state` `CREATE TABLE` block (immediately before the `agent_status_state` comment, line ~404), insert:

```sql
CREATE TABLE IF NOT EXISTS agent_console_signal (
    agent_id TEXT PRIMARY KEY,
    working_at TEXT NOT NULL,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
);
```

- [ ] **Step 4: Add the lease TTL constant in `api_v2.py`**

Immediately after `TURN_BUSY_STALE_SECONDS = 120` (line 363), insert:

```python
# Console-working lease (2026-06-05): the managed-claude PTY spinner footer refreshes
# this lease every TERMINAL re-emit (~2s). A short TTL keeps `working` honest — it must
# be a small multiple of the spinner redraw cadence so it self-expires within seconds of
# the spinner stopping, but long enough to survive a coalesced-output gap. ADDITIVE only:
# OR'd into derived `working`, it never clears turn_busy.
CONSOLE_WORKING_LEASE_SECONDS = 12
```

- [ ] **Step 5: Add the endpoint in `api_v2.py`**

Immediately before `@router.post("/agents/{agent_id}/turn-start")` (line 14985), insert:

```python
@router.post("/agents/{agent_id}/console-working")
async def agent_console_working(agent_id: str, request: Request):
    """Spinner-gated working lease from the managed-claude console PTY.

    The host bridge POSTs this while the claude TUI working footer
    ("✻ <verb> for <time>" / "esc to interrupt") is visible. It stamps a short
    TTL lease that is OR'd into derived `working` — additive, never clears
    turn_busy, self-expires when the spinner stops. Idempotent best-effort.
    """
    now = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO agent_console_signal (agent_id, working_at) VALUES (?, ?) "
            "ON CONFLICT(agent_id) DO UPDATE SET working_at = excluded.working_at",
            (agent_id, now),
        )
        await db.commit()
        await _invalidate_agent_live_state(db, agent_id)
    return {"ok": True}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `pytest service/tests/test_console_working_lease.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add service/db.py service/routers/api_v2.py service/tests/test_console_working_lease.py
git commit -m "feat(status): console-working lease table + endpoint"
```

---

### Task 4: OR the lease into derived `working`

**Files:**
- Modify: `service/routers/api_v2.py` (`_compute_live_status_cache`, turn_busy block ~lines 4035-4056)
- Test: `service/tests/test_console_working_lease.py` (extend)

- [ ] **Step 1: Write the failing test (extend the Task 3 file)**

```python
@pytest.mark.asyncio
async def test_fresh_console_lease_derives_working(tmp_path, monkeypatch):
    monkeypatch.setenv("AIFY_DB_PATH", str(tmp_path / "t2.db"))
    await dbmod.init_db()
    import service.routers.api_v2 as api
    async with dbmod.get_db() as db:
        await db.execute(
            "INSERT INTO agents (id, role, runtime) VALUES (?,?,?)",
            ("a1", "coder", "claude-code"),
        )
        # Fresh lease, no turn_busy, no run.
        from datetime import datetime, timezone
        await db.execute(
            "INSERT INTO agent_console_signal (agent_id, working_at) VALUES (?, ?)",
            ("a1", datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM agents WHERE id = ?", ("a1",))
        agent_row = await cur.fetchone()
        cache = await api._compute_live_status_cache(db, agent_row)
    assert cache.get("working") is True


@pytest.mark.asyncio
async def test_stale_console_lease_not_working(tmp_path, monkeypatch):
    monkeypatch.setenv("AIFY_DB_PATH", str(tmp_path / "t3.db"))
    await dbmod.init_db()
    import service.routers.api_v2 as api
    from datetime import datetime, timezone, timedelta
    async with dbmod.get_db() as db:
        await db.execute(
            "INSERT INTO agents (id, role, runtime) VALUES (?,?,?)",
            ("a1", "coder", "claude-code"),
        )
        old = (datetime.now(timezone.utc) - timedelta(seconds=api.CONSOLE_WORKING_LEASE_SECONDS + 30)).isoformat()
        await db.execute(
            "INSERT INTO agent_console_signal (agent_id, working_at) VALUES (?, ?)",
            ("a1", old),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM agents WHERE id = ?", ("a1",))
        agent_row = await cur.fetchone()
        cache = await api._compute_live_status_cache(db, agent_row)
    assert cache.get("working") is not True
```

> Note: the `working` key must be the same boolean field `_compute_live_status_cache` already returns for a fresh `turn_busy`. Before writing the impl, read lines 4035-4080 and confirm the exact key name the cache dict uses for the busy flag; use that identical key in both the impl and these asserts.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest service/tests/test_console_working_lease.py -v`
Expected: `test_fresh_console_lease_derives_working` FAILS (working is not True); the stale test passes vacuously.

- [ ] **Step 3: OR the lease into the busy flag**

In `_compute_live_status_cache`, immediately after the existing turn_busy block that sets `turn_busy = True` (line ~4056), insert:

```python
    # Console-working lease (2026-06-05): a fresh spinner-gated lease derives working
    # even when turn_busy is 0 (the transcript detector's premature /turn-end during a
    # long thinking phase). ADDITIVE — only ever sets, never clears; self-heals via TTL.
    if not turn_busy:
        _cw = await db.execute(
            "SELECT working_at FROM agent_console_signal WHERE agent_id = ?",
            (agent_row["id"],),
        )
        _cwrow = await _cw.fetchone()
        if _cwrow:
            _seen = _iso_to_epoch(str(_cwrow["working_at"] or ""))
            if _seen and datetime.now(timezone.utc).timestamp() - _seen <= CONSOLE_WORKING_LEASE_SECONDS:
                turn_busy = True
```

> If Step 1's note found the busy variable is named other than `turn_busy` at this point in the function, set that variable instead.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest service/tests/test_console_working_lease.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add service/routers/api_v2.py service/tests/test_console_working_lease.py
git commit -m "feat(status): derive working from fresh console-working lease"
```

---

### Task 5: Drive the lease from the bridge; retire the crude any-output pulse for claude

**Files:**
- Modify: `mcp/stdio/server.js` (`onOutput` callback ~line 766; new `pulseConsoleWorking` near `pulseTerminalTurnBusy` ~line 643; `stateFor` exposure already present)
- Test: `mcp/stdio/tests/console-working-pulse.test.js`

- [ ] **Step 1: Write the failing test**

```js
#!/usr/bin/env node
// Pure-predicate test of the pulse gate: claude+working -> emits console-working;
// claude+idle -> does not; non-claude -> falls back to the legacy terminal pulse.
import assert from "node:assert/strict";
import { decideConsolePulse } from "../server.js";

assert.deepEqual(
  decideConsolePulse({ runtime: "claude-code", consoleClass: "working", agentId: "a1" }),
  { kind: "console-working", agentId: "a1" },
);
assert.deepEqual(
  decideConsolePulse({ runtime: "claude-code", consoleClass: "idle", agentId: "a1" }),
  { kind: "none" },
);
assert.deepEqual(
  decideConsolePulse({ runtime: "claude-code", consoleClass: "unknown", agentId: "a1" }),
  { kind: "none" },
);
assert.deepEqual(
  decideConsolePulse({ runtime: "codex", consoleClass: null, agentId: "a2" }),
  { kind: "terminal-pulse", agentId: "a2" },
);
assert.deepEqual(decideConsolePulse({ runtime: "claude-code", consoleClass: "working", agentId: "" }), { kind: "none" });

console.log("console-working-pulse.test.js: all assertions passed");
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node mcp/stdio/tests/console-working-pulse.test.js`
Expected: FAIL — `decideConsolePulse` is not exported.

- [ ] **Step 3: Add the pure decision helper + the pulse function in `server.js`**

Immediately after `pulseTerminalTurnBusy` (line ~663), add:

```js
// Pure gate (exported for tests): given a terminal's runtime + console classification,
// decide which working pulse to emit. Claude uses the spinner-gated console lease; other
// runtimes keep the legacy any-output terminal pulse (they own native turn detectors).
export function decideConsolePulse({ runtime, consoleClass, agentId }) {
  const aid = String(agentId || "").trim();
  if (!aid) return { kind: "none" };
  if (runtime === "claude-code") {
    return consoleClass === "working" ? { kind: "console-working", agentId: aid } : { kind: "none" };
  }
  return { kind: "terminal-pulse", agentId: aid };
}

const CONSOLE_WORKING_REMIT_MS = 2000;
const CONSOLE_WORKING_TIMERS = new Map();

// Refresh the server-side console-working lease while the claude spinner is visible.
// Debounced to ~once / CONSOLE_WORKING_REMIT_MS so a per-second spinner redraw does not
// spam the endpoint. No clear timer: the lease self-expires server-side (TTL).
function pulseConsoleWorking(terminalId, agentId) {
  const aid = String(agentId || "").trim();
  if (!aid) return;
  const last = CONSOLE_WORKING_TIMERS.get(terminalId) || 0;
  const now = Date.now();
  if (now - last < CONSOLE_WORKING_REMIT_MS) return;
  CONSOLE_WORKING_TIMERS.set(terminalId, now);
  httpCall("POST", `/agents/${encodeURIComponent(aid)}/console-working`, {}).catch(() => {});
}
```

- [ ] **Step 4: Rewire the `onOutput` callback (line ~775-778)**

Replace the existing claude-agnostic pulse block:

```js
    try {
      const agentId = TERMINAL_MANAGER.stateFor?.(terminalId)?.agentId || "";
      if (agentId) pulseTerminalTurnBusy(terminalId, agentId);
    } catch {}
```

with the gated dispatch:

```js
    try {
      const st = TERMINAL_MANAGER.stateFor?.(terminalId) || {};
      const decision = decideConsolePulse({
        runtime: st.runtime,
        consoleClass: st.consoleClass,
        agentId: st.agentId,
      });
      if (decision.kind === "console-working") pulseConsoleWorking(terminalId, decision.agentId);
      else if (decision.kind === "terminal-pulse") pulseTerminalTurnBusy(terminalId, decision.agentId);
    } catch {}
```

- [ ] **Step 5: Run the bridge test to verify it passes**

Run: `node mcp/stdio/tests/console-working-pulse.test.js`
Expected: PASS.

- [ ] **Step 6: Syntax-check + run the full bridge test sweep**

```bash
node --check mcp/stdio/server.js
for t in mcp/stdio/tests/claude-console-spinner.test.js mcp/stdio/tests/terminal-runtime-console-class.test.js mcp/stdio/tests/console-working-pulse.test.js; do node "$t"; done
```

Expected: each prints `all assertions passed`.

- [ ] **Step 7: Commit**

```bash
git add mcp/stdio/server.js mcp/stdio/tests/console-working-pulse.test.js
git commit -m "feat(status): spinner-gated console-working pulse; retire any-output pulse for claude"
```

---

### Task 6: Docs + deploy + live verification

**Files:**
- Modify: `KNOWN_ISSUES.md`, `DECISIONS.md`, `.claude/skills/aify-comms-debug/SKILL.md`, `.agents/skills/aify-comms-debug/SKILL.md`

- [ ] **Step 1: Record the decision in `DECISIONS.md`**

Add an entry dated 2026-06-05 explaining: the transcript turn-detector is structurally blind to live generation (transcript grows per completed message, not per token); the managed-claude console PTY spinner footer is the only host-observable live-generation signal; we derive `working` from a spinner-gated, TTL-bounded console lease (`CONSOLE_WORKING_LEASE_SECONDS = 12`) that is additive/OR-ed into derived working and never clobbers `turn_busy`; the legacy any-output `pulseTerminalTurnBusy` is retired for claude (kept for other runtimes).

- [ ] **Step 2: Update the troubleshooting skill (both copies, keep byte-identical)**

Under the status-labels section of `.claude/skills/aify-comms-debug/SKILL.md` AND `.agents/skills/aify-comms-debug/SKILL.md`, add a "managed claude shows online while thinking" entry: cause = transcript lag during long generation; fix = the console-working lease derives working from the visible spinner; if it still under-reports, confirm the bridge was reinstalled (`install.sh`) and the service rebuilt, and that the spinner footer renders in the Console.

- [ ] **Step 3: Deploy both halves**

```bash
docker compose up -d --build && curl -s http://localhost:8800/health
bash install.sh --client claude http://localhost:8800
```

Expected: health returns `{"status":"healthy"}`; installer reports the bridge copied to `~/.aify-comms`.

- [ ] **Step 4: Live-verify the under-report is gone**

Restart a managed claude wrapper, send it a dispatch that triggers a long thinking phase, and watch the dashboard while `✻ ... for <time>` is visible.

Expected: the agent dot reads `working` for the whole spinner phase and returns to `online`/`idle` within ~12s of the spinner disappearing. Confirm `agent_console_signal` is being stamped:

```bash
curl -s http://localhost:8800/api/v1/agents | python -c "import sys,json;[print(a['id'],a.get('status')) for a in json.load(sys.stdin)]"
```

- [ ] **Step 5: Commit the docs**

```bash
git add KNOWN_ISSUES.md DECISIONS.md .claude/skills/aify-comms-debug/SKILL.md .agents/skills/aify-comms-debug/SKILL.md
git commit -m "docs(status): console-working lease for managed-claude under-report"
```

---

## Self-Review

**Spec coverage:** spinner classifier (Task 1) ✓; host-side classification (Task 2) ✓; lease store + endpoint (Task 3) ✓; derived-working OR (Task 4) ✓; bridge pulse + retire crude pulse (Task 5) ✓; weak/additive contract honored — `unknown` is a no-op and the lease never clears `turn_busy` (Tasks 1, 4, 5) ✓; docs + deploy + live verify (Task 6) ✓.

**Placeholder scan:** the only deferred content is the captured fixtures (Task 1 Step 1), which is a concrete capture step producing real ground-truth files, not a hand-wave; regexes are fully written and tuned against those fixtures.

**Type consistency:** `classifyClaudeConsoleTail`/`stripAnsi` (Task 1) reused verbatim in Task 2; `state.consoleClass` set in Task 2, read in Task 5; `decideConsolePulse` shape `{kind, agentId}` consistent across Task 5 test + impl; `agent_console_signal(agent_id, working_at)` consistent across Tasks 3 and 4; `CONSOLE_WORKING_LEASE_SECONDS` defined Task 3, used Task 4. The one open verification is the `working` key name in the cache dict — Task 4 Step 1 explicitly instructs confirming it before writing the impl.
