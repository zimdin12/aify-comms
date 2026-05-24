# RuntimeAdapter — Unified Runtime Abstraction Design Spec

**Status:** Draft — pending operator review
**Author:** comms-senior-dev (Claude Opus 4.7) + operator
**Date:** 2026-05-25
**Related branch:** `feature/dashboard-console-mode`

## Goal

Replace the per-runtime ad-hoc code paths in `mcp/stdio/` and `service/routers/api_v2.py` with a single `RuntimeAdapter` interface that every supported runtime (claude-code, codex, hermes, pi, opencode) implements. The bridge, server, and dashboard consume the adapter instead of branching on `runtime === "..."`.

The first user-visible win is **stable session-handle capture and resume across all runtimes and launch modes** — the operator-reported "missing handles all the time" pain. The architectural win is that adding a fifth runtime becomes "write one adapter file," not "thread a new branch through twelve files."

## Why

Today's pain points (Phase 1 systematic-debugging findings):

| Symptom | Root cause |
|---|---|
| comms-senior-dev-pi has `sessionHandle: ""` after multiple turns | `managed_via_wrapper` flow bypasses PiSession's `extractPiSessionState`; nothing else captures pi's session id back to the server |
| Codex dashboard Console always launches fresh (no `--resume`) | Carve-out in `api_v2.py:6952-6966` protecting against `os error 2` from a bridge-level codex resume, which doesn't actually apply to the codex-aify wrapper path |
| `pi --model unknown` failures (now patched) | No central place defines "what is a placeholder model value" — fix landed only in pi-session.js + runtimes.js |
| Hermes empty-runtimeConfig until config.yaml was hand-patched | The `RUNTIME_SESSION_ENV_VARS` map and the env-resolution logic live in three files; nothing enforces consistency |
| Each new feature (steering, interrupts, channel mode, gateway URLs) lands as a per-runtime if-branch | Six runtimes × N features = quadratic complexity |

The unifying root cause is **per-runtime ad-hoc paths.** Every fix has touched the same set of files in slightly different ways. With four production runtimes (claude/codex/hermes/pi) plus opencode and likely future additions, the maintenance cost of the ad-hoc shape now exceeds the cost of extracting the abstraction.

## Scope

This spec defines the **full** `RuntimeAdapter` contract. Implementation is staged across three plans:

- **Plan 1 — Session lifecycle** (this scope, ships first): adapter foundation, `getCurrentSessionId`, `resumeArgs`, `normalizeSessionHandle`, `normalizeModelOverride`, `diagnosticEnv`. Server uses stored handle for all runtimes. Codex carve-out removed.
- **Plan 2 — Capabilities + delivery mode** (next): `supportsResident`, `supportsManaged`, `supportsSteering`, `supportsInterrupt`, `supportsMultiClient`, `preferredDeliveryMode`. Drops `pi-session-resume` spawn-fresh-worker pattern in favor of `managed_via_wrapper` for pi.
- **Plan 3 — Console + delivery** (last): `consoleCommand`, `wrapperName`, `injectMessage`, `interrupt`, `steer`. Server-side Python adapter introduced; per-runtime branches in `api_v2.py` collapse to adapter calls.

Defining the entire contract upfront avoids three rounds of interface evolution; each subsequent plan only fills in methods rather than reshaping existing ones.

### Out of scope

- Adding new runtimes (the adapter makes this easy, but no new runtime is being added in this work)
- Refactoring the dashboard frontend (`service/new_dashboard/`) — the dashboard already consumes a runtime-agnostic API surface; adapter changes are transparent to it
- The `install.sh` hermes config-path bug (tracked separately as task #115) — adjacent but distinct issue
- The codex codex_thread_id / session-file GC behavior — out of our control, codex's problem

## Architecture

### File layout

```
mcp/stdio/adapters/
├── base.js                      # RuntimeAdapter abstract class — full contract
├── claude.js                    # ClaudeAdapter
├── codex.js                     # CodexAdapter
├── hermes.js                    # HermesAdapter
├── pi.js                        # PiAdapter
├── opencode.js                  # OpencodeAdapter
└── index.js                     # adapterFor(name) factory + registry

mcp/stdio/tests/adapters/
├── contract.test.js             # contract suite — every adapter must pass
├── claude.test.js               # claude-specific quirks
├── codex.test.js
├── hermes.test.js
├── pi.test.js
└── opencode.test.js

service/runtimes/                # introduced in Plan 3
├── __init__.py
├── base.py                      # Python RuntimeAdapter
├── claude.py
├── codex.py
├── hermes.py
├── pi.py
└── opencode.py
```

### Full RuntimeAdapter contract (JS — bridge side)

```js
// mcp/stdio/adapters/base.js

export class RuntimeAdapter {
  // ─────────────────── IDENTITY ───────────────────
  get name() { throw new Error("abstract"); }
  // "claude-code" / "codex" / "hermes" / "pi" / "opencode"

  get displayName() { return this.name; }
  // "Claude Code", "Codex", "Hermes", "Pi", "OpenCode"

  // ─────────────────── SESSION LIFECYCLE — Plan 1 ───────────────────

  get sessionEnvVars() { throw new Error("abstract"); }
  // Ordered list of env vars that may hold the current session id.
  // First non-empty wins.
  // claude: ["CLAUDE_SESSION_ID"]
  // codex:  ["CODEX_THREAD_ID"]
  // hermes: ["HERMES_SESSION_ID", "HERMES_SESSION"]
  // pi:     ["PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID"]

  getCurrentSessionId() {
    for (const v of this.sessionEnvVars) {
      const raw = String(process.env[v] || "").trim();
      const normalized = this.normalizeSessionHandle(raw);
      if (normalized) return normalized;
    }
    return null;
  }

  normalizeSessionHandle(raw) {
    // Strip placeholder values and obviously-invalid strings.
    // Default: trim, reject empty + placeholder set.
    // Subclasses may extend (e.g. claude rejects literal "65" from a known bug).
    const t = String(raw || "").trim();
    if (!t) return "";
    if (HANDLE_PLACEHOLDERS.has(t.toLowerCase())) return "";
    return t;
  }

  resumeArgs(handle) {
    // Returns the CLI args to pass to the wrapper / runtime to resume the
    // given handle. Empty array if no resume support or handle is empty.
    const h = this.normalizeSessionHandle(handle);
    return h ? ["--resume", h] : [];
  }

  // ─────────────────── MODEL/CONFIG NORMALIZATION — Plan 1 ───────────────────

  normalizeModelOverride(raw) {
    // Strip placeholder model values ("unknown" / "default" / "auto") to "".
    // Empty string means "use runtime's own configured default."
    // Centralizes the patch we landed in commit 4f0fef7.
    const t = String(raw || "").trim();
    if (MODEL_PLACEHOLDERS.has(t.toLowerCase())) return "";
    return t;
  }

  // ─────────────────── DIAGNOSTICS — Plan 1 ───────────────────

  diagnosticEnv() {
    // Returns { varName: value } for the bridge startup banner.
    // Subclasses include runtime-specific env vars (gateway URL, app-server URL, etc.)
    const out = {};
    for (const v of this.sessionEnvVars) {
      out[v] = String(process.env[v] || "").trim() || "(unset)";
    }
    return out;
  }

  // ─────────────────── CAPABILITIES — Plan 2 ───────────────────

  get supportsResident() { throw new Error("not yet implemented"); }
  // True if this runtime can receive resident dispatches (live agent).
  // claude: true (channel notifications)
  // codex:  true (app-server WS)
  // hermes: depends on gatewayUrl being live
  // pi:     false (single-client RPC, no gateway)
  // opencode: false (not yet validated)

  get supportsManaged() { throw new Error("not yet implemented"); }
  // True if managed dispatch (PTY/headless) works for this runtime.

  get supportsSteering() { throw new Error("not yet implemented"); }
  // True if mid-turn steering is supported (steer="true" in comms_send).

  get supportsInterrupt() { throw new Error("not yet implemented"); }

  get supportsMultiClient() { throw new Error("not yet implemented"); }
  // True if multiple processes can be attached to the same live session.
  // claude: true (channel binding)
  // codex:  true (app-server multi-client)
  // hermes: true (gateway)
  // pi:     false (--mode rpc is single-client stdio mutex)
  // opencode: false

  get preferredDeliveryMode() { throw new Error("not yet implemented"); }
  // "resident" | "managed" | "managed-via-wrapper"
  // What dispatch shape should the server pick for this runtime when the
  // operator hasn't specified one?

  // ─────────────────── CONSOLE / WRAPPER — Plan 3 ───────────────────

  get wrapperName() { throw new Error("not yet implemented"); }
  // "claude-aify" / "codex-aify" / "hermes-aify" / "pi-aify" / "opencode"

  consoleCommand({ agentId, handle, interactive }) {
    throw new Error("not yet implemented");
  }
  // Returns the full shell command for the dashboard Console to launch this
  // runtime. Used by _default_console_command in api_v2.py once Python adapter
  // lands in Plan 3.

  // ─────────────────── DELIVERY — Plan 3 ───────────────────

  async injectMessage({ text, runId, fromAgentId, threadId }) {
    throw new Error("not yet implemented");
  }
  // Sends a message into the live agent session. Implementation per runtime:
  //   claude: notifications/claude/channel
  //   codex:  turn/start via app-server WS
  //   hermes: prompt.submit via gateway WS
  //   pi:     (not supported — see preferredDeliveryMode)

  async interrupt({ reason }) { throw new Error("not yet implemented"); }
  async steer({ text }) { throw new Error("not yet implemented"); }
}

const HANDLE_PLACEHOLDERS = new Set(["unknown", "default", "none", "null"]);
const MODEL_PLACEHOLDERS = new Set(["unknown", "default", "auto"]);
```

### Adapter factory

```js
// mcp/stdio/adapters/index.js

import { ClaudeAdapter } from "./claude.js";
import { CodexAdapter } from "./codex.js";
import { HermesAdapter } from "./hermes.js";
import { PiAdapter } from "./pi.js";
import { OpencodeAdapter } from "./opencode.js";

const REGISTRY = {
  "claude-code": ClaudeAdapter,
  "codex": CodexAdapter,
  "hermes": HermesAdapter,
  "pi": PiAdapter,
  "opencode": OpencodeAdapter,
};

export function adapterFor(name) {
  const key = String(name || "").trim().toLowerCase();
  const cls = REGISTRY[key];
  if (!cls) {
    throw new Error(`Unknown runtime "${name}". Known: ${Object.keys(REGISTRY).join(", ")}`);
  }
  return new cls();
}

export function supportedRuntimes() {
  return Object.keys(REGISTRY);
}
```

### Per-adapter overrides (Plan 1 visible state)

| Adapter | `name` | `sessionEnvVars` | `displayName` | Plan 1 quirks |
|---|---|---|---|---|
| ClaudeAdapter | `claude-code` | `["CLAUDE_SESSION_ID"]` | `Claude Code` | none |
| CodexAdapter | `codex` | `["CODEX_THREAD_ID"]` | `Codex` | adds `appServerUrl` to `diagnosticEnv()` |
| HermesAdapter | `hermes` | `["HERMES_SESSION_ID", "HERMES_SESSION"]` | `Hermes` | adds `gatewayUrl` to `diagnosticEnv()` |
| PiAdapter | `pi` | `["PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID"]` | `Pi` | none |
| OpencodeAdapter | `opencode` | `["OPENCODE_SESSION_ID", "OPENCODE_SESSION"]` | `OpenCode` | none |

### Bridge integration (server.js — Plan 1)

```js
import { adapterFor } from "./adapters/index.js";

const _runtimeName = String(process.env.AIFY_RUNTIME || "").trim();
let _adapter = null;
try { _adapter = adapterFor(_runtimeName); } catch { /* unknown runtime — log + continue without adapter */ }

// Startup diagnostic banner now uses adapter.diagnosticEnv()
if (_adapter) {
  const env = _adapter.diagnosticEnv();
  const handle = _adapter.getCurrentSessionId();
  console.error(`[aify] bridge startup runtime=${_runtimeName} agentId=${_agentId} sessionId=${handle || "(none)"} env=${JSON.stringify(env)}`);
}

// On every comms_register call, attach current session handle if non-null
// (replaces the per-runtime extractPiSessionState / extractCodexSessionId / etc.)
function buildRegisterPayload(args) {
  const payload = { ...args };
  if (_adapter && !payload.sessionHandle) {
    const handle = _adapter.getCurrentSessionId();
    if (handle) payload.sessionHandle = handle;
  }
  return payload;
}

// Periodic heartbeat (60s) checks if handle changed; if so, POST update
const HEARTBEAT_MS = 60_000;
let _lastReportedHandle = null;
const heartbeatTimer = setInterval(async () => {
  if (!_adapter) return;
  const current = _adapter.getCurrentSessionId();
  if (current && current !== _lastReportedHandle) {
    try {
      await postSessionHandleUpdate(_agentId, current);
      _lastReportedHandle = current;
    } catch { /* best effort */ }
  }
}, HEARTBEAT_MS);
heartbeatTimer.unref();
```

### Server integration (api_v2.py — Plan 1)

Two changes:

1. **`_default_console_command` simplification:** drop the codex carve-out, use stored `session_handle` for `--resume` for all runtimes. The wrappers themselves are responsible for graceful fallback on stale handles (codex-aify gains this in Plan 1; the others already have it).

```python
def _default_console_command(session, workspace, *, interactive=False):
    agent_id = str(session["agent_id"] or "").strip()
    handle = str(session["session_handle"] or "").strip()
    runtime = _normalize_runtime(session["runtime"] or "")
    wrapper = _wrapper_for_runtime(runtime)  # claude-aify, codex-aify, etc.
    if not wrapper:
        return f"{runtime or 'agent'} --aify-agent {agent_id}"
    parts = [wrapper, "--aify-agent", agent_id]
    if not interactive and runtime == "claude-code":
        parts.append("--auto")
    if handle and _runtime_supports_resume(runtime):
        parts.extend(["--resume", handle])
    return " ".join(parts)
```

2. **Heartbeat handler accepts `sessionHandle`:** any heartbeat POST with non-empty `sessionHandle` persists it to `agents.session_handle`. Empty values never overwrite.

### codex-aify wrapper stale-handle fallback (Plan 1)

The wrapper currently passes `--resume $HANDLE` directly to `codex`. If codex has GC'd the session file, codex bails with os error 2 and the wrapper exits — broken Console.

Fix: wrap the resume attempt:

```bash
if [ -n "$CODEX_RESUME_HANDLE" ]; then
  if ! exec "$CODEX_RUNTIME_COMMAND" "${CODEX_ARGS[@]}" --resume "$CODEX_RESUME_HANDLE"; then
    echo "[codex-aify] resume of $CODEX_RESUME_HANDLE failed; starting fresh codex session" >&2
    exec "$CODEX_RUNTIME_COMMAND" "${CODEX_ARGS[@]}"
  fi
fi
exec "$CODEX_RUNTIME_COMMAND" "${CODEX_ARGS[@]}"
```

(Or equivalent using a wrapped shell trap — exact form decided during planning.)

## Data flow (Plan 1 only)

```
┌─────────────────────────────────────────────────────────────────────┐
│  Operator runs `claude-aify --aify-agent X` (or any *-aify wrapper) │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌───────────────────────────────────────────────┐
        │  Wrapper exports CLAUDE_SESSION_ID if known,  │
        │  passes --resume $HANDLE if known,            │
        │  exec runtime                                  │
        └───────────────────────────────────────────────┘
                              │
                              ▼
        ┌───────────────────────────────────────────────┐
        │  Runtime starts, sets/loads session id in env │
        │  Runtime spawns aify-comms MCP bridge          │
        └───────────────────────────────────────────────┘
                              │
                              ▼
        ┌───────────────────────────────────────────────┐
        │  Bridge boot:                                  │
        │    adapter = adapterFor(AIFY_RUNTIME)         │
        │    handle = adapter.getCurrentSessionId()     │
        │    if handle: include in comms_register POST  │
        └───────────────────────────────────────────────┘
                              │
                              ▼
        ┌───────────────────────────────────────────────┐
        │  Server persists handle to agents.session_handle│
        │  (heartbeat handler accepts sessionHandle key)  │
        └───────────────────────────────────────────────┘
                              │
                              ▼
        ┌───────────────────────────────────────────────┐
        │  Bridge heartbeat (every 60s):                 │
        │    current = adapter.getCurrentSessionId()     │
        │    if current != lastReported:                 │
        │       POST /agents/{id}/session-handle         │
        └───────────────────────────────────────────────┘
                              │
                              ▼
        ┌───────────────────────────────────────────────┐
        │  Future Console / managed-dispatch launches:   │
        │    _default_console_command reads handle       │
        │    builds `wrapper --aify-agent X --resume H`  │
        └───────────────────────────────────────────────┘
```

## Failure modes & error handling

| Failure | Behavior |
|---|---|
| Unknown runtime name passed to `adapterFor()` | Throws explicit error with the list of known runtimes; bridge logs the error and continues without adapter (no session-handle capture, but bridge stays alive) |
| Stale handle (runtime can't resume) | Wrapper catches the failure (e.g. codex os error 2) and starts fresh; bridge's next heartbeat reports the new session id |
| Bridge can't reach the server during heartbeat | Best-effort — failure is silently swallowed, next heartbeat retries |
| Runtime supports multiple session env vars but env has stale value from previous session | Adapter reads in declared order; first non-empty wins. If runtime sets a fresh value in a later env var, operator must intervene (rare; not worth defensive code) |
| Two bridges running with conflicting handles for same agent | Last-write-wins on the server; the active bridge's heartbeat will re-converge within 60s |
| `sessionHandle` passed by client during `comms_register` AND adapter has different value | Client-provided `sessionHandle` wins (explicit beats discovered) |

## Testing strategy

### Contract suite (`mcp/stdio/tests/adapters/contract.test.js`)

Every adapter must pass:

1. `adapter.name` returns a non-empty string matching the registry key
2. `adapter.sessionEnvVars` is a non-empty array of strings
3. `adapter.getCurrentSessionId()` returns null when all vars are unset
4. `adapter.getCurrentSessionId()` returns the value when first var is set
5. `adapter.getCurrentSessionId()` returns null when var is set to a placeholder ("unknown", "default", "none")
6. `adapter.resumeArgs("")` returns `[]`
7. `adapter.resumeArgs("real-handle")` returns `["--resume", "real-handle"]`
8. `adapter.resumeArgs("unknown")` returns `[]` (placeholder rejection)
9. `adapter.normalizeModelOverride("unknown")` returns `""`
10. `adapter.normalizeModelOverride("gpt-5.5")` returns `"gpt-5.5"`
11. `adapter.diagnosticEnv()` returns an object with the runtime's session env vars

### Per-adapter tests

- ClaudeAdapter: `CLAUDE_SESSION_ID=abc123 → getCurrentSessionId() === "abc123"`
- CodexAdapter: same with `CODEX_THREAD_ID`; `diagnosticEnv()` includes `AIFY_CODEX_APP_SERVER_URL`
- HermesAdapter: `HERMES_SESSION_ID` first, falls back to `HERMES_SESSION`; `diagnosticEnv()` includes `AIFY_HERMES_GATEWAY_URL`
- PiAdapter: tries `PI_SESSION_ID`, `OMP_SESSION_ID`, `AIFY_PI_SESSION_ID` in order
- OpencodeAdapter: tries `OPENCODE_SESSION_ID`, `OPENCODE_SESSION`

### Integration tests

- Bridge integration: spawn bridge with mocked env vars, verify it POSTs `sessionHandle` in `comms_register`
- Heartbeat: change env var mid-flight, verify next heartbeat POSTs update
- Stale codex: codex-aify wrapper test with a fake codex that fails on `--resume`, verify fresh fallback succeeds

### Regression tests

- `test_api_v2_regressions.py::test_console_command_uses_stored_handle_for_codex` — pin the codex carve-out removal
- `test_api_v2_regressions.py::test_console_command_uses_stored_handle_for_pi` — pin pi behavior
- Existing `test_resident_hermes_with_gateway_url_does_not_require_session_handle` etc. still pass

## Rollout & migration

1. **Plan 1 lands behind no flag.** The adapter is purely additive: it reads env vars that already exist, POSTs handle data the server already accepts (on the existing `comms_register` payload). Existing agents are unaffected until their bridges next restart.
2. **Existing agent records with `session_handle: ""`:** unchanged at rest. Heal naturally on next bridge launch (within 60s).
3. **Existing agent records with stale `session_handle`:** the wrapper-side stale-handle fallback (Plan 1, codex-aify) catches the failure and the next heartbeat reports the fresh handle.
4. **No DB migration needed.** `agents.session_handle` already exists.
5. **Documentation:** README, install guides, DECISIONS.md updated in Plan 1's final task.

## Open questions

1. **Should the bridge POST the handle on every `comms_*` call** (not just register + heartbeat)? Cheaper to do it everywhere; cost is one DB write per call. **Recommendation:** yes, but make it a no-op when handle hasn't changed since last POST. Cheap, robust.
2. **Should the heartbeat interval be configurable?** **Recommendation:** yes, via `AIFY_SESSION_HEARTBEAT_MS` env var; default 60000. Tests can use shorter intervals.
3. **What happens if `AIFY_RUNTIME` is unset?** Today: bridge can still run. **Recommendation:** bridge logs a warning, skips adapter-driven features, continues. Better to soft-degrade than refuse to start.
4. **Should `adapterFor()` accept aliases?** E.g. `"claude"` → `"claude-code"`. **Recommendation:** yes, define an alias table in `index.js`. Common normalization.

## Success criteria

- After Plan 1 lands and ships, opening Console on any managed-via-wrapper or resident agent that has run at least one turn produces a command with `--resume <handle>` for every runtime that supports resume.
- `comms-senior-dev-pi` (current example of the bug) shows non-empty `sessionHandle` within 60s of its next bridge launch.
- Codex dashboard Console launches with `--resume <handle>` instead of always-fresh.
- `node --test mcp/stdio/tests/adapters/` runs to completion with all green.
- `pytest service/tests/test_api_v2_regressions.py` runs to completion with all green.
- The bridge startup banner shows `sessionId=...` instead of `(unset)` whenever the env is set.

## Why this design over alternatives

- **vs. hooks-only (Option A from brainstorming):** the hook approach requires touching every wrapper's hook script (claude/codex/hermes/pi/opencode = 5 files) and depends on each runtime supporting our hook conventions in perpetuity. The adapter approach concentrates change in one place (the bridge's adapter file per runtime) and doesn't depend on hook lifecycle.
- **vs. PTY-banner parsing (Option B):** banner parsing is fragile across CLI version bumps and complicates every wrapper. Env-var reading is the canonical, stable interface every runtime already exposes.
- **vs. ad-hoc patches per runtime (current state):** quadratic complexity. Each new feature (steering, channel mode, gateway URLs) added an if-branch in three files. The adapter pattern collapses every new feature to "add one method to the contract, implement in 5 adapter files."
- **vs. staging the adapter contract across plans:** defining the full contract upfront (with most methods stubbed `throw new Error("not yet implemented")`) means Plan 2 and Plan 3 are pure fill-in. No interface reshape. Senior-dev preference: design the API once, well, before implementing any of it.

## References

- Systematic-debugging Phase 1 findings (this session)
- Operator-confirmed feedback: clean architecture always (saved to `~/.claude/projects/.../memory/feedback-clean-architecture-always.md`)
- Existing per-runtime code: `mcp/stdio/runtimes.js`, `mcp/stdio/pi-session.js`, `service/routers/api_v2.py:_default_console_command`
- Related: `docs/RUNTIME_DELIVERY_TARGET.md`, `DECISIONS.md`
