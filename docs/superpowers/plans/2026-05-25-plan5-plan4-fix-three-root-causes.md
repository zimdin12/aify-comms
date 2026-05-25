# Plan 5 — Fix Plan 4's three root causes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task has a TDD-style step list with checkboxes (`- [ ]`) for tracking.

**Goal:** Fix the three confirmed root causes from Phase 1 of systematic debugging so managed wrapper-backed dispatch works for codex/hermes/pi and `online` status stops lying.

**Architecture:** Three independent sections (A/B/C), one per root cause. Section B is the meat — symmetric channel-claim across runtimes. Final section D is holistic review + docs.

**Tech stack:** Python (FastAPI + sqlite + pytest), Node.js ESM (MCP + node:test), bash (install.sh + wrappers).

---

## Section A — Hermes web_dist pre-build

Goal: make `hermes-aify` work on fresh hermes installs.

### Task A1: Detect and pre-build hermes web_dist in install.sh

**Files:**
- Modify: `install.sh` (hermes branch — search for `--client hermes` handling)
- Test: `install_test/test_install_hermes.sh` (if exists; otherwise smoke verify)

- [ ] **Step 1: Recon**

Read `install.sh` to find the hermes-install branch (search for `hermes` or `hermes-aify`). Identify where the wrapper is written/copied. Note the hermes install root probe — operator's hermes install is at `C:\Users\Administrator\AppData\Local\hermes\hermes-agent\hermes_cli\web_dist`.

- [ ] **Step 2: Write failing smoke test**

Create `service/tests/test_install_hermes_prebuild.py` (Python because that's the test runner the repo uses). Stub: assert that the hermes-aify install path runs an idempotent `npm install + npm run build` if `web_dist/` is absent, and skips if present.

```python
import subprocess
import tempfile
from pathlib import Path

def test_install_hermes_prebuilds_web_dist_if_missing(tmp_path, monkeypatch):
    fake_hermes_root = tmp_path / "hermes-agent" / "hermes_cli"
    fake_hermes_root.mkdir(parents=True)
    fake_web = tmp_path / "hermes-agent" / "web"
    fake_web.mkdir(parents=True)
    (fake_web / "package.json").write_text('{"scripts":{"build":"echo built > ../hermes_cli/web_dist/index.html"}}')

    monkeypatch.setenv("AIFY_HERMES_INSTALL_ROOT", str(tmp_path / "hermes-agent"))
    result = subprocess.run(
        ["bash", "install.sh", "--client", "hermes", "--prebuild-dry-run", "http://127.0.0.1:8800"],
        capture_output=True, text=True,
    )
    assert "prebuilding hermes web_dist" in (result.stdout + result.stderr).lower()
```

(Adapt the assert to whatever string the implementer chooses for the install.sh log line.)

- [ ] **Step 3: Run test, expect failure**

```bash
cd C:/Docker/aify-comms && python -m pytest service/tests/test_install_hermes_prebuild.py -v
```

Expected: FAIL (the install.sh hermes branch doesn't yet detect missing web_dist).

- [ ] **Step 4: Implement**

Add to `install.sh` hermes branch — pseudocode (implementer adapts to actual file structure):

```bash
prebuild_hermes_web_dist() {
  local hermes_install_root="${AIFY_HERMES_INSTALL_ROOT:-}"
  if [ -z "$hermes_install_root" ]; then
    # Auto-detect via `hermes config path` (which reports the install dir)
    hermes_install_root="$(hermes config path 2>/dev/null | head -1 | sed 's|/hermes_cli/.*||')"
  fi
  if [ -z "$hermes_install_root" ] || [ ! -d "$hermes_install_root" ]; then
    echo "[install.sh] hermes install root not found; skipping web_dist prebuild" >&2
    return 0
  fi
  local web_dist="$hermes_install_root/hermes_cli/web_dist"
  local web_src="$hermes_install_root/web"
  if [ -f "$web_dist/index.html" ]; then
    echo "[install.sh] hermes web_dist already present at $web_dist" >&2
    return 0
  fi
  if [ ! -d "$web_src" ]; then
    echo "[install.sh] hermes web source not found at $web_src; cannot prebuild" >&2
    return 0
  fi
  echo "[install.sh] prebuilding hermes web_dist (one-time; runs npm install + npm run build)" >&2
  (cd "$web_src" && npm install && npm run build) || {
    echo "[install.sh] hermes web_dist prebuild failed — hermes-aify dashboard probe will continue to fall back. Re-run install.sh after fixing." >&2
    return 1
  }
  echo "[install.sh] hermes web_dist prebuilt at $web_dist" >&2
}
```

Wire `prebuild_hermes_web_dist` into the hermes branch of install.sh (call it after wrapper install). Add a `--prebuild-dry-run` flag that logs intent but doesn't actually invoke npm — useful for the test.

- [ ] **Step 5: Run test to verify pass**

```bash
python -m pytest service/tests/test_install_hermes_prebuild.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add install.sh service/tests/test_install_hermes_prebuild.py
git commit -m "feat(install): prebuild hermes web_dist on hermes-install branch

Detects missing ~/AppData/Local/hermes/hermes-agent/hermes_cli/web_dist
during install.sh --client hermes and runs npm install + npm run build
once. Without this, hermes-aify's dashboard probe fails silently
(observed 2026-05-25 — operator's hermes install lacked web_dist,
wrapper fell through to plain hermes, AIFY_HERMES_GATEWAY_URL never
exported)."
```

---

### Task A2: Make hermes-aify fallback warning visible

**Files:**
- Modify: `install.sh` (the heredoc that writes the hermes-aify wrapper)

- [ ] **Step 1: Locate the heredoc**

Find the `cat > "$WRAPPER" <<HERMES_AIFY` block in install.sh.

- [ ] **Step 2: Add a banner to the fallback path**

Right before each `exec "$HERMES_RUNTIME_COMMAND" "${HERMES_ARGS[@]}"` fallthrough in the wrapper (currently 4 fallthroughs: line 137 port allocation, 164 wait_for_http, 172 token capture, 189 SKIP_GATEWAY=1), prepend:

```bash
echo "[hermes-aify] WARNING: AIFY_HERMES_GATEWAY_URL was NOT exported to this hermes session." >&2
echo "[hermes-aify]   Reason: ${AIFY_HERMES_FALLBACK_REASON:-dashboard probe failed}" >&2
echo "[hermes-aify]   Log: $AIFY_HERMES_DASHBOARD_LOG" >&2
echo "[hermes-aify]   Effect: comms wake/dispatch to this agent will report 'hermes-missing-handle'." >&2
echo "[hermes-aify]   Fix: re-run install.sh --client hermes to prebuild hermes web_dist." >&2
```

Set `AIFY_HERMES_FALLBACK_REASON` distinctly at each fallthrough site (`port_alloc_failed` / `dashboard_unreachable` / `token_capture_failed` / `gateway_disabled`).

- [ ] **Step 3: No automated test (operator-facing log)**

Smoke verify by running a hermes-aify in a sub-shell with `AIFY_HERMES_SKIP_GATEWAY=1` and confirming the banner appears.

- [ ] **Step 4: Commit**

```bash
git add install.sh
git commit -m "feat(wrapper-hermes): make AIFY_HERMES_GATEWAY_URL fallback visible

When hermes-aify falls back to plain hermes (dashboard probe failure or
SKIP_GATEWAY=1), surface a WARNING block explaining why the gateway URL
is unset and how to fix it. Without this, the fallback is silent and
operator sees 'hermes-missing-handle' wake mode with no obvious cause
(observed 2026-05-25)."
```

---

### Task A3: Update install.hermes.md

**Files:**
- Modify: `install.hermes.md`

- [ ] **Step 1: Add a section on web_dist prebuild**

Document the new `prebuild_hermes_web_dist` step + the operator-visible warning when fallback fires.

- [ ] **Step 2: Commit**

```bash
git add install.hermes.md
git commit -m "docs(install-hermes): note web_dist prebuild + fallback warning"
```

---

## Section B — Symmetric channel-claim for codex/hermes/pi

Goal: wrapper-backed managed dispatches for codex/hermes/pi get claimed and delivered, same pattern as claude-code.

### Task B1: Failing test — `supportedExecutionModes` returns `'channel'` for wrapper-backed managed codex/hermes/pi

**Files:**
- Create: `mcp/stdio/test/test-dispatch-execution-channel-claim.js`
- Test command: `node --test mcp/stdio/test/test-dispatch-execution-channel-claim.js`

- [ ] **Step 1: Write failing test**

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { supportedExecutionModes } from "../dispatch-execution.js";

const WRAPPER_BACKED = new Set(["codex", "hermes", "pi"]);

test("codex managed + wrapper-backed pushes 'channel'", () => {
  const modes = supportedExecutionModes(
    { runtime: "codex", sessionMode: "managed", capabilities: ["native-managed-run"] },
    { managedViaWrapperRuntimes: WRAPPER_BACKED },
  );
  assert.deepEqual(modes, ["channel"]);
});

test("hermes managed + wrapper-backed pushes 'channel'", () => {
  const modes = supportedExecutionModes(
    { runtime: "hermes", sessionMode: "managed", capabilities: ["native-managed-run"] },
    { managedViaWrapperRuntimes: WRAPPER_BACKED },
  );
  assert.deepEqual(modes, ["channel"]);
});

test("pi managed + wrapper-backed pushes 'channel'", () => {
  const modes = supportedExecutionModes(
    { runtime: "pi", sessionMode: "managed", capabilities: ["native-managed-run"] },
    { managedViaWrapperRuntimes: WRAPPER_BACKED },
  );
  assert.deepEqual(modes, ["channel"]);
});

test("codex managed + NOT wrapper-backed still pushes 'managed' (legacy path)", () => {
  const modes = supportedExecutionModes(
    { runtime: "codex", sessionMode: "managed", capabilities: ["native-managed-run"] },
    { managedViaWrapperRuntimes: new Set() },
  );
  assert.deepEqual(modes, ["managed"]);
});

test("resident codex still returns 'resident' regardless of wrapper-backed flag", () => {
  const modes = supportedExecutionModes(
    { runtime: "codex", sessionMode: "resident", capabilities: ["resident-run"] },
    { managedViaWrapperRuntimes: WRAPPER_BACKED },
  );
  assert.deepEqual(modes, ["resident"]);
});
```

- [ ] **Step 2: Run test, expect failure**

```bash
cd C:/Docker/aify-comms && node --test mcp/stdio/test/test-dispatch-execution-channel-claim.js
```

Expected: 3 failures (the wrapper-backed-channel paths don't push 'channel').

- [ ] **Step 3: Implement**

Edit `mcp/stdio/dispatch-execution.js`. Replace the block at lines 28-34 with:

```javascript
const modes = [];
const WRAPPER_BACKED_CHANNEL_RUNTIMES = new Set(["codex", "hermes", "pi"]);
if (
  sessionMode === "managed" &&
  (capabilities.includes("native-managed-run") || NATIVE_MANAGED_RUNTIMES.has(runtime)) &&
  !isWrapperBacked
) {
  modes.push("managed");
}
// Plan 5 symmetric channel-claim: when this runtime IS wrapper-backed in
// managed mode, the bridge inside the wrapper PTY claims the same way
// claude-channel.js does — via executionModes=['channel']. The service
// routes wrapper-backed managed dispatches as execution_mode='channel'
// (api_v2.py:1047); without this branch, the bridge claims [] and the
// run sits queued forever (observed 2026-05-25, graph-senior-dev).
if (
  sessionMode === "managed" &&
  isWrapperBacked &&
  WRAPPER_BACKED_CHANNEL_RUNTIMES.has(runtime)
) {
  modes.push("channel");
}
if (sessionMode === "resident" && capabilities.includes("resident-run")) {
  if (runtime === "codex" || runtime === "hermes" || runtime === "opencode" || runtime === "pi") {
    modes.push("resident");
  }
}
return modes;
```

- [ ] **Step 4: Run test to verify pass**

```bash
node --test mcp/stdio/test/test-dispatch-execution-channel-claim.js
```

Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp/stdio/dispatch-execution.js mcp/stdio/test/test-dispatch-execution-channel-claim.js
git commit -m "feat(bridge): claim channel-mode runs for wrapper-backed managed codex/hermes/pi

Adds the symmetric counterpart to api_v2.py:1047 (execution_mode='channel'
for wrapper-backed managed). Without this, the bridge inside *-aify
wrappers requested executionModes=[] and never claimed the queued runs."
```

---

### Task B2: Server-side — widen `_CHANNEL_MANAGED_RUNTIMES`

**Files:**
- Modify: `service/routers/api_v2.py:260` (the `_CHANNEL_MANAGED_RUNTIMES` set)
- Test: `service/tests/test_dispatch_channel_claim.py`

- [ ] **Step 1: Write failing test**

```python
import pytest
from fastapi.testclient import TestClient
from service.main import app


@pytest.mark.parametrize("runtime", ["codex", "hermes", "pi"])
def test_channel_claim_accepts_wrapper_backed_runtimes(runtime, db_with_settings, agent_factory, run_factory):
    """Plan 5: codex/hermes/pi bridges with executionModes=['channel'] must be allowed to claim."""
    agent = agent_factory(runtime=runtime, session_mode="managed", capabilities=["native-managed-run"])
    run = run_factory(target_agent=agent["id"], execution_mode="channel", status="queued")
    client = TestClient(app)
    res = client.post(
        "/api/v1/dispatch/claim",
        json={"agentId": agent["id"], "executionModes": ["channel"], "machineId": "test-machine", "bridgeId": "bridge-abc"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data.get("run") is not None, f"Expected to claim run for {runtime} but got: {data}"
    assert data["run"]["id"] == run["id"]
    assert data["run"]["executionMode"] == "channel"
```

(Wire `db_with_settings`, `agent_factory`, `run_factory` to existing test conftest fixtures or write inline equivalents.)

- [ ] **Step 2: Run test, expect failure**

```bash
cd C:/Docker/aify-comms && python -m pytest service/tests/test_dispatch_channel_claim.py -v
```

Expected: FAIL (claim returns `run: null` because codex/hermes/pi not in whitelist).

- [ ] **Step 3: Implement**

Edit `service/routers/api_v2.py:260`. Replace:

```python
_CHANNEL_MANAGED_RUNTIMES = {"claude-code"}
```

with:

```python
# Plan 5 (2026-05-25): wrapper-backed managed dispatches for codex/hermes/pi
# route as execution_mode='channel' (set at line 1047 below). The matching
# claim-side whitelist must include these runtimes so their bridges can
# pick up the runs. Without this, runs sit queued — observed 2026-05-25.
_CHANNEL_MANAGED_RUNTIMES = {"claude-code", "codex", "hermes", "pi"}
```

- [ ] **Step 4: Run test to verify pass**

```bash
python -m pytest service/tests/test_dispatch_channel_claim.py -v
```

Expected: 3/3 PASS.

- [ ] **Step 5: Run full suite to catch regressions**

```bash
python -m pytest service/tests/ -x
```

Expected: All pass. If a legacy test now claims codex/hermes/pi channel runs unexpectedly, inspect — the legacy assertion is probably wrong post-Plan-5.

- [ ] **Step 6: Commit**

```bash
git add service/routers/api_v2.py service/tests/test_dispatch_channel_claim.py
git commit -m "feat(server): allow codex/hermes/pi to claim channel-mode runs

Plan 4 routed wrapper-backed managed dispatches as execution_mode=channel
but only claude-code was whitelisted to claim them. Result: runs sat
queued. Widens the whitelist to match the route."
```

---

### Task B3: Per-runtime controller wiring for managed channel-mode delivery

**Files:**
- Read first: `mcp/stdio/controllers/codex-controller.js`, `mcp/stdio/controllers/hermes-controller.js`, `mcp/stdio/controllers/pi-controller.js`
- Modify each as needed.
- Test: `mcp/stdio/test/test-managed-channel-delivery.js`

**Implementer recon first:** Read each controller. They already handle `executionMode='channel'` for resident-channel via Plan 3. Confirm whether the same code path can serve `executionMode='channel' + sessionMode='managed' + wrapper-backed`, or whether a new branch is needed.

The expected answer (per Phase 1 analysis): the existing channel-delivery path in each controller works because the bridge is in-process with the runtime, same as resident. If a controller's `channel` branch is gated on `sessionMode === 'resident'`, lift that gate to allow `'managed'` when wrapper-backed.

- [ ] **Step 1: Failing test — channel-mode delivery from a managed wrapper-backed bridge lands on the right controller**

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { CodexAdapter } from "../adapters/codex.js";

test("codex managed + wrapper-backed + channel-mode injectMessage routes to CodexLegacyController", async () => {
  const adapter = new CodexAdapter();
  // Stub controllerFor to return a sentinel — the implementer adapts to
  // whatever shape the codex adapter actually uses for managed channel routing.
  const opts = {
    executionMode: "channel",
    sessionMode: "managed",
    wrapperBacked: true,
    runtime: "codex",
    payload: { body: "hello" },
  };
  const ctrl = adapter.controllerFor(opts);
  assert.ok(ctrl, "expected a controller for channel-mode managed wrapper-backed codex");
  assert.equal(ctrl.constructor.name, "CodexLegacyController");
});
```

(Repeat for hermes and pi.)

- [ ] **Step 2: Run test, expect failure**

```bash
node --test mcp/stdio/test/test-managed-channel-delivery.js
```

Expected: FAIL — controllers don't currently route `executionMode='channel' + sessionMode='managed' + wrapperBacked=true` to the channel-delivery controller.

- [ ] **Step 3: Implement controller routing**

For each of `codex-controller.js`, `hermes-controller.js`, `pi-controller.js`, find the `controllerFor` (or equivalent) routing logic. If the channel branch is gated on `sessionMode === 'resident'`, change to `(sessionMode === 'resident' || (sessionMode === 'managed' && opts.wrapperBacked))`. Add a comment citing Plan 5.

- [ ] **Step 4: Run test to verify pass**

```bash
node --test mcp/stdio/test/test-managed-channel-delivery.js
```

Expected: 3/3 PASS.

- [ ] **Step 5: Run full adapter+controller test suite**

```bash
node --test mcp/stdio/test/test-*.js
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add mcp/stdio/controllers/ mcp/stdio/test/test-managed-channel-delivery.js
git commit -m "feat(controllers): route wrapper-backed managed channel-mode to per-runtime delivery

Mirrors the resident-channel delivery path for wrapper-backed managed
dispatches. The bridge inside *-aify is in-process with the runtime
(same as resident), so the same controller can inject messages."
```

---

### Task B4: server.js — pass `managedViaWrapperRuntimes` to dispatch-execution

**Files:**
- Modify: `mcp/stdio/server.js` (wherever `supportedExecutionModes(state.info, ...)` is called)

- [ ] **Step 1: Locate the call sites**

```bash
grep -n "supportedExecutionModes" mcp/stdio/server.js
```

- [ ] **Step 2: Verify `managedViaWrapperRuntimes` is being passed**

For each call site, ensure the options object includes `{ managedViaWrapperRuntimes: <set-or-array> }`. The set is fetched from server settings (`/api/v1/settings` → `managed_via_wrapper`). If it isn't being passed, add it.

If the bridge already pulls settings on startup (search for `GET /api/v1/settings`), wire the result into the options. Else add a startup fetch.

- [ ] **Step 3: Tests already covered by Task B1**

No new test — Task B1 verifies the function output. If the options aren't being passed, B1's integration would have caught it.

- [ ] **Step 4: Smoke verify**

Rebuild container and re-launch a codex-aify. Confirm the bridge polls `/dispatch/claim` with `executionModes=['channel']` in its request body (curl on the service, or check docker logs).

- [ ] **Step 5: Commit (only if any change needed)**

```bash
git add mcp/stdio/server.js
git commit -m "fix(bridge): pass managedViaWrapperRuntimes to supportedExecutionModes

Without this option, the channel-claim branch in dispatch-execution.js
cannot detect wrapper-backed status and the bridge falls back to []."
```

---

### Task B5: Live smoke — graph-senior-dev round-trip

**Files:**
- No code changes; this is a verification step.

- [ ] **Step 1: Rebuild container**

```bash
cd C:/Docker/aify-comms && docker compose up -d --build
curl -4 -s http://127.0.0.1:8800/health
```

Expected: healthy.

- [ ] **Step 2: Re-deploy wrappers**

```bash
./redeploy.sh
```

Expected: 4 wrappers refreshed (claude/codex/hermes/pi).

- [ ] **Step 3: From this conversation, send a message to graph-senior-dev**

```bash
# Operator can do this themselves via comms_send, or the implementer
# uses mcp__aify-comms__comms_send if available.
```

- [ ] **Step 4: Within 30 s, expect a reply to land back via comms inbox / channel**

If yes — fix confirmed live.
If no — capture `dispatch_runs.run_xxx.error_text`, bridge_instances state, and re-investigate Phase 1.

---

## Section C — `has_live_worker` gate in agent read path

Goal: dashboard no longer shows `online` for managed agents without a live worker.

### Task C1: Failing test — read-path downgrades stale `online`

**Files:**
- Test: `service/tests/test_agent_status_read_gate.py`

- [ ] **Step 1: Write failing test**

```python
import pytest
import time
from fastapi.testclient import TestClient
from service.main import app


def test_managed_agent_with_no_terminal_session_downgrades_from_online(
    db_with_settings, agent_factory,
):
    """When agent_live_state.status='online' is cached but no live terminal_session
    exists for a managed wrapper-backed agent, GET /api/v1/agents/{id} must NOT
    return status='online'. Plan 5 read-path gate."""
    agent = agent_factory(
        runtime="codex", session_mode="managed", capabilities=["native-managed-run"]
    )
    # Stamp the cache with stale 'online'
    db_with_settings.execute(
        """INSERT OR REPLACE INTO agent_live_state
        (agent_id, status, reason, environment_id, session_id, terminal_id, active_run_id, refresh_after, updated_at)
        VALUES (?, 'online', 'stale', '', 'sess-fake', '', '', ?, ?)""",
        (agent["id"], "2030-01-01T00:00:00Z", "2030-01-01T00:00:00Z"),
    )
    db_with_settings.commit()

    client = TestClient(app)
    res = client.get(f"/api/v1/agents/{agent['id']}")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] != "online", (
        f"Expected status downgrade because no live terminal_session row exists, got {body['status']}"
    )
    assert body["status"] in ("available", "offline"), f"Unexpected fallback status: {body['status']}"
```

- [ ] **Step 2: Run test, expect failure**

```bash
python -m pytest service/tests/test_agent_status_read_gate.py -v
```

Expected: FAIL (read path trusts cached `online`).

- [ ] **Step 3: Implement — gate in agent serializer**

Find the agent-row → JSON serializer in `service/routers/api_v2.py`. It's the function that builds the response for `GET /api/v1/agents` and `GET /api/v1/agents/{id}` — likely named `_agent_payload` or `_serialize_agent` or similar. Add a final-step downgrade for managed wrapper-backed agents:

```python
def _enforce_live_worker_gate(payload: dict, db, settings) -> dict:
    """Plan 5 (2026-05-25): downgrade cached 'online' to 'available' for managed
    wrapper-backed agents that have no non-terminated terminal_sessions row.
    Without this gate, the live-state cache lies — refresh_after is keyed on
    heartbeat freshness, not worker presence."""
    if payload.get("status") != "online":
        return payload
    session_mode = (payload.get("sessionMode") or "").lower()
    if session_mode != "managed":
        return payload
    runtime = (payload.get("runtime") or "").lower()
    if not _managed_via_wrapper_for_runtime(settings, runtime):
        return payload
    row = db.execute(
        """SELECT 1 FROM terminal_sessions
        WHERE agent_id = ? AND status NOT IN ('stopped', 'failed', 'exited')
        LIMIT 1""",
        (payload["id"],),
    ).fetchone()
    if row is not None:
        return payload
    payload["status"] = "available"
    payload["statusReason"] = "no-live-worker (Plan 5 read-path gate)"
    return payload
```

Wire `_enforce_live_worker_gate(payload, db, settings)` into the per-agent serializer just before returning the payload.

- [ ] **Step 4: Run test to verify pass**

```bash
python -m pytest service/tests/test_agent_status_read_gate.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest service/tests/ -x
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add service/routers/api_v2.py service/tests/test_agent_status_read_gate.py
git commit -m "fix(api): gate 'online' status on live terminal_session for managed wrapper-backed

Plan 4 Task 10 left the live-state cache trusting heartbeat freshness for
refresh_after, so cached 'online' persisted even after the wrapper PTY
exited. This adds a final-step downgrade in the agent serializer: if
status='online' but no live terminal_sessions row, downgrade to
'available'. Cheap check at the API boundary; cache stays for
performance but no longer lies."
```

---

### Task C2: Cache writeback — correct the next read

**Files:**
- Modify: `service/routers/api_v2.py` (the same area as C1; add a writeback after the gate fires)

- [ ] **Step 1: Failing test — second read also returns downgraded status**

Extend test_agent_status_read_gate.py:

```python
def test_downgrade_writeback_persists_to_cache(db_with_settings, agent_factory):
    """After the read-path gate fires once, agent_live_state should be updated
    so the dashboard's next poll sees the downgrade without re-running the
    check."""
    # ... same setup as test_managed_agent_with_no_terminal_session_downgrades_from_online ...
    client = TestClient(app)
    res1 = client.get(f"/api/v1/agents/{agent['id']}")
    assert res1.json()["status"] != "online"

    row = db_with_settings.execute(
        "SELECT status FROM agent_live_state WHERE agent_id = ?", (agent["id"],)
    ).fetchone()
    assert row is not None
    assert row["status"] != "online", f"Cache should reflect downgrade, got {row['status']}"
```

- [ ] **Step 2: Run test, expect failure**

Expected: FAIL — cache row still has 'online'.

- [ ] **Step 3: Implement writeback in `_enforce_live_worker_gate`**

After setting `payload["status"] = "available"`, also UPDATE `agent_live_state` to match:

```python
db.execute(
    """UPDATE agent_live_state SET status = ?, reason = ?, updated_at = ?
    WHERE agent_id = ?""",
    ("available", "no-live-worker (Plan 5 read-path gate)",
     _now_iso(), payload["id"]),
)
db.commit()
```

- [ ] **Step 4: Run test to verify pass**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add service/routers/api_v2.py service/tests/test_agent_status_read_gate.py
git commit -m "fix(api): writeback live-worker-gate downgrade to agent_live_state cache

Without this, every read re-runs the terminal_sessions check. Cheap
writeback (one UPDATE) keeps the cache honest."
```

---

## Section D — Holistic review + docs + finish

### Task D1: Run full test suites

- [ ] Run `python -m pytest service/tests/ -x` — must be green.
- [ ] Run `node --test mcp/stdio/test/test-*.js` — must be green.
- [ ] Rebuild container + smoke `/health`.

### Task D2: Code-reviewer subagent over Plan 5 diff

Dispatch `superpowers:requesting-code-review` against `git diff 1c8d1c8..HEAD`. Address Critical and Important findings. Note Minor findings for follow-up.

### Task D3: Update DECISIONS.md

Add three entries (one per root cause) documenting:
- The symptom operators saw
- The architectural gap that allowed it
- The fix and why it's the right boundary

### Task D4: Update aify-comms-debug skill

`.claude/skills/aify-comms-debug/SKILL.md` and `.agents/skills/aify-comms-debug/SKILL.md`. Add three detection recipes:
- "hermes wrapper fell through to plain hermes" — check `~/.local/state/aify-comms/hermes-aify-dashboard-*.log`
- "queued managed run never claimed" — check `dispatch_runs.execution_mode='channel'` + `claim_bridge_id=''`
- "agent shows online without console" — verify with `SELECT * FROM terminal_sessions WHERE agent_id = ? AND status NOT IN ('stopped','failed')`

### Task D5: Finishing skill

Invoke `superpowers:finishing-a-development-branch`. Present 4 options (merge / PR / keep / discard). Recommend keep for additional live testing.

---

## Self-review checklist (controller runs before dispatching subagents)

- [ ] Every section has at least one TDD-driven implementation task.
- [ ] No "TODO", "TBD", or placeholder steps.
- [ ] Exact file paths everywhere.
- [ ] Each task ends with a commit message that explains the WHY.
- [ ] Section B's controller wiring (Task B3) requires the implementer to read the actual codex-controller.js / hermes-controller.js / pi-controller.js first because the routing logic may already cover the case — be open to "no change required" from the implementer if the routing is already correct.
- [ ] No tests on opencode (operator memory: skip opencode entirely).
- [ ] 500-line rule respected: install.sh delta is small; api_v2.py addition is ~15 lines; dispatch-execution.js addition is ~12 lines. No new files exceed 200 lines.
