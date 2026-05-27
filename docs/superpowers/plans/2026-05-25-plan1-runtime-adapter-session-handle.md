# Plan 1 — RuntimeAdapter + Session-Handle Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce the `RuntimeAdapter` foundation per `docs/superpowers/specs/2026-05-25-runtime-adapter-design.md`. Plan 1 ships every adapter method that Plans 2 and 3 will need (most stubbed as `throw new Error("not yet implemented")`) plus a working implementation of the session-lifecycle methods, the bridge integration that captures handles back to the server, the codex carve-out removal, and the codex-aify wrapper stale-handle fallback.

**Architecture:** A new `mcp/stdio/adapters/` package defines one `RuntimeAdapter` abstract class plus one concrete adapter per runtime (claude, codex, hermes, pi, opencode). The bridge `server.js` consumes the adapter to read the current runtime session id, attach it to `comms_register` payloads, and POST changes to `/api/v2/agents/{id}/session-handle` every 60s. `service/routers/api_v2.py:_default_console_command()` is simplified to use the stored handle for `--resume` across all runtimes. `install.sh` adds a stale-handle fallback to `codex-aify`.

**Tech Stack:** Node 20 + ES modules (`node --test` runner with `assert`), Python 3 + FastAPI + pytest, bash for wrapper scripts.

---

## File Structure

### Create

| Path | Responsibility |
|---|---|
| `mcp/stdio/adapters/base.js` | Abstract `RuntimeAdapter` class — full Plan 1+2+3 contract; Plan 1 methods implemented, Plan 2/3 stubbed to throw |
| `mcp/stdio/adapters/claude.js` | `ClaudeAdapter` — `name="claude-code"`, `sessionEnvVars=["CLAUDE_SESSION_ID"]` |
| `mcp/stdio/adapters/codex.js` | `CodexAdapter` — `name="codex"`, `sessionEnvVars=["CODEX_THREAD_ID"]`, `diagnosticEnv()` includes `AIFY_CODEX_APP_SERVER_URL` |
| `mcp/stdio/adapters/hermes.js` | `HermesAdapter` — `name="hermes"`, `sessionEnvVars=["HERMES_SESSION_ID","HERMES_SESSION"]`, `diagnosticEnv()` includes `AIFY_HERMES_GATEWAY_URL` |
| `mcp/stdio/adapters/pi.js` | `PiAdapter` — `name="pi"`, `sessionEnvVars=["PI_SESSION_ID","OMP_SESSION_ID","AIFY_PI_SESSION_ID"]` |
| `mcp/stdio/adapters/opencode.js` | `OpencodeAdapter` — `name="opencode"`, `sessionEnvVars=["OPENCODE_SESSION_ID","OPENCODE_SESSION"]` |
| `mcp/stdio/adapters/index.js` | `adapterFor(name)` factory + `supportedRuntimes()` listing |
| `mcp/stdio/tests/adapters/contract.test.js` | Shared contract suite — every adapter must pass |
| `mcp/stdio/tests/adapters/claude.test.js` | Claude-specific |
| `mcp/stdio/tests/adapters/codex.test.js` | Codex-specific (incl. `appServerUrl` in diagnosticEnv) |
| `mcp/stdio/tests/adapters/hermes.test.js` | Hermes-specific (incl. `gatewayUrl` in diagnosticEnv) |
| `mcp/stdio/tests/adapters/pi.test.js` | Pi-specific (incl. multi-var fallback order) |
| `mcp/stdio/tests/adapters/opencode.test.js` | Opencode-specific (incl. multi-var fallback) |
| `mcp/stdio/tests/adapters/factory.test.js` | Factory + alias resolution + supportedRuntimes() |
| `mcp/stdio/tests/adapter-heartbeat.test.js` | Bridge heartbeat integration — POST to PATCH endpoint when handle changes |
| `service/tests/test_console_command_resume.py` | Regression: `_default_console_command` includes `--resume` for codex when handle stored |

### Modify

| Path | Lines (approx.) | Change |
|---|---|---|
| `mcp/stdio/server.js` | 195-230 | Replace ad-hoc startup banner with `adapter.diagnosticEnv()`; add `_adapter` module-level |
| `mcp/stdio/server.js` | comms_register handler | When `args.sessionHandle` is empty, fill from `_adapter.getCurrentSessionId()` |
| `mcp/stdio/server.js` | heartbeat / bottom-of-file | Add 60s `setInterval` that PATCHes `/agents/{id}/session-handle` when adapter handle changes |
| `service/routers/api_v2.py` | 6911-6971 | Simplify `_default_console_command()`: stored handle drives `--resume` for codex too (drop carve-out) |
| `install.sh` | codex-aify section | Wrap `exec codex --resume $HANDLE` in a try-then-fresh-fallback |

### Out of scope

- Removing `RUNTIME_SESSION_ENV_VARS` from `runtimes.js` (defer to Plan 3 — keep dual source for backwards compat during Plan 1 to limit blast radius)
- Python-side `service/runtimes/` adapter package (Plan 3)
- Capability flags `supportsResident` etc. (Plan 2)

---

## Task 1: RuntimeAdapter base class + contract suite

**Files:**
- Create: `mcp/stdio/adapters/base.js`
- Create: `mcp/stdio/tests/adapters/contract.test.js`

- [ ] **Step 1: Write the failing contract test**

Create `mcp/stdio/tests/adapters/contract.test.js`:

```javascript
import assert from "assert";
import test from "node:test";
import { RuntimeAdapter } from "../../adapters/base.js";

// A test double that fills in the abstract members so we can exercise the base
// class's shared logic (normalizeSessionHandle / normalizeModelOverride /
// getCurrentSessionId default impl / diagnosticEnv default impl).
class TestAdapter extends RuntimeAdapter {
  get name() { return "test-runtime"; }
  get sessionEnvVars() { return ["TEST_SESSION_ID", "TEST_SESSION_ALT"]; }
}

test("getCurrentSessionId returns null when all env vars unset", () => {
  delete process.env.TEST_SESSION_ID;
  delete process.env.TEST_SESSION_ALT;
  const a = new TestAdapter();
  assert.strictEqual(a.getCurrentSessionId(), null);
});

test("getCurrentSessionId returns first non-empty env var value", () => {
  process.env.TEST_SESSION_ID = "abc-123";
  delete process.env.TEST_SESSION_ALT;
  const a = new TestAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "abc-123");
  delete process.env.TEST_SESSION_ID;
});

test("getCurrentSessionId falls back to second env var when first is empty", () => {
  process.env.TEST_SESSION_ID = "";
  process.env.TEST_SESSION_ALT = "fallback-id";
  const a = new TestAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "fallback-id");
  delete process.env.TEST_SESSION_ID;
  delete process.env.TEST_SESSION_ALT;
});

test("getCurrentSessionId rejects placeholder handle values", () => {
  process.env.TEST_SESSION_ID = "unknown";
  const a = new TestAdapter();
  assert.strictEqual(a.getCurrentSessionId(), null);
  process.env.TEST_SESSION_ID = "default";
  assert.strictEqual(a.getCurrentSessionId(), null);
  process.env.TEST_SESSION_ID = "none";
  assert.strictEqual(a.getCurrentSessionId(), null);
  process.env.TEST_SESSION_ID = "null";
  assert.strictEqual(a.getCurrentSessionId(), null);
  delete process.env.TEST_SESSION_ID;
});

test("normalizeSessionHandle trims whitespace", () => {
  const a = new TestAdapter();
  assert.strictEqual(a.normalizeSessionHandle("  real-handle  "), "real-handle");
});

test("normalizeSessionHandle returns empty for placeholder", () => {
  const a = new TestAdapter();
  assert.strictEqual(a.normalizeSessionHandle("unknown"), "");
  assert.strictEqual(a.normalizeSessionHandle("Default"), "");
  assert.strictEqual(a.normalizeSessionHandle(""), "");
  assert.strictEqual(a.normalizeSessionHandle(null), "");
  assert.strictEqual(a.normalizeSessionHandle(undefined), "");
});

test("resumeArgs returns [--resume, handle] for real handle", () => {
  const a = new TestAdapter();
  assert.deepStrictEqual(a.resumeArgs("real-handle"), ["--resume", "real-handle"]);
});

test("resumeArgs returns [] for empty or placeholder handle", () => {
  const a = new TestAdapter();
  assert.deepStrictEqual(a.resumeArgs(""), []);
  assert.deepStrictEqual(a.resumeArgs("unknown"), []);
  assert.deepStrictEqual(a.resumeArgs(null), []);
});

test("normalizeModelOverride strips placeholders", () => {
  const a = new TestAdapter();
  assert.strictEqual(a.normalizeModelOverride("unknown"), "");
  assert.strictEqual(a.normalizeModelOverride("default"), "");
  assert.strictEqual(a.normalizeModelOverride("auto"), "");
  assert.strictEqual(a.normalizeModelOverride(""), "");
});

test("normalizeModelOverride preserves real model names", () => {
  const a = new TestAdapter();
  assert.strictEqual(a.normalizeModelOverride("gpt-5.5"), "gpt-5.5");
  assert.strictEqual(a.normalizeModelOverride("claude-sonnet-4-6"), "claude-sonnet-4-6");
});

test("diagnosticEnv reports session env vars with their values or (unset)", () => {
  delete process.env.TEST_SESSION_ID;
  process.env.TEST_SESSION_ALT = "captured";
  const a = new TestAdapter();
  const env = a.diagnosticEnv();
  assert.strictEqual(env.TEST_SESSION_ID, "(unset)");
  assert.strictEqual(env.TEST_SESSION_ALT, "captured");
  delete process.env.TEST_SESSION_ALT;
});

test("abstract base throws when name/sessionEnvVars not overridden", () => {
  class Bad extends RuntimeAdapter {}
  const b = new Bad();
  assert.throws(() => b.name, /abstract/);
  assert.throws(() => b.sessionEnvVars, /abstract/);
});

test("Plan 2 capability getters throw 'not yet implemented'", () => {
  const a = new TestAdapter();
  assert.throws(() => a.supportsResident, /not yet implemented/);
  assert.throws(() => a.supportsManaged, /not yet implemented/);
  assert.throws(() => a.supportsSteering, /not yet implemented/);
  assert.throws(() => a.supportsInterrupt, /not yet implemented/);
  assert.throws(() => a.supportsMultiClient, /not yet implemented/);
  assert.throws(() => a.preferredDeliveryMode, /not yet implemented/);
});

test("Plan 3 console + delivery methods throw 'not yet implemented'", () => {
  const a = new TestAdapter();
  assert.throws(() => a.wrapperName, /not yet implemented/);
  assert.throws(() => a.consoleCommand({ agentId: "x", handle: "", interactive: true }), /not yet implemented/);
  assert.rejects(() => a.injectMessage({ text: "hi" }), /not yet implemented/);
  assert.rejects(() => a.interrupt({ reason: "x" }), /not yet implemented/);
  assert.rejects(() => a.steer({ text: "x" }), /not yet implemented/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/contract.test.js`
Expected: FAIL — `Cannot find module '../../adapters/base.js'`.

- [ ] **Step 3: Implement `mcp/stdio/adapters/base.js`**

Create `mcp/stdio/adapters/base.js`:

```javascript
// Abstract runtime adapter. Every supported runtime (claude-code, codex,
// hermes, pi, opencode) ships a subclass that fills in `name` and
// `sessionEnvVars` at minimum. The base class supplies shared session-handle
// normalization, model-override normalization, default diagnosticEnv()
// implementation, and stubs for the Plan 2 (capability) and Plan 3 (console +
// delivery) methods so the contract surface is defined upfront.

const HANDLE_PLACEHOLDERS = new Set(["unknown", "default", "none", "null"]);
const MODEL_PLACEHOLDERS = new Set(["unknown", "default", "auto"]);

export class RuntimeAdapter {
  // ─────────────────── IDENTITY ───────────────────

  get name() { throw new Error("abstract: subclass must override name"); }
  get displayName() { return this.name; }

  // ─────────────────── SESSION LIFECYCLE (Plan 1) ───────────────────

  get sessionEnvVars() { throw new Error("abstract: subclass must override sessionEnvVars"); }

  getCurrentSessionId() {
    for (const v of this.sessionEnvVars) {
      const raw = process.env[v];
      const normalized = this.normalizeSessionHandle(raw);
      if (normalized) return normalized;
    }
    return null;
  }

  normalizeSessionHandle(raw) {
    const text = String(raw == null ? "" : raw).trim();
    if (!text) return "";
    if (HANDLE_PLACEHOLDERS.has(text.toLowerCase())) return "";
    return text;
  }

  resumeArgs(handle) {
    const h = this.normalizeSessionHandle(handle);
    return h ? ["--resume", h] : [];
  }

  // ─────────────────── MODEL/CONFIG NORMALIZATION (Plan 1) ───────────────────

  normalizeModelOverride(raw) {
    const text = String(raw == null ? "" : raw).trim();
    if (!text) return "";
    if (MODEL_PLACEHOLDERS.has(text.toLowerCase())) return "";
    return text;
  }

  // ─────────────────── DIAGNOSTICS (Plan 1) ───────────────────

  diagnosticEnv() {
    const out = {};
    for (const v of this.sessionEnvVars) {
      const val = String(process.env[v] || "").trim();
      out[v] = val || "(unset)";
    }
    return out;
  }

  // ─────────────────── CAPABILITIES (Plan 2 — stubbed) ───────────────────

  get supportsResident() { throw new Error("not yet implemented: Plan 2"); }
  get supportsManaged() { throw new Error("not yet implemented: Plan 2"); }
  get supportsSteering() { throw new Error("not yet implemented: Plan 2"); }
  get supportsInterrupt() { throw new Error("not yet implemented: Plan 2"); }
  get supportsMultiClient() { throw new Error("not yet implemented: Plan 2"); }
  get preferredDeliveryMode() { throw new Error("not yet implemented: Plan 2"); }

  // ─────────────────── CONSOLE / WRAPPER (Plan 3 — stubbed) ───────────────────

  get wrapperName() { throw new Error("not yet implemented: Plan 3"); }
  consoleCommand(_opts) { throw new Error("not yet implemented: Plan 3"); }

  // ─────────────────── DELIVERY (Plan 3 — stubbed) ───────────────────

  async injectMessage(_opts) { throw new Error("not yet implemented: Plan 3"); }
  async interrupt(_opts) { throw new Error("not yet implemented: Plan 3"); }
  async steer(_opts) { throw new Error("not yet implemented: Plan 3"); }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/contract.test.js`
Expected: PASS — all assertions green.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/adapters/base.js mcp/stdio/tests/adapters/contract.test.js
git commit -m "feat(adapters): RuntimeAdapter base class with full Plan 1/2/3 contract"
```

---

## Task 2: ClaudeAdapter

**Files:**
- Create: `mcp/stdio/adapters/claude.js`
- Create: `mcp/stdio/tests/adapters/claude.test.js`

- [ ] **Step 1: Write the failing test**

Create `mcp/stdio/tests/adapters/claude.test.js`:

```javascript
import assert from "assert";
import test from "node:test";
import { ClaudeAdapter } from "../../adapters/claude.js";

test("ClaudeAdapter identity", () => {
  const a = new ClaudeAdapter();
  assert.strictEqual(a.name, "claude-code");
  assert.strictEqual(a.displayName, "Claude Code");
  assert.deepStrictEqual(a.sessionEnvVars, ["CLAUDE_SESSION_ID"]);
});

test("ClaudeAdapter reads CLAUDE_SESSION_ID", () => {
  process.env.CLAUDE_SESSION_ID = "claude-abc-123";
  const a = new ClaudeAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "claude-abc-123");
  delete process.env.CLAUDE_SESSION_ID;
});

test("ClaudeAdapter resumeArgs for real handle", () => {
  const a = new ClaudeAdapter();
  assert.deepStrictEqual(a.resumeArgs("claude-abc-123"), ["--resume", "claude-abc-123"]);
});

test("ClaudeAdapter diagnosticEnv exposes CLAUDE_SESSION_ID", () => {
  process.env.CLAUDE_SESSION_ID = "active-id";
  const a = new ClaudeAdapter();
  assert.deepStrictEqual(a.diagnosticEnv(), { CLAUDE_SESSION_ID: "active-id" });
  delete process.env.CLAUDE_SESSION_ID;
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/claude.test.js`
Expected: FAIL — `Cannot find module '../../adapters/claude.js'`.

- [ ] **Step 3: Implement `mcp/stdio/adapters/claude.js`**

Create `mcp/stdio/adapters/claude.js`:

```javascript
import { RuntimeAdapter } from "./base.js";

export class ClaudeAdapter extends RuntimeAdapter {
  get name() { return "claude-code"; }
  get displayName() { return "Claude Code"; }
  get sessionEnvVars() { return ["CLAUDE_SESSION_ID"]; }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/claude.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/adapters/claude.js mcp/stdio/tests/adapters/claude.test.js
git commit -m "feat(adapters): ClaudeAdapter"
```

---

## Task 3: CodexAdapter

**Files:**
- Create: `mcp/stdio/adapters/codex.js`
- Create: `mcp/stdio/tests/adapters/codex.test.js`

- [ ] **Step 1: Write the failing test**

Create `mcp/stdio/tests/adapters/codex.test.js`:

```javascript
import assert from "assert";
import test from "node:test";
import { CodexAdapter } from "../../adapters/codex.js";

test("CodexAdapter identity", () => {
  const a = new CodexAdapter();
  assert.strictEqual(a.name, "codex");
  assert.strictEqual(a.displayName, "Codex");
  assert.deepStrictEqual(a.sessionEnvVars, ["CODEX_THREAD_ID"]);
});

test("CodexAdapter reads CODEX_THREAD_ID", () => {
  process.env.CODEX_THREAD_ID = "019d-thread";
  const a = new CodexAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "019d-thread");
  delete process.env.CODEX_THREAD_ID;
});

test("CodexAdapter diagnosticEnv includes app-server URL", () => {
  process.env.CODEX_THREAD_ID = "thread-x";
  process.env.AIFY_CODEX_APP_SERVER_URL = "ws://127.0.0.1:1234";
  const a = new CodexAdapter();
  const env = a.diagnosticEnv();
  assert.strictEqual(env.CODEX_THREAD_ID, "thread-x");
  assert.strictEqual(env.AIFY_CODEX_APP_SERVER_URL, "ws://127.0.0.1:1234");
  delete process.env.CODEX_THREAD_ID;
  delete process.env.AIFY_CODEX_APP_SERVER_URL;
});

test("CodexAdapter diagnosticEnv reports (unset) when app-server missing", () => {
  delete process.env.CODEX_THREAD_ID;
  delete process.env.AIFY_CODEX_APP_SERVER_URL;
  const a = new CodexAdapter();
  const env = a.diagnosticEnv();
  assert.strictEqual(env.AIFY_CODEX_APP_SERVER_URL, "(unset)");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/codex.test.js`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `mcp/stdio/adapters/codex.js`**

Create `mcp/stdio/adapters/codex.js`:

```javascript
import { RuntimeAdapter } from "./base.js";

export class CodexAdapter extends RuntimeAdapter {
  get name() { return "codex"; }
  get displayName() { return "Codex"; }
  get sessionEnvVars() { return ["CODEX_THREAD_ID"]; }

  diagnosticEnv() {
    const env = super.diagnosticEnv();
    env.AIFY_CODEX_APP_SERVER_URL = String(process.env.AIFY_CODEX_APP_SERVER_URL || "").trim() || "(unset)";
    return env;
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/codex.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/adapters/codex.js mcp/stdio/tests/adapters/codex.test.js
git commit -m "feat(adapters): CodexAdapter with app-server URL in diagnosticEnv"
```

---

## Task 4: HermesAdapter

**Files:**
- Create: `mcp/stdio/adapters/hermes.js`
- Create: `mcp/stdio/tests/adapters/hermes.test.js`

- [ ] **Step 1: Write the failing test**

Create `mcp/stdio/tests/adapters/hermes.test.js`:

```javascript
import assert from "assert";
import test from "node:test";
import { HermesAdapter } from "../../adapters/hermes.js";

test("HermesAdapter identity", () => {
  const a = new HermesAdapter();
  assert.strictEqual(a.name, "hermes");
  assert.strictEqual(a.displayName, "Hermes");
  assert.deepStrictEqual(a.sessionEnvVars, ["HERMES_SESSION_ID", "HERMES_SESSION"]);
});

test("HermesAdapter prefers HERMES_SESSION_ID over HERMES_SESSION", () => {
  process.env.HERMES_SESSION_ID = "primary";
  process.env.HERMES_SESSION = "fallback";
  const a = new HermesAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "primary");
  delete process.env.HERMES_SESSION_ID;
  delete process.env.HERMES_SESSION;
});

test("HermesAdapter falls back to HERMES_SESSION when HERMES_SESSION_ID empty", () => {
  delete process.env.HERMES_SESSION_ID;
  process.env.HERMES_SESSION = "fallback-id";
  const a = new HermesAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "fallback-id");
  delete process.env.HERMES_SESSION;
});

test("HermesAdapter diagnosticEnv includes gateway URL", () => {
  process.env.AIFY_HERMES_GATEWAY_URL = "ws://127.0.0.1:9999/api/ws?token=x";
  const a = new HermesAdapter();
  const env = a.diagnosticEnv();
  assert.strictEqual(env.AIFY_HERMES_GATEWAY_URL, "ws://127.0.0.1:9999/api/ws?token=x");
  delete process.env.AIFY_HERMES_GATEWAY_URL;
});

test("HermesAdapter diagnosticEnv reports (unset) for gateway when missing", () => {
  delete process.env.AIFY_HERMES_GATEWAY_URL;
  const a = new HermesAdapter();
  const env = a.diagnosticEnv();
  assert.strictEqual(env.AIFY_HERMES_GATEWAY_URL, "(unset)");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/hermes.test.js`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `mcp/stdio/adapters/hermes.js`**

Create `mcp/stdio/adapters/hermes.js`:

```javascript
import { RuntimeAdapter } from "./base.js";

export class HermesAdapter extends RuntimeAdapter {
  get name() { return "hermes"; }
  get displayName() { return "Hermes"; }
  get sessionEnvVars() { return ["HERMES_SESSION_ID", "HERMES_SESSION"]; }

  diagnosticEnv() {
    const env = super.diagnosticEnv();
    env.AIFY_HERMES_GATEWAY_URL = String(process.env.AIFY_HERMES_GATEWAY_URL || "").trim() || "(unset)";
    return env;
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/hermes.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/adapters/hermes.js mcp/stdio/tests/adapters/hermes.test.js
git commit -m "feat(adapters): HermesAdapter with gateway URL in diagnosticEnv"
```

---

## Task 5: PiAdapter

**Files:**
- Create: `mcp/stdio/adapters/pi.js`
- Create: `mcp/stdio/tests/adapters/pi.test.js`

- [ ] **Step 1: Write the failing test**

Create `mcp/stdio/tests/adapters/pi.test.js`:

```javascript
import assert from "assert";
import test from "node:test";
import { PiAdapter } from "../../adapters/pi.js";

test("PiAdapter identity", () => {
  const a = new PiAdapter();
  assert.strictEqual(a.name, "pi");
  assert.strictEqual(a.displayName, "Pi");
  assert.deepStrictEqual(a.sessionEnvVars, ["PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID"]);
});

test("PiAdapter prefers PI_SESSION_ID", () => {
  process.env.PI_SESSION_ID = "pi-1";
  process.env.OMP_SESSION_ID = "omp-1";
  process.env.AIFY_PI_SESSION_ID = "aify-pi-1";
  const a = new PiAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "pi-1");
  delete process.env.PI_SESSION_ID;
  delete process.env.OMP_SESSION_ID;
  delete process.env.AIFY_PI_SESSION_ID;
});

test("PiAdapter falls back to OMP_SESSION_ID", () => {
  delete process.env.PI_SESSION_ID;
  process.env.OMP_SESSION_ID = "omp-2";
  process.env.AIFY_PI_SESSION_ID = "aify-pi-2";
  const a = new PiAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "omp-2");
  delete process.env.OMP_SESSION_ID;
  delete process.env.AIFY_PI_SESSION_ID;
});

test("PiAdapter falls back to AIFY_PI_SESSION_ID last", () => {
  delete process.env.PI_SESSION_ID;
  delete process.env.OMP_SESSION_ID;
  process.env.AIFY_PI_SESSION_ID = "aify-pi-3";
  const a = new PiAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "aify-pi-3");
  delete process.env.AIFY_PI_SESSION_ID;
});

test("PiAdapter normalizeModelOverride strips placeholders (pi-specific regression)", () => {
  const a = new PiAdapter();
  assert.strictEqual(a.normalizeModelOverride("unknown"), "");
  assert.strictEqual(a.normalizeModelOverride("gpt-5.5"), "gpt-5.5");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/pi.test.js`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `mcp/stdio/adapters/pi.js`**

Create `mcp/stdio/adapters/pi.js`:

```javascript
import { RuntimeAdapter } from "./base.js";

export class PiAdapter extends RuntimeAdapter {
  get name() { return "pi"; }
  get displayName() { return "Pi"; }
  get sessionEnvVars() { return ["PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID"]; }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/pi.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/adapters/pi.js mcp/stdio/tests/adapters/pi.test.js
git commit -m "feat(adapters): PiAdapter with multi-var session resolution"
```

---

## Task 6: OpencodeAdapter

**Files:**
- Create: `mcp/stdio/adapters/opencode.js`
- Create: `mcp/stdio/tests/adapters/opencode.test.js`

- [ ] **Step 1: Write the failing test**

Create `mcp/stdio/tests/adapters/opencode.test.js`:

```javascript
import assert from "assert";
import test from "node:test";
import { OpencodeAdapter } from "../../adapters/opencode.js";

test("OpencodeAdapter identity", () => {
  const a = new OpencodeAdapter();
  assert.strictEqual(a.name, "opencode");
  assert.strictEqual(a.displayName, "OpenCode");
  assert.deepStrictEqual(a.sessionEnvVars, ["OPENCODE_SESSION_ID", "OPENCODE_SESSION"]);
});

test("OpencodeAdapter prefers OPENCODE_SESSION_ID", () => {
  process.env.OPENCODE_SESSION_ID = "oc-primary";
  process.env.OPENCODE_SESSION = "oc-fallback";
  const a = new OpencodeAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "oc-primary");
  delete process.env.OPENCODE_SESSION_ID;
  delete process.env.OPENCODE_SESSION;
});

test("OpencodeAdapter falls back to OPENCODE_SESSION", () => {
  delete process.env.OPENCODE_SESSION_ID;
  process.env.OPENCODE_SESSION = "oc-fallback-only";
  const a = new OpencodeAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "oc-fallback-only");
  delete process.env.OPENCODE_SESSION;
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/opencode.test.js`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `mcp/stdio/adapters/opencode.js`**

Create `mcp/stdio/adapters/opencode.js`:

```javascript
import { RuntimeAdapter } from "./base.js";

export class OpencodeAdapter extends RuntimeAdapter {
  get name() { return "opencode"; }
  get displayName() { return "OpenCode"; }
  get sessionEnvVars() { return ["OPENCODE_SESSION_ID", "OPENCODE_SESSION"]; }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/opencode.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/adapters/opencode.js mcp/stdio/tests/adapters/opencode.test.js
git commit -m "feat(adapters): OpencodeAdapter"
```

---

## Task 7: Adapter factory + alias resolution

**Files:**
- Create: `mcp/stdio/adapters/index.js`
- Create: `mcp/stdio/tests/adapters/factory.test.js`

- [ ] **Step 1: Write the failing test**

Create `mcp/stdio/tests/adapters/factory.test.js`:

```javascript
import assert from "assert";
import test from "node:test";
import { adapterFor, supportedRuntimes } from "../../adapters/index.js";
import { ClaudeAdapter } from "../../adapters/claude.js";
import { CodexAdapter } from "../../adapters/codex.js";
import { HermesAdapter } from "../../adapters/hermes.js";
import { PiAdapter } from "../../adapters/pi.js";
import { OpencodeAdapter } from "../../adapters/opencode.js";

test("adapterFor returns ClaudeAdapter for claude-code", () => {
  assert.ok(adapterFor("claude-code") instanceof ClaudeAdapter);
});

test("adapterFor returns ClaudeAdapter for claude alias", () => {
  assert.ok(adapterFor("claude") instanceof ClaudeAdapter);
});

test("adapterFor returns CodexAdapter for codex", () => {
  assert.ok(adapterFor("codex") instanceof CodexAdapter);
});

test("adapterFor returns HermesAdapter for hermes", () => {
  assert.ok(adapterFor("hermes") instanceof HermesAdapter);
});

test("adapterFor returns PiAdapter for pi", () => {
  assert.ok(adapterFor("pi") instanceof PiAdapter);
});

test("adapterFor returns PiAdapter for omp alias", () => {
  assert.ok(adapterFor("omp") instanceof PiAdapter);
});

test("adapterFor returns PiAdapter for oh-my-pi alias", () => {
  assert.ok(adapterFor("oh-my-pi") instanceof PiAdapter);
});

test("adapterFor returns OpencodeAdapter for opencode", () => {
  assert.ok(adapterFor("opencode") instanceof OpencodeAdapter);
});

test("adapterFor is case-insensitive and trims whitespace", () => {
  assert.ok(adapterFor("  CLAUDE-CODE  ") instanceof ClaudeAdapter);
  assert.ok(adapterFor("Codex") instanceof CodexAdapter);
});

test("adapterFor throws on unknown runtime", () => {
  assert.throws(() => adapterFor("not-a-real-runtime"), /Unknown runtime/);
});

test("adapterFor throws on empty input", () => {
  assert.throws(() => adapterFor(""), /Unknown runtime/);
  assert.throws(() => adapterFor(null), /Unknown runtime/);
});

test("supportedRuntimes lists the five canonical names", () => {
  const names = supportedRuntimes();
  assert.deepStrictEqual([...names].sort(), [
    "claude-code", "codex", "hermes", "opencode", "pi",
  ]);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/factory.test.js`
Expected: FAIL — `Cannot find module '../../adapters/index.js'`.

- [ ] **Step 3: Implement `mcp/stdio/adapters/index.js`**

Create `mcp/stdio/adapters/index.js`:

```javascript
import { ClaudeAdapter } from "./claude.js";
import { CodexAdapter } from "./codex.js";
import { HermesAdapter } from "./hermes.js";
import { PiAdapter } from "./pi.js";
import { OpencodeAdapter } from "./opencode.js";

const REGISTRY = new Map([
  ["claude-code", ClaudeAdapter],
  ["codex", CodexAdapter],
  ["hermes", HermesAdapter],
  ["pi", PiAdapter],
  ["opencode", OpencodeAdapter],
]);

const ALIASES = new Map([
  ["claude", "claude-code"],
  ["claude_code", "claude-code"],
  ["hermes-agent", "hermes"],
  ["hermes_agent", "hermes"],
  ["oh-my-pi", "pi"],
  ["oh_my_pi", "pi"],
  ["omp", "pi"],
  ["pi-agent", "pi"],
  ["pi_agent", "pi"],
]);

export function adapterFor(name) {
  const key = String(name == null ? "" : name).trim().toLowerCase();
  const canonical = ALIASES.get(key) || key;
  const cls = REGISTRY.get(canonical);
  if (!cls) {
    throw new Error(`Unknown runtime "${name}". Known: ${[...REGISTRY.keys()].join(", ")}`);
  }
  return new cls();
}

export function supportedRuntimes() {
  return [...REGISTRY.keys()];
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/factory.test.js`
Expected: PASS.

- [ ] **Step 5: Run the full adapter test directory to confirm nothing regressed**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/`
Expected: PASS — all 6 test files green.

- [ ] **Step 6: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/adapters/index.js mcp/stdio/tests/adapters/factory.test.js
git commit -m "feat(adapters): factory with alias resolution + supportedRuntimes()"
```

---

## Task 8: Bridge startup banner uses adapter

**Files:**
- Modify: `mcp/stdio/server.js:203-217` (the existing startup-diagnostic block from commit 8758dda)

- [ ] **Step 1: Read the current startup banner block**

Run: `cd C:/Docker/aify-comms && sed -n '195,230p' mcp/stdio/server.js`
Expected: Output shows the existing `try { ... console.error("[aify] bridge startup: ..."); ... } catch { ... }` block landing around line 208-217.

- [ ] **Step 2: Add static import of adapterFor at the top of server.js**

This Task is the first of three (Tasks 8, 9, 10) that need `adapterFor` available at module scope. Add the import near the existing top-of-file imports in `mcp/stdio/server.js`:

```javascript
import { adapterFor } from "./adapters/index.js";
```

Also add the module-level adapter resolution (used by this task and Tasks 9-10):

```javascript
let __runtimeAdapter = null;
try {
  const __rt = String(process.env.AIFY_RUNTIME || "").trim();
  if (__rt) __runtimeAdapter = adapterFor(__rt);
} catch { /* unknown runtime — bridge continues without adapter */ }
```

Place this after the existing module-level constants but BEFORE the existing startup-banner block. (Tasks 9 and 10 will reuse `__runtimeAdapter` from this declaration — do not duplicate it.)

- [ ] **Step 3: Replace the existing startup banner block**

Edit `mcp/stdio/server.js`. Find the block:

```javascript
// Startup diagnostic: surface the env vars the bridge sees so operators
// can verify env propagation through *-aify → runtime → MCP child.
// Operator-reported 2026-05-25: sc-hermes-test-1 stuck with empty
// runtimeConfig despite multiple "relaunch hermes-aify" attempts.
// Without this log the failure point in the env chain is invisible.
try {
  const _runtime = String(process.env.AIFY_RUNTIME || "").trim();
  const _agentId = String(process.env.AIFY_AGENT_ID || "").trim();
  const _sessionMode = String(process.env.AIFY_SESSION_MODE || "").trim();
  const _wrapperFlag = String(process.env.AIFY_MANAGED_VIA_WRAPPER || "").trim();
  const _rawGw = _rawHermesGatewayUrl || "(unset)";
  const _gw = AIFY_HERMES_GATEWAY_URL || "(unset/invalid)";
  const _codexApp = String(process.env.AIFY_CODEX_APP_SERVER_URL || "").trim() || "(unset)";
  console.error(`[aify] bridge startup: runtime=${_runtime || "(unset)"} agentId=${_agentId || "(unset)"} sessionMode=${_sessionMode || "(unset)"} wrapperChild=${_wrapperFlag || "0"} hermesGwRaw=${_rawGw.slice(0, 80)} hermesGwResolved=${_gw.slice(0, 80)} codexAppServer=${_codexApp.slice(0, 80)}`);
} catch { /* best effort */ }
```

Replace with:

```javascript
// Startup diagnostic: surface the env vars the bridge sees so operators
// can verify env propagation through *-aify → runtime → MCP child.
// Now adapter-driven (Plan 1 of the RuntimeAdapter refactor): the runtime
// adapter knows which env vars to report for its runtime.
try {
  const _runtime = String(process.env.AIFY_RUNTIME || "").trim();
  const _agentId = String(process.env.AIFY_AGENT_ID || "").trim();
  const _sessionMode = String(process.env.AIFY_SESSION_MODE || "").trim();
  const _wrapperFlag = String(process.env.AIFY_MANAGED_VIA_WRAPPER || "").trim();
  let _diag = "(no adapter)";
  let _handle = "(none)";
  if (__runtimeAdapter) {
    try {
      _handle = __runtimeAdapter.getCurrentSessionId() || "(none)";
      _diag = JSON.stringify(__runtimeAdapter.diagnosticEnv());
    } catch (err) {
      _diag = `(adapter read failed: ${err?.message || err})`;
    }
  }
  console.error(`[aify] bridge startup: runtime=${_runtime || "(unset)"} agentId=${_agentId || "(unset)"} sessionMode=${_sessionMode || "(unset)"} wrapperChild=${_wrapperFlag || "0"} sessionId=${_handle} env=${_diag}`);
} catch { /* best effort */ }
```

- [ ] **Step 3: Syntax-check server.js**

Run: `cd C:/Docker/aify-comms && node --check mcp/stdio/server.js && echo OK`
Expected: `OK`

- [ ] **Step 4: Smoke-run the existing bridge tests to confirm no regression**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/runtime-state.test.js mcp/stdio/tests/binding-file.test.js mcp/stdio/tests/comms-contracts-defaults.test.js`
Expected: PASS — these don't depend on the banner but exercise the startup path.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/server.js
git commit -m "feat(bridge): startup banner uses RuntimeAdapter for env reporting"
```

---

## Task 9: comms_register attaches sessionHandle from adapter

**Files:**
- Modify: `mcp/stdio/server.js` — comms_register tool handler

- [ ] **Step 1: Find the comms_register handler**

Run: `cd C:/Docker/aify-comms && grep -n '"comms_register"' mcp/stdio/server.js`
Expected: Output shows the registration line (around 2361).

Run: `cd C:/Docker/aify-comms && sed -n '2360,2450p' mcp/stdio/server.js | head -80`
Expected: shows the `server.registerTool("comms_register", ...)` block.

- [ ] **Step 2: Write a failing integration test**

Create `mcp/stdio/tests/adapter-register-payload.test.js`:

```javascript
// Verifies the helper that fills sessionHandle from the adapter when the
// caller leaves it empty. Keeps the actual server.registerTool side-effecty
// path out of unit testing; just pin the helper contract.
import assert from "assert";
import test from "node:test";

import { adapterFor } from "../adapters/index.js";

// Helper extracted from server.js's comms_register handler. Once Plan 1 lands,
// this helper lives at mcp/stdio/server.js and is exported for testing via
// fillSessionHandleFromAdapter.
import { fillSessionHandleFromAdapter } from "../register-helpers.js";

test("fillSessionHandleFromAdapter preserves caller-supplied handle", () => {
  process.env.CLAUDE_SESSION_ID = "from-env";
  const adapter = adapterFor("claude-code");
  const args = { agentId: "a", sessionHandle: "caller-handle" };
  const out = fillSessionHandleFromAdapter(args, adapter);
  assert.strictEqual(out.sessionHandle, "caller-handle");
  delete process.env.CLAUDE_SESSION_ID;
});

test("fillSessionHandleFromAdapter fills empty sessionHandle from adapter env", () => {
  process.env.CLAUDE_SESSION_ID = "from-env";
  const adapter = adapterFor("claude-code");
  const args = { agentId: "a" };
  const out = fillSessionHandleFromAdapter(args, adapter);
  assert.strictEqual(out.sessionHandle, "from-env");
  delete process.env.CLAUDE_SESSION_ID;
});

test("fillSessionHandleFromAdapter leaves empty when env has no handle", () => {
  delete process.env.CLAUDE_SESSION_ID;
  const adapter = adapterFor("claude-code");
  const args = { agentId: "a" };
  const out = fillSessionHandleFromAdapter(args, adapter);
  assert.strictEqual(out.sessionHandle || "", "");
});

test("fillSessionHandleFromAdapter is a no-op with null adapter", () => {
  const args = { agentId: "a" };
  const out = fillSessionHandleFromAdapter(args, null);
  assert.deepStrictEqual(out, args);
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapter-register-payload.test.js`
Expected: FAIL — `Cannot find module '../register-helpers.js'`.

- [ ] **Step 4: Create `mcp/stdio/register-helpers.js`**

Create `mcp/stdio/register-helpers.js`:

```javascript
// Small helpers used by server.js's comms_register tool handler.
// Extracted into a module so they can be unit-tested without spinning up
// the full MCP server.

export function fillSessionHandleFromAdapter(args, adapter) {
  if (!adapter) return args;
  const existing = String(args?.sessionHandle || "").trim();
  if (existing) return args;
  const detected = adapter.getCurrentSessionId();
  if (!detected) return args;
  return { ...args, sessionHandle: detected };
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapter-register-payload.test.js`
Expected: PASS.

- [ ] **Step 6: Wire the helper into server.js's comms_register handler**

Edit `mcp/stdio/server.js`. Task 8 already added the `adapterFor` import and `__runtimeAdapter` module-level declaration — do NOT duplicate those. Just add this import alongside the other top-of-file imports:

```javascript
import { fillSessionHandleFromAdapter } from "./register-helpers.js";
```

Inside the `server.registerTool("comms_register", ...)` callback, find the line that parses `args` (look for `const agentId = String(args?.agentId || "").trim();` or similar near the top of the handler). **Before** that line, insert:

```javascript
args = fillSessionHandleFromAdapter(args, __runtimeAdapter);
```

- [ ] **Step 7: Syntax-check + smoke-run existing tests**

Run: `cd C:/Docker/aify-comms && node --check mcp/stdio/server.js && node --test mcp/stdio/tests/binding-file.test.js mcp/stdio/tests/runtime-state.test.js`
Expected: All green.

- [ ] **Step 8: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/server.js mcp/stdio/register-helpers.js mcp/stdio/tests/adapter-register-payload.test.js
git commit -m "feat(bridge): comms_register fills sessionHandle from RuntimeAdapter"
```

---

## Task 10: 60s heartbeat POSTs handle changes

**Files:**
- Create: `mcp/stdio/session-handle-heartbeat.js`
- Create: `mcp/stdio/tests/adapter-heartbeat.test.js`
- Modify: `mcp/stdio/server.js` — start the heartbeat in the bootstrap section

- [ ] **Step 1: Write the failing heartbeat test**

Create `mcp/stdio/tests/adapter-heartbeat.test.js`:

```javascript
import assert from "assert";
import test from "node:test";
import { startSessionHandleHeartbeat } from "../session-handle-heartbeat.js";

function makeMockAdapter(returnValues) {
  let i = 0;
  return {
    getCurrentSessionId: () => {
      const v = returnValues[Math.min(i, returnValues.length - 1)];
      i += 1;
      return v;
    },
  };
}

test("startSessionHandleHeartbeat POSTs when handle changes", async () => {
  const calls = [];
  const adapter = makeMockAdapter([null, "new-handle"]);
  const stop = startSessionHandleHeartbeat({
    adapter,
    agentId: "agent-x",
    intervalMs: 10,
    postFn: async (agentId, handle) => { calls.push({ agentId, handle }); },
  });
  await new Promise((r) => setTimeout(r, 50));
  stop();
  assert.ok(calls.length >= 1, "expected at least one POST");
  assert.strictEqual(calls[0].agentId, "agent-x");
  assert.strictEqual(calls[0].handle, "new-handle");
});

test("startSessionHandleHeartbeat does not POST when handle unchanged", async () => {
  const calls = [];
  const adapter = makeMockAdapter(["same-handle", "same-handle", "same-handle"]);
  const stop = startSessionHandleHeartbeat({
    adapter,
    agentId: "agent-y",
    intervalMs: 10,
    postFn: async (agentId, handle) => { calls.push({ agentId, handle }); },
  });
  await new Promise((r) => setTimeout(r, 50));
  stop();
  assert.strictEqual(calls.length, 1, "expected exactly one POST (first appearance)");
});

test("startSessionHandleHeartbeat is a no-op without adapter or agentId", () => {
  const stop1 = startSessionHandleHeartbeat({ adapter: null, agentId: "x", intervalMs: 10, postFn: async () => {} });
  const stop2 = startSessionHandleHeartbeat({ adapter: {}, agentId: "", intervalMs: 10, postFn: async () => {} });
  // Both stop() calls must succeed even though they're no-ops
  stop1();
  stop2();
});

test("startSessionHandleHeartbeat swallows post errors", async () => {
  const adapter = makeMockAdapter(["h1"]);
  let stopCalled = false;
  const stop = startSessionHandleHeartbeat({
    adapter,
    agentId: "agent-z",
    intervalMs: 10,
    postFn: async () => { throw new Error("network fail"); },
  });
  await new Promise((r) => setTimeout(r, 30));
  stopCalled = true;
  stop();
  assert.ok(stopCalled, "process did not crash on post failure");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapter-heartbeat.test.js`
Expected: FAIL — `Cannot find module '../session-handle-heartbeat.js'`.

- [ ] **Step 3: Implement `mcp/stdio/session-handle-heartbeat.js`**

Create `mcp/stdio/session-handle-heartbeat.js`:

```javascript
// Periodic re-read of the runtime adapter's current session id. When it
// changes, POST the new value to the aify-comms server via the existing
// PATCH /api/v2/agents/{agent_id}/session-handle endpoint. This is the
// canonical "report-back" path that lets the dashboard Console launch with
// --resume even after a fresh runtime spawn.

export function startSessionHandleHeartbeat({ adapter, agentId, intervalMs, postFn }) {
  const noop = () => {};
  if (!adapter || !agentId || typeof postFn !== "function" || !Number.isFinite(intervalMs) || intervalMs <= 0) {
    return noop;
  }
  let lastHandle = null;
  let stopped = false;

  const tick = async () => {
    if (stopped) return;
    let current = null;
    try { current = adapter.getCurrentSessionId(); } catch { return; }
    if (!current || current === lastHandle) return;
    try {
      await postFn(agentId, current);
      lastHandle = current;
    } catch {
      // best-effort — next tick will retry
    }
  };

  // Fire once immediately so first launch captures handle without waiting.
  tick();

  const timer = setInterval(tick, intervalMs);
  if (typeof timer.unref === "function") timer.unref();

  return () => {
    stopped = true;
    clearInterval(timer);
  };
}

// Default poster: PATCH /api/v2/agents/{id}/session-handle
export function makeDefaultHandlePoster(baseUrl) {
  const root = String(baseUrl || "").replace(/\/+$/, "");
  return async (agentId, sessionHandle) => {
    const url = `${root}/api/v2/agents/${encodeURIComponent(agentId)}/session-handle`;
    const res = await fetch(url, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionHandle, requestedBy: "bridge-heartbeat" }),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`session-handle PATCH ${res.status}: ${text.slice(0, 200)}`);
    }
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapter-heartbeat.test.js`
Expected: PASS.

- [ ] **Step 5: Wire heartbeat startup into server.js**

Edit `mcp/stdio/server.js`. Near the other module-level imports, add:

```javascript
import { startSessionHandleHeartbeat, makeDefaultHandlePoster } from "./session-handle-heartbeat.js";
```

Near where the bridge declares other long-running timers (search for `environmentHeartbeatTimer` to find the analogous spot), add the heartbeat boot. After the existing `__runtimeAdapter` declaration introduced in Task 8, insert:

```javascript
const __HEARTBEAT_MS = Number(process.env.AIFY_SESSION_HEARTBEAT_MS || "60000") || 60000;
const __serverUrl = String(process.env.AIFY_SERVER_URL || process.env.CLAUDE_MCP_SERVER_URL || "http://127.0.0.1:8800").trim();
const __stopHandleHeartbeat = startSessionHandleHeartbeat({
  adapter: __runtimeAdapter,
  agentId: String(process.env.AIFY_AGENT_ID || "").trim(),
  intervalMs: __HEARTBEAT_MS,
  postFn: makeDefaultHandlePoster(__serverUrl),
});
```

In `cleanupOnExit()` (around lines 247-274 of server.js), find the block that clears the other timers (look for `clearInterval(environmentHeartbeatTimer)`) and add:

```javascript
  try { __stopHandleHeartbeat(); } catch { /* best effort */ }
```

- [ ] **Step 6: Syntax-check + smoke-run**

Run: `cd C:/Docker/aify-comms && node --check mcp/stdio/server.js && node --test mcp/stdio/tests/binding-file.test.js mcp/stdio/tests/runtime-state.test.js`
Expected: All green.

- [ ] **Step 7: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/server.js mcp/stdio/session-handle-heartbeat.js mcp/stdio/tests/adapter-heartbeat.test.js
git commit -m "feat(bridge): 60s session-handle heartbeat POSTs adapter handle changes"
```

---

## Task 11: Server `_default_console_command` uses stored handle universally

**Files:**
- Modify: `service/routers/api_v2.py:6911-6971`
- Create: `service/tests/test_console_command_resume.py`

- [ ] **Step 1: Write the failing regression test**

Create `service/tests/test_console_command_resume.py`:

```python
"""Pin that _default_console_command emits `--resume <handle>` for all runtimes
that support it once the handle is stored. The codex carve-out (removed in
Plan 1 of the RuntimeAdapter refactor) is the primary regression target."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.routers.api_v2 import _default_console_command


def _session(*, agent_id, handle, runtime):
    return {"agent_id": agent_id, "session_handle": handle, "runtime": runtime}


def test_claude_managed_includes_resume():
    cmd = _default_console_command(
        _session(agent_id="a", handle="h1", runtime="claude-code"),
        "/tmp",
        interactive=False,
    )
    assert "claude-aify" in cmd
    assert "--aify-agent a" in cmd
    assert "--auto" in cmd
    assert "--resume h1" in cmd


def test_claude_interactive_no_resume():
    cmd = _default_console_command(
        _session(agent_id="a", handle="h1", runtime="claude-code"),
        "/tmp",
        interactive=True,
    )
    assert "claude-aify --aify-agent a" in cmd
    assert "--resume" not in cmd, (
        "Human-opened Console intentionally stays fresh for claude — see api_v2 comment"
    )


def test_codex_managed_includes_resume():
    """Regression for Plan 1: drop the codex carve-out; managed launches now resume."""
    cmd = _default_console_command(
        _session(agent_id="a", handle="thread-uuid", runtime="codex"),
        "/tmp",
        interactive=False,
    )
    assert "codex-aify" in cmd
    assert "--aify-agent a" in cmd
    assert "--resume thread-uuid" in cmd


def test_codex_interactive_includes_resume_when_handle_known():
    """Operator-driven Plan 1 decision: interactive Console resumes if we have a
    handle. codex-aify wrapper handles stale handles gracefully."""
    cmd = _default_console_command(
        _session(agent_id="a", handle="thread-uuid", runtime="codex"),
        "/tmp",
        interactive=True,
    )
    assert "codex-aify --aify-agent a" in cmd
    assert "--resume thread-uuid" in cmd


def test_codex_no_handle_no_resume():
    cmd = _default_console_command(
        _session(agent_id="a", handle="", runtime="codex"),
        "/tmp",
        interactive=False,
    )
    assert "codex-aify --aify-agent a" in cmd
    assert "--resume" not in cmd


def test_hermes_managed_includes_resume():
    cmd = _default_console_command(
        _session(agent_id="a", handle="hh", runtime="hermes"),
        "/tmp",
        interactive=False,
    )
    assert "hermes-aify --aify-agent a" in cmd
    assert "--resume hh" in cmd


def test_pi_managed_includes_resume():
    cmd = _default_console_command(
        _session(agent_id="a", handle="omp-uuid", runtime="pi"),
        "/tmp",
        interactive=False,
    )
    assert "pi-aify --aify-agent a" in cmd
    assert "--resume omp-uuid" in cmd


def test_pi_interactive_no_resume():
    cmd = _default_console_command(
        _session(agent_id="a", handle="omp-uuid", runtime="pi"),
        "/tmp",
        interactive=True,
    )
    # Pi interactive intentionally stays fresh — comments in api_v2 explain the
    # 026H control-sequence trap. Plan 1 preserves this behavior.
    assert "pi-aify --aify-agent a" in cmd
    assert "--resume" not in cmd
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_console_command_resume.py -v`
Expected: FAILS on `test_codex_managed_includes_resume` and `test_codex_interactive_includes_resume_when_handle_known` because today's `_default_console_command` returns `codex-aify --aify-agent a` with no `--resume`.

- [ ] **Step 3: Edit `_default_console_command` to drop the codex carve-out**

Open `service/routers/api_v2.py`. Find the function (around line 6911). Replace the body of the `elif runtime == "codex":` branch to follow the same pattern as `hermes`. Specifically, change:

```python
    elif runtime == "codex":
        # Operator-verified 2026-05-22: `codex resume --include-non-interactive
        # <handle>` fails with "no such file or directory (os error 2)" when
        # the saved handle is stale relative to codex's session storage
        # (codex deletes session files independently of aify-comms). And
        # the bridge's native RPC adapter (createCodexController) already
        # drives turns through its own app-server connection, so the
        # Console PTY doesn't need to share that session-id — it can be
        # a fresh codex-aify shell. Aligns with the interactive path
        # and the same simplification pi uses.
        # Native context preservation: the bridge's controller still
        # passes `runtimeState.sessionId/threadId` on its own app-server
        # connection — Console is supplementary visibility, not the
        # delivery surface.
        return f"codex-aify --aify-agent {agent_id}"
```

to:

```python
    elif runtime == "codex":
        # 2026-05-25 Plan 1 of the RuntimeAdapter refactor — the carve-out
        # that always launched fresh was overcautious. The dashboard Console
        # uses codex-aify (not raw `codex resume`), and codex-aify gains a
        # stale-handle fallback (see install.sh) so a missing session file
        # downgrades to fresh instead of breaking the wrapper. With that in
        # place, the codex Console resumes its stored handle the same way
        # claude/hermes/pi do.
        parts = ["codex-aify", "--aify-agent", agent_id]
```

(Note: replace `return f"codex-aify --aify-agent {agent_id}"` with the `parts = [...]` assignment so control falls through to the shared `if handle: parts.extend(...)` tail at the bottom of the function.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_console_command_resume.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Run the existing api_v2 regression suite**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_api_v2_regressions.py -v -k "console"`
Expected: PASS — including any pre-existing console-command tests.

- [ ] **Step 6: Commit**

```bash
cd C:/Docker/aify-comms
git add service/routers/api_v2.py service/tests/test_console_command_resume.py
git commit -m "fix(console): codex Console resumes stored handle (carve-out removed)"
```

---

## Task 12: codex-aify wrapper stale-handle fallback

**Files:**
- Modify: `install.sh` — the codex-aify wrapper-generation section

- [ ] **Step 1: Find the codex-aify wrapper-generation block**

Run: `cd C:/Docker/aify-comms && grep -n "install_codex_wrapper\|cat .*codex-aify\|codex-aify\$" install.sh | head -10`
Expected: Output shows line numbers where the codex-aify wrapper script is generated.

Run: `cd C:/Docker/aify-comms && awk '/install_codex_wrapper\(\)/{f=1} f{print NR": "$0; if (/^}/) exit}' install.sh | head -120`
Expected: The full function body (~80-100 lines).

- [ ] **Step 2: Identify the exec line that needs guarding**

Inside `install_codex_wrapper()` find a line of the form:

```bash
exec "$CODEX_RUNTIME_COMMAND" "${CODEX_ARGS[@]}"
```

There may be ONE such exec at end-of-script, OR multiple if there are early-return branches. Plan 1 only needs the FINAL exec to gain the resume-then-fallback guard.

- [ ] **Step 3: Write a smoke test for the fallback shape**

Create `mcp/stdio/tests/codex-aify-fallback.test.js`:

```javascript
// Smoke test: ensure the codex-aify wrapper installed by install.sh
// includes the resume-fallback shell guard introduced in Plan 1.
// Pinning a textual marker keeps the regression cheap (we don't actually
// spawn codex; we verify the installed script reflects the intended shape).
import assert from "assert";
import test from "node:test";
import fs from "fs";
import path from "path";

const INSTALL_SH = path.resolve("install.sh");

test("install.sh codex-aify wrapper contains stale-handle fallback marker", () => {
  const src = fs.readFileSync(INSTALL_SH, "utf8");
  // The wrapper-generation block must include the comment + guard so a
  // future refactor can't silently remove the safety net.
  assert.ok(
    src.includes("# Plan 1: try-resume, fall back to fresh codex if the saved session"),
    "expected the Plan 1 fallback comment in install.sh"
  );
  assert.ok(
    src.includes("CODEX_RESUME_HANDLE"),
    "expected a CODEX_RESUME_HANDLE variable to be parsed in the wrapper"
  );
});
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/codex-aify-fallback.test.js`
Expected: FAIL — markers don't exist in `install.sh` yet.

- [ ] **Step 5: Edit `install.sh` to add the fallback**

Locate the codex-aify wrapper generation. Find where the wrapper script's body is written (typically inside a `cat > "$WRAPPER" <<'CODEX_AIFY_WRAPPER'` heredoc).

Add the resume-handle parsing BEFORE the existing exec. Find an existing block that processes `--resume <handle>` (mirror the pi-aify / hermes-aify pattern) and ensure it captures the handle into a shell variable like `CODEX_RESUME_HANDLE`.

Replace the final `exec` block at end-of-script with the fallback pattern. Specifically, find the final exec (e.g. `exec "$CODEX_RUNTIME_COMMAND" "${CODEX_ARGS[@]}"`) and replace with:

```bash
# Plan 1: try-resume, fall back to fresh codex if the saved session
# file has been GC'd by codex itself (os error 2). The wrapper does not
# abort on a stale handle — the operator gets a fresh codex shell and
# the bridge heartbeat will report the new session id within 60s.
if [ -n "${CODEX_RESUME_HANDLE:-}" ]; then
  if "$CODEX_RUNTIME_COMMAND" "${CODEX_ARGS[@]}" resume --include-non-interactive "$CODEX_RESUME_HANDLE" --check-only >/dev/null 2>&1; then
    exec "$CODEX_RUNTIME_COMMAND" "${CODEX_ARGS[@]}" resume --include-non-interactive "$CODEX_RESUME_HANDLE"
  else
    echo "[codex-aify] saved session $CODEX_RESUME_HANDLE not found in codex storage; starting fresh codex" >&2
  fi
fi
exec "$CODEX_RUNTIME_COMMAND" "${CODEX_ARGS[@]}"
```

(Adjust the `--check-only` flag to whatever codex actually supports for a non-spawning probe. If codex has no such flag, fall back to `[ -f "$HOME/.codex/sessions/$CODEX_RESUME_HANDLE.jsonl" ]` as the existence check.)

If `CODEX_RESUME_HANDLE` does not yet exist in the wrapper, add this argument-parsing snippet earlier in the wrapper body (mirroring the existing `--aify-agent` parser):

```bash
CODEX_RESUME_HANDLE=""
CODEX_ARGS_FILTERED=()
PREV_ARG=""
for ARG in "${CODEX_ARGS[@]}"; do
  if [ "$PREV_ARG" = "--resume" ]; then
    CODEX_RESUME_HANDLE="$ARG"
    PREV_ARG=""
    continue
  fi
  case "$ARG" in
  --resume=*)
    CODEX_RESUME_HANDLE="${ARG#*=}"
    continue
    ;;
  --resume)
    PREV_ARG="$ARG"
    continue
    ;;
  esac
  CODEX_ARGS_FILTERED+=("$ARG")
  PREV_ARG="$ARG"
done
CODEX_ARGS=("${CODEX_ARGS_FILTERED[@]}")
```

- [ ] **Step 6: Run the smoke test to verify it passes**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/codex-aify-fallback.test.js`
Expected: PASS.

- [ ] **Step 7: Shellcheck the install.sh codex-aify block**

Run: `cd C:/Docker/aify-comms && bash -n install.sh && echo OK`
Expected: `OK` — no shell syntax errors.

- [ ] **Step 8: Commit**

```bash
cd C:/Docker/aify-comms
git add install.sh mcp/stdio/tests/codex-aify-fallback.test.js
git commit -m "fix(codex-aify): try-resume-then-fresh fallback on stale handle"
```

---

## Task 13: Docs — README + DECISIONS.md update

**Files:**
- Modify: `README.md` — mention RuntimeAdapter foundation in the architecture section
- Modify: `DECISIONS.md` — record the unified-handle-capture decision

- [ ] **Step 1: Read existing DECISIONS.md tail**

Run: `cd C:/Docker/aify-comms && tail -60 DECISIONS.md`
Expected: A series of `## YYYY-MM-DD — Topic` sections.

- [ ] **Step 2: Append a new decision section**

Append to `DECISIONS.md`:

```markdown

## 2026-05-25 — RuntimeAdapter foundation + unified session-handle capture

**Decision:** Introduce a `RuntimeAdapter` abstract class in `mcp/stdio/adapters/` with one concrete subclass per supported runtime (claude-code, codex, hermes, pi, opencode). Plan 1 implements session-lifecycle methods (`getCurrentSessionId`, `resumeArgs`, `normalizeSessionHandle`, `normalizeModelOverride`, `diagnosticEnv`). Bridge consumes the adapter to (a) report the current runtime session id in the startup banner, (b) fill `sessionHandle` in `comms_register` payloads, and (c) PATCH `/api/v2/agents/{id}/session-handle` every 60s when the handle changes.

The dashboard Console's `_default_console_command` is simplified to use the stored handle for `--resume` across all runtimes. The codex carve-out (always-fresh) is removed; codex-aify gains a try-resume-then-fresh fallback so a stale session file degrades gracefully instead of breaking the wrapper.

**Why:** Operator-reported "missing handles all the time" — `agents.session_handle` stayed empty for wrapper-backed managed agents because nothing was reporting back the runtime-created session id after first launch. Each runtime had its own ad-hoc capture path (`extractPiSessionState`, codex app-server events, hermes gateway query, claude channel sidecar) and the new `managed_via_wrapper` flow bypassed them all. The adapter pattern collapses every future per-runtime quirk to one method per runtime.

**Plans 2 and 3 (not yet implemented):** Plan 2 fills in the capability flags (`supportsResident`, `supportsManaged`, `supportsSteering`, `supportsInterrupt`, `supportsMultiClient`, `preferredDeliveryMode`) and routes pi delivery away from `pi-session-resume`'s spawn-fresh-worker pattern into `managed_via_wrapper`. Plan 3 extracts a Python `service/runtimes/` package and migrates `_default_console_command`, the dispatch dispatcher, and the delivery shims to consume adapter calls instead of branching on `runtime == "..."`.

**Trade-off accepted:** The very first turn of a brand-new agent still launches fresh (no handle exists yet); the first heartbeat (≤60s after that turn) captures the new id. Mid-session `/clear` operations (claude) won't update the env var the bridge already has cached — operator must restart the wrapper to recapture. Both are documented as known limitations.

**See also:** `docs/superpowers/specs/2026-05-25-runtime-adapter-design.md`, `docs/superpowers/plans/2026-05-25-plan1-runtime-adapter-session-handle.md`.
```

- [ ] **Step 3: Update README.md mention of the adapter directory**

Run: `cd C:/Docker/aify-comms && grep -n "mcp/stdio/" README.md | head -5`
Expected: lines mentioning the `mcp/stdio/` path.

Find the "Repo layout" or "Architecture" section that lists `mcp/stdio/`. Add a line referencing the new `adapters/` subdirectory. Example:

```markdown
| `mcp/stdio/adapters/` | Per-runtime `RuntimeAdapter` classes — session-id capture, resume args, diagnostic env. See `docs/superpowers/specs/2026-05-25-runtime-adapter-design.md`. |
```

(Insert the line under the existing `mcp/stdio/` entry, matching the table format already present.)

- [ ] **Step 4: Commit**

```bash
cd C:/Docker/aify-comms
git add DECISIONS.md README.md
git commit -m "docs: record RuntimeAdapter Plan 1 decision + adapters/ in README repo layout"
```

---

## Task 14: Full-suite smoke + verification

**Files:** none — verification only.

- [ ] **Step 1: Run the full Node test suite**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/`
Expected: ALL GREEN. No regressions in any pre-existing test.

- [ ] **Step 2: Run the full Python test suite**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/ -q`
Expected: ALL GREEN.

- [ ] **Step 3: Syntax-check all touched JS files**

Run: `cd C:/Docker/aify-comms && for f in mcp/stdio/server.js mcp/stdio/adapters/base.js mcp/stdio/adapters/claude.js mcp/stdio/adapters/codex.js mcp/stdio/adapters/hermes.js mcp/stdio/adapters/pi.js mcp/stdio/adapters/opencode.js mcp/stdio/adapters/index.js mcp/stdio/register-helpers.js mcp/stdio/session-handle-heartbeat.js; do node --check "$f" || echo "BROKEN: $f"; done && echo "all ok"`
Expected: `all ok`.

- [ ] **Step 4: Shellcheck install.sh**

Run: `cd C:/Docker/aify-comms && bash -n install.sh && echo OK`
Expected: `OK`.

- [ ] **Step 5: Live smoke (operator runs)**

Steps (operator-driven):

```bash
# 1. Restart the bridge for any active *-aify wrapper to pick up adapters + heartbeat
# 2. Open a fresh terminal, run pi-aify (or any *-aify)
# 3. Inside the runtime, run a comms_* command (or just wait 60s)
# 4. Check the agent's runtime config:
curl -4 -s "http://127.0.0.1:8800/api/v1/agents/<agent-id>" | python -c "import json,sys; d=json.load(sys.stdin); print(d['agent']['sessionHandle'])"
# 5. Expect: non-empty session handle within 60s of the first turn
# 6. Open the dashboard Console for that agent — verify the command line includes --resume
```

- [ ] **Step 6: Push to origin**

Run: `cd C:/Docker/aify-comms && git push origin feature/dashboard-console-mode`
Expected: branch updated on origin.

---

## After all tasks complete

Announce: "I'm using the finishing-a-development-branch skill to complete this work."

REQUIRED SUB-SKILL: Use `superpowers:finishing-a-development-branch`.
