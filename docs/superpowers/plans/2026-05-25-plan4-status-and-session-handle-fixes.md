# Plan 4 — Status, Session-Handle, and Default-Path Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close 12 operator-surfaced gaps from live testing of Plans 1+2+3 — flip defaults to wrapper-backed delivery, deprecate synth terminals for wrapper-backed runtimes, capture session-handle for fresh managed launches, stop status taxonomy from lying, fix codex-aify stale-handle probe.

**Architecture:** Five phases (A→F) with the final phase as a holistic review pass. Per-runtime `discoverSessionId()` lets the bridge heartbeat capture session ids from runtime-native storage when env-reads return empty. New `ready` status sits between `online` and `working`. Managed-via-wrapper becomes the default for codex/hermes/pi.

**Tech Stack:** Node 20 + ES modules (`node --test`), Python 3 + FastAPI + pytest, SQLite for agents/runtime_state, bash for wrappers.

---

## File Structure

### Create

| Path | Responsibility |
|---|---|
| `mcp/stdio/turn-busy-heartbeat.js` | Bridge-side heartbeat: while controller.start() promise unresolved, POSTs `turn_busy=1` every 30s. ≤200 lines. |
| `mcp/stdio/tests/turn-busy-heartbeat.test.js` | Unit tests for the heartbeat |
| `redeploy.sh` | Detect installed `~/.local/bin/*-aify` wrappers and re-run `install.sh --client X SERVER_URL` for each |
| `mcp/stdio/tests/adapters/discover-session-id.test.js` | Cross-language contract test for discoverSessionId |
| `service/tests/runtimes/test_discover_session_id.py` | Python per-adapter discoverSessionId tests |
| `service/tests/test_ready_status_endpoint.py` | New PATCH /api/v2/agents/{id}/ready endpoint test |
| `service/tests/test_status_taxonomy.py` | Status resolver regression: managed-no-worker shows available |
| `service/tests/test_synth_terminal_deprecation.py` | Synth row NOT created for wrapper-backed runtimes |

### Modify

| Path | Change |
|---|---|
| `service/routers/api_v2.py:DEFAULT_SETTINGS` | Flip `managed_via_wrapper` to `["codex","hermes","pi"]`, `managed_pty_eager_spawn` to `True` |
| `service/routers/api_v2.py:_ensure_managed_pty_for_dispatch` (and related synth-row creators) | Skip synth row when `_managed_via_wrapper_for_runtime` returns True for that runtime |
| `service/routers/api_v2.py:_compute_agent_status` (and `_refresh_agent_live_state`) | Managed agents need a live `terminal_session` OR live RPC controller before claiming `online`; otherwise `available` |
| `service/routers/api_v2.py` — new endpoint | `PATCH /api/v2/agents/{id}/ready` |
| `mcp/stdio/adapters/base.js` + each concrete adapter | Add `discoverSessionId()` (async, default returns null; per-runtime overrides) |
| `service/runtimes/base.py` + each concrete adapter | Add `async def discover_session_id() -> str | None` |
| `mcp/stdio/session-handle-heartbeat.js` | Fall back to `await adapter.discoverSessionId()` when env-read returns null |
| `mcp/stdio/server.js` | Start the new turn-busy-heartbeat alongside existing heartbeats; POST `ready` when controller handshake completes |
| `install.sh` codex-aify section | Replace narrow `~/.codex/sessions/<id>.jsonl` probe with multi-path check (or `codex sessions list` if available) |
| `mcp/stdio/controllers/codex-controller.js` | Mirror the same probe logic in the bridge-side stale-handle handling |
| `mcp/stdio/controllers/hermes-controller.js` (or runtimes.js routing) | Drop `hermes-session-resume` wake-mode path; gateway path is the single source |
| `service/new_dashboard/app.js:chooseSessionConsoleWidget` | Prefer wrapper PTY when both wrapper PTY and synth terminal exist for the same agent; cache choice sticky per agent |
| `service/new_dashboard/styles.css` (or equivalent) | Color coding: `ready=green`, `online=light green`, `available=grey`, `working=animated` |
| `install.codex.md` | Document codex session storage layout |
| `DECISIONS.md` + `README.md` + skills (`.claude/` and `.agents/` mirrors) | Plan 4 entries |

### Out of scope

- `runtimes.js` helper extraction (#123 — separate plan)
- `service/routers/api_v2.py` decomposition (Plan 5 — separate plan)
- Opencode multi-client wiring via `opencode serve`

---

## Phase A — Defaults flip + synth deprecation

### Task 1: Flip DEFAULT_SETTINGS

**Files:**
- Modify: `service/routers/api_v2.py:DEFAULT_SETTINGS` (find via `grep -n "DEFAULT_SETTINGS" service/routers/api_v2.py | head -3`)
- Create: `service/tests/test_default_settings_plan4.py`

- [ ] **Step 1: Write failing test**

Create `service/tests/test_default_settings_plan4.py`:

```python
"""Plan 4 default settings flip — wrapper-backed delivery is now the default."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.routers.api_v2 import DEFAULT_SETTINGS


def test_managed_via_wrapper_defaults_to_codex_hermes_pi():
    assert DEFAULT_SETTINGS["managed_via_wrapper"] == ["codex", "hermes", "pi"], (
        f"Plan 4 default flip: expected [codex,hermes,pi], got {DEFAULT_SETTINGS['managed_via_wrapper']}"
    )


def test_managed_pty_eager_spawn_defaults_to_true():
    assert DEFAULT_SETTINGS["managed_pty_eager_spawn"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_default_settings_plan4.py -v`
Expected: FAIL — current defaults are `False` / not-list.

- [ ] **Step 3: Update DEFAULT_SETTINGS**

Edit `service/routers/api_v2.py`. Find `DEFAULT_SETTINGS = {...}` (around line 200-260 — confirm via grep). Update:

```python
    "managed_via_wrapper": ["codex", "hermes", "pi"],  # Plan 4 (2026-05-25): wrapper-backed default; was False
    "managed_pty_eager_spawn": True,                    # Plan 4 (2026-05-25): auto-spawn on dispatch; was False
```

- [ ] **Step 4: Run test to verify pass**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_default_settings_plan4.py -v`
Expected: PASS 2/2.

- [ ] **Step 5: Run broader regression suite**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_api_v2_regressions.py service/tests/test_managed_via_wrapper_adapter.py -v 2>&1 | tail -15`
Expected: all pass. Some Plan 2 tests may have used the False default — update inline to pass explicit `False` if they rely on the old default behavior.

- [ ] **Step 6: Commit**

```bash
cd C:/Docker/aify-comms
git add service/routers/api_v2.py service/tests/test_default_settings_plan4.py
# include any test_api_v2_regressions.py inline updates
git commit -m "feat(server): DEFAULT_SETTINGS — managed_via_wrapper + eager_spawn default ON (Plan 4)"
```

---

### Task 2: Synth-terminal deprecation for wrapper-backed runtimes

**Files:**
- Modify: `service/routers/api_v2.py` — locate synth terminal_session creation (search for `virtual-rpc` or `aify://virtual-rpc`)
- Create: `service/tests/test_synth_terminal_deprecation.py`

- [ ] **Step 1: Locate synth row creation**

Run: `cd C:/Docker/aify-comms && grep -n "virtual-rpc\|aify://virtual-rpc" service/routers/api_v2.py | head -20`

Identify the function(s) that create the synth `terminal_session` row for managed dispatch. Typical name pattern: `_ensure_virtual_rpc_terminal_for_runtime` or inline in a dispatch handler.

- [ ] **Step 2: Write failing test**

Create `service/tests/test_synth_terminal_deprecation.py`:

```python
"""Plan 4 synth-terminal deprecation: when managed_via_wrapper is on for a
runtime, synth terminal_session row must NOT be created. The wrapper PTY
IS the terminal. Synth stays only for opencode + hard-failure fallback.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))


def test_synth_skipped_for_wrapper_backed_runtimes():
    from service.routers.api_v2 import _synth_terminal_should_be_created
    settings_on = {"managed_via_wrapper": ["codex", "hermes", "pi"]}
    # Wrapper-backed runtimes: synth must be skipped
    assert _synth_terminal_should_be_created("codex", settings_on) is False
    assert _synth_terminal_should_be_created("hermes", settings_on) is False
    assert _synth_terminal_should_be_created("pi", settings_on) is False


def test_synth_still_created_for_opencode():
    from service.routers.api_v2 import _synth_terminal_should_be_created
    settings_on = {"managed_via_wrapper": ["codex", "hermes", "pi"]}
    # Opencode: still synth (no wrapper exists)
    assert _synth_terminal_should_be_created("opencode", settings_on) is True


def test_synth_used_when_wrapper_setting_off():
    from service.routers.api_v2 import _synth_terminal_should_be_created
    settings_off = {"managed_via_wrapper": False}
    # Legacy/rollback path
    assert _synth_terminal_should_be_created("codex", settings_off) is True
    assert _synth_terminal_should_be_created("hermes", settings_off) is True
    assert _synth_terminal_should_be_created("pi", settings_off) is True
```

- [ ] **Step 3: Run to verify failure**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_synth_terminal_deprecation.py -v`
Expected: FAIL — helper doesn't exist yet.

- [ ] **Step 4: Implement `_synth_terminal_should_be_created` helper**

Edit `service/routers/api_v2.py`. Add helper near `_managed_via_wrapper_for_runtime` (search for that function for placement):

```python
def _synth_terminal_should_be_created(runtime: str, settings: dict[str, Any]) -> bool:
    """Plan 4 (2026-05-25): synth-terminal (aify://virtual-rpc/<runtime>) is
    deprecated for wrapper-backed runtimes. The wrapper PTY IS the terminal.
    Synth stays only for opencode (no aify wrapper exists) + hard-failure
    fallback (wrapper missing).
    """
    if _managed_via_wrapper_for_runtime(settings, runtime):
        return False
    return True
```

- [ ] **Step 5: Wire helper into synth-row creation callsite**

Find the synth-row creator (from Step 1) and gate it with `_synth_terminal_should_be_created(...)`. Example:

```python
if _synth_terminal_should_be_created(runtime, settings):
    # existing synth-row creation logic
    ...
```

Be careful: the synth fallback for "wrapper failed to spawn" must remain. Detect that case by checking if the wrapper-spawn step returned an error before falling back to synth.

- [ ] **Step 6: Run new tests + regression suite**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_synth_terminal_deprecation.py service/tests/test_api_v2_regressions.py -v -k "synth or virtual_rpc or wrapper" 2>&1 | tail -30`
Expected: new tests pass; existing tests may need inline updates to match new behavior.

- [ ] **Step 7: Commit**

```bash
cd C:/Docker/aify-comms
git add service/routers/api_v2.py service/tests/test_synth_terminal_deprecation.py
git commit -m "feat(server): synth-terminal deprecated for wrapper-backed runtimes (Plan 4)"
```

---

## Phase B — discoverSessionId per-adapter

### Task 3: Add `discoverSessionId` to JS adapter contract

**Files:**
- Modify: `mcp/stdio/adapters/base.js`
- Create: `mcp/stdio/tests/adapters/discover-session-id.test.js`

- [ ] **Step 1: Write failing contract test**

Create `mcp/stdio/tests/adapters/discover-session-id.test.js`:

```javascript
import assert from "assert";
import test from "node:test";
import { RuntimeAdapter } from "../../adapters/base.js";

class _TestAdapter extends RuntimeAdapter {
  get name() { return "test-runtime"; }
  get sessionEnvVars() { return ["TEST_SESSION_ID"]; }
}

test("RuntimeAdapter.discoverSessionId default returns null", async () => {
  const a = new _TestAdapter();
  assert.strictEqual(await a.discoverSessionId(), null);
});

test("discoverSessionId is async", () => {
  const a = new _TestAdapter();
  const result = a.discoverSessionId();
  assert.ok(typeof result.then === "function", "discoverSessionId must return a Promise");
});
```

- [ ] **Step 2: Verify fail**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/discover-session-id.test.js`
Expected: FAIL — method doesn't exist.

- [ ] **Step 3: Add `discoverSessionId` to base.js**

Edit `mcp/stdio/adapters/base.js`. After `getCurrentSessionId()`:

```javascript
  // Plan 4 (2026-05-25): runtime-native session discovery for fresh managed
  // launches where the env-read path returns null. Default returns null;
  // each concrete adapter overrides with its own discovery (filesystem scan,
  // SQLite query, gateway RPC, etc.).
  async discoverSessionId() {
    return null;
  }
```

- [ ] **Step 4: Verify pass**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/discover-session-id.test.js`
Expected: 2/2 pass.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/adapters/base.js mcp/stdio/tests/adapters/discover-session-id.test.js
git commit -m "feat(adapters/js): discoverSessionId contract (default null)"
```

---

### Task 4: Add `discover_session_id` to Python adapter contract

**Files:**
- Modify: `service/runtimes/base.py`
- Create: `service/tests/runtimes/test_discover_session_id.py`

- [ ] **Step 1: Write failing test**

Create `service/tests/runtimes/test_discover_session_id.py`:

```python
"""Plan 4 discover_session_id contract — per-adapter runtime-native discovery
for fresh managed launches."""

import asyncio
import pytest
from service.runtimes.base import RuntimeAdapter


class _TestAdapter(RuntimeAdapter):
    name = "test-runtime"
    display_name = "Test"
    session_env_vars = ["TEST_SESSION_ID"]
    supports_resident = True
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    supports_multi_client = True
    preferred_delivery_mode = "managed-via-wrapper"


def test_base_discover_session_id_returns_none():
    a = _TestAdapter()
    assert asyncio.run(a.discover_session_id()) is None
```

- [ ] **Step 2: Verify fail**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/test_discover_session_id.py -v`
Expected: FAIL — method doesn't exist.

- [ ] **Step 3: Add to base.py**

Edit `service/runtimes/base.py`. After `get_current_session_id`:

```python
    async def discover_session_id(self) -> str | None:
        """Plan 4 (2026-05-25): runtime-native session discovery. Default
        returns None; subclasses override with filesystem/SQLite/RPC discovery.
        Called by bridge heartbeat as a fallback when env-read returns None.
        """
        return None
```

- [ ] **Step 4: Verify pass**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/test_discover_session_id.py -v`
Expected: 1/1 pass.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add service/runtimes/base.py service/tests/runtimes/test_discover_session_id.py
git commit -m "feat(adapters/py): discover_session_id contract (default None)"
```

---

### Task 5: PiAdapter discoverSessionId (both languages)

**Files:**
- Modify: `mcp/stdio/adapters/pi.js`, `service/runtimes/pi.py`
- Modify: `mcp/stdio/tests/adapters/pi.test.js`, `service/tests/runtimes/test_per_adapter.py`

- [ ] **Step 1: Recon pi storage**

Run: `cd C:/Docker/aify-comms && ls ~/.omp/agent/sessions/ 2>/dev/null | head -10`

Pi storage layout (confirmed in Plan 4 spec): `~/.omp/agent/sessions/` directory + `agent.db` SQLite. Pick the cleaner option:

- **Option A (filesystem):** find newest `.jsonl` or `.json` file in `~/.omp/agent/sessions/`, parse the session id from filename or first-line `session_id` field
- **Option B (SQLite):** open `~/.omp/agent/agent.db` read-only, query `SELECT session_id FROM sessions ORDER BY started_at DESC LIMIT 1` (or whatever the table schema is)

Check schema first: `sqlite3 ~/.omp/agent/agent.db ".schema"` and `sqlite3 ~/.omp/agent/agent.db ".tables"`.

Pick filesystem if schema is opaque; SQLite if it's clean.

- [ ] **Step 2: Write failing tests**

For JS — append to `mcp/stdio/tests/adapters/pi.test.js`:

```javascript

test("PiAdapter.discoverSessionId reads pi session storage", async () => {
  // Functional test if real ~/.omp/agent/ exists, otherwise smoke test.
  const a = new PiAdapter();
  const result = await a.discoverSessionId();
  if (result !== null) {
    assert.ok(typeof result === "string" && result.length > 0,
      "if discoverSessionId returns non-null, it must be a non-empty string");
  }
  // Else null is acceptable (no pi sessions on this host).
});
```

For Python — append to `service/tests/runtimes/test_per_adapter.py`:

```python


def test_pi_adapter_discover_session_id():
    """Functional test if real ~/.omp/agent/ exists, otherwise smoke test."""
    import asyncio
    from service.runtimes.pi import PiAdapter
    result = asyncio.run(PiAdapter().discover_session_id())
    if result is not None:
        assert isinstance(result, str) and len(result) > 0
```

- [ ] **Step 3: Verify fail (JS)**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/pi.test.js`
Expected: PASSES the smoke shape test for default-null (Plan 1 base default returns null). After implementation, the override may return non-null.

This means both tests pass even before implementation if you just inherit. To actually drive TDD: temporarily make the test assert `assert.ok(result !== null, "PiAdapter must override discoverSessionId")` to force the override, then relax to the smoke shape once implemented.

Actually use TDD-style:

```javascript
test("PiAdapter overrides discoverSessionId (does not inherit base null)", async () => {
  const a = new PiAdapter();
  // Override must exist on the prototype, not just inherit base.
  const own = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(a), "discoverSessionId");
  assert.ok(own && typeof own.value === "function",
    "PiAdapter must define its own discoverSessionId override");
});
```

Same idea for Python: assert `PiAdapter.discover_session_id` is not the base class's method.

- [ ] **Step 4: Implement PiAdapter overrides**

Edit `mcp/stdio/adapters/pi.js`:

```javascript
import path from "path";
import os from "os";
import fs from "fs/promises";

// ... existing class ...

  async discoverSessionId() {
    // Plan 4: read pi's session storage. Filesystem scan of
    // ~/.omp/agent/sessions/ — pick the file with the newest mtime,
    // parse session id from filename (uuid pattern) or json content.
    const dir = path.join(os.homedir(), ".omp", "agent", "sessions");
    try {
      const entries = await fs.readdir(dir);
      if (!entries.length) return null;
      const stats = await Promise.all(entries.map(async (name) => {
        const full = path.join(dir, name);
        try {
          const s = await fs.stat(full);
          return { name, full, mtime: s.mtimeMs };
        } catch {
          return null;
        }
      }));
      const valid = stats.filter(Boolean).sort((a, b) => b.mtime - a.mtime);
      if (!valid.length) return null;
      // Try to parse session id from filename (uuid pattern) first
      const newest = valid[0];
      const uuidMatch = newest.name.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
      if (uuidMatch) return uuidMatch[0];
      // Fallback: read first line as JSON, look for session_id field
      try {
        const content = await fs.readFile(newest.full, "utf8");
        const firstLine = content.split("\n")[0];
        const parsed = JSON.parse(firstLine);
        const id = parsed.session_id || parsed.sessionId || parsed.id;
        if (id && typeof id === "string") return id;
      } catch {
        // not JSON or no id field
      }
      return null;
    } catch {
      return null;
    }
  }
```

Edit `service/runtimes/pi.py`:

```python
import asyncio
import json
import os
import re
from pathlib import Path

# ... existing class ...

    async def discover_session_id(self) -> str | None:
        sessions_dir = Path.home() / ".omp" / "agent" / "sessions"
        try:
            entries = list(sessions_dir.iterdir())
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            return None
        if not entries:
            return None
        # Sort by mtime descending
        try:
            entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return None
        newest = entries[0]
        # Try uuid pattern in filename
        m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", newest.name, re.IGNORECASE)
        if m:
            return m.group(0)
        # Fallback: parse first-line JSON for session_id field
        try:
            text = newest.read_text(encoding="utf-8", errors="replace").splitlines()
            if text:
                obj = json.loads(text[0])
                for key in ("session_id", "sessionId", "id"):
                    val = obj.get(key)
                    if isinstance(val, str) and val:
                        return val
        except (json.JSONDecodeError, OSError):
            pass
        return None
```

- [ ] **Step 5: Verify tests pass**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/pi.test.js && python -m pytest service/tests/runtimes/test_per_adapter.py -v -k pi`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/adapters/pi.js service/runtimes/pi.py mcp/stdio/tests/adapters/pi.test.js service/tests/runtimes/test_per_adapter.py
git commit -m "feat(adapters): PiAdapter discoverSessionId — filesystem scan ~/.omp/agent/sessions/"
```

---

### Task 6: CodexAdapter discoverSessionId

**Files:**
- Modify: `mcp/stdio/adapters/codex.js`, `service/runtimes/codex.py`
- Modify: tests

- [ ] **Step 1: Recon codex storage layout**

Run from WSL: `ls ~/.codex/sessions/ | head -10 && find ~/.codex/sessions -maxdepth 3 -type f -name "*.jsonl" 2>/dev/null | head -10`

Determine: flat (`~/.codex/sessions/<uuid>.jsonl`), date-sharded (`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`), or dir-per-session.

- [ ] **Step 2: Write failing test**

Append to `mcp/stdio/tests/adapters/codex.test.js`:

```javascript

test("CodexAdapter overrides discoverSessionId", async () => {
  const own = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(new CodexAdapter()), "discoverSessionId");
  assert.ok(own && typeof own.value === "function",
    "CodexAdapter must define its own discoverSessionId override");
});
```

Append to `service/tests/runtimes/test_per_adapter.py`:

```python


def test_codex_adapter_overrides_discover_session_id():
    from service.runtimes.codex import CodexAdapter
    assert CodexAdapter.discover_session_id is not CodexAdapter.__mro__[1].discover_session_id, (
        "CodexAdapter must override discover_session_id"
    )
```

- [ ] **Step 3: Implement based on recon**

Edit `mcp/stdio/adapters/codex.js` and `service/runtimes/codex.py` — implementation depends on actual codex storage layout from Step 1.

Common shapes:
- **Flat:** `glob('~/.codex/sessions/*.jsonl')` → newest mtime → extract uuid from filename
- **Date-sharded:** `glob('~/.codex/sessions/**/*.jsonl')` recursively → newest mtime → extract uuid from filename (`rollout-<uuid>.jsonl` or similar)
- **Dir-per-session:** `readdir('~/.codex/sessions/')` → newest dir mtime → dir name is the uuid

Use the actual layout from Step 1.

JS skeleton:

```javascript
async discoverSessionId() {
  const root = path.join(os.homedir(), ".codex", "sessions");
  // ... walk based on recon results ...
  // Extract uuid from result. Return null on miss.
}
```

Python skeleton:

```python
async def discover_session_id(self) -> str | None:
    root = Path.home() / ".codex" / "sessions"
    # ... walk based on recon results ...
    return None
```

If the recon reveals codex offers `codex sessions list` CLI, prefer that — call it via subprocess and parse the output. Otherwise filesystem.

- [ ] **Step 4: Test + commit**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/codex.test.js && python -m pytest service/tests/runtimes/test_per_adapter.py -v -k codex`
Expected: pass.

Commit: `feat(adapters): CodexAdapter discoverSessionId — <chosen approach>`

---

### Task 7: HermesAdapter discoverSessionId

**Files:**
- Modify: `mcp/stdio/adapters/hermes.js`, `service/runtimes/hermes.py`
- Modify: tests

- [ ] **Step 1: Write failing override-existence tests**

Append to test files (same pattern as Tasks 5 + 6).

- [ ] **Step 2: Implement**

Hermes has a gateway: if `AIFY_HERMES_GATEWAY_URL` is set in process env, query gateway's `session.most_recent` JSON-RPC method via WebSocket. Otherwise fall back to filesystem scan of `~/.hermes/sessions/` if it exists.

JS implementation (uses `ws` package — already imported in `mcp/stdio/runtimes.js`):

```javascript
import WebSocket from "ws";

async discoverSessionId() {
  const gw = String(process.env.AIFY_HERMES_GATEWAY_URL || "").trim();
  if (gw && /^wss?:\/\//i.test(gw)) {
    try {
      return await new Promise((resolve, reject) => {
        const ws = new WebSocket(gw);
        const timer = setTimeout(() => { ws.close(); reject(new Error("timeout")); }, 3000);
        ws.on("open", () => {
          ws.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "session.most_recent" }));
        });
        ws.on("message", (data) => {
          try {
            const msg = JSON.parse(data.toString());
            const id = msg?.result?.session_id || msg?.result?.id || null;
            clearTimeout(timer);
            ws.close();
            resolve(id || null);
          } catch (err) {
            clearTimeout(timer);
            ws.close();
            reject(err);
          }
        });
        ws.on("error", (err) => { clearTimeout(timer); reject(err); });
      });
    } catch {
      // fall through to filesystem
    }
  }
  // Filesystem fallback
  try {
    const dir = path.join(os.homedir(), ".hermes", "sessions");
    const entries = await fs.readdir(dir);
    if (!entries.length) return null;
    const stats = await Promise.all(entries.map(async (n) => {
      try { return { n, mtime: (await fs.stat(path.join(dir, n))).mtimeMs }; } catch { return null; }
    }));
    const valid = stats.filter(Boolean).sort((a, b) => b.mtime - a.mtime);
    return valid.length ? valid[0].n.replace(/\.json$|\.jsonl$/, "") : null;
  } catch {
    return null;
  }
}
```

Python equivalent uses `websockets` if available, otherwise `aiohttp.ClientSession.ws_connect`, with same filesystem fallback. Implementer picks the easiest websocket client present in `requirements.txt`.

- [ ] **Step 3: Test + commit**

Same as Task 5/6.

---

### Task 8: ClaudeAdapter discoverSessionId

**Files:**
- Modify: `mcp/stdio/adapters/claude.js`, `service/runtimes/claude.py`
- Modify: tests

- [ ] **Step 1: Write failing override-existence tests** (same pattern)

- [ ] **Step 2: Implement**

Claude exports `CLAUDE_SESSION_ID` env when claude-channel.js binds. The base `getCurrentSessionId()` already picks this up. So `discoverSessionId` is primarily a fallback path: parse the claude session transcript JSONL files (location varies by claude version — typically `~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl`).

```javascript
async discoverSessionId() {
  // Env path already covered by getCurrentSessionId. Fallback: read claude's
  // transcript directory and find the newest .jsonl file's name as the session id.
  const root = path.join(os.homedir(), ".claude", "projects");
  try {
    const projects = await fs.readdir(root);
    let newest = null;
    for (const proj of projects) {
      const projDir = path.join(root, proj);
      try {
        const files = await fs.readdir(projDir);
        for (const f of files) {
          if (!f.endsWith(".jsonl")) continue;
          const full = path.join(projDir, f);
          const stat = await fs.stat(full);
          if (!newest || stat.mtimeMs > newest.mtime) {
            newest = { mtime: stat.mtimeMs, id: f.replace(/\.jsonl$/, "") };
          }
        }
      } catch { /* skip */ }
    }
    return newest?.id || null;
  } catch {
    return null;
  }
}
```

Python equivalent.

- [ ] **Step 3: Test + commit**

---

### Task 9: Heartbeat extension — fall back to discoverSessionId

**Files:**
- Modify: `mcp/stdio/session-handle-heartbeat.js`
- Modify: `mcp/stdio/tests/adapter-heartbeat.test.js`

- [ ] **Step 1: Write failing test**

Append to `mcp/stdio/tests/adapter-heartbeat.test.js`:

```javascript

test("Heartbeat falls back to adapter.discoverSessionId when env empty", async () => {
  const { startSessionHandleHeartbeat } = await import("../session-handle-heartbeat.js");
  const calls = [];
  const adapter = {
    getCurrentSessionId: () => null, // env empty
    discoverSessionId: async () => "discovered-handle-xyz",
  };
  const stop = startSessionHandleHeartbeat({
    adapter,
    agentId: "agent-discovery",
    intervalMs: 10,
    postFn: async (agentId, handle) => { calls.push({ agentId, handle }); },
  });
  await new Promise(r => setTimeout(r, 50));
  stop();
  assert.ok(calls.length >= 1, "heartbeat should fall through to discoverSessionId");
  assert.strictEqual(calls[0].handle, "discovered-handle-xyz");
});
```

- [ ] **Step 2: Verify fail**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapter-heartbeat.test.js`
Expected: FAIL — heartbeat currently only reads env.

- [ ] **Step 3: Update heartbeat**

Edit `mcp/stdio/session-handle-heartbeat.js`. In the `tick` function:

```javascript
const tick = async () => {
  if (stopped) return;
  let current = null;
  try { current = adapter.getCurrentSessionId(); } catch { /* swallow */ }
  if (!current && typeof adapter.discoverSessionId === "function") {
    try { current = await adapter.discoverSessionId(); } catch { /* swallow */ }
  }
  if (!current || current === lastHandle) return;
  try {
    await postFn(agentId, current);
    lastHandle = current;
  } catch {
    // best-effort
  }
};
```

- [ ] **Step 4: Test + commit**

```bash
cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapter-heartbeat.test.js
git add mcp/stdio/session-handle-heartbeat.js mcp/stdio/tests/adapter-heartbeat.test.js
git commit -m "feat(bridge): heartbeat falls back to adapter.discoverSessionId on empty env"
```

---

## Phase C — Status taxonomy fix

### Task 10: Managed agents without live worker show `available`

**Files:**
- Modify: `service/routers/api_v2.py:_compute_agent_status` (find via `grep -n "_compute_agent_status\|_refresh_agent_live_state" service/routers/api_v2.py`)
- Create: `service/tests/test_status_taxonomy.py`

- [ ] **Step 1: Write failing test**

Create `service/tests/test_status_taxonomy.py`:

```python
"""Plan 4 status taxonomy: managed agents without a live terminal_session
OR live RPC controller must show `available`, not `online`."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))


def test_managed_agent_no_terminal_session_returns_available():
    """When a managed agent has no live terminal_session row and no RPC
    child registration, status must be `available` not `online`."""
    # Construct synthetic agent row + ensure _compute_agent_status returns 'available'
    # Use whatever pattern the existing test_api_v2_regressions.py uses
    from unittest import mock
    from service.routers.api_v2 import _compute_agent_status

    row = {
        "id": "test-managed-no-worker",
        "status": "online",
        "session_mode": "managed",
        "runtime": "codex",
        "last_seen": "2026-05-25T00:00:00Z",
    }

    # Mock _has_live_terminal_session + _has_live_rpc_controller to return False
    # (no live worker). Status should resolve to 'available'.
    # If those helpers don't exist yet, this test will fail — that's the point.
    import asyncio
    with mock.patch("service.routers.api_v2._has_live_terminal_session", return_value=False), \
         mock.patch("service.routers.api_v2._has_live_rpc_controller", return_value=False):
        result = asyncio.run(_compute_agent_status(row, idle_minutes=5, offline_minutes=30, db=None))
        assert result == "available", f"managed-no-worker should be 'available', got {result!r}"
```

- [ ] **Step 2: Verify fail**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_status_taxonomy.py -v`
Expected: FAIL — `_has_live_terminal_session` / `_has_live_rpc_controller` don't exist yet.

- [ ] **Step 3: Implement live-worker check helpers**

Edit `service/routers/api_v2.py`. Add helpers:

```python
async def _has_live_terminal_session(db, agent_id: str) -> bool:
    """Plan 4: true when this agent has a live terminal_session row owned
    by a current bridge instance (managed-via-wrapper path)."""
    if db is None:
        return False
    cursor = await db.execute(
        """
        SELECT COUNT(*) as cnt FROM terminal_sessions
        WHERE agent_id = ?
          AND status IN ('running', 'starting')
        """,
        (agent_id,),
    )
    row = await cursor.fetchone()
    return bool(row and int(row["cnt"] or 0) > 0)


def _has_live_rpc_controller(agent_id: str) -> bool:
    """Plan 4: true when an in-memory RPC controller is registered for this
    agent (managed-RPC fallback path — opencode, or wrapper-spawn failure).
    Checks the synth-rpc registry the bridge maintains."""
    # Implementation depends on whether an in-memory registry exists.
    # If not, return False — this case is rarer post-Plan-4 anyway since
    # wrapper-backed is now default.
    return False
```

Update `_compute_agent_status`:

```python
async def _compute_agent_status(row, idle_minutes, offline_minutes, db=None):
    # ... existing logic ...

    # Plan 4: managed agents must have a live worker before claiming `online`.
    session_mode = (row.get("session_mode") if isinstance(row, dict) else row["session_mode"]) or ""
    if session_mode == "managed" and status not in _MANUAL_STATUSES:
        agent_id = row.get("id") if isinstance(row, dict) else row["id"]
        if db is not None:
            has_live_terminal = await _has_live_terminal_session(db, agent_id)
        else:
            has_live_terminal = False
        has_live_rpc = _has_live_rpc_controller(agent_id)
        if not (has_live_terminal or has_live_rpc):
            return "available"

    # ... rest unchanged ...
```

- [ ] **Step 4: Run test + regression suite**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_status_taxonomy.py service/tests/test_api_v2_regressions.py -v 2>&1 | tail -25`
Expected: new test passes; some existing tests may need inline updates (managed agents that didn't expect to flip to `available`).

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add service/routers/api_v2.py service/tests/test_status_taxonomy.py
git commit -m "fix(server): managed agents without live worker show 'available' not 'online'"
```

---

### Task 11: turn-busy-heartbeat.js — keep `working` fresh during long turns

**Files:**
- Create: `mcp/stdio/turn-busy-heartbeat.js` (≤200 lines)
- Create: `mcp/stdio/tests/turn-busy-heartbeat.test.js`
- Modify: `mcp/stdio/server.js` to start the heartbeat alongside existing ones

- [ ] **Step 1: Write failing test**

Create `mcp/stdio/tests/turn-busy-heartbeat.test.js`:

```javascript
import assert from "assert";
import test from "node:test";
import { startTurnBusyHeartbeat } from "../turn-busy-heartbeat.js";

test("startTurnBusyHeartbeat POSTs turn_busy=1 every interval", async () => {
  const calls = [];
  let controllerActive = true;
  const stop = startTurnBusyHeartbeat({
    agentId: "agent-x",
    intervalMs: 10,
    isActive: () => controllerActive,
    postFn: async (agentId) => { calls.push(agentId); },
  });
  await new Promise(r => setTimeout(r, 50));
  stop();
  assert.ok(calls.length >= 2, `expected ≥2 POSTs during 50ms with 10ms interval; got ${calls.length}`);
  assert.strictEqual(calls[0], "agent-x");
});

test("startTurnBusyHeartbeat stops POSTing when isActive returns false", async () => {
  const calls = [];
  let controllerActive = true;
  const stop = startTurnBusyHeartbeat({
    agentId: "agent-y",
    intervalMs: 10,
    isActive: () => controllerActive,
    postFn: async (agentId) => { calls.push(agentId); },
  });
  await new Promise(r => setTimeout(r, 30));
  controllerActive = false;
  const callsBeforeStop = calls.length;
  await new Promise(r => setTimeout(r, 50));
  stop();
  // After flipping to inactive, no new calls should arrive
  assert.strictEqual(calls.length, callsBeforeStop,
    `expected no new POSTs after isActive→false; got ${calls.length - callsBeforeStop} extra`);
});

test("startTurnBusyHeartbeat is no-op without required params", () => {
  const stop1 = startTurnBusyHeartbeat({});
  stop1(); // should not throw
});
```

- [ ] **Step 2: Verify fail**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/turn-busy-heartbeat.test.js`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `mcp/stdio/turn-busy-heartbeat.js`**

```javascript
// Plan 4 turn-busy heartbeat. While a runtime controller's start() promise
// is unresolved (mid-turn), POSTs turn_busy=1 to keep server-side status
// fresh independent of pre_llm_call / PostToolUse hook firing. Solves
// the operator-observed "working flapping to online during long turns"
// issue.
//
// File budget per 500-line rule: ≤200 lines.

export function startTurnBusyHeartbeat({ agentId, intervalMs, isActive, postFn }) {
  const noop = () => {};
  if (!agentId || typeof isActive !== "function" || typeof postFn !== "function"
      || !Number.isFinite(intervalMs) || intervalMs <= 0) {
    return noop;
  }
  let stopped = false;

  const tick = async () => {
    if (stopped) return;
    try {
      if (!isActive()) return;
      await postFn(agentId);
    } catch {
      // best-effort
    }
  };

  const timer = setInterval(tick, intervalMs);
  if (typeof timer.unref === "function") timer.unref();

  return () => {
    stopped = true;
    clearInterval(timer);
  };
}

export function makeDefaultTurnBusyPoster(baseUrl) {
  const root = String(baseUrl || "").replace(/\/+$/, "");
  return async (agentId) => {
    const url = `${root}/api/v1/agents/${encodeURIComponent(agentId)}/turn-start`;
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: "bridge-heartbeat" }),
    });
    if (!res.ok && res.status !== 404) {
      // 404 means agent doesn't exist anymore — ok to stop. Other errors swallowed.
      throw new Error(`turn-busy heartbeat ${res.status}`);
    }
  };
}
```

- [ ] **Step 4: Wire into server.js bootstrap**

Edit `mcp/stdio/server.js`. Near where `startSessionHandleHeartbeat` is invoked, add:

```javascript
import { startTurnBusyHeartbeat, makeDefaultTurnBusyPoster } from "./turn-busy-heartbeat.js";

// ... existing module-level setup ...

// Track active controller's start() promise — when it's unresolved, heartbeat fires
const __turnBusyState = { activePromise: null };
function __markControllerStart(promise) { __turnBusyState.activePromise = promise; }
function __isControllerActive() { return __turnBusyState.activePromise && __turnBusyState.activePromise.then && !__turnBusyState.resolved; }

// Patch any place the bridge invokes controller.start() to set __turnBusyState.activePromise
// (left as an integration point — the implementer threads this through launchRuntimeRun
//  or wherever Plan 3's adapter.controllerFor().start() is called)

const __stopTurnBusyHeartbeat = startTurnBusyHeartbeat({
  agentId: String(process.env.AIFY_AGENT_ID || "").trim(),
  intervalMs: 30000, // 30s
  isActive: __isControllerActive,
  postFn: makeDefaultTurnBusyPoster(__serverUrl),
});
```

Add `try { __stopTurnBusyHeartbeat(); } catch {}` to the existing `cleanupOnExit()` block.

- [ ] **Step 5: Run tests**

```bash
cd C:/Docker/aify-comms && node --test mcp/stdio/tests/turn-busy-heartbeat.test.js
node --check mcp/stdio/server.js
node --test mcp/stdio/tests/binding-file.test.js mcp/stdio/tests/runtime-state.test.js
```
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add mcp/stdio/turn-busy-heartbeat.js mcp/stdio/tests/turn-busy-heartbeat.test.js mcp/stdio/server.js
git commit -m "feat(bridge): turn-busy heartbeat keeps 'working' status fresh during long turns"
```

---

### Task 12: New `ready` status + PATCH endpoint

**Files:**
- Modify: `service/routers/api_v2.py` — add `PATCH /api/v2/agents/{id}/ready` endpoint + status taxonomy update
- Create: `service/tests/test_ready_status_endpoint.py`

- [ ] **Step 1: Write failing test**

Create `service/tests/test_ready_status_endpoint.py`:

```python
"""Plan 4 ready status: new endpoint PATCH /api/v2/agents/{id}/ready sets
agent_turn_state.ready=True; status resolver returns 'ready' when worker
is live + ready=True + turn_busy=False."""

import sys, tempfile, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import unittest
import json
from fastapi import FastAPI
from fastapi.testclient import TestClient


class ReadyStatusEndpointTests(unittest.TestCase):
    def setUp(self):
        from service.db import init_db
        from service.routers.api_v2 import router
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test-ready.db")
        init_db(self.db_path)
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_patch_ready_endpoint_exists(self):
        # Register an agent
        self.client.post("/api/v1/register", json={
            "agentId": "test-ready", "role": "tester",
            "runtime": "codex", "sessionMode": "managed",
        })
        resp = self.client.patch("/api/v2/agents/test-ready/ready", json={"ready": True})
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_patch_ready_returns_404_for_unknown_agent(self):
        resp = self.client.patch("/api/v2/agents/nonexistent/ready", json={"ready": True})
        self.assertEqual(resp.status_code, 404)
```

- [ ] **Step 2: Verify fail**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_ready_status_endpoint.py -v`
Expected: FAIL — endpoint doesn't exist.

- [ ] **Step 3: Add endpoint + status taxonomy**

Edit `service/routers/api_v2.py`. Find a similar PATCH endpoint (e.g. `update_agent_session_handle` at line 9185) and model the new one after it:

```python
class AgentReadyUpdate(BaseModel):
    ready: bool = True
    requestedBy: str = ""


@router.patch("/agents/{agent_id}/ready")
async def update_agent_ready(agent_id: str, req: AgentReadyUpdate, request: Request):
    """Plan 4: bridge POSTs here when an adapter's controller.start() has
    completed initial handshake. Status taxonomy transitions agent from
    `starting` → `ready` (which sits between `online` and `working`)."""
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        now = _now()
        # Update agent_turn_state.ready
        await db.execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, ready, updated_at)
            VALUES (?, 0, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET ready = ?, updated_at = ?
            """,
            (agent_id, 1 if req.ready else 0, now, 1 if req.ready else 0, now),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("agent_ready", {"agentId": agent_id, "ready": req.ready})
        return {"ok": True, "agentId": agent_id, "ready": req.ready}
    finally:
        await db.close()
```

Update `_compute_agent_status` to return `"ready"` when `live_worker AND ready=True AND turn_busy=False`. Adjust the order:

```python
# After the existing "managed-no-worker → available" branch:
if has_live_terminal or has_live_rpc:
    turn_state = await _get_agent_turn_state(db, agent_id)
    if turn_state.get("turn_busy"):
        return "working"
    if turn_state.get("ready"):
        return "ready"
    # Has worker but not handshake-complete → keep existing online behavior
```

May need a schema migration for `agent_turn_state.ready` column if not present. Check `service/db.py` migrations.

- [ ] **Step 4: Test + commit**

```bash
cd C:/Docker/aify-comms && python -m pytest service/tests/test_ready_status_endpoint.py -v
git add service/routers/api_v2.py service/tests/test_ready_status_endpoint.py service/db.py
git commit -m "feat(server): new 'ready' status + PATCH /agents/{id}/ready endpoint (Plan 4)"
```

---

### Task 13: Adapter `ready` emission hooks in controllers

**Files:**
- Modify: `mcp/stdio/controllers/{claude,codex,hermes,pi}-controller.js`
- Modify: `mcp/stdio/server.js` — POST to `/ready` when controller signals handshake done

- [ ] **Step 1: Add `markReady()` hook to BaseController**

Edit `mcp/stdio/controllers/base-controller.js`. Add:

```javascript
  // Plan 4: subclasses call this when their initial handshake completes.
  // Bridge listens and POSTs PATCH /api/v2/agents/{id}/ready.
  _onReady = null;  // setter assigned by bridge

  setReadyListener(fn) { this._onReady = fn; }
  markReady() {
    if (typeof this._onReady === "function") {
      try { this._onReady(); } catch { /* swallow */ }
    }
  }
```

- [ ] **Step 2: Each controller calls `this.markReady()` after handshake**

For each controller (claude/codex/hermes/pi), find the point AFTER initial handshake completes:
- **claude:** after claude-channel.js confirms channel binding (channelEnabled becomes true)
- **codex:** after app-server WS `initialized` notification
- **hermes (resident):** after gateway WS `prompt.submit` first ack
- **hermes (managed):** after wrapper TUI renders or first ACP `session.init` ack
- **pi:** after omp emits `agent_ready` event (see existing `extractPiSessionState` patterns)

Insert `this.markReady();` at that point in each controller.

- [ ] **Step 3: Bridge wires `markReady` listener to PATCH /ready**

In `mcp/stdio/server.js` where the controller is instantiated (look at `launchRuntimeRun` path or controller spawn):

```javascript
controller.setReadyListener(async () => {
  const url = `${__serverUrl}/api/v2/agents/${encodeURIComponent(__agentId)}/ready`;
  try {
    await fetch(url, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ready: true, requestedBy: "controller-handshake" }),
    });
  } catch { /* best-effort */ }
});
```

- [ ] **Step 4: Test + commit**

Existing controller tests should still pass. Add a new smoke test that verifies `markReady` callback fires (mock the listener, assert it's invoked).

```bash
git add mcp/stdio/controllers/*.js mcp/stdio/server.js
git commit -m "feat(controllers): emit ready signal after handshake (Plan 4)"
```

---

## Phase D — codex-aify stale-handle probe

### Task 14: Codex storage layout recon + wrapper fix

**Files:**
- Modify: `install.sh` (codex-aify wrapper generation)
- Modify: `mcp/stdio/controllers/codex-controller.js`
- Modify: `install.codex.md`
- Modify: `mcp/stdio/tests/codex-aify-fallback.test.js`

- [ ] **Step 1: Recon codex storage layout on operator's WSL**

(Implementer should ask the operator OR check both common shapes.)

Probe steps:

```bash
ls ~/.codex/sessions/ 2>/dev/null | head -10
find ~/.codex/sessions -maxdepth 3 -type f -name "*.jsonl" 2>/dev/null | head -5
codex sessions list 2>&1 | head -10
codex --help | grep -i session
```

Document findings.

- [ ] **Step 2: Update wrapper probe in install.sh**

Find the `CODEX_RESUME_HANDLE` probe in `install.sh` (search for `~/.codex/sessions`). Replace narrow flat-layout check with multi-path:

```bash
# Plan 4: codex's session storage may use any of: flat layout
# (~/.codex/sessions/<id>.jsonl), date-sharded
# (~/.codex/sessions/YYYY/MM/DD/rollout-*<id>.jsonl), or dir-per-session.
# Check all candidates.
CODEX_SESSION_FOUND=""
for candidate in \
    "$HOME/.codex/sessions/$CODEX_RESUME_HANDLE.jsonl" \
    "$HOME/.codex/sessions/$CODEX_RESUME_HANDLE/rollout.jsonl"; do
  if [ -f "$candidate" ]; then
    CODEX_SESSION_FOUND="$candidate"
    break
  fi
done
# Also try recursive find for date-sharded layout
if [ -z "$CODEX_SESSION_FOUND" ]; then
  CODEX_SESSION_FOUND="$(find "$HOME/.codex/sessions" -type f -name "*$CODEX_RESUME_HANDLE*" 2>/dev/null | head -1)"
fi

if [ -n "$CODEX_SESSION_FOUND" ]; then
  exec "$CODEX_RUNTIME_COMMAND" "${CODEX_ARGS[@]}" resume --include-non-interactive "$CODEX_RESUME_HANDLE"
fi
echo "[codex-aify] saved session $CODEX_RESUME_HANDLE not found in codex storage; starting fresh codex" >&2
```

- [ ] **Step 3: Mirror the probe in codex-controller.js**

Update `mcp/stdio/controllers/codex-controller.js`'s stale-handle check (if it has one) to mirror the same multi-path probe.

- [ ] **Step 4: Update test to verify new probe shape**

Update `mcp/stdio/tests/codex-aify-fallback.test.js`:

```javascript
test("install.sh codex-aify wrapper checks multiple session storage layouts", () => {
  const src = fs.readFileSync(INSTALL_SH, "utf8");
  // Must check at least 2 candidate paths
  assert.ok(src.includes(".codex/sessions/$CODEX_RESUME_HANDLE.jsonl") ||
            src.includes(".codex/sessions/$CODEX_RESUME_HANDLE/"),
            "expected wrapper to check multiple codex session storage paths");
  assert.ok(/find.*\.codex\/sessions/.test(src) || /CODEX_SESSION_FOUND/.test(src),
            "expected multi-layout probe in wrapper");
});
```

- [ ] **Step 5: Document in install.codex.md**

Append a section to `install.codex.md` describing the codex session storage layout(s) the wrapper supports, with a link to this Plan 4 spec.

- [ ] **Step 6: Test + commit**

```bash
cd C:/Docker/aify-comms && node --test mcp/stdio/tests/codex-aify-fallback.test.js && bash -n install.sh
git add install.sh mcp/stdio/controllers/codex-controller.js install.codex.md mcp/stdio/tests/codex-aify-fallback.test.js
git commit -m "fix(codex-aify): probe accepts flat + date-sharded + dir-per-session layouts (Plan 4)"
```

---

## Phase E — Wrapper/operator UX

### Task 15: Smoke-verify claude-aify MCP fix

**Files:** None — verification only.

- [ ] **Step 1: Launch a fresh claude-aify session manually**

(Operator-side or implementer follows-up. Plan 4 implementer dispatches a brief subagent to test this if a fresh claude-aify can be launched without disrupting current sessions.)

In a NEW terminal: `claude-aify --aify-agent verify-mcp-test`

- [ ] **Step 2: Inside the claude session, list MCP tools**

Run inside claude:
- Type `/mcp` or look at the available tools list
- Confirm at least the operator's typical MCPs (`aify-comms`, `aify-comms-channel`, and any others in `~/.claude.json`) are present

- [ ] **Step 3: Close issue or escalate**

If MCPs are visible → close issue #2. If still missing → investigate further: check `~/.claude.json` mcpServers entry; check the wrapper actually omits `--strict-mcp-config` (`grep strict-mcp-config ~/.local/bin/claude-aify` should show the env-gate, not unconditional).

- [ ] **Step 4: Commit a verification note**

If verified working, add a note to DECISIONS.md under the Plan 4 entry:

```markdown
**Issue 2 verification (2026-05-25):** Smoke-verified that claude-aify launched with the redeployed wrapper successfully loads the operator's full ~/.claude.json mcpServers list. Option A fix from commit 6b79dd0 is operational.
```

Commit message: `docs: confirm claude-aify MCP isolation fix verified live (Plan 4 issue 2)`

---

### Task 16: redeploy.sh — auto-detect and reinstall wrappers

**Files:**
- Create: `redeploy.sh` (at repo root)
- Create: `mcp/stdio/tests/redeploy-script.test.js` (smoke pin)

- [ ] **Step 1: Write failing test**

```javascript
// mcp/stdio/tests/redeploy-script.test.js
import assert from "assert";
import test from "node:test";
import fs from "fs";
import path from "path";

test("redeploy.sh exists at repo root", () => {
  assert.ok(fs.existsSync(path.resolve("redeploy.sh")),
    "redeploy.sh must exist at repo root");
});

test("redeploy.sh detects installed *-aify wrappers and reinvokes install.sh", () => {
  const src = fs.readFileSync(path.resolve("redeploy.sh"), "utf8");
  assert.ok(/-aify/.test(src), "must reference *-aify wrappers");
  assert.ok(/install\.sh/.test(src), "must invoke install.sh");
  assert.ok(/--client/.test(src), "must pass --client to install.sh");
});
```

- [ ] **Step 2: Verify fail**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/redeploy-script.test.js`
Expected: FAIL — redeploy.sh doesn't exist.

- [ ] **Step 3: Create `redeploy.sh`**

```bash
#!/bin/bash
# redeploy.sh — Plan 4 helper. Detects which *-aify wrappers are installed
# at ~/.local/bin/ and reinvokes install.sh --client <X> SERVER_URL for each.
#
# Use after pulling new aify-comms changes to refresh the operator's
# installed wrappers without manually invoking install.sh per-client.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_SERVER="${AIFY_DEFAULT_SERVER_URL:-http://192.0.2.10:8800}"
SERVER_URL="${1:-$DEFAULT_SERVER}"

if [ ! -f "$REPO_ROOT/install.sh" ]; then
  echo "redeploy.sh: install.sh not found at $REPO_ROOT" >&2
  exit 1
fi

WRAPPERS_DIR="$HOME/.local/bin"
if [ ! -d "$WRAPPERS_DIR" ]; then
  echo "redeploy.sh: $WRAPPERS_DIR does not exist; nothing to redeploy" >&2
  exit 0
fi

CLIENTS=()
for client in claude codex hermes pi opencode; do
  if [ -x "$WRAPPERS_DIR/${client}-aify" ]; then
    CLIENTS+=("$client")
  fi
done

if [ ${#CLIENTS[@]} -eq 0 ]; then
  echo "redeploy.sh: no *-aify wrappers detected in $WRAPPERS_DIR"
  exit 0
fi

echo "redeploy.sh: detected wrappers: ${CLIENTS[*]}"
echo "redeploy.sh: server URL: $SERVER_URL"

for client in "${CLIENTS[@]}"; do
  echo "redeploy.sh: refreshing $client..."
  bash "$REPO_ROOT/install.sh" --client "$client" "$SERVER_URL" || {
    echo "redeploy.sh: install.sh failed for $client" >&2
    exit 1
  }
done

echo "redeploy.sh: all wrappers refreshed."
echo
echo "Reminder: restart any open *-aify sessions to pick up the new wrappers."
```

```bash
chmod +x redeploy.sh
```

- [ ] **Step 4: Test + commit**

```bash
cd C:/Docker/aify-comms && bash -n redeploy.sh && node --test mcp/stdio/tests/redeploy-script.test.js
git add redeploy.sh mcp/stdio/tests/redeploy-script.test.js
chmod +x redeploy.sh  # ensure executable bit preserved
git update-index --chmod=+x redeploy.sh
git commit -m "feat: redeploy.sh — auto-detect and refresh *-aify wrappers (Plan 4)"
```

---

### Task 17: Drop hermes-session-resume

**Files:**
- Modify: `service/routers/api_v2.py` — find the wake-mode logic that returns `hermes-session-resume`
- Modify: `mcp/stdio/controllers/hermes-*-controller.js` — remove the resume path if it exists separately from gateway path
- Modify: tests

- [ ] **Step 1: Locate `hermes-session-resume` references**

Run: `cd C:/Docker/aify-comms && grep -rn "hermes-session-resume" mcp/stdio service --include="*.py" --include="*.js"`

Note all locations.

- [ ] **Step 2: Write failing test (deprecation)**

Create `service/tests/test_hermes_session_resume_removed.py`:

```python
"""Plan 4: hermes-session-resume wake-mode is removed. Hermes resident
agents must use the gateway path (hermes-live wake-mode); fresh handles
captured via discoverSessionId."""


def test_hermes_session_resume_not_returned_by_wake_mode_logic():
    """Verify that no code path returns 'hermes-session-resume' as a wake mode."""
    import subprocess
    out = subprocess.run(
        ["grep", "-rn", "hermes-session-resume", "service/routers/api_v2.py"],
        capture_output=True, text=True
    )
    # After Plan 4, the only remaining references should be in comments
    # explaining the removal. No string literal returned as a wake_mode value.
    for line in out.stdout.splitlines():
        if "wake_mode" in line.lower() and "return" in line.lower() and "hermes-session-resume" in line:
            raise AssertionError(f"hermes-session-resume still returned as wake mode: {line}")
```

- [ ] **Step 3: Verify fail**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_hermes_session_resume_removed.py -v`
Expected: FAIL if hermes-session-resume is still returned anywhere.

- [ ] **Step 4: Remove `hermes-session-resume` wake-mode branch**

For each location from Step 1:
- If the code returns `"hermes-session-resume"` as a wake_mode, replace with `"hermes-live"` (use gateway) or `"hermes-missing-handle"` (fall back to error message)
- If a controller branch handles it, delete the branch
- Tests asserting `hermes-session-resume` should now expect `hermes-live` or be removed

- [ ] **Step 5: Test + commit**

```bash
cd C:/Docker/aify-comms && python -m pytest service/tests/test_hermes_session_resume_removed.py service/tests/test_api_v2_regressions.py -v 2>&1 | tail -20
git add service/routers/api_v2.py mcp/stdio/controllers/hermes-*-controller.js service/tests/
git commit -m "feat(server): drop hermes-session-resume wake-mode — gateway is single source (Plan 4)"
```

---

### Task 18: chooseSessionConsoleWidget — prefer wrapper PTY

**Files:**
- Modify: `service/new_dashboard/app.js:chooseSessionConsoleWidget`
- Modify: `service/new_dashboard/app.test.mjs` (or wherever the existing widget tests live)

- [ ] **Step 1: Find existing chooser**

Run: `cd C:/Docker/aify-comms && grep -n "chooseSessionConsoleWidget" service/new_dashboard/app.js service/new_dashboard/app.test.mjs`

- [ ] **Step 2: Write failing test**

Append to `service/new_dashboard/app.test.mjs`:

```javascript
test("chooseSessionConsoleWidget prefers wrapper PTY when both wrapper PTY and synth exist for same agent", () => {
  const result = chooseSessionConsoleWidget({
    agentId: "test-x",
    terminals: [
      { id: "synth-1", command: "aify://virtual-rpc/codex", agent_id: "test-x", status: "running" },
      { id: "pty-1", command: "codex-aify --aify-agent test-x", agent_id: "test-x", status: "running" },
    ],
    // ... other params required by the function signature ...
  });
  assert.strictEqual(result.kind, "xterm", "wrapper PTY should win when both exist");
  assert.strictEqual(result.terminalId, "pty-1");
});

test("chooseSessionConsoleWidget keeps synth when only synth exists (no wrapper)", () => {
  const result = chooseSessionConsoleWidget({
    agentId: "test-y",
    terminals: [
      { id: "synth-1", command: "aify://virtual-rpc/opencode", agent_id: "test-y", status: "running" },
    ],
  });
  assert.strictEqual(result.kind, "synth");
});
```

- [ ] **Step 3: Update chooseSessionConsoleWidget**

Find the function in `service/new_dashboard/app.js` and add wrapper-PTY preference:

```javascript
function chooseSessionConsoleWidget({ agentId, terminals, ...rest }) {
  // Plan 4: when both a wrapper PTY and a synth virtual-rpc terminal exist
  // for the same agent, prefer the wrapper PTY (operator-facing real
  // console). Synth survives only as the legacy/fallback path.
  const aidTerminals = (terminals || []).filter(t => t.agent_id === agentId && t.status === "running");
  const wrapperPty = aidTerminals.find(t => t.command && !t.command.startsWith("aify://virtual-rpc/"));
  if (wrapperPty) {
    return { kind: "xterm", terminalId: wrapperPty.id, ... };
  }
  const synth = aidTerminals.find(t => t.command && t.command.startsWith("aify://virtual-rpc/"));
  if (synth) {
    return { kind: "synth", terminalId: synth.id, ... };
  }
  // ... rest of existing logic for hermes iframe, codex synth, etc. ...
}
```

- [ ] **Step 4: Test + commit**

```bash
cd C:/Docker/aify-comms && node --test service/new_dashboard/app.test.mjs
git add service/new_dashboard/app.js service/new_dashboard/app.test.mjs
git commit -m "fix(dashboard): chooseSessionConsoleWidget prefers wrapper PTY over synth (Plan 4)"
```

---

### Task 19: Dashboard color coding for `ready`

**Files:**
- Modify: `service/new_dashboard/styles.css` (or equivalent)
- Modify: `service/new_dashboard/app.js` (status-class mapping)

- [ ] **Step 1: Find status color mapping**

Run: `cd C:/Docker/aify-comms && grep -rn "status-online\|status-available\|status-working" service/new_dashboard/ | head -10`

- [ ] **Step 2: Add `status-ready` styling**

Update styles.css to add:

```css
.status-ready {
  /* Plan 4: handshake-complete signal — distinct from process-alive. */
  color: var(--status-ready, #2ecc71); /* green */
  font-weight: 600;
}
.status-online {
  color: var(--status-online, #88c999); /* light green */
}
```

Update the JS status-class mapping to recognize `"ready"`:

```javascript
const STATUS_CLASSES = {
  available: "status-available",
  starting: "status-starting",
  ready: "status-ready",       // new
  online: "status-online",
  working: "status-working",
  // ...
};
```

- [ ] **Step 3: Commit**

```bash
git add service/new_dashboard/styles.css service/new_dashboard/app.js
git commit -m "feat(dashboard): color coding for new 'ready' status (Plan 4)"
```

---

## Phase F — Holistic review + finishing

### Task 20: Full Node + Python smoke

- [ ] **Step 1: Run full suites**

```bash
cd C:/Docker/aify-comms && node --test mcp/stdio/tests/*.test.js mcp/stdio/tests/adapters/*.test.js mcp/stdio/tests/controllers/*.test.js 2>&1 | tail -20
python -m pytest service/tests/ -q 2>&1 | tail -10
```

Expected: all pass (modulo Plan 1's known `test_new_dashboard_app.py` UTF-8 pre-existing failures).

If any new test failures surfaced, address inline before continuing.

- [ ] **Step 2: Rebuild container + verify health**

```bash
docker compose up -d --build 2>&1 | tail -5
curl -4 -s http://127.0.0.1:8800/health
```

Expected: `{"status":"healthy"}`.

- [ ] **Step 3: Commit any test-suite or container fixes**

---

### Task 21: Holistic code reviewer subagent

- [ ] **Step 1: Dispatch the reviewer**

Same pattern as Plan 3 Task 119: dispatch a subagent (general-purpose, no implementation tools needed; Explore type works) with the full Plan 4 commit list to review architecturally + per-file.

Specifically check:
- 500-line rule on all new files (turn-busy-heartbeat.js, redeploy.sh, etc.)
- discoverSessionId implementations don't crash on missing dirs
- ready emission paths are actually wired in all 4 controllers
- Status taxonomy doesn't break existing operator workflows (e.g., dashboard filters)
- No new pre-existing test regressions
- Cross-language consistency (Python + JS adapter symmetry maintained)

- [ ] **Step 2: Address Critical / Important concerns inline**

Each concern → small fix commit. Skip Minor unless trivially easy.

- [ ] **Step 3: Re-run full suites after fixes**

Same as Task 20 Step 1.

---

### Task 22: Documentation refresh

- [ ] **Step 1: Update DECISIONS.md**

Append:

```markdown

## 2026-05-25 — Plan 4: Status, session-handle, default-path fixes

**Decision:** Close 12 operator-surfaced gaps from Plans 1+2+3 live testing.
Flip wrapper-backed delivery to default. Deprecate synth terminals for
wrapper-backed runtimes (synth stays only for opencode + hard-failure
fallback). Per-adapter `discoverSessionId` closes the "missing handles"
gap for fresh managed launches. Status taxonomy stops lying — managed
agents without a live worker show `available` not `online`; new `ready`
status sits between `online` and `working`. codex-aify stale-handle probe
accepts multi-layout codex session storage. hermes-session-resume
removed (gateway path is the single source). New helpers: turn-busy
heartbeat, redeploy.sh.

**Why:** Operator-driven live testing after Plans 1+2+3 surfaced a tight
cluster of issues that were each blocking everyday use. Plan 4 is the
"polish layer that makes the architecture deployable."

**See:** `docs/superpowers/specs/2026-05-25-plan4-status-and-session-handle-fixes-design.md`,
`docs/superpowers/plans/2026-05-25-plan4-status-and-session-handle-fixes.md`.
```

- [ ] **Step 2: Update README.md**

Add `redeploy.sh` to repo layout. Mention Plan 4 in the architecture section.

- [ ] **Step 3: Update skills + install guides**

- `.claude/skills/aify-comms/SKILL.md` and `.agents/` mirror: add a note about the new `ready` status if status references appear there
- `install.codex.md`: codex session storage layout already documented in Task 14
- `install.hermes.md`: drop the `hermes-session-resume` mention now that wake-mode is gone

- [ ] **Step 4: Commit + push**

```bash
cd C:/Docker/aify-comms
git add DECISIONS.md README.md .claude/skills .agents/skills install.*.md
git commit -m "docs: record Plan 4 — status, session-handle, default-path fixes"
git push origin feature/dashboard-console-mode 2>&1 | tail -5
```

---

### Task 23: finishing-a-development-branch

- [ ] **Step 1: Invoke the finishing skill**

Announce: "I'm using the finishing-a-development-branch skill to complete Plan 4."

Use `superpowers:finishing-a-development-branch`. Follow its workflow to present merge / PR / cleanup options to the operator.

---

## After all tasks complete

The branch is ready for the operator's final ack + merge. Plans 1+2+3+4 collectively delivered a unified `RuntimeAdapter` architecture across all supported runtimes, with status taxonomy that doesn't lie and session-handle capture that works for every launch mode.
