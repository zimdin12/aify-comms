# Plan 3 — Controllers + Delivery Migration Design Spec

**Status:** Draft — pending operator review
**Author:** comms-senior-dev (Claude Opus 4.7) + operator
**Date:** 2026-05-25
**Related branch:** `feature/dashboard-console-mode`
**Builds on:** [Plan 1 spec](2026-05-25-runtime-adapter-design.md), [Plan 2 spec](2026-05-25-runtime-adapter-plan2-capabilities-design.md)

## Goal

Complete the `RuntimeAdapter` foundation by filling in the remaining stubbed methods on both languages (`consoleCommand`, `wrapperName`, `injectMessage`, `interrupt`, `steer`, plus a new `is_resident_ready`), then collapse the per-runtime if-branches in `_default_console_command`, `_default_capabilities_for`, and the bridge dispatch dispatcher into single adapter calls. Extract `mcp/stdio/runtimes.js`'s inline per-runtime controllers into `mcp/stdio/controllers/<runtime>-controller.js` files so the monolithic dispatcher shrinks from ~1000 lines to ~300.

The user-visible payoff is restoring the `channelEnabled` per-config gate that Plan 2 Task 14 simplification dropped (tracked as #120 — claude resident agents must have channel binding established before advertising `resident-run`). The architectural payoff is that the JS bridge and Python server stop branching on `runtime === "..."` for delivery decisions; adding a sixth runtime in the future becomes "write one adapter + one controller file."

## Why

After Plan 1 + Plan 2, the adapter packages declare WHAT each runtime supports (capabilities) but not HOW the bridge delivers messages or HOW the server builds Console commands. Both still have per-runtime if-branches:

- `service/routers/api_v2.py:_default_console_command` — six branches with different shell-command shapes per runtime (around line 6911).
- `service/routers/api_v2.py:_default_capabilities_for` — Plan 2 simplified the per-runtime branches but dropped the claude `channelEnabled` gate (regression #120).
- `mcp/stdio/runtimes.js` — `createCodexController`, `createHermesController`, `createPiController`, etc., all inline in one ~1000-line file. The dispatcher `launchRuntimeRun` switches on runtime to pick the controller.

Plan 3 routes these consumers through the adapter and extracts the controllers into their own files. Two consequences:

1. **`channelEnabled` regression closed.** Plan 2 Task 14 collapsed per-runtime resident gating into a single rule (`adapter.supports_resident && gateway_ok_for_hermes`), losing the inline `channelEnabled` check that claude needed. Plan 3 reintroduces it through `adapter.is_resident_ready(runtime_config)` — per-runtime gating without per-runtime branches in the consumer.
2. **runtimes.js shrinks past the 500-line rule.** Operator-stated rule: files >500 lines need splitting. `runtimes.js` is a monolith at ~1000+ lines. Plan 3 extracts each runtime's controller into its own ≤300-line file, giving each per-runtime delivery surface a clean boundary, isolated tests, and obvious "where do I add a quirk" location.

## Scope

### In scope (Plan 3)

1. **Python adapter methods**: `console_command(agent_id, handle, interactive)`, `wrapper_name`, `is_resident_ready(runtime_config)`.
2. **JS adapter methods**: `controllerFor(opts)` factory, plus `injectMessage`/`interrupt`/`steer` delegates that route through the controller returned by `controllerFor`.
3. **Controller extraction**: `mcp/stdio/controllers/{base,claude,codex,hermes,pi,opencode}-controller.js` files. Each implements `BaseController`. Extract the existing inline implementations from `runtimes.js` one at a time, keeping the existing dispatch e2e tests green throughout.
4. **Consumer migration**:
   - `_default_console_command` per-runtime tail → `adapter.console_command(...)`
   - `_default_capabilities_for` resident gate → `adapter.is_resident_ready(...)` (restores #120)
   - `launchRuntimeRun` per-runtime dispatch → `adapter.controllerFor(...)`
5. **runtimes.js cleanup**: target ≤300 lines remaining (factory + small helpers only); per-runtime code lives in `controllers/`.
6. **Tests**: per-controller tests + per-adapter assertions for the new Python methods + cross-language consistency unchanged (it covers capability flags only, which Plan 2 already handles).
7. **Docs**: DECISIONS.md entry, README.md repo-layout update for `mcp/stdio/controllers/`.

### Out of scope (later)

- Decomposing `service/routers/api_v2.py` (currently ~9000 lines — egregious 500-line violation). Plan 3 will NOT add to it but won't refactor the whole file. Plan 5 material.
- Plan 4 (runtime-ready event hook + ready status taxonomy).
- Wiring opencode through `opencode serve` for multi-client capability.

## Architecture

### Python adapter contract additions

```python
# service/runtimes/base.py — class attributes already established in Plans 1+2

class RuntimeAdapter:
    # Plan 3 additions — concrete in subclasses

    @property
    def wrapper_name(self) -> str:
        raise NotImplementedError  # claude → "claude-aify", codex → "codex-aify", ...

    def console_command(self, *, agent_id: str, handle: str, interactive: bool) -> str:
        raise NotImplementedError  # builds the dashboard Console launch command

    def is_resident_ready(self, runtime_config: dict) -> bool:
        # Default: every runtime is resident-ready if it supports_resident.
        # Subclasses with extra per-config gates override.
        return self.supports_resident
```

Per-runtime overrides:

```python
# claude.py
class ClaudeAdapter(RuntimeAdapter):
    wrapper_name = "claude-aify"

    def console_command(self, *, agent_id, handle, interactive):
        if interactive:
            return f"claude-aify --aify-agent {agent_id}"
        parts = ["claude-aify", "--aify-agent", agent_id, "--auto"]
        if handle:
            parts.extend(["--resume", handle])
        return " ".join(parts)

    def is_resident_ready(self, runtime_config: dict) -> bool:
        # Restores Plan 2 Task 14 dropped gate (#120). Claude is resident-capable
        # only when claude-channel.js has successfully bound the channel binding,
        # which sets runtime_config.channelEnabled = True.
        return bool(runtime_config and runtime_config.get("channelEnabled") is True)
```

```python
# codex.py
class CodexAdapter(RuntimeAdapter):
    wrapper_name = "codex-aify"

    def console_command(self, *, agent_id, handle, interactive):
        # Plan 1 dropped the codex carve-out — both interactive AND managed
        # resume the stored handle. codex-aify wrapper has the
        # try-resume-then-fresh fallback.
        parts = ["codex-aify", "--aify-agent", agent_id]
        if handle:
            parts.extend(["--resume", handle])
        return " ".join(parts)
    # is_resident_ready inherits default (always True since supports_resident=True)
```

```python
# hermes.py
class HermesAdapter(RuntimeAdapter):
    wrapper_name = "hermes-aify"

    def console_command(self, *, agent_id, handle, interactive):
        parts = ["hermes-aify", "--aify-agent", agent_id]
        if handle:
            parts.extend(["--resume", handle])
        return " ".join(parts)

    def is_resident_ready(self, runtime_config: dict) -> bool:
        # Resident hermes requires a live gateway URL (the tui_gateway WS endpoint).
        # Without it the resident-channel controller has nothing to connect to.
        import re
        gw = str((runtime_config or {}).get("gatewayUrl", "")).strip()
        return bool(re.match(r"^wss?://", gw, re.IGNORECASE))
```

```python
# pi.py
class PiAdapter(RuntimeAdapter):
    wrapper_name = "pi-aify"

    def console_command(self, *, agent_id, handle, interactive):
        if interactive:
            # Plan 1: pi interactive intentionally stays fresh.
            # Resume of the managed RPC session id would emit 026H control-sequence
            # noise into the operator's PTY.
            return f"pi-aify --aify-agent {agent_id}"
        parts = ["pi-aify", "--aify-agent", agent_id]
        if handle:
            parts.extend(["--resume", handle])
        return " ".join(parts)
    # is_resident_ready inherits default (always False since supports_resident=False)
```

```python
# opencode.py
class OpencodeAdapter(RuntimeAdapter):
    wrapper_name = "opencode"  # No wrapper — aify-comms doesn't ship one for opencode.

    def console_command(self, *, agent_id, handle, interactive):
        # No wrapper to launch through. Future opencode-serve integration
        # would change this. For now: plain opencode CLI.
        return "opencode"
    # is_resident_ready inherits default (always False)
```

### JS adapter contract additions

```js
// mcp/stdio/adapters/base.js — extended

export class RuntimeAdapter {
  // Plan 3 additions

  controllerFor(opts) {
    // Subclasses return a controller instance for the given opts (which
    // include executionMode, agentInfo, sessionHandle, etc.) or null when
    // the mode is unsupported.
    throw new Error("abstract");
  }

  async injectMessage(opts) {
    // Default delegates to a controller. Subclasses usually inherit this.
    const controller = this.controllerFor(opts);
    if (!controller) throw new Error(`No controller for runtime=${this.name} executionMode=${opts.executionMode}`);
    return controller.injectMessage(opts);
  }

  async interrupt(opts) {
    const controller = this.controllerFor(opts);
    if (!controller) return;
    return controller.interrupt(opts);
  }

  async steer(opts) {
    const controller = this.controllerFor(opts);
    if (!controller) throw new Error(`Steering not available for runtime=${this.name}`);
    return controller.steer(opts);
  }
}
```

Per-runtime `controllerFor` overrides return the right controller per execution mode:

```js
// claude.js
import { ClaudeController } from "../controllers/claude-controller.js";
import { ChannelInjectionController } from "../controllers/channel-injection-controller.js";

export class ClaudeAdapter extends RuntimeAdapter {
  // ... Plans 1+2 ...

  controllerFor(opts) {
    if (opts?.executionMode === "channel") return new ChannelInjectionController(opts);
    return new ClaudeController(opts);  // managed-via-wrapper or default
  }
}
```

(Exact controller class names depend on what's extracted from runtimes.js — Plan 3 implementer may consolidate or split based on what's actually there. The contract: `controllerFor` returns the right class.)

### Controller class hierarchy

```
mcp/stdio/controllers/
├── base-controller.js               # Abstract BaseController class
├── claude-controller.js             # ClaudeController (managed-via-wrapper + channel)
├── codex-controller.js              # CodexController (resident app-server + managed wrapper)
├── hermes-controller.js             # HermesController (resident gateway + managed wrapper)
├── pi-controller.js                 # PiController (managed wrapper only post Plan 2 flip)
└── opencode-controller.js           # OpencodeController (managed only)
```

```js
// base-controller.js
export class BaseController {
  constructor(opts) { this.opts = opts; }

  // Lifecycle
  async start(ctx) { throw new Error("abstract: subclass must override start"); }

  // Delivery surface
  async injectMessage(opts) { throw new Error("abstract: subclass must override injectMessage"); }
  async interrupt(opts)     { throw new Error("abstract: subclass must override interrupt"); }
  async steer(opts)         { throw new Error("abstract: subclass must override steer"); }

  // Optional terminal-frame stream (synth terminal). Null if controller
  // doesn't expose a terminal — e.g., a managed-via-wrapper controller
  // gets its terminal from TerminalProcessManager, not from itself.
  get terminalSink() { return null; }
}
```

### `_default_console_command` collapse

Before (per Plan 1, ~60 lines of per-runtime branching):

```python
def _default_console_command(session, workspace, *, interactive=False):
    agent_id = ...
    handle = ...
    runtime = ...
    if runtime == "claude-code":
        if interactive:
            return f"claude-aify --aify-agent {agent_id}"
        parts = ["claude-aify", ...]
        ...
    elif runtime == "codex":
        parts = ["codex-aify", ...]
        ...
    elif runtime == "hermes":
        parts = ["hermes-aify", ...]
        ...
    elif runtime == "pi":
        if interactive:
            return f"pi-aify --aify-agent {agent_id}"
        parts = ["pi-aify", ...]
        ...
    elif runtime == "opencode":
        return "opencode"
    ...
```

After Plan 3:

```python
def _default_console_command(session, workspace, *, interactive=False):
    agent_id = str(session["agent_id"] or "").strip()
    handle = str(session["session_handle"] or "").strip()
    runtime = _normalize_runtime(session["runtime"] or "")
    try:
        adapter = adapter_for(runtime)
    except ValueError:
        return f"{runtime or 'agent'} --aify-agent {agent_id}"
    return adapter.console_command(agent_id=agent_id, handle=handle, interactive=interactive)
```

### `_default_capabilities_for` resident gate

Plan 2 dropped the per-config gating. Plan 3 restores it via the adapter:

```python
def _default_capabilities_for(runtime, session_mode, session_handle, runtime_config):
    runtime_n = _normalize_runtime(runtime or "")
    try:
        adapter = adapter_for(runtime_n)
    except ValueError:
        return []

    caps = []
    if session_mode == "resident":
        if adapter.supports_resident and adapter.is_resident_ready(runtime_config or {}):
            caps.append("resident-run")
    else:
        if adapter.supports_managed:
            caps.append("managed-run")
    # ... rest unchanged ...
```

The JS side mirrors:

```js
// runtimes.js
export function defaultCapabilitiesForRuntime(runtime, sessionMode, sessionHandle, runtimeConfig) {
  const adapter = adapterFor(runtime);
  const caps = [];
  if (sessionMode === "resident") {
    if (adapter.supportsResident && adapter.isResidentReady(runtimeConfig || {})) caps.push("resident-run");
  } else {
    if (adapter.supportsManaged) caps.push("managed-run");
  }
  // ... rest unchanged ...
}
```

Wait — the JS adapter doesn't have `isResidentReady` in the Plan 3 specialization (per language A choice — Python owns this). So either:
- A. We DO add `isResidentReady` to JS (small contract violation — operator chose specialization)
- B. JS keeps the inline hermes-gatewayUrl + (NEW) claude-channelEnabled checks; only the Python side gets `is_resident_ready`

I'll go with **B for purity** in the spec. The JS side has the inline checks because the JS dispatcher needs them anyway for routing, and adding `isResidentReady` to JS would be a slight specialization violation. The Python side, which is the one with the regression (#120), gets the clean adapter method. If this turns out to be ugly during implementation, the implementer is empowered to add a `isResidentReady` to JS too.

Trade-off accepted: there's a small duplicated rule (`runtime_config.channelEnabled === true` for claude resident) in both languages until either is_resident_ready lands on JS too, or both consumers route through the Python source-of-truth. Plan 5 cleanup.

### `launchRuntimeRun` dispatcher collapse

Before:

```js
export async function launchRuntimeRun(opts) {
  const runtime = normalizeRuntime(opts.runtime);
  let controller;
  if (runtime === "codex") controller = createCodexController(opts);
  else if (runtime === "hermes") controller = createHermesController(opts);
  else if (runtime === "pi") controller = createPiController(opts);
  else if (runtime === "claude-code") controller = createClaudeController(opts);
  else if (runtime === "opencode") controller = createOpencodeController(opts);
  if (!controller) throw ...;
  return controller.start(ctx);
}
```

After:

```js
export async function launchRuntimeRun(opts) {
  const adapter = adapterFor(opts.runtime);
  const controller = adapter.controllerFor(opts);
  if (!controller) throw new Error(`No controller for ${opts.runtime} ${opts.executionMode}`);
  return controller.start(ctx);
}
```

Plus the controller-instantiating logic that was inline in `createXxxController` moves into the per-runtime controller class constructor or `controllerFor` override.

### Extraction order (one runtime at a time)

To minimize regression risk during the runtimes.js extraction:

1. **opencode** first — smallest, least-used, simplest controller. Proves the pattern.
2. **pi** — only managed-via-wrapper after Plan 2 flip; smaller code surface than the resident-capable runtimes.
3. **claude** — channel-injection is well-understood; existing claude-channel.js tests pin behavior.
4. **hermes** — gateway resident is recent (Plan 2 era); tests are solid.
5. **codex** — last because it's the most complex (app-server WS + managed wrapper + interrupt + steer + multiple termination paths).

Each extraction step:
- Move the inline `createXxxController` body into `mcp/stdio/controllers/<runtime>-controller.js`.
- Adapter's `controllerFor` returns instances of the new class.
- Existing dispatch tests (`codex-resident-dispatch.test.js` etc.) stay green throughout.
- Update `launchRuntimeRun` to drop the per-runtime branch.

### Failure modes

| Failure | Behavior |
|---|---|
| `adapter.controllerFor(opts)` returns null | `launchRuntimeRun` throws clear "No controller for X mode Y" — operator sees actionable message |
| Per-controller file grows past 400 lines | Controller is doing too much. Split into per-execution-mode subclass (e.g., `codex-resident-controller.js` + `codex-managed-controller.js` with shared base). |
| `is_resident_ready` returns False but Plan 2 stored `resident-run` in agent capabilities | Server-side reconciliation: when an agent's `capabilities` doesn't match what `_default_capabilities_for` produces now, the dispatch loop re-derives. Existing Plan 2 reconciliation covers this. |
| Test count balloons | One test file per controller. Each ≤200 lines. Cross-controller integration covered by the existing dispatch e2e tests. |

## Testing strategy

### Adapter contract tests (extended)

Existing `mcp/stdio/tests/adapters/contract.test.js` — Plan 1 test double `TestAdapter` only declares `name` + `sessionEnvVars`. Plan 3 adds the new Plan 3 method stubs to the contract; without override they should throw. The existing "Plan 3 stubs throw" assertions in `contract.test.js` cover this — no change needed unless the adapter contract evolves.

### Per-adapter capability + console_command tests (Python)

Extend `service/tests/runtimes/test_per_adapter.py`:

```python
def test_claude_adapter_console_command():
    a = ClaudeAdapter()
    assert a.console_command(agent_id="x", handle="", interactive=False) == "claude-aify --aify-agent x --auto"
    assert a.console_command(agent_id="x", handle="h", interactive=False) == "claude-aify --aify-agent x --auto --resume h"
    assert a.console_command(agent_id="x", handle="h", interactive=True) == "claude-aify --aify-agent x"


def test_claude_adapter_is_resident_ready():
    a = ClaudeAdapter()
    assert a.is_resident_ready({"channelEnabled": True}) is True
    assert a.is_resident_ready({"channelEnabled": False}) is False
    assert a.is_resident_ready({}) is False
    assert a.is_resident_ready(None) is False


# ... per-adapter equivalents for codex/hermes/pi/opencode ...
```

### Per-controller tests (JS)

Create `mcp/stdio/tests/controllers/<runtime>-controller.test.js` for each. Each verifies:
- `start(ctx)` lifecycle (resolves, fires turn events)
- `injectMessage(opts)` delivery to the underlying transport (mocked)
- `interrupt(opts)` returns immediately, signals cancellation
- `steer(text)` appends to active turn (where supported)
- `terminalSink` exposes synth terminal frames (where applicable)

Existing integration tests (`codex-resident-dispatch.test.js`, etc.) continue covering the integrated path. The per-controller tests are unit-level.

### Regression tests

Pin the channelEnabled gate restoration:

```python
# service/tests/test_resident_gate_restored.py
def test_claude_resident_without_channel_enabled_does_not_advertise_resident_run():
    caps = _default_capabilities_for("claude-code", "resident", "session-x", {})
    assert "resident-run" not in caps, (
        "claude resident without channelEnabled must not advertise resident-run (Plan 3 #120 restoration)"
    )

def test_claude_resident_with_channel_enabled_advertises_resident_run():
    caps = _default_capabilities_for("claude-code", "resident", "session-x", {"channelEnabled": True})
    assert "resident-run" in caps
```

### Console command regression tests

`service/tests/test_console_command_resume.py` (Plan 1) keeps its existing 8 tests. They should pass unchanged after Plan 3 since the new adapter-based code produces the same outputs.

## Rollout

1. Land Python adapter additions (Tasks 1-2).
2. Land JS adapter `controllerFor` and delegate methods (Task 3).
3. Extract BaseController + opencode/pi/claude/hermes/codex controllers one at a time (Tasks 4-9).
4. Migrate Python `_default_console_command` consumer (Task 10).
5. Migrate Python `_default_capabilities_for` to use `is_resident_ready` (Task 11 — closes #120).
6. Migrate JS `launchRuntimeRun` to `adapter.controllerFor` (Task 12).
7. Shrink runtimes.js — remove dead `createXxxController` factories + `pi-session-resume` remnant (Task 13).
8. Docs + smoke + push (Task 14).

Each step keeps the existing test suite green. The controller extraction (Tasks 4-9) carries the most regression risk; do them carefully with the integrated dispatch tests as the safety net.

## Success criteria

- Both adapter packages declare and implement Plan 3 methods.
- `mcp/stdio/runtimes.js` ≤ 350 lines after extraction.
- Each controller file ≤ 400 lines.
- All existing tests pass (Plan 1 + Plan 2 + integration tests).
- New per-controller tests pass.
- `service/tests/test_resident_gate_restored.py` passes — #120 closed.
- The dashboard Console launches correctly for every runtime + interactive/managed combination.

## Open questions

None — all four operator brainstorm answers (specialize per language, full controller extraction, one-shot delivery, channelEnabled restoration) are integrated.

## References

- Plan 1 spec: `docs/superpowers/specs/2026-05-25-runtime-adapter-design.md`
- Plan 2 spec: `docs/superpowers/specs/2026-05-25-runtime-adapter-plan2-capabilities-design.md`
- Task #120: Restore channelEnabled per-config resident gating (this plan closes it)
- Operator feedback: 500-line file rule (`~/.claude/.../memory/feedback-500-line-rule.md`)
- Operator feedback: clean architecture always (`~/.claude/.../memory/feedback-clean-architecture-always.md`)
