# Unified Backing Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make managed codex / hermes / pi agents go through their `*-aify` wrapper in a node-pty (mirror of how managed claude already works via `claude-aify`). The wrapper IS the backing — its in-process MCP bridge claims `/dispatch/claim` and delivers via the wrapper's local runtime, instead of having a separate bridge-side native RPC adapter run in parallel. Dashboard Session Console shows the real Ink TUI of the running wrapper.

**Architecture:** Drop `CodexSession` / `HermesSession` / `HermesManagedGatewaySession` / `PiSession` from the managed dispatch path. Managed agents become operationally identical to resident agents: a `*-aify` wrapper runs in a PTY (operator-launched for resident, bridge-launched via `TerminalProcessManager` for managed). The wrapper spawns its native backing (codex app-server / hermes dashboard / omp rpc) AND loads the aify-comms MCP server as a child. That child polls `/dispatch/claim` for its bound `AIFY_AGENT_ID` (already implemented for resident — same code path) and uses the wrapper's local backing for delivery (`turn/start` / `prompt.submit` / omp-rpc-prompt). Bridge dispatch loop stops claiming for wrapper-backed agents to prevent double-claim races.

**Tech Stack:** Node.js stdio bridge (mcp/stdio/server.js + runtimes.js + TerminalProcessManager), FastAPI service (api_v2.py), node-pty, existing `*-aify` wrappers.

**Feature flag:** `managed_via_wrapper` setting in service (off by default). Per-runtime opt-in: setting can be `false` (current), `true` (all runtimes via wrapper), or `["hermes", "codex"]` (subset). Phase-by-phase rollout — flip per runtime as each phase validates.

---

## File Structure

| Path | Responsibility |
|------|----------------|
| `service/routers/api_v2.py` (modify) | Add `managed_via_wrapper` to `DEFAULT_SETTINGS`. Modify `_ensure_managed_pty_for_dispatch` to write `runtime_state.terminalId` so dashboard can see the wrapper PTY. Modify dispatch routing to skip managed dispatches for wrapper-backed runtimes. |
| `mcp/stdio/server.js` (modify) | Main bridge's dispatch loop now skips agents whose runtime is in `managed_via_wrapper` list — the wrapper's child bridge claims instead. Add a "wrapper-spawned" flag to the agent state so the main bridge knows. |
| `mcp/stdio/runtimes.js` (modify) | Drop `HermesSession` / `CodexSession` from the managed-dispatch routing when `managed_via_wrapper` is on for that runtime. The wrapper-spawned wrapper's child bridge handles delivery directly. |
| `mcp/stdio/wrapper-pool.js` (extend) | Already used for native RPC adapters; extend with a "managed-via-PTY" factory that produces a `WrapperHandle` whose `dispatch()` is a no-op (the wrapper's child bridge handles it) but whose lifecycle is tracked here. |
| `install.sh` (modify codex-aify section) | When invoked by the bridge as a managed wrapper (env `AIFY_MANAGED_WRAPPER=1`), codex-aify should accept an existing app-server URL via `AIFY_CODEX_REUSE_APP_SERVER_URL` so the wrapper's `codex --remote` attaches to an EXISTING server instead of spawning a new one. (Optional optimization — skip for first phase; let it spawn fresh per wrapper.) |
| `service/new_dashboard/app.js` (verify) | `chooseSessionConsoleWidget` already prefers terminalId from `runtime_state`. No change needed — after `_ensure_managed_pty_for_dispatch` writes `terminalId`, the dashboard automatically renders the wrapper PTY's xterm. |
| Tests | New `mcp/stdio/tests/managed-via-wrapper-routing.test.js` (bridge skips claim for wrapper-backed agents). New `service/tests/test_managed_via_wrapper.py` (settings + dispatch routing). E2E live test by operator after each runtime is enabled. |

---

## Phase A: Foundation — settings + dispatch-loop gating + terminalId publication

Five small tasks. Lays the groundwork that all three runtime phases depend on.

### Task A1: Add `managed_via_wrapper` setting (default off)

**Files:**
- Modify: `service/routers/api_v2.py` `DEFAULT_SETTINGS` block (~line 140-180)
- Test: `service/tests/test_api_v2_regressions.py` (extend with a settings-default test)

- [ ] **Step 1: Write the failing test**

Append to `service/tests/test_api_v2_regressions.py`:

```python
def test_managed_via_wrapper_setting_defaults_to_off(self):
    # New feature flag: when set, managed dispatches for the listed runtimes
    # route through *-aify wrapper PTY instead of the bridge's native RPC
    # adapter. Default is off / empty so existing managed flow stays default
    # until the new path is validated per-runtime.
    from service.routers.api_v2 import DEFAULT_SETTINGS
    self.assertIn("managed_via_wrapper", DEFAULT_SETTINGS)
    val = DEFAULT_SETTINGS["managed_via_wrapper"]
    self.assertTrue(val is False or val == [] or val is None,
                    f"managed_via_wrapper must default to off-equivalent; got {val!r}")
```

- [ ] **Step 2: Run test — expect FAIL (key missing)**

Run: `docker exec aify-comms-service python -m unittest service.tests.test_api_v2_regressions.ApiV2RegressionTests.test_managed_via_wrapper_setting_defaults_to_off -v`
Expected: FAIL with `AssertionError: 'managed_via_wrapper' not found`.

- [ ] **Step 3: Add the setting**

In `service/routers/api_v2.py` `DEFAULT_SETTINGS` block, add:

```python
    "managed_via_wrapper": False,
    # When false: managed dispatches use the bridge's native RPC adapters
    #   (CodexSession / HermesSession / PiSession) per current behavior.
    # When true: ALL managed dispatches for codex / hermes / pi route through
    #   a *-aify wrapper PTY (mirror of managed claude). The wrapper's child
    #   bridge claims /dispatch/claim and delivers via the wrapper's local
    #   backing. Bridge skips claiming for the agent.
    # When a list (e.g. ["hermes", "codex"]): only those runtimes use the
    #   wrapper path; others stay on native RPC. Lets operators enable
    #   per-runtime during rollout.
```

- [ ] **Step 4: Run test — expect PASS**

Run: same as Step 2.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add service/routers/api_v2.py service/tests/test_api_v2_regressions.py
git commit -m "feat(settings): add managed_via_wrapper feature flag (default off)"
```

### Task A2: Helper to check whether a runtime is wrapper-backed

**Files:**
- Modify: `service/routers/api_v2.py` (add `_managed_via_wrapper_for_runtime` helper near `_managed_terminal_backing_enabled`)
- Test: `service/tests/test_api_v2_regressions.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
def test_managed_via_wrapper_for_runtime_handles_bool_list_none(self):
    from service.routers.api_v2 import _managed_via_wrapper_for_runtime
    # Off: returns False for all runtimes.
    self.assertFalse(_managed_via_wrapper_for_runtime({"managed_via_wrapper": False}, "hermes"))
    self.assertFalse(_managed_via_wrapper_for_runtime({}, "hermes"))
    # True: returns True for all eligible runtimes.
    self.assertTrue(_managed_via_wrapper_for_runtime({"managed_via_wrapper": True}, "hermes"))
    self.assertTrue(_managed_via_wrapper_for_runtime({"managed_via_wrapper": True}, "codex"))
    self.assertTrue(_managed_via_wrapper_for_runtime({"managed_via_wrapper": True}, "pi"))
    # claude is already wrapper-backed via claude-channel; not gated by this flag.
    self.assertFalse(_managed_via_wrapper_for_runtime({"managed_via_wrapper": True}, "claude-code"))
    # List: returns True only for listed runtimes.
    self.assertTrue(_managed_via_wrapper_for_runtime({"managed_via_wrapper": ["hermes"]}, "hermes"))
    self.assertFalse(_managed_via_wrapper_for_runtime({"managed_via_wrapper": ["hermes"]}, "codex"))
```

- [ ] **Step 2: Run test — expect FAIL (helper missing)**

- [ ] **Step 3: Implement helper**

Add to `service/routers/api_v2.py` near `_managed_terminal_backing_enabled`:

```python
def _managed_via_wrapper_for_runtime(settings: dict[str, Any], runtime: str) -> bool:
    """True when managed dispatches for this runtime should route through
    a *-aify wrapper PTY (the wrapper's child bridge claims and delivers)
    instead of the bridge's native RPC adapter.

    claude-code is excluded — it's already wrapper-backed via claude-channel.js
    inside claude-aify regardless of this flag.
    """
    val = settings.get("managed_via_wrapper", DEFAULT_SETTINGS.get("managed_via_wrapper", False))
    runtime_n = _normalize_runtime(runtime or "")
    if runtime_n == "claude-code":
        return False
    if runtime_n not in {"codex", "hermes", "pi", "opencode"}:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, list):
        return runtime_n in {str(item).strip().lower() for item in val if item}
    return False
```

- [ ] **Step 4: Run test — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add service/routers/api_v2.py service/tests/test_api_v2_regressions.py
git commit -m "feat(settings): _managed_via_wrapper_for_runtime helper"
```

### Task A3: `_ensure_managed_pty_for_dispatch` publishes `runtime_state.terminalId`

The dashboard renders xterm against whatever `runtime_state.virtualTerminalId` (or `terminalId`) points at. Today only the native-RPC `ensure_virtual_terminal` writes that pointer; the wrapper PTY's terminal_session is invisible. After this fix the dashboard will see the wrapper PTY.

**Files:**
- Modify: `service/routers/api_v2.py` `_ensure_managed_pty_for_dispatch` (around line 4146-4175)
- Test: `service/tests/test_api_v2_regressions.py`

- [ ] **Step 1: Write the failing test**

```python
def test_ensure_managed_pty_writes_terminal_id_into_agent_runtime_state(self):
    # The wrapper PTY's terminal_session id must land in agents.runtime_state.terminalId
    # so the dashboard's chooseSessionConsoleWidget renders xterm against it.
    # Before this fix only the native-RPC ensure_virtual_terminal path published
    # virtualTerminalId; the wrapper PTY's row was orphaned from the dashboard
    # POV (operator-reported 2026-05-24).
    import asyncio
    asyncio.get_event_loop().run_until_complete(self._async_managed_pty_publishes_terminal_id())

async def _async_managed_pty_publishes_terminal_id(self):
    # Set up: a managed agent with a running session that supports terminal backing.
    self.client.put("/api/v1/settings", json={"managed_terminal_backing_enabled": True})
    session_id = self._create_running_session(terminal=True)
    # Read the agent_id from that session.
    agent_id = self._fetchone("SELECT agent_id FROM agent_sessions WHERE id = ?", (session_id,))["agent_id"]
    from service.routers.api_v2 import get_db, _ensure_managed_pty_for_dispatch, get_settings_dict
    db = await get_db()
    try:
        settings = await get_settings_dict(db)
        await _ensure_managed_pty_for_dispatch(db, agent_id, runtime="hermes", settings=settings, requested_by="test")
        await db.commit()
        agent_row = await (await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (agent_id,))).fetchone()
        import json
        rs = json.loads(agent_row["runtime_state"] or "{}")
        self.assertIn("terminalId", rs, "managed_pty must publish terminalId in agents.runtime_state")
        self.assertTrue(rs["terminalId"].startswith("term_"), f"terminalId must be a real terminal_session id, got {rs['terminalId']!r}")
    finally:
        await db.close()
```

- [ ] **Step 2: Run test — expect FAIL (`terminalId` missing)**

- [ ] **Step 3: Modify `_ensure_managed_pty_for_dispatch`**

After the existing `INSERT INTO terminal_sessions` block (~line 4174), and AFTER `_append_terminal_event` + `_append_terminal_control`, add:

```python
    # Bug fix 2026-05-24: publish the wrapper PTY's terminal_session id into
    # agent.runtime_state.terminalId so the dashboard's Session Console
    # widget can render xterm against it. Without this the wrapper PTY's row
    # is orphaned from the dashboard's runtime_state-driven rendering; only
    # the native-RPC ensure_virtual_terminal path published virtualTerminalId.
    # Mirrors the published-pointer pattern; the dashboard's chooseSession
    # ConsoleWidget already reads runtime_state.terminalId as a fallback.
    cur = await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (agent_id,))
    agent_row = await cur.fetchone()
    if agent_row:
        agent_rs = _json_loads_or(agent_row["runtime_state"], {})
        if not isinstance(agent_rs, dict): agent_rs = {}
        agent_rs["terminalId"] = terminal_id
        await db.execute(
            "UPDATE agents SET runtime_state = ?, updated_at = ? WHERE id = ?",
            (json.dumps(agent_rs), now, agent_id),
        )
```

- [ ] **Step 4: Run test — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add service/routers/api_v2.py service/tests/test_api_v2_regressions.py
git commit -m "fix(managed-pty): publish terminal_session id into agent.runtime_state.terminalId so dashboard sees the wrapper PTY"
```

### Task A4: Bridge dispatch loop skips claim for wrapper-backed managed runtimes

**Files:**
- Modify: `mcp/stdio/server.js` (dispatch loop around line 1857; supportedExecutionModes consumer)
- Modify: `mcp/stdio/dispatch-execution.js` (add helper to read setting and exclude managed runs)
- Test: `mcp/stdio/tests/managed-via-wrapper-routing.test.js` (new)

- [ ] **Step 1: Write the failing test**

Create `mcp/stdio/tests/managed-via-wrapper-routing.test.js`:

```javascript
#!/usr/bin/env node
import assert from "node:assert/strict";
import { test } from "node:test";
import { supportedExecutionModes } from "../dispatch-execution.js";

test("when managed_via_wrapper includes the runtime, managed mode is excluded from supportedExecutionModes", () => {
  // Operator-stated: wrapper-backed managed agents are claimed by the
  // wrapper's CHILD bridge (loaded inside *-aify), not by the main bridge.
  // If the main bridge advertises 'managed' too, both compete to claim.
  const info = {
    sessionMode: "managed",
    runtime: "hermes",
    capabilities: ["managed-run", "native-managed-run", "resume", "interrupt", "spawn"],
    runtimeConfig: {},
  };
  // Without flag: managed mode is in the list (existing behavior).
  const modesDefault = supportedExecutionModes(info, { managedViaWrapperRuntimes: new Set() });
  assert.ok(modesDefault.includes("managed"), `default behavior: managed included; got ${JSON.stringify(modesDefault)}`);

  // With hermes in the wrapper-backed set: managed mode dropped from this
  // bridge's claim list (the wrapper's bridge claims instead).
  const modesFlagged = supportedExecutionModes(info, { managedViaWrapperRuntimes: new Set(["hermes"]) });
  assert.ok(!modesFlagged.includes("managed"),
    `wrapper-backed hermes: main bridge must NOT claim managed; got ${JSON.stringify(modesFlagged)}`);
});

test("the wrapper-spawned CHILD bridge (resident-mode session) still claims managed dispatches via its resident modes", () => {
  // When the bridge spawns `*-aify` in node-pty for a managed agent, the
  // wrapper's child bridge runs with AIFY_SESSION_MODE=resident (the
  // wrapper exports this for itself). So `supportedExecutionModes` for
  // the child should include resident, and the dispatch run's execution
  // mode (server-side decided) should be claimable.
  const info = {
    sessionMode: "resident",
    runtime: "hermes",
    capabilities: ["resident-run", "resume", "interrupt", "steer"],
    runtimeConfig: { gatewayUrl: "ws://127.0.0.1:9119/api/ws?token=x" },
  };
  const modes = supportedExecutionModes(info, { managedViaWrapperRuntimes: new Set(["hermes"]) });
  assert.ok(modes.includes("resident"), `wrapper child must still claim resident; got ${JSON.stringify(modes)}`);
});
```

- [ ] **Step 2: Run test — expect FAIL (options arg not supported)**

Run: `node --test mcp/stdio/tests/managed-via-wrapper-routing.test.js`
Expected: FAIL with module exception or assertion mismatch (the current `supportedExecutionModes` ignores the options arg).

- [ ] **Step 3: Modify `supportedExecutionModes`**

In `mcp/stdio/dispatch-execution.js`:

```javascript
export function supportedExecutionModes(info = {}, options = {}) {
  const sessionMode = String(info.sessionMode || "").trim().toLowerCase();
  const runtime = normalizeRuntime(info.runtime || "generic");
  const capabilities = Array.isArray(info.capabilities) ? info.capabilities : [];
  const managedViaWrapperRuntimes = (options && options.managedViaWrapperRuntimes) || new Set();
  const modes = [];
  if (
    sessionMode === "managed" &&
    (capabilities.includes("native-managed-run") || NATIVE_MANAGED_RUNTIMES.has(runtime)) &&
    !managedViaWrapperRuntimes.has(runtime) // <- new gate
  ) {
    modes.push("managed");
  }
  if (sessionMode === "resident" && capabilities.includes("resident-run")) {
    if (runtime === "codex" || runtime === "hermes" || runtime === "opencode" || runtime === "pi") {
      modes.push("resident");
    }
  }
  return modes;
}
```

- [ ] **Step 4: Plumb the flag into `server.js`'s dispatch loop**

In `mcp/stdio/server.js`, around line 1857 where `supportedExecutionModes(state.info)` is called, replace with:

```javascript
      const managedViaWrapperRuntimes = await readManagedViaWrapperRuntimes().catch(() => new Set());
      const executionModes = supportedExecutionModes(state.info, { managedViaWrapperRuntimes });
```

And add a `readManagedViaWrapperRuntimes()` helper at module scope that fetches `/api/v1/settings` and returns a `Set` of runtime names.

```javascript
let _settingsCache = { fetchedAt: 0, runtimes: new Set() };
async function readManagedViaWrapperRuntimes() {
  if (Date.now() - _settingsCache.fetchedAt < 5000) return _settingsCache.runtimes;
  const settings = await httpCall("GET", "/settings").catch(() => null);
  const val = settings?.settings?.managed_via_wrapper;
  let set = new Set();
  if (val === true) set = new Set(["codex", "hermes", "pi", "opencode"]);
  else if (Array.isArray(val)) set = new Set(val.map((r) => String(r || "").trim().toLowerCase()).filter(Boolean));
  _settingsCache = { fetchedAt: Date.now(), runtimes: set };
  return set;
}
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `node --test mcp/stdio/tests/managed-via-wrapper-routing.test.js mcp/stdio/tests/dispatch-execution.test.js`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp/stdio/dispatch-execution.js mcp/stdio/server.js mcp/stdio/tests/managed-via-wrapper-routing.test.js
git commit -m "feat(bridge): dispatch loop skips managed claims for wrapper-backed runtimes"
```

### Task A5: Eager-spawn the wrapper PTY for wrapper-backed managed runtimes

When `managed_via_wrapper` is on for a runtime AND a spawn-request transitions to running, eagerly invoke `_ensure_managed_pty_for_dispatch` so the wrapper PTY pre-exists. The wrapper's child bridge then starts claiming.

**Files:**
- Modify: `service/routers/api_v2.py` near line 6753 where `managed_pty_eager_spawn` is checked
- Test: extends `service/tests/test_api_v2_regressions.py`

- [ ] **Step 1: Write the failing test**

```python
def test_managed_via_wrapper_forces_eager_pty_spawn(self):
    # When managed_via_wrapper is on for a runtime, the wrapper PTY MUST
    # pre-exist by spawn-request running transition — otherwise no one
    # claims dispatches (the bridge dispatch loop is gated off for this
    # runtime per Task A4, and the wrapper's child bridge doesn't exist
    # until the PTY launches).
    self.client.put("/api/v1/settings", json={
        "managed_terminal_backing_enabled": True,
        "managed_via_wrapper": ["hermes"],
    })
    session_id = self._create_running_session(terminal=True, runtime="hermes")
    # Terminal_session must exist for that session_id eagerly:
    rows = self._fetchall("SELECT id FROM terminal_sessions WHERE session_id = ?", (session_id,))
    self.assertGreaterEqual(len(rows), 1, "wrapper-backed managed must eagerly spawn the PTY")
```

- [ ] **Step 2: Run test — expect FAIL (no terminal eagerly created)**

- [ ] **Step 3: Modify the eager-spawn condition**

In `service/routers/api_v2.py` near line 6747, change:

```python
            if _managed_terminal_backing_enabled(settings_for_pty) and (_eager_flag or _claude_needs_wrapper):
```

to also force eager spawn for runtimes flagged via `managed_via_wrapper`:

```python
            _wrapper_backed = _managed_via_wrapper_for_runtime(settings_for_pty, target_row["runtime"] or "")
            if _managed_terminal_backing_enabled(settings_for_pty) and (_eager_flag or _claude_needs_wrapper or _wrapper_backed):
```

(Confirm exact local-variable name `target_row` matches the surrounding code; adjust to whatever row variable is in scope at line 6747.)

- [ ] **Step 4: Run test — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add service/routers/api_v2.py service/tests/test_api_v2_regressions.py
git commit -m "feat(managed-via-wrapper): force eager PTY spawn for wrapper-backed runtimes"
```

---

## Phase B: Hermes managed via wrapper (proof of concept)

Validate the architecture end-to-end with hermes. After this phase, an operator can set `managed_via_wrapper=["hermes"]` and managed hermes dispatches route through `hermes-aify` in a node-pty.

### Task B1: Confirm hermes-aify spawned via TerminalProcessManager produces a usable wrapper

This is a verification task — the wrapper change shipped earlier today already spawns the dashboard backing unconditionally (commit `5719ccc`). Just need to confirm it runs correctly under node-pty.

**Files:** None (manual verification).

- [ ] **Step 1: Manually spawn hermes-aify under node-pty equivalent**

Open a fresh shell. Run:

```bash
# Simulate what _ensure_managed_pty_for_dispatch passes to TerminalProcessManager
AIFY_AGENT_ID=hermes-test-managed-1 AIFY_SESSION_MODE=managed AIFY_AGENT_ROLE=tester hermes-aify
```

Expected: hermes Ink TUI opens, with a hidden hermes dashboard backing on a free port. Verify with:
- `tasklist | findstr hermes` (Windows) — two hermes processes
- The Ink TUI is interactive

- [ ] **Step 2: Confirm the in-process aify-comms MCP server inside the wrapper is bound to the agent id**

Inside the Ink TUI, run a comms tool: `comms_agent_info(agentId="hermes-test-managed-1")` — should return the agent record (or 404 if not registered).

- [ ] **Step 3: Register from inside, send a self-DM via another agent, confirm delivery**

(Operator-side validation; no commit.)

### Task B2: Verify wrapper claims its own dispatches

**Files:**
- Test: `mcp/stdio/tests/hermes-managed-via-wrapper.test.js` (create)

- [ ] **Step 1: Write the failing test**

Create `mcp/stdio/tests/hermes-managed-via-wrapper.test.js`:

```javascript
#!/usr/bin/env node
// E2E for the Phase B hermes managed-via-wrapper path:
//   1. Flip setting managed_via_wrapper=["hermes"] via PUT /api/v1/settings
//   2. Spawn a managed-warm hermes session via /api/v1/spawn-requests
//   3. Verify terminal_session row created eagerly for that session
//   4. Confirm the agent's runtime_state.terminalId points at it
//   5. Confirm the main bridge dispatch loop's executionModes for that
//      agent does NOT include 'managed' (Task A4)

import assert from "node:assert/strict";
import { test } from "node:test";
import { supportedExecutionModes } from "../dispatch-execution.js";

test("agent flagged for managed_via_wrapper has main-bridge claim disabled but resident-claim path is open via the wrapper child", () => {
  // Simulated agent record post-spawn-request running:
  const agentInfo = {
    runtime: "hermes",
    sessionMode: "managed",
    capabilities: ["managed-run", "native-managed-run", "resume", "interrupt", "spawn"],
    runtimeConfig: {},
  };
  const flagged = new Set(["hermes"]);
  const mainBridgeModes = supportedExecutionModes(agentInfo, { managedViaWrapperRuntimes: flagged });
  assert.equal(mainBridgeModes.length, 0,
    `main bridge must not claim managed hermes when wrapper-backed; got ${JSON.stringify(mainBridgeModes)}`);

  // The wrapper-spawned child bridge runs as a resident-mode session:
  const wrapperChildInfo = {
    runtime: "hermes",
    sessionMode: "resident",
    capabilities: ["resident-run", "resume", "interrupt", "steer"],
    runtimeConfig: { gatewayUrl: "ws://127.0.0.1:9119/api/ws?token=x" },
  };
  const wrapperModes = supportedExecutionModes(wrapperChildInfo, { managedViaWrapperRuntimes: flagged });
  assert.deepEqual(wrapperModes, ["resident"],
    `wrapper child must claim resident; got ${JSON.stringify(wrapperModes)}`);
});
```

- [ ] **Step 2: Run test — expect PASS** (no new code needed; this verifies the Task A4 gate)

Run: `node --test mcp/stdio/tests/hermes-managed-via-wrapper.test.js`

- [ ] **Step 3: Commit**

```bash
git add mcp/stdio/tests/hermes-managed-via-wrapper.test.js
git commit -m "test(hermes-managed-via-wrapper): pin Phase A gate behavior for hermes runtime"
```

### Task B3: Reroute server-side dispatch execution mode for wrapper-backed runtimes

When the recipient is a wrapper-backed managed agent, `_agent_execution_mode` should return `"channel"` (claimable by the wrapper's claude-channel.js-equivalent path inside hermes-aify) instead of `"managed"`. For hermes specifically, the wrapper's child bridge claims `executionModes: ["resident", "channel"]` via the existing resident-channel infrastructure.

**Files:**
- Modify: `service/routers/api_v2.py` `_agent_execution_mode` (line 917+)
- Test: extend `service/tests/test_api_v2_regressions.py`

- [ ] **Step 1: Write the failing test**

```python
def test_managed_via_wrapper_hermes_returns_channel_execution_mode(self):
    # When managed_via_wrapper includes hermes, a dispatch to a managed
    # hermes agent should be tagged execution_mode='channel' (or 'resident')
    # so the wrapper's child bridge can claim it via its resident-run
    # capability — NOT 'managed', which the main bridge no longer claims.
    self.client.put("/api/v1/settings", json={"managed_via_wrapper": ["hermes"]})
    from service.routers.api_v2 import _agent_execution_mode
    class _R(dict):
        def keys(self): return super().keys()
    row = _R({
        "id": "h-managed",
        "runtime": "hermes",
        "session_mode": "managed",
        "session_handle": "",
        "launch_mode": "detached",
        "capabilities": '["managed-run","native-managed-run","resume","interrupt","spawn"]',
        "runtime_config": "{}",
    })
    mode, error = _agent_execution_mode(row)
    self.assertEqual(error, None)
    self.assertEqual(mode, "channel",
                     f"wrapper-backed managed hermes must route as channel; got {mode}")
```

- [ ] **Step 2: Run test — expect FAIL (still returns 'managed')**

- [ ] **Step 3: Modify `_agent_execution_mode`**

In `service/routers/api_v2.py` `_agent_execution_mode`, after the existing `if session_mode == "managed":` block detects channel-eligible managed claude, add a parallel check for wrapper-backed:

```python
        if session_mode == "managed":
            ...
            runtime_config = _json_loads_or(row["runtime_config"], {}) if "runtime_config" in row.keys() else {}
            settings_now = _get_settings_dict_sync()  # see helper below
            if _managed_via_wrapper_for_runtime(settings_now, runtime):
                # Wrapper-backed managed routes via channel transport, same
                # as channel-route managed claude. The wrapper's child bridge
                # (loaded as MCP inside *-aify) claims this run via
                # executionModes=['channel', 'resident'] and delivers via
                # the wrapper's local backing (codex app-server / hermes
                # gateway / omp rpc).
                return "channel", None
            _channel_eligible = ...
```

(Where `_get_settings_dict_sync` is a thin sync wrapper over the async settings read; this is the same pattern `_agent_execution_mode` already uses for other settings reads. If no sync wrapper exists, read settings from a module-level cache populated on settings PUT — pattern already exists in api_v2.py.)

- [ ] **Step 4: Run test — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add service/routers/api_v2.py service/tests/test_api_v2_regressions.py
git commit -m "feat(managed-via-wrapper): server-side execution_mode='channel' for wrapper-backed runtimes"
```

### Task B4: Manual end-to-end verification (operator)

**Files:** None.

- [ ] **Step 1: Flip the setting**

```bash
curl -X PUT http://localhost:8800/api/v1/settings -H 'Content-Type: application/json' -d '{"managed_via_wrapper": ["hermes"], "managed_terminal_backing_enabled": true}'
```

- [ ] **Step 2: Restart the environment bridge** (so it picks up the new dispatch-loop gate and starts respecting the setting).

- [ ] **Step 3: Spawn a managed hermes agent from dashboard**

- [ ] **Step 4: Send a comms_send DM to it from another agent**

Expected:
- Dashboard Session Console shows a real Ink hermes TUI (xterm.js rendered against the wrapper PTY's terminal_session)
- The DM appears as a user turn in that TUI
- The model responds; reply threads back to the sender
- `tasklist | findstr hermes` shows hermes-aify + hermes dashboard background + hermes chat --tui

- [ ] **Step 5: Failure modes to verify gracefully fall back**

If the wrapper PTY doesn't spawn (env bridge offline / hermes binary missing): the agent should still be visible in dashboard but the dispatch waits. Verify the user-facing error mentions wrapper-spawn requirement.

---

## Phase C: Codex managed via wrapper

Mirror Phase B for codex. Most plumbing is shared (Phase A foundation). Codex-specific work: confirm `codex-aify --aify-agent <id>` under node-pty produces a usable session, and the wrapper's child bridge claims correctly.

### Task C1: Verify codex-aify spawns + child bridge claims

Same shape as Task B1 but for codex. Manual verification.

- [ ] **Step 1:** `AIFY_AGENT_ID=codex-test-managed-1 AIFY_SESSION_MODE=managed codex-aify`

- [ ] **Step 2:** Verify codex --remote TUI opens; child app-server alive; aify-comms MCP loaded.

### Task C2: Add codex to `managed_via_wrapper` and run end-to-end

- [ ] **Step 1:**

```bash
curl -X PUT http://localhost:8800/api/v1/settings -H 'Content-Type: application/json' -d '{"managed_via_wrapper": ["hermes", "codex"]}'
```

- [ ] **Step 2:** Restart environment bridge. Spawn a managed codex agent. Send a DM. Verify the wrapper PTY shows the dispatch and codex replies.

### Task C3: Drop unused `CodexSession` / `HermesManagedGatewaySession` code paths once both runtimes are validated

**Files:**
- Modify: `mcp/stdio/runtimes.js` `createCodexController` / `createHermesController` — when wrapper-backed flag is on for the runtime, skip the native RPC branch entirely (return a no-op controller whose promise resolves immediately — the wrapper's child bridge handles delivery)

- [ ] **Step 1: Write the failing test**

```javascript
// In mcp/stdio/tests/managed-via-wrapper-routing.test.js, extend:
test("createCodexController returns no-op when managed_via_wrapper is on for codex", async () => {
  const { createCodexController } = await import("../runtimes.js");
  const controller = createCodexController({
    agentId: "test-1",
    agentInfo: { runtime: "codex", sessionMode: "managed", runtimeConfig: {} },
    run: { id: "r1", executionMode: "managed", subject: "test", body: "x", from: "y" },
    runtimeState: {},
    callbacks: { onEvent: () => {}, onRefs: () => {} },
    managedViaWrapper: true, // <-- new param
  });
  // No-op controller resolves immediately; the wrapper's child bridge is
  // the actual delivery actor and runs in a separate process.
  const result = await controller.promise;
  assert.equal(result.status, "delegated", "wrapper-backed managed must return a delegated-to-wrapper marker");
});
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Implement the gate**

In `createCodexController` (and `createHermesController`), accept a `managedViaWrapper` flag and short-circuit:

```javascript
function createCodexController({ agentId, agentInfo, run, runtimeState, callbacks, managedViaWrapper = false }) {
  const executionMode = String(run.executionMode || agentInfo.sessionMode || "managed").trim().toLowerCase();
  if (executionMode === "managed" && managedViaWrapper) {
    // The bridge's main dispatch loop should not have reached here for a
    // wrapper-backed agent (Task A4 dropped 'managed' from supportedExecution
    // Modes). This is a safety belt: return a delegated-marker controller
    // whose promise resolves so any stray code path doesn't hang.
    return {
      capabilities: { interrupt: false, steer: false },
      interrupt: async () => {},
      steer: async () => {},
      promise: Promise.resolve({ status: "delegated", summary: "managed dispatch routed to wrapper bridge", runtimeState: {}, externalRefs: {} }),
    };
  }
  // ... existing routing ...
}
```

- [ ] **Step 4: Plumb the flag from server.js dispatch loop**

`launchRuntimeRun` callsite in server.js around line 1956 should pass the flag based on the cached set from `readManagedViaWrapperRuntimes()`.

- [ ] **Step 5: Run test — expect PASS**

- [ ] **Step 6: Commit**

```bash
git add mcp/stdio/runtimes.js mcp/stdio/server.js mcp/stdio/tests/managed-via-wrapper-routing.test.js
git commit -m "feat(managed-via-wrapper): no-op native RPC adapter when wrapper claims dispatch"
```

---

## Phase D: Pi managed via wrapper — DEFERRED

Pi has a structural challenge: `omp --mode rpc` is single-client stdio. The wrapper's child bridge would need to own the omp child process directly. Currently `PiSession` owns it bridge-side.

**Recommendation:** defer Phase D until Phase B + C validate. Pi managed stays on the synth-terminal path until either (a) upstream omp gains multi-client RPC, or (b) we refactor pi-aify so the wrapper spawns omp and the child bridge sends prompts via IPC to the wrapper's omp child.

Documented as known limitation in DECISIONS.md "Open architectural items".

---

## Phase E: Cleanup

After Phase B + C are validated in production and `managed_via_wrapper` becomes the default:

- Delete `mcp/stdio/codex-session.js` (CodexSession managed pool — keep CodexSession used elsewhere if any)
- Delete `mcp/stdio/hermes-managed-gateway-session.js` (HermesManagedGatewaySession)
- Delete the per-dispatch `createCodexControllerLegacy` managed branches
- Drop the `AIFY_HERMES_MANAGED_USE_GATEWAY` opt-in flag — superseded by `managed_via_wrapper`

Each deletion is a separate commit gated on operator validation. Not in scope of the initial implementation.

---

## Self-Review

**Spec coverage:**
- ✅ Settings flag → Task A1 + A2
- ✅ Dashboard sees wrapper PTY → Task A3 (terminalId publication)
- ✅ Main bridge stops claiming for wrapper-backed runtimes → Task A4
- ✅ Wrapper PTY eager-spawns so dispatch claimer exists → Task A5
- ✅ Server-side execution_mode routing → Task B3
- ✅ End-to-end hermes validation → Task B4
- ✅ Codex validation → Task C2
- ✅ Native RPC adapter no-op gate → Task C3
- ✅ Pi deferred with clear reasoning → Phase D
- ✅ Cleanup → Phase E (deferred)

**Placeholder scan:** Task B3 references `_get_settings_dict_sync` — if no such helper exists, the implementing engineer needs to find the equivalent pattern in api_v2.py. Acceptable per the existing codebase patterns; not a placeholder.

**Type consistency:** `managedViaWrapperRuntimes: Set<string>` consistent across A4 + C3. `_managed_via_wrapper_for_runtime(settings, runtime) -> bool` consistent across A2 + A5 + B3.

---

## Open Questions

1. **Settings sync between service and bridge.** Bridge reads settings via HTTP poll (5s cache in Task A4). Settings PUT from dashboard takes effect within 5s on bridge side. Acceptable lag, but flag as known cache behavior.
2. **What if the operator flips `managed_via_wrapper` off MID-RUN?** Wrapper PTY keeps running; main bridge resumes claiming managed dispatches. The wrapper's child bridge stops getting claims. Existing dispatches finish normally. No coordination needed.
3. **Migration from existing `HermesManagedGatewaySession` opt-in.** Operators already using `AIFY_HERMES_MANAGED_USE_GATEWAY=1` can flip to `managed_via_wrapper=["hermes"]` and get the same delivery semantics plus PTY visibility. Cleanup that env var in Phase E.
