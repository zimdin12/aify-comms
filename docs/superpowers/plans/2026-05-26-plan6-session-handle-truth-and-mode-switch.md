# Plan 6 — Session-handle truth + manual mode switch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task has a TDD-style step list with checkboxes (`- [ ]`) for tracking.

**Goal:** (1) Make the server's stored session handle always reflect the runtime's actual current session — bridge prefers `discoverSessionId()` over env-read at heartbeat and at register. (2) Wrappers re-export the truthful session id at session start. (3) Operators can flip an agent between resident and managed from the dashboard.

**Architecture:** Three independent sections (A/B/C) + a holistic finish. Section A is the smallest and unblocks the operator's live ping-pong failure. Section B is per-runtime wrapper edits. Section C is a new endpoint plus dashboard UI in two places.

**Tech stack:** Python (FastAPI + sqlite + pytest), Node.js ESM (MCP + node:test), bash (install.sh + wrappers), vanilla JS + CSS (new_dashboard/app.js).

---

## Section A — Bridge: prefer `discoverSessionId()` over env

Goal: bridge corrects stale handles automatically.

### Task A1: Reverse heartbeat priority in `session-handle-heartbeat.js`

**Files:**
- Modify: `mcp/stdio/session-handle-heartbeat.js`
- Test: `mcp/stdio/tests/session-handle-heartbeat-priority.test.js` (new)

- [ ] **Step 1: Write failing test**

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { startSessionHandleHeartbeat } from "../session-handle-heartbeat.js";

test("heartbeat prefers discoverSessionId over getCurrentSessionId when both return values", async () => {
  const calls = [];
  const adapter = {
    getCurrentSessionId() { calls.push("env"); return "stale-env-id"; },
    async discoverSessionId() { calls.push("discover"); return "fresh-discover-id"; },
  };
  const posted = [];
  const stop = startSessionHandleHeartbeat({
    agentId: "test-agent",
    adapter,
    postFn: async (agentId, handle) => { posted.push({ agentId, handle }); },
    intervalMs: 50,
  });
  await new Promise((r) => setTimeout(r, 80));
  stop();
  assert.deepEqual(
    posted.map((p) => p.handle),
    ["fresh-discover-id"],
    "Plan 6 A1: discover result must win over env"
  );
});

test("heartbeat falls back to env when discoverSessionId returns null", async () => {
  const adapter = {
    getCurrentSessionId() { return "env-only-id"; },
    async discoverSessionId() { return null; },
  };
  const posted = [];
  const stop = startSessionHandleHeartbeat({
    agentId: "test-agent",
    adapter,
    postFn: async (agentId, handle) => { posted.push({ agentId, handle }); },
    intervalMs: 50,
  });
  await new Promise((r) => setTimeout(r, 80));
  stop();
  assert.deepEqual(posted.map((p) => p.handle), ["env-only-id"]);
});

test("heartbeat falls back to env when discoverSessionId throws", async () => {
  const adapter = {
    getCurrentSessionId() { return "env-only-id"; },
    async discoverSessionId() { throw new Error("gateway unreachable"); },
  };
  const posted = [];
  const stop = startSessionHandleHeartbeat({
    agentId: "test-agent",
    adapter,
    postFn: async (agentId, handle) => { posted.push({ agentId, handle }); },
    intervalMs: 50,
  });
  await new Promise((r) => setTimeout(r, 80));
  stop();
  assert.deepEqual(posted.map((p) => p.handle), ["env-only-id"]);
});
```

- [ ] **Step 2: Run test, expect failure**

```bash
node --test mcp/stdio/tests/session-handle-heartbeat-priority.test.js
```

Expected: 1st test fails (currently env wins).

- [ ] **Step 3: Implement — reverse priority in `session-handle-heartbeat.js`**

Replace the `tick()` body with:

```javascript
const tick = async () => {
  if (stopped) return;
  let current = null;
  // Plan 6 A1 (2026-05-26): runtime discovery is authoritative.
  // env-read is fallback — operators leave stale env vars in their
  // shells (HERMES_SESSION_ID etc.), and the prior fallback order
  // pinned those stale values in the server's stored handle indefinitely.
  // Discover-first is self-correcting; env-fallback preserves the
  // legacy behavior when the runtime can't be probed.
  if (typeof adapter.discoverSessionId === "function") {
    try { current = await adapter.discoverSessionId(); } catch { /* swallow; fall through */ }
  }
  if (!current) {
    try { current = adapter.getCurrentSessionId(); } catch { /* swallow */ }
  }
  if (!current || current === lastHandle) return;
  try { await postFn(agentId, current); lastHandle = current; } catch { /* swallow; retry next tick */ }
};
```

- [ ] **Step 4: Run test to verify pass**

Expected: 3/3 PASS.

- [ ] **Step 5: Run full JS suite to catch regressions**

```bash
for f in mcp/stdio/tests/session-handle-heartbeat*.test.js mcp/stdio/tests/*-heartbeat*.test.js; do node --test "$f"; done
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add mcp/stdio/session-handle-heartbeat.js mcp/stdio/tests/session-handle-heartbeat-priority.test.js
git commit -m "fix(bridge): heartbeat prefers discoverSessionId over env (Plan 6 A1)

Operators frequently have stale HERMES_SESSION_ID / CODEX_THREAD_ID
in their shells from prior runtime sessions. The old fallback order
(env first, discover only when env was null) pinned those stale
values in the server's stored handle indefinitely, causing
prompt.submit to fail with 'session not found' for both resident
and managed delivery (observed 2026-05-26 — sc-hermes-test-1
ping-pong; hermes-test pseudo-terminal).

Reverses priority: discover authoritative, env fallback. Strictly
additive — if discover throws or returns null, behavior is identical
to before."
```

---

### Task A2: Apply same reversal at initial register in `server.js`

**Files:**
- Modify: `mcp/stdio/server.js` around line 873-883 (the auto-register path)
- Test: `mcp/stdio/tests/auto-register-discover-priority.test.js` (new)

- [ ] **Step 1: Recon — read `server.js:830-960` end-to-end before writing the test.** The auto-register function builds `sessionHandle` from `process.env.AIFY_SESSION_HANDLE || defaultSessionHandleForRuntime(runtime)`. We need to add a `discoverSessionId()` call (via `adapterFor(runtime)`) BEFORE that fallback.

- [ ] **Step 2: Write failing test**

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
// Import the helper function (export it from server.js as part of A2 if not already exported)
import { computeInitialSessionHandle } from "../server.js";  // expose for testability

test("auto-register prefers discoverSessionId over env-default", async () => {
  const adapter = {
    getCurrentSessionId() { return "stale-env-id"; },
    async discoverSessionId() { return "fresh-discover-id"; },
  };
  const result = await computeInitialSessionHandle({ adapter, envHandle: "stale-env-id" });
  assert.equal(result, "fresh-discover-id");
});

test("auto-register falls back to env when discover returns null", async () => {
  const adapter = {
    getCurrentSessionId() { return "env-fallback-id"; },
    async discoverSessionId() { return null; },
  };
  const result = await computeInitialSessionHandle({ adapter, envHandle: "env-fallback-id" });
  assert.equal(result, "env-fallback-id");
});
```

- [ ] **Step 3: Run test, expect failure (function doesn't exist yet)**

```bash
node --test mcp/stdio/tests/auto-register-discover-priority.test.js
```

- [ ] **Step 4: Implement — extract + export the helper**

Add to `mcp/stdio/server.js`:

```javascript
export async function computeInitialSessionHandle({ adapter, envHandle }) {
  // Plan 6 A2: discover authoritative, env fallback. See A1 rationale.
  if (adapter && typeof adapter.discoverSessionId === "function") {
    try {
      const discovered = await adapter.discoverSessionId();
      if (discovered) return String(discovered).trim();
    } catch { /* swallow; fall through to env */ }
  }
  return String(envHandle || "").trim();
}
```

Wire it into the auto-register flow:

```javascript
// At line ~873:
const envHandle = String(process.env.AIFY_SESSION_HANDLE || defaultSessionHandleForRuntime(runtime) || "").trim();
const initialHandle = await computeInitialSessionHandle({ adapter: __runtimeAdapter, envHandle });
```

- [ ] **Step 5: Run test to verify pass**

- [ ] **Step 6: Run JS suite to catch regressions**

- [ ] **Step 7: Commit**

```bash
git add mcp/stdio/server.js mcp/stdio/tests/auto-register-discover-priority.test.js
git commit -m "fix(bridge): auto-register prefers discoverSessionId over env (Plan 6 A2)

Extends Plan 6 A1's heartbeat fix to the initial register path. Without
this, the FIRST registration of an agent (before the 60s heartbeat
fires) still gets the stale env value baked into agent.session_handle
and runtime_state.sessionId. Subsequent dispatches in that 60s window
fail at prompt.submit time."
```

---

## Section B — Wrappers: rediscover and re-export at session start

Goal: env vars seen by the inner bridge reflect the runtime's actual session, not whatever stale value the operator's shell exported.

### Task B1: hermes-aify queries gateway for real session id

**Files:**
- Modify: `install.sh` (the hermes-aify wrapper heredoc, starting ~line 750)
- Test: `service/tests/test_install_hermes_session_rediscover.py` (new — bash-level smoke test invoking install.sh in dry-run mode and verifying the new code path is emitted into the wrapper)

- [ ] **Step 1: Recon**

Read the hermes-aify heredoc fully. Find the spot **after** `AIFY_HERMES_TOKEN=$(curl -s "$AIFY_HERMES_DASHBOARD_URL/" | ...)` succeeds and **before** `exec "$HERMES_RUNTIME_COMMAND" chat --tui`.

The gateway responds to `session.most_recent` JSON-RPC over WS. We need a one-shot WS round-trip. Reuse pattern from the Python hermes adapter at `service/runtimes/hermes.py` — `websockets.connect(url)` + send `{"jsonrpc": "2.0", "id": 1, "method": "session.most_recent"}` + read response.

For bash, the cleanest path is `node -e '...'` (node is already required by the install, and the gateway protocol is trivial — single send + recv). Add a `rediscover_hermes_session_id()` helper inside the wrapper heredoc:

```bash
rediscover_hermes_session_id() {
  local gateway_url="$1"
  local token="$2"
  local result
  result="$(AIFY_GATEWAY_URL="$gateway_url" AIFY_GATEWAY_TOKEN="$token" node -e '
    const WebSocket = (function(){
      try { return require("ws"); } catch { return require("/c/Docker/aify-comms/mcp/stdio/node_modules/ws"); }
    })();
    const url = process.env.AIFY_GATEWAY_URL;
    const ws = new WebSocket(url, { perMessageDeflate: false });
    let done = false;
    const timeout = setTimeout(() => { if (!done) { ws.terminate(); process.exit(0); } }, 3000);
    ws.on("open", () => ws.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "session.most_recent" })));
    ws.on("message", (raw) => {
      try {
        const msg = JSON.parse(String(raw));
        if (msg.id === 1 && msg.result && msg.result.sessionId) {
          done = true;
          clearTimeout(timeout);
          process.stdout.write(String(msg.result.sessionId));
          ws.close();
          process.exit(0);
        }
      } catch {}
    });
    ws.on("error", () => { clearTimeout(timeout); process.exit(0); });
  ' 2>/dev/null)"
  echo "$result"
}
```

- [ ] **Step 2: Write failing test**

```python
# service/tests/test_install_hermes_session_rediscover.py
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

def test_hermes_wrapper_includes_rediscover_helper():
    """The hermes-aify wrapper heredoc in install.sh must include a
    rediscover_hermes_session_id helper that queries the gateway for the
    real session id after the dashboard probe succeeds."""
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    assert "rediscover_hermes_session_id" in text, (
        "Plan 6 B1: install.sh hermes branch must define rediscover_hermes_session_id"
    )
    assert "session.most_recent" in text, (
        "Plan 6 B1: hermes wrapper must call gateway's session.most_recent RPC"
    )

def test_hermes_wrapper_overwrites_session_env_after_rediscover():
    """After rediscover returns a non-empty id, the wrapper must overwrite
    HERMES_SESSION_ID and AIFY_SESSION_HANDLE before exec'ing hermes."""
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    # Pattern: `export HERMES_SESSION_ID="..."` appearing AFTER the rediscover call
    rediscover_idx = text.find("rediscover_hermes_session_id")
    assert rediscover_idx > 0
    # Find the export after that point
    later = text[rediscover_idx:]
    assert 'export HERMES_SESSION_ID="' in later, (
        "Plan 6 B1: wrapper must export HERMES_SESSION_ID from rediscover result"
    )
```

- [ ] **Step 3: Run test, expect failure**

```bash
python -m pytest service/tests/test_install_hermes_session_rediscover.py -v
```

- [ ] **Step 4: Implement**

Modify the hermes-aify heredoc in install.sh: inside the `if [ "${AIFY_HERMES_SKIP_GATEWAY:-0}" != "1" ]; then` block, AFTER the existing `AIFY_HERMES_TOKEN` capture, ADD:

```bash
# Plan 6 B1 (2026-05-26): rediscover the real hermes session id from the
# gateway's session.most_recent RPC. Overwrites HERMES_SESSION_ID /
# AIFY_SESSION_HANDLE so the inner aify-comms MCP bridge registers with
# the truthful id (not whatever stale value the operator's shell
# inherited). Failures here are non-fatal — the bridge's discover-first
# heartbeat (Plan 6 A1) will correct any drift within 60s.
HERMES_REDISCOVERED_SESSION_ID="$(rediscover_hermes_session_id "$AIFY_HERMES_GATEWAY" "$AIFY_HERMES_TOKEN")"
if [ -n "$HERMES_REDISCOVERED_SESSION_ID" ]; then
  if [ "$HERMES_REDISCOVERED_SESSION_ID" != "$HERMES_SESSION_HANDLE" ]; then
    echo "[hermes-aify] session id changed: '$HERMES_SESSION_HANDLE' -> '$HERMES_REDISCOVERED_SESSION_ID' (rediscovered from gateway)" >&2
  fi
  export HERMES_SESSION_ID="$HERMES_REDISCOVERED_SESSION_ID"
  export AIFY_SESSION_HANDLE="$HERMES_REDISCOVERED_SESSION_ID"
  HERMES_SESSION_HANDLE="$HERMES_REDISCOVERED_SESSION_ID"
fi
```

And add the `rediscover_hermes_session_id()` helper function near the top of the wrapper heredoc.

- [ ] **Step 5: Run test to verify pass**

```bash
python -m pytest service/tests/test_install_hermes_session_rediscover.py -v
```

- [ ] **Step 6: Commit**

```bash
git add install.sh service/tests/test_install_hermes_session_rediscover.py
git commit -m "feat(wrapper-hermes): rediscover real session id from gateway (Plan 6 B1)

After dashboard probe succeeds, queries gateway's session.most_recent
JSON-RPC to get the actual current hermes session id. Overwrites
HERMES_SESSION_ID / AIFY_SESSION_HANDLE so the inner bridge registers
with the truthful id, not a stale value inherited from the operator's
parent shell.

Failures non-fatal — bridge heartbeat (Plan 6 A1) is the safety net."
```

---

### Task B2: codex-aify queries app-server for thread id

**Files:**
- Modify: `install.sh` (codex-aify wrapper heredoc)
- Test: `service/tests/test_install_codex_session_rediscover.py`

- [ ] **Step 1: Recon — confirm codex app-server's introspection endpoint**

The wrapper already spawns `codex app-server --listen ws://...`. Query the WS for the most-recent thread id. Need to verify codex app-server's actual RPC. If no introspection RPC exists, fall back to filesystem scan (same as Python adapter's `discover_session_id()` in `service/runtimes/codex.py`).

Either way, the wrapper emits `CODEX_THREAD_ID` if it can determine one.

- [ ] **Step 2: Write failing test pinning expected behavior**

```python
def test_codex_wrapper_includes_rediscover_helper():
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    assert "rediscover_codex_thread_id" in text or "rediscover_codex_session_id" in text, (
        "Plan 6 B2: codex-aify must rediscover thread id after app-server start"
    )

def test_codex_wrapper_overwrites_thread_env_after_rediscover():
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    # Pattern: an export CODEX_THREAD_ID after the rediscover call
    needle = "rediscover_codex_"
    idx = text.find(needle)
    assert idx > 0
    later = text[idx:]
    assert 'export CODEX_THREAD_ID' in later or 'export AIFY_SESSION_HANDLE' in later
```

- [ ] **Step 3: Run test, expect failure.**

- [ ] **Step 4: Implement**

Add `rediscover_codex_thread_id()` helper in install.sh's codex-aify heredoc. Use either the app-server WS or the filesystem scan (mimic Python `discover_session_id()` — `find ~/.codex/sessions -type f -name "rollout-*.jsonl" -printf "%T@ %p\n" | sort -nr | head -1 | sed 's/.*rollout-[^-]*-[^-]*-[^-]*-[^-]*-[^-]*-\([^.]*\)\.jsonl/\1/'`).

After app-server is reachable:

```bash
CODEX_REDISCOVERED_THREAD_ID="$(rediscover_codex_thread_id)"
if [ -n "$CODEX_REDISCOVERED_THREAD_ID" ] && [ "$CODEX_REDISCOVERED_THREAD_ID" != "$CODEX_RESUME_HANDLE" ]; then
  echo "[codex-aify] thread id rediscovered: '$CODEX_REDISCOVERED_THREAD_ID'" >&2
  export CODEX_THREAD_ID="$CODEX_REDISCOVERED_THREAD_ID"
  export AIFY_SESSION_HANDLE="$CODEX_REDISCOVERED_THREAD_ID"
fi
```

- [ ] **Step 5: Run test to verify pass + commit.**

---

### Task B3: pi-aify queries pi-session-state for session id

**Files:**
- Modify: `install.sh` (pi-aify wrapper heredoc)
- Test: `service/tests/test_install_pi_session_rediscover.py`

- [ ] **Step 1: Recon** — the pi-aify wrapper at line 110 already calls `curl -sS "$AIFY_WATCHDOG_URL"` against `/api/v1/agents/<agent>/pi-session-state`. The response shape (per Plan 4) includes `sessionId`. Reuse the watchdog block to extract the session id.

- [ ] **Step 2-6: Same TDD shape as B1/B2.**

After the watchdog completes (successful or not), parse the response for `"sessionId":"<id>"` (a simple grep is enough — no need for jq dependency in bash). Export `PI_SESSION_ID` and `AIFY_SESSION_HANDLE`.

- [ ] **Step 7: Commit**

```bash
git commit -m "feat(wrapper-pi): rediscover real session id from pi-session-state (Plan 6 B3)"
```

---

### Task B4: claude-aify validates env handle against on-disk transcript

**Files:**
- Modify: `install.sh` (claude-aify wrapper heredoc)
- Test: `service/tests/test_install_claude_session_validate.py`

- [ ] **Step 1: Recon** — claude transcripts live at `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`. If `CLAUDE_SESSION_ID` is set but no matching file exists, the env value is stale; unset before exec'ing claude.

- [ ] **Step 2-6: TDD shape.**

Add `validate_claude_session_id()` helper:

```bash
validate_claude_session_id() {
  local id="$1"
  local cwd_root="$2"
  [ -z "$id" ] && return 1
  local encoded
  encoded="$(printf '%s' "$cwd_root" | sed 's|/|-|g; s|\\|-|g; s|:||g')"
  local jsonl="$HOME/.claude/projects/${encoded}/${id}.jsonl"
  [ -f "$jsonl" ]
}

if [ -n "${CLAUDE_RESUME_ID:-}" ] && ! validate_claude_session_id "$CLAUDE_RESUME_ID" "$PWD"; then
  echo "[claude-aify] CLAUDE_SESSION_ID '$CLAUDE_RESUME_ID' has no transcript at ~/.claude/projects/...; clearing" >&2
  unset CLAUDE_RESUME_ID
  unset CLAUDE_SESSION_ID
fi
```

- [ ] **Step 7: Commit**

---

### Task B5: Update install.*.md docs

**Files:**
- Modify: `install.hermes.md`, `install.codex.md`, `install.pi.md`, `install.claude.md`

- [ ] **Step 1: Add a "Session rediscover" subsection to each.** One paragraph each: where it queries, what env var it overwrites, what happens on failure.

- [ ] **Step 2: Commit**

```bash
git commit -m "docs(install): document Plan 6 B per-wrapper session rediscover"
```

---

## Section C — Manual resident/managed mode switching UI

Goal: operator can flip an agent's `session_mode` from the dashboard.

### Task C1: `PATCH /agents/{id}/session-mode` endpoint

**Files:**
- Modify: `service/routers/api_v2.py` (add new endpoint near other `/agents/{id}/...` PATCH endpoints)
- Test: `service/tests/test_agent_session_mode_switch.py`

- [ ] **Step 1: Failing test**

```python
import pytest
from fastapi.testclient import TestClient
# ... standard setup similar to test_dispatch_channel_claim.py

def test_switch_resident_to_managed(client, agent_factory):
    agent = agent_factory(runtime="hermes", session_mode="resident")
    res = client.patch(f"/api/v1/agents/{agent['id']}/session-mode", json={"mode": "managed"})
    assert res.status_code == 200
    # Verify the change persisted
    res2 = client.get(f"/api/v1/agents/{agent['id']}")
    assert res2.json()["agent"]["sessionMode"] == "managed"

def test_switch_managed_to_resident_for_hermes_without_gateway_returns_409(client, agent_factory):
    agent = agent_factory(runtime="hermes", session_mode="managed", runtime_config={})
    res = client.patch(f"/api/v1/agents/{agent['id']}/session-mode", json={"mode": "resident"})
    assert res.status_code == 409
    assert "gateway" in res.json().get("detail", "").lower()

def test_switch_blocked_by_active_run(client, agent_factory, run_factory):
    agent = agent_factory(runtime="codex", session_mode="resident")
    run_factory(target_agent=agent["id"], status="started")
    res = client.patch(f"/api/v1/agents/{agent['id']}/session-mode", json={"mode": "managed"})
    assert res.status_code == 409
    assert "active" in res.json().get("detail", "").lower()
```

- [ ] **Step 2: Run test, expect 405/404 (endpoint missing).**

- [ ] **Step 3: Implement**

Add to `api_v2.py`:

```python
class SessionModeSwitchRequest(BaseModel):
    mode: str
    force: bool = False


@router.patch("/agents/{agent_id}/session-mode")
async def switch_agent_session_mode(agent_id: str, req: SessionModeSwitchRequest, request: Request):
    """Plan 6 C1 (2026-05-26): operator-driven resident/managed mode flip.
    Today the wrapper auto-detects via TTY; this endpoint lets the operator
    override the agent's session_mode regardless of how the wrapper was
    launched. Returns 409 when the switch is unsafe (active run, missing
    gateway for hermes resident, etc.)."""
    new_mode = _normalize_session_mode(req.mode)
    if new_mode not in ("resident", "managed"):
        raise HTTPException(400, "mode must be 'resident' or 'managed'")
    db = await get_db()
    try:
        agent = await (await db.execute("SELECT * FROM agents WHERE id=?", (agent_id,))).fetchone()
        if not agent:
            raise HTTPException(404, "agent not found")
        current_mode = _normalize_session_mode(agent["session_mode"] or "")
        if current_mode == new_mode:
            return {"ok": True, "agentId": agent_id, "mode": new_mode, "changed": False}

        # Block if an active run is in flight.
        active = await _get_dispatch_state_for_agent(db, agent_id)
        if active.get("activeRun") and not req.force:
            raise HTTPException(409, f"Agent has an active run (id={active['activeRun']['runId']}); wait for it to finish before switching mode")

        # Hermes resident requires gatewayUrl.
        if new_mode == "resident" and _normalize_runtime(agent["runtime"]) == "hermes" and not req.force:
            rc = _json_loads_or(agent["runtime_config"], {})
            if not rc.get("gatewayUrl"):
                raise HTTPException(409, "Hermes resident requires runtimeConfig.gatewayUrl. Re-launch hermes-aify (which exports AIFY_HERMES_GATEWAY_URL) and re-register, or pass force=true.")

        await db.execute("UPDATE agents SET session_mode=? WHERE id=?", (new_mode, agent_id))
        await _append_dispatch_event(db, "", f"mode_switch_{current_mode}_to_{new_mode}", agent_id, extra={"by": "dashboard"})
        await db.commit()
        return {"ok": True, "agentId": agent_id, "mode": new_mode, "changed": True, "previousMode": current_mode}
    finally:
        await db.close()
```

- [ ] **Step 4-5: Run tests + commit.**

---

### Task C2: State transition side effects (resident → managed eager-spawn / managed → resident PTY release)

**Files:**
- Modify: `service/routers/api_v2.py` (within the endpoint added in C1)
- Test: `service/tests/test_agent_session_mode_switch.py` (extend)

- [ ] **Step 1: Failing test for resident → managed eager-spawn**

Asserts that after switching a wrapper-backed runtime from resident → managed, a `terminal_sessions` row appears (eager-spawn fired).

- [ ] **Step 2: Implement**

After `UPDATE agents SET session_mode = ?`, if new_mode == "managed" and the runtime is wrapper-backed, call `_ensure_managed_pty_for_dispatch(...)` to seed the eager-spawn.

For managed → resident: call `_stop_managed_pty(...)` if a terminal exists.

- [ ] **Step 3-4: Run test + commit.**

---

### Task C3: `manual_session_mode` setting

**Files:**
- Modify: `service/routers/api_v2.py` (`DEFAULT_SETTINGS`)
- Test: `service/tests/test_settings_manual_mode.py`

- [ ] **Step 1: Add to `DEFAULT_SETTINGS`:**

```python
"manual_session_mode": False,
```

- [ ] **Step 2: Verify settings PUT round-trip via test.**

- [ ] **Step 3: Commit.**

---

### Task C4: Dashboard Details panel chip

**Files:**
- Modify: `service/new_dashboard/app.js` (find the Details-panel rendering function)
- Modify: `service/new_dashboard/styles.css` (new `.mode-switch-chip` class)

- [ ] **Step 1: Recon** — find where the Details panel renders `sessionMode`. Add a sibling chip with `[Switch to managed]` / `[Switch to resident]` text.

- [ ] **Step 2: Implement**

```javascript
function renderModeSwitch(agent) {
  if (!state.settings?.manual_session_mode) return "";
  const current = String(agent.sessionMode || "").toLowerCase();
  if (current !== "resident" && current !== "managed") return "";
  const target = current === "resident" ? "managed" : "resident";
  return `<button class="mode-switch-chip" data-agent-id="${esc(agent.id)}" data-target="${target}">Switch to ${target}</button>`;
}
```

Wire click handler:

```javascript
document.addEventListener("click", async (ev) => {
  const btn = ev.target.closest(".mode-switch-chip");
  if (!btn) return;
  const agentId = btn.dataset.agentId;
  const target = btn.dataset.target;
  try {
    const res = await fetch(`/api/v1/agents/${encodeURIComponent(agentId)}/session-mode`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: target }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      toast(`Mode switch failed: ${body.detail || res.statusText}`);
      return;
    }
    toast(`Agent "${agentId}" switched to ${target}`);
    refreshAgents();
  } catch (e) {
    toast(`Mode switch error: ${e.message}`);
  }
});
```

- [ ] **Step 3: Commit.**

---

### Task C5: Dashboard Sessions panel action menu item

**Files:**
- Modify: `service/new_dashboard/app.js` (find the Sessions row rendering)

- [ ] **Step 1: Add the same mode-switch button to the per-session row's action menu.** Honors the same `manual_session_mode` setting.

- [ ] **Step 2: Commit.**

---

### Task C6: Settings UI toggle for `manual_session_mode`

**Files:**
- Modify: `service/new_dashboard/app.js` (settings panel rendering)
- Test: existing settings round-trip test in `test_settings_manual_mode.py`

- [ ] **Step 1: Add a checkbox in the settings panel.**
- [ ] **Step 2: When `false`, the mode-switch chips/buttons are hidden (already gated by `renderModeSwitch`).**
- [ ] **Step 3: Commit.**

---

## Section D — Holistic review + finish

### Task D1: Run full test suites

- [ ] `python -m pytest service/tests/ --ignore=service/tests/test_new_dashboard_app.py -x`
- [ ] All JS tests: `for f in mcp/stdio/tests/*.test.js; do node --test "$f"; done`

### Task D2: Code-reviewer subagent

Dispatch over `git diff <base>..HEAD`. Address Critical and Important; note Minor for follow-up.

### Task D3: Update DECISIONS.md

Three entries — one per section.

### Task D4: Update `aify-comms-debug` skill

Add detection recipes:
- "stale session handle on resident hermes/codex/pi" — symptom: `prompt.submit failed: session not found` for hermes, "session not found" for codex / pi.
- "manual session-mode switch unavailable" — settings has `manual_session_mode=false`.

### Task D5: Finishing skill

Invoke `superpowers:finishing-a-development-branch`. Present 4 options (merge / PR / keep / discard). Recommend keep for live testing.

---

## Self-review checklist

- [ ] Every section has TDD-driven tasks.
- [ ] No placeholders.
- [ ] Section A is the smallest and ship-able independently if Sections B/C run long.
- [ ] No opencode test execution (operator memory: feedback-opencode-skip).
- [ ] 500-line rule: install.sh growth is unavoidable but still tracked; api_v2.py adds ~80 lines (note for the inevitable future split).
