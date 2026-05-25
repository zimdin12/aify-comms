# Plan 3 — Controllers + Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill in remaining Plan 3 adapter stubs (Python: `console_command`, `wrapper_name`, `is_resident_ready`; JS: `controllerFor` + delegates), extract the per-runtime controller factories from the 4106-line `mcp/stdio/runtimes.js` into `mcp/stdio/controllers/<runtime>-controller.js` files, migrate the per-runtime consumers in `api_v2.py` and `runtimes.js` to adapter calls, and close the channelEnabled regression (#120).

**Architecture:** Each Python adapter class gains 3 methods that the server consumes. Each JS adapter gains `controllerFor(opts)` that returns a controller class instance. Per-runtime controller code moves out of `runtimes.js` into per-file modules. Consumers (`_default_console_command`, `_default_capabilities_for`, `launchRuntimeRun`) collapse from per-runtime if-branches to single adapter calls.

**Tech Stack:** Node 20 + ES modules (`node --test`), Python 3 + FastAPI + pytest. Plan 1+2 adapter packages already exist.

---

## File Structure

### Create

| Path | Responsibility |
|---|---|
| `mcp/stdio/controllers/base-controller.js` | Abstract `BaseController` class (start, injectMessage, interrupt, steer, terminalSink getter) |
| `mcp/stdio/controllers/opencode-controller.js` | OpencodeController extracted from runtimes.js — managed only |
| `mcp/stdio/controllers/pi-controller.js` | PiController extracted from runtimes.js — managed-via-wrapper (Plan 2 flipped pi) |
| `mcp/stdio/controllers/claude-controller.js` | ClaudeController extracted — channel + managed |
| `mcp/stdio/controllers/hermes-controller.js` | HermesController extracted — resident gateway + managed wrapper |
| `mcp/stdio/controllers/codex-controller.js` | CodexController extracted — resident app-server + managed wrapper |
| `mcp/stdio/tests/controllers/base-controller.test.js` | Contract assertions for the abstract class |
| `mcp/stdio/tests/controllers/<runtime>-controller.test.js` (×5) | Per-controller behavior tests |
| `service/tests/test_resident_gate_restored.py` | Regression: claude resident requires `channelEnabled=True` (closes #120) |
| `service/tests/runtimes/test_console_command.py` | Per-adapter `console_command()` assertions |
| `service/tests/runtimes/test_is_resident_ready.py` | Per-adapter `is_resident_ready()` assertions |

### Modify

| Path | Change |
|---|---|
| `service/runtimes/{claude,codex,hermes,pi,opencode}.py` | Add `wrapper_name`, `console_command`, `is_resident_ready` methods |
| `mcp/stdio/adapters/{claude,codex,hermes,pi,opencode}.js` | Add `controllerFor(opts)` returning the per-runtime controller; add `injectMessage`/`interrupt`/`steer` delegates that route through `controllerFor` |
| `mcp/stdio/runtimes.js` | Remove `createClaudeController`/`createCodexController`/`createCodexControllerPooled`/`createCodexControllerLegacy`/`createOpenCodeController`/`createPiController`/`createPiControllerManaged`/`createPiControllerLegacy`/`createHermesController`/`createHermesResidentChannelController`/`createHermesControllerManaged`/`createHermesControllerManagedGateway`/`createHermesControllerSingleShot`/`createTerminalDeliveryController` inline functions. Replace `launchRuntimeRun` body with `adapterFor(runtime).controllerFor(opts).start(...)`. Target: ≤350 lines remaining (down from 4106). |
| `service/routers/api_v2.py:_default_console_command` (lines ~6911-6971) | Replace per-runtime tail with `adapter_for(runtime).console_command(...)` |
| `service/routers/api_v2.py:_default_capabilities_for` | Replace inline hermes-gateway check with `adapter.is_resident_ready(runtime_config)` |
| `DECISIONS.md` | Append Plan 3 entry |
| `README.md` | Add `mcp/stdio/controllers/` to repo layout |

### Out of scope

- Decomposing `service/routers/api_v2.py` (13586 lines — separate plan)
- Wiring `opencode serve` for multi-client capability (separate follow-up)
- Plan 4 (runtime-ready event hook + ready status)

---

## Task 1: Python adapters — wrapper_name + console_command

**Files:**
- Modify: `service/runtimes/claude.py`, `codex.py`, `hermes.py`, `pi.py`, `opencode.py`
- Create: `service/tests/runtimes/test_console_command.py`

- [ ] **Step 1: Write failing tests**

Create `service/tests/runtimes/test_console_command.py`:

```python
"""Per-adapter wrapper_name + console_command assertions.

The console_command outputs must match what _default_console_command produced
in Plan 1/2 so the regression suite (test_console_command_resume.py) passes
unchanged after Plan 3's migration.
"""

def test_claude_wrapper_name():
    from service.runtimes.claude import ClaudeAdapter
    assert ClaudeAdapter().wrapper_name == "claude-aify"


def test_claude_console_command_interactive():
    from service.runtimes.claude import ClaudeAdapter
    cmd = ClaudeAdapter().console_command(agent_id="a", handle="h", interactive=True)
    assert cmd == "claude-aify --aify-agent a"
    # Interactive intentionally drops --resume — matches Plan 1 spec.


def test_claude_console_command_managed_with_handle():
    from service.runtimes.claude import ClaudeAdapter
    cmd = ClaudeAdapter().console_command(agent_id="a", handle="h", interactive=False)
    assert cmd == "claude-aify --aify-agent a --auto --resume h"


def test_claude_console_command_managed_no_handle():
    from service.runtimes.claude import ClaudeAdapter
    cmd = ClaudeAdapter().console_command(agent_id="a", handle="", interactive=False)
    assert cmd == "claude-aify --aify-agent a --auto"


def test_codex_wrapper_name():
    from service.runtimes.codex import CodexAdapter
    assert CodexAdapter().wrapper_name == "codex-aify"


def test_codex_console_command_with_handle():
    # Plan 1 dropped the codex carve-out; both interactive AND managed resume
    from service.runtimes.codex import CodexAdapter
    a = CodexAdapter()
    assert a.console_command(agent_id="a", handle="h", interactive=True) == "codex-aify --aify-agent a --resume h"
    assert a.console_command(agent_id="a", handle="h", interactive=False) == "codex-aify --aify-agent a --resume h"


def test_codex_console_command_no_handle():
    from service.runtimes.codex import CodexAdapter
    cmd = CodexAdapter().console_command(agent_id="a", handle="", interactive=False)
    assert cmd == "codex-aify --aify-agent a"


def test_hermes_wrapper_name():
    from service.runtimes.hermes import HermesAdapter
    assert HermesAdapter().wrapper_name == "hermes-aify"


def test_hermes_console_command_with_handle():
    from service.runtimes.hermes import HermesAdapter
    cmd = HermesAdapter().console_command(agent_id="a", handle="h", interactive=False)
    assert cmd == "hermes-aify --aify-agent a --resume h"


def test_pi_wrapper_name():
    from service.runtimes.pi import PiAdapter
    assert PiAdapter().wrapper_name == "pi-aify"


def test_pi_console_command_interactive_no_resume():
    # Plan 1: pi interactive stays fresh (avoids 026H control-sequence trap)
    from service.runtimes.pi import PiAdapter
    cmd = PiAdapter().console_command(agent_id="a", handle="h", interactive=True)
    assert cmd == "pi-aify --aify-agent a"


def test_pi_console_command_managed_with_handle():
    from service.runtimes.pi import PiAdapter
    cmd = PiAdapter().console_command(agent_id="a", handle="h", interactive=False)
    assert cmd == "pi-aify --aify-agent a --resume h"


def test_opencode_wrapper_name():
    # opencode has no aify wrapper today; uses the bare CLI
    from service.runtimes.opencode import OpencodeAdapter
    assert OpencodeAdapter().wrapper_name == "opencode"


def test_opencode_console_command():
    from service.runtimes.opencode import OpencodeAdapter
    cmd = OpencodeAdapter().console_command(agent_id="a", handle="h", interactive=False)
    assert cmd == "opencode"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/test_console_command.py -v`
Expected: FAIL — `NotImplementedError: Plan 3 — not yet implemented` or AttributeError on wrapper_name.

- [ ] **Step 3: Implement Plan 3 methods in each Python adapter**

Update `service/runtimes/claude.py`:

```python
"""ClaudeAdapter — Python mirror of mcp/stdio/adapters/claude.js."""

from __future__ import annotations

from .base import RuntimeAdapter


class ClaudeAdapter(RuntimeAdapter):
    name = "claude-code"
    display_name = "Claude Code"
    session_env_vars = ["CLAUDE_SESSION_ID"]
    supports_resident = True
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    supports_multi_client = True
    preferred_delivery_mode = "managed-via-wrapper"

    # Plan 3 additions
    wrapper_name = "claude-aify"

    def console_command(self, *, agent_id: str, handle: str, interactive: bool) -> str:
        if interactive:
            # Human-opened console: consistent with Plan 1 — fresh, no --resume.
            # claude-aify sets up the channel binding itself.
            return f"claude-aify --aify-agent {agent_id}"
        parts = ["claude-aify", "--aify-agent", agent_id, "--auto"]
        if handle:
            parts.extend(["--resume", handle])
        return " ".join(parts)

    def is_resident_ready(self, runtime_config: dict) -> bool:
        # Restores Plan 2 Task 14 dropped gate (#120). Claude is
        # resident-capable only after claude-channel.js has bound the
        # channel, which sets runtime_config.channelEnabled = True.
        if not runtime_config:
            return False
        return runtime_config.get("channelEnabled") is True
```

Update `service/runtimes/codex.py` — add Plan 3 block after capability attributes:

```python
    # Plan 3 additions
    wrapper_name = "codex-aify"

    def console_command(self, *, agent_id: str, handle: str, interactive: bool) -> str:
        # Plan 1 dropped the codex carve-out — both interactive and managed
        # resume the stored handle. codex-aify wrapper has the
        # try-resume-then-fresh fallback.
        parts = ["codex-aify", "--aify-agent", agent_id]
        if handle:
            parts.extend(["--resume", handle])
        return " ".join(parts)

    # is_resident_ready inherits from base (returns supports_resident == True)
```

Update `service/runtimes/hermes.py` — add Plan 3 block:

```python
import re

# ... existing class body ...

    # Plan 3 additions
    wrapper_name = "hermes-aify"

    def console_command(self, *, agent_id: str, handle: str, interactive: bool) -> str:
        parts = ["hermes-aify", "--aify-agent", agent_id]
        if handle:
            parts.extend(["--resume", handle])
        return " ".join(parts)

    def is_resident_ready(self, runtime_config: dict) -> bool:
        # Resident hermes requires a live tui_gateway URL.
        if not runtime_config:
            return False
        gw = str(runtime_config.get("gatewayUrl", "")).strip()
        return bool(re.match(r"^wss?://", gw, re.IGNORECASE))
```

Update `service/runtimes/pi.py` — add Plan 3 block:

```python
    # Plan 3 additions
    wrapper_name = "pi-aify"

    def console_command(self, *, agent_id: str, handle: str, interactive: bool) -> str:
        if interactive:
            # Plan 1: pi interactive intentionally stays fresh — resuming the
            # managed RPC session id into the operator's PTY emits 026H
            # control-sequence noise.
            return f"pi-aify --aify-agent {agent_id}"
        parts = ["pi-aify", "--aify-agent", agent_id]
        if handle:
            parts.extend(["--resume", handle])
        return " ".join(parts)

    # is_resident_ready inherits from base (returns supports_resident == False)
```

Update `service/runtimes/opencode.py` — add Plan 3 block:

```python
    # Plan 3 additions
    wrapper_name = "opencode"  # No aify wrapper for opencode today.

    def console_command(self, *, agent_id: str, handle: str, interactive: bool) -> str:
        # opencode is launched as a plain CLI; no wrapper, no --resume.
        # The opencode CLI session-resume integration is a separate follow-up.
        return "opencode"

    # is_resident_ready inherits from base (returns supports_resident == False)
```

Also update `service/runtimes/base.py` to make `is_resident_ready` a default that returns `supports_resident` instead of raising NotImplementedError. Find the existing definition (probably empty / not yet present) and add/update:

```python
    # Plan 3 — default implementation. Subclasses with extra per-config gates
    # (claude channelEnabled, hermes gatewayUrl) override.
    def is_resident_ready(self, runtime_config: dict) -> bool:
        return self.supports_resident
```

If `is_resident_ready` was previously a stub raising NotImplementedError, replace it with the default above.

- [ ] **Step 4: Run test to verify pass**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/test_console_command.py -v`
Expected: 14/14 pass.

- [ ] **Step 5: Run base contract tests for regression**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/test_base.py -v`
Expected: PASS. The `test_plan_3_methods_raise_not_implemented` test in test_base.py may fail now that `is_resident_ready` doesn't raise — update that assertion inline to test the default behavior instead:

Find the test in `service/tests/runtimes/test_base.py` and update:

```python
def test_plan_3_methods_raise_not_implemented():
    a = _TestAdapter()
    with pytest.raises(NotImplementedError):
        _ = a.wrapper_name
    with pytest.raises(NotImplementedError):
        a.console_command(agent_id="x", handle="", interactive=True)
    # is_resident_ready is now a concrete default (Plan 3), not a stub.
    # Default impl returns self.supports_resident.
    assert a.is_resident_ready({}) is True  # _TestAdapter.supports_resident=True
```

- [ ] **Step 6: Commit**

```bash
cd C:/Docker/aify-comms
git add service/runtimes/base.py service/runtimes/claude.py service/runtimes/codex.py service/runtimes/hermes.py service/runtimes/pi.py service/runtimes/opencode.py service/tests/runtimes/test_console_command.py service/tests/runtimes/test_base.py
git commit -m "feat(runtimes/py): Plan 3 — wrapper_name + console_command on all adapters"
```

---

## Task 2: Python is_resident_ready per-adapter

**Files:**
- Create: `service/tests/runtimes/test_is_resident_ready.py`

(The actual method overrides happened in Task 1 — claude's channelEnabled check, hermes's gatewayUrl check. This task pins them with explicit tests.)

- [ ] **Step 1: Write failing tests**

Create `service/tests/runtimes/test_is_resident_ready.py`:

```python
"""Per-adapter is_resident_ready assertions. Closes #120 — claude resident
requires runtime_config.channelEnabled=True before advertising resident-run.
"""


def test_claude_is_resident_ready_requires_channel_enabled():
    from service.runtimes.claude import ClaudeAdapter
    a = ClaudeAdapter()
    assert a.is_resident_ready({"channelEnabled": True}) is True
    assert a.is_resident_ready({"channelEnabled": False}) is False
    assert a.is_resident_ready({"channelEnabled": "true"}) is False  # strict identity check
    assert a.is_resident_ready({}) is False
    assert a.is_resident_ready(None) is False


def test_codex_is_resident_ready_always_true():
    from service.runtimes.codex import CodexAdapter
    a = CodexAdapter()
    # Codex supports_resident=True, no per-config gate; inherits default.
    assert a.is_resident_ready({}) is True
    assert a.is_resident_ready({"anything": "else"}) is True
    # None is the only safety boundary
    # (base class default reads self.supports_resident regardless of config)
    assert a.is_resident_ready(None) is True


def test_hermes_is_resident_ready_requires_valid_gateway_url():
    from service.runtimes.hermes import HermesAdapter
    a = HermesAdapter()
    assert a.is_resident_ready({"gatewayUrl": "ws://127.0.0.1:9999/api/ws"}) is True
    assert a.is_resident_ready({"gatewayUrl": "wss://example.com/api/ws?token=x"}) is True
    assert a.is_resident_ready({"gatewayUrl": "http://nope"}) is False
    assert a.is_resident_ready({"gatewayUrl": "${AIFY_HERMES_GATEWAY_URL}"}) is False  # unresolved placeholder
    assert a.is_resident_ready({"gatewayUrl": ""}) is False
    assert a.is_resident_ready({}) is False
    assert a.is_resident_ready(None) is False


def test_pi_is_resident_ready_always_false():
    from service.runtimes.pi import PiAdapter
    a = PiAdapter()
    # PiAdapter.supports_resident == False — default returns False regardless.
    assert a.is_resident_ready({}) is False
    assert a.is_resident_ready({"channelEnabled": True}) is False  # no override; still False


def test_opencode_is_resident_ready_always_false():
    from service.runtimes.opencode import OpencodeAdapter
    a = OpencodeAdapter()
    assert a.is_resident_ready({}) is False
```

- [ ] **Step 2: Run to verify pass**

(All overrides landed in Task 1; this task just adds explicit pins.)

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/test_is_resident_ready.py -v`
Expected: 5/5 pass.

- [ ] **Step 3: Commit**

```bash
cd C:/Docker/aify-comms
git add service/tests/runtimes/test_is_resident_ready.py
git commit -m "test(runtimes/py): pin is_resident_ready per-adapter behavior"
```

---

## Task 3: Migrate `_default_console_command` to adapter

**Files:**
- Modify: `service/routers/api_v2.py` (function around line 6911)
- Existing test pin: `service/tests/test_console_command_resume.py` (Plan 1's 8-test suite)

- [ ] **Step 1: Read current implementation**

Run: `cd C:/Docker/aify-comms && sed -n '6900,6985p' service/routers/api_v2.py`

Verify the per-runtime tail (claude-code / codex / hermes / pi / opencode branches).

- [ ] **Step 2: Replace function body with adapter call**

Edit `service/routers/api_v2.py`. Find `def _default_console_command(session, workspace, *, interactive=False):` and replace its body. The new shape:

```python
def _default_console_command(session, workspace: str, *, interactive: bool = False) -> str:
    """Build the dashboard Console launch command for an agent session.

    Plan 3 (2026-05-25): per-runtime tail collapses to
    `adapter.console_command(...)`. The adapter owns the per-runtime quirks
    (claude interactive stays fresh, codex always resumes, pi interactive
    avoids the 026H trap, opencode is plain CLI).
    """
    from service.runtimes import adapter_for

    agent_id = str(session["agent_id"] or "").strip()
    handle = str(session["session_handle"] or "").strip()
    runtime = _normalize_runtime(session["runtime"] or "")

    try:
        adapter = adapter_for(runtime)
    except ValueError:
        # Unknown runtime — fall back to a generic invocation
        return f"{runtime or 'agent'} --aify-agent {agent_id}"

    return adapter.console_command(
        agent_id=agent_id,
        handle=handle,
        interactive=interactive,
    )
```

- [ ] **Step 3: Run existing console-command tests**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_console_command_resume.py -v`
Expected: 8/8 pass — Plan 1's regression suite stays green because adapter output matches the prior per-runtime tail.

- [ ] **Step 4: Run broader regression suite**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_api_v2_regressions.py -v -k "console" 2>&1 | tail -20`
Expected: PASS. If any console-related regression test pins a SPECIFIC string that differs from `adapter.console_command`'s output (e.g. argument order), update inline.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add service/routers/api_v2.py
# include test_api_v2_regressions.py if you had to update assertions
git commit -m "feat(server): _default_console_command collapses to adapter.console_command"
```

---

## Task 4: Migrate `_default_capabilities_for` — closes #120

**Files:**
- Modify: `service/routers/api_v2.py` (function around line 831)
- Create: `service/tests/test_resident_gate_restored.py`

- [ ] **Step 1: Write failing regression test**

Create `service/tests/test_resident_gate_restored.py`:

```python
"""Plan 3 (2026-05-25) — closes #120. Restores the per-config resident gate
that Plan 2 Task 14 dropped. Claude resident agents must have
runtime_config.channelEnabled=True before advertising `resident-run`;
hermes resident agents must have a valid gatewayUrl.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.routers.api_v2 import _default_capabilities_for


def test_claude_resident_without_channel_enabled_does_not_advertise_resident_run():
    caps = _default_capabilities_for("claude-code", "resident", "session-x", {})
    assert "resident-run" not in caps, (
        f"claude resident without channelEnabled must not advertise resident-run (#120). caps={caps}"
    )


def test_claude_resident_with_channel_enabled_advertises_resident_run():
    caps = _default_capabilities_for("claude-code", "resident", "session-x", {"channelEnabled": True})
    assert "resident-run" in caps, f"expected resident-run; caps={caps}"


def test_hermes_resident_without_gateway_url_does_not_advertise_resident_run():
    caps = _default_capabilities_for("hermes", "resident", "session-y", {})
    assert "resident-run" not in caps


def test_hermes_resident_with_gateway_url_advertises_resident_run():
    caps = _default_capabilities_for(
        "hermes", "resident", "session-y",
        {"gatewayUrl": "ws://127.0.0.1:9999/api/ws?token=x"},
    )
    assert "resident-run" in caps


def test_codex_resident_always_advertises_resident_run():
    # No per-config gate for codex; supports_resident=True is sufficient.
    caps = _default_capabilities_for("codex", "resident", "session-z", {})
    assert "resident-run" in caps


def test_pi_resident_never_advertises_resident_run():
    # PiAdapter.supports_resident=False; capabilities should reflect that
    # regardless of session_mode or config.
    caps = _default_capabilities_for("pi", "resident", "session-q", {})
    assert "resident-run" not in caps
```

- [ ] **Step 2: Run to verify failure**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_resident_gate_restored.py -v`
Expected: At least 2 failures — `test_claude_resident_without_channel_enabled_does_not_advertise_resident_run` (Plan 2 currently advertises resident-run for any claude resident with `supports_resident=True`) and maybe the hermes empty case.

- [ ] **Step 3: Update `_default_capabilities_for`**

Find the function in `service/routers/api_v2.py` (around line 831). Plan 2's version has an inline hermes `gatewayUrl` check. Replace the resident-gating branch with `adapter.is_resident_ready(runtime_config)`:

```python
def _default_capabilities_for(
    runtime: str,
    session_mode: str,
    session_handle: str,
    runtime_config: dict[str, Any],
) -> list[str]:
    """Build the default capability list for an agent registration.

    Plan 3 (2026-05-25): resident gating routes through adapter.is_resident_ready()
    which closes the #120 regression — claude resident needs channelEnabled,
    hermes resident needs a valid gatewayUrl, both rolled into the adapter.
    """
    from service.runtimes import adapter_for

    runtime_n = _normalize_runtime(runtime or "")
    try:
        adapter = adapter_for(runtime_n)
    except ValueError:
        return []

    caps: list[str] = []
    session_mode_n = _normalize_session_mode(session_mode or "")

    if session_mode_n == "resident":
        # Plan 3: adapter.is_resident_ready() encapsulates per-runtime,
        # per-config gating (channelEnabled for claude, gatewayUrl for hermes).
        if adapter.supports_resident and adapter.is_resident_ready(runtime_config or {}):
            caps.append("resident-run")
    else:
        if adapter.supports_managed:
            caps.append("managed-run")

    if adapter.supports_resident or adapter.supports_managed:
        caps.append("resume")
    if adapter.supports_interrupt:
        caps.append("interrupt")
    if adapter.supports_steering:
        caps.append("steer")

    if session_mode_n != "resident" and adapter.supports_managed:
        caps.append("spawn")

    return caps
```

- [ ] **Step 4: Run to verify pass**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_resident_gate_restored.py service/tests/test_default_capabilities_adapter.py -v`
Expected: ALL pass — Plan 2's `test_default_capabilities_adapter.py` regression tests should also stay green because the adapter delegation doesn't change capability shape for non-gated runtimes.

If any Plan 2 test now fails because it expects `resident-run` for claude with empty `runtime_config`, that's the #120 regression resurfacing — UPDATE the Plan 2 test to pass `channelEnabled=True` in its registration setup.

- [ ] **Step 5: Run broader regression suite**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_api_v2_regressions.py -v 2>&1 | tail -10`
Expected: 229+ pass. Update any test inline that asserts claude resident gets resident-run without channelEnabled.

- [ ] **Step 6: Commit**

```bash
cd C:/Docker/aify-comms
git add service/routers/api_v2.py service/tests/test_resident_gate_restored.py
# include any updated existing tests
git commit -m "fix(server): _default_capabilities_for uses adapter.is_resident_ready (#120)"
```

---

## Task 5: BaseController abstract class + contract tests

**Files:**
- Create: `mcp/stdio/controllers/base-controller.js`
- Create: `mcp/stdio/tests/controllers/base-controller.test.js`

- [ ] **Step 1: Write failing contract test**

Create `mcp/stdio/tests/controllers/base-controller.test.js`:

```javascript
import assert from "assert";
import test from "node:test";
import { BaseController } from "../../controllers/base-controller.js";

test("BaseController abstract methods throw on direct instantiation", async () => {
  const c = new BaseController({ agentId: "x" });
  await assert.rejects(() => c.start({}), /abstract/);
  await assert.rejects(() => c.injectMessage({}), /abstract/);
  await assert.rejects(() => c.interrupt({}), /abstract/);
  await assert.rejects(() => c.steer({}), /abstract/);
});

test("BaseController preserves opts on instance", () => {
  const c = new BaseController({ agentId: "x", runtime: "test" });
  assert.deepStrictEqual(c.opts, { agentId: "x", runtime: "test" });
});

test("BaseController terminalSink defaults to null", () => {
  const c = new BaseController({ agentId: "x" });
  assert.strictEqual(c.terminalSink, null);
});

test("BaseController subclass can override start", async () => {
  class TestController extends BaseController {
    async start(ctx) { return { ok: true, ctx }; }
  }
  const c = new TestController({ agentId: "x" });
  const result = await c.start({ runId: "r" });
  assert.deepStrictEqual(result, { ok: true, ctx: { runId: "r" } });
});
```

- [ ] **Step 2: Run to verify fail**

Run: `cd C:/Docker/aify-comms && mkdir -p mcp/stdio/controllers && node --test mcp/stdio/tests/controllers/base-controller.test.js`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `mcp/stdio/controllers/base-controller.js`**

```javascript
// Abstract base for per-runtime controllers extracted from runtimes.js as
// part of Plan 3. Each controller owns one runtime's delivery + lifecycle:
// start/interrupt/steer/injectMessage, plus an optional terminalSink for
// synth-terminal stream consumers.
//
// Per the 500-line file rule, each subclass lives in its own file under
// mcp/stdio/controllers/ and targets ≤400 lines.

export class BaseController {
  constructor(opts) {
    this.opts = opts || {};
  }

  // Lifecycle — begin work, returns a promise that resolves on turn-completed
  async start(_ctx) {
    throw new Error("BaseController.start is abstract — subclass must override");
  }

  // Delivery — inject a message into the live session (resident) or
  // forward it to the wrapper PTY (managed). Returns when message accepted.
  async injectMessage(_opts) {
    throw new Error("BaseController.injectMessage is abstract — subclass must override");
  }

  // Cancel the active turn. Returns immediately; final state arrives via
  // turn-completed callback.
  async interrupt(_opts) {
    throw new Error("BaseController.interrupt is abstract — subclass must override");
  }

  // Mid-turn append. Some runtimes (codex turn/steer, hermes session.steer)
  // support this; others don't (subclass throws or returns rejected promise).
  async steer(_opts) {
    throw new Error("BaseController.steer is abstract — subclass must override");
  }

  // Optional synth-terminal frame source. Subclasses with a terminal stream
  // (pi-session, codex remote, hermes gateway) return an EventEmitter-like
  // object; subclasses without one return null.
  get terminalSink() {
    return null;
  }
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/controllers/base-controller.test.js`
Expected: PASS — 4/4 green.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/controllers/base-controller.js mcp/stdio/tests/controllers/base-controller.test.js
git commit -m "feat(controllers): BaseController abstract class for Plan 3 extraction"
```

---

## Task 6: JS adapters — controllerFor + delegate methods

**Files:**
- Modify: `mcp/stdio/adapters/{claude,codex,hermes,pi,opencode}.js`
- Create: `mcp/stdio/tests/adapters/controller-for.test.js`

(Per-runtime controller files come in Tasks 7-11. This task wires the adapter contract.)

- [ ] **Step 1: Write failing test**

Create `mcp/stdio/tests/adapters/controller-for.test.js`:

```javascript
import assert from "assert";
import test from "node:test";

import { ClaudeAdapter } from "../../adapters/claude.js";
import { CodexAdapter } from "../../adapters/codex.js";
import { HermesAdapter } from "../../adapters/hermes.js";
import { PiAdapter } from "../../adapters/pi.js";
import { OpencodeAdapter } from "../../adapters/opencode.js";

// These tests pin the controllerFor contract surface. The actual controllers
// are extracted in Tasks 7-11 — until then, controllerFor returns null for
// "not yet extracted" runtimes. After Task 12 lands, this should return
// concrete controller instances per runtime.

test("ClaudeAdapter exposes controllerFor", () => {
  const a = new ClaudeAdapter();
  assert.strictEqual(typeof a.controllerFor, "function");
});

test("CodexAdapter exposes controllerFor", () => {
  const a = new CodexAdapter();
  assert.strictEqual(typeof a.controllerFor, "function");
});

test("HermesAdapter exposes controllerFor", () => {
  const a = new HermesAdapter();
  assert.strictEqual(typeof a.controllerFor, "function");
});

test("PiAdapter exposes controllerFor", () => {
  const a = new PiAdapter();
  assert.strictEqual(typeof a.controllerFor, "function");
});

test("OpencodeAdapter exposes controllerFor", () => {
  const a = new OpencodeAdapter();
  assert.strictEqual(typeof a.controllerFor, "function");
});

test("Pi resident mode returns null (Plan 2 flip)", () => {
  // PiAdapter.supports_resident=false — controllerFor for resident mode
  // must return null (or undefined) so launchRuntimeRun rejects.
  const a = new PiAdapter();
  const c = a.controllerFor({ runtime: "pi", executionMode: "resident", agentInfo: {}, run: {}, runtimeState: {}, callbacks: {} });
  assert.ok(c === null || c === undefined, `pi resident must return null/undefined; got ${c}`);
});
```

- [ ] **Step 2: Run to verify fail**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/controller-for.test.js`
Expected: FAIL — `controllerFor is not a function` because base class's `controllerFor` throws "abstract" and subclasses haven't overridden yet.

- [ ] **Step 3: Add `controllerFor` + delegate methods on the base class**

Edit `mcp/stdio/adapters/base.js`. Find the Plan 2/3 stub section and update:

```javascript
  // Plan 3 — controllerFor returns the runtime's controller instance for
  // the given dispatch opts, or null when the mode isn't supported.
  // Subclasses override; default raises so unimplemented adapters fail loudly.
  controllerFor(_opts) {
    throw new Error(`controllerFor is abstract — ${this.name} adapter must override`);
  }

  // Plan 3 — delegate methods route through whichever controller controllerFor returns.
  async injectMessage(opts) {
    const c = this.controllerFor(opts);
    if (!c) throw new Error(`No controller for runtime=${this.name} executionMode=${opts?.executionMode}`);
    return c.injectMessage(opts);
  }

  async interrupt(opts) {
    const c = this.controllerFor(opts);
    if (!c) return;
    return c.interrupt(opts);
  }

  async steer(opts) {
    const c = this.controllerFor(opts);
    if (!c) throw new Error(`Steering not available for runtime=${this.name}`);
    return c.steer(opts);
  }
```

- [ ] **Step 4: Add per-adapter controllerFor overrides (stub form — concrete in Tasks 7-11)**

Each per-runtime adapter adds a `controllerFor` that initially routes nothing (returns null) — Tasks 7-11 fill in actual controllers as they're extracted.

For each of `mcp/stdio/adapters/claude.js`, `codex.js`, `hermes.js`, `pi.js`, `opencode.js`, append BEFORE the closing `}`:

```javascript
  controllerFor(_opts) {
    // Concrete controller wired up in Plan 3 Task 7-11.
    return null;
  }
```

EXCEPT pi's controllerFor must explicitly return null for resident mode (per Plan 2 flip) — but since the stub returns null for everything, that's automatic. Tasks 7-11 will refine.

- [ ] **Step 5: Run to verify pass**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/controller-for.test.js`
Expected: 6/6 pass.

- [ ] **Step 6: Run the full adapter test directory**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/*.test.js`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/adapters/base.js mcp/stdio/adapters/claude.js mcp/stdio/adapters/codex.js mcp/stdio/adapters/hermes.js mcp/stdio/adapters/pi.js mcp/stdio/adapters/opencode.js mcp/stdio/tests/adapters/controller-for.test.js
git commit -m "feat(adapters): Plan 3 controllerFor contract + null-returning stubs"
```

---

## Task 7: Extract OpencodeController

**Files:**
- Create: `mcp/stdio/controllers/opencode-controller.js`
- Create: `mcp/stdio/tests/controllers/opencode-controller.test.js`
- Modify: `mcp/stdio/adapters/opencode.js` (wire controllerFor)
- Modify: `mcp/stdio/runtimes.js` (remove `createOpenCodeController`)

(OpencodeController is the smallest — opencode runs as a plain CLI subprocess without a wrapper. Best first extraction to prove the pattern.)

- [ ] **Step 1: Read the existing `createOpenCodeController`**

Run: `cd C:/Docker/aify-comms && sed -n '2759,2944p' mcp/stdio/runtimes.js`

This is the function body to extract. Read it fully — note its dependencies (imports from runtimes.js: `spawnProcess`, `runtimeChildEnv`, `buildSystemPrompt`, `buildUserPrompt`, etc.). Those must be re-imported in the new controller file.

- [ ] **Step 2: Write a smoke test (failing because file doesn't exist)**

Create `mcp/stdio/tests/controllers/opencode-controller.test.js`:

```javascript
import assert from "assert";
import test from "node:test";
import { OpencodeController } from "../../controllers/opencode-controller.js";
import { BaseController } from "../../controllers/base-controller.js";

test("OpencodeController extends BaseController", () => {
  const c = new OpencodeController({ agentId: "x", agentInfo: {}, run: {}, runtimeState: {}, callbacks: {} });
  assert.ok(c instanceof BaseController, "OpencodeController must extend BaseController");
});

test("OpencodeController exposes start/injectMessage/interrupt/steer", () => {
  const c = new OpencodeController({ agentId: "x", agentInfo: {}, run: {}, runtimeState: {}, callbacks: {} });
  assert.strictEqual(typeof c.start, "function");
  assert.strictEqual(typeof c.injectMessage, "function");
  assert.strictEqual(typeof c.interrupt, "function");
  assert.strictEqual(typeof c.steer, "function");
});
```

- [ ] **Step 3: Run to verify failure**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/controllers/opencode-controller.test.js`
Expected: FAIL — module not found.

- [ ] **Step 4: Create `mcp/stdio/controllers/opencode-controller.js`**

Extract the `createOpenCodeController` body from `runtimes.js` (lines ~2759-2944). Convert from factory function to class form:

- The factory returned an object with `{start, interrupt, steer, terminalSink, ...}` methods. The class implements those as instance methods.
- Imports it uses (`spawnProcess`, `runtimeChildEnv`, `opencodePermissionConfig`, `buildSystemPrompt`, `buildUserPrompt`, etc.) get re-imported from `../runtimes.js`.
- Preserve ALL behavior verbatim — same env, same args, same callback handling.

Skeleton:

```javascript
// OpencodeController — extracted from createOpenCodeController in runtimes.js
// as part of Plan 3. Owns the opencode runtime's spawn/lifecycle/delivery.
// File budget per 500-line rule: ≤400 lines.

import { BaseController } from "./base-controller.js";
import {
  spawnProcess,
  runtimeChildEnv,
  opencodePermissionConfig,
  buildSystemPrompt,
  buildUserPrompt,
  // ... other helpers that were used inside createOpenCodeController ...
} from "../runtimes.js";

export class OpencodeController extends BaseController {
  constructor(opts) {
    super(opts);
    // Initialize instance state previously in the factory closure.
  }

  async start(ctx) {
    // Body translated from createOpenCodeController's returned start() function.
    // Same spawn args, same callback wiring, same error handling.
  }

  async injectMessage(opts) {
    // Opencode doesn't support live injection — it's a one-shot CLI invocation.
    // Returning a rejected promise here matches the prior createOpenCodeController
    // behavior (no injection path existed).
    throw new Error("opencode does not support mid-session message injection");
  }

  async interrupt(opts) {
    // Body translated from the factory's interrupt() function.
  }

  async steer(opts) {
    throw new Error("opencode does not support steering");
  }
}
```

**Translation guidance:**
- Variables that were factory-local become `this.X` instance properties.
- `start`/`interrupt` close over `opts` in the factory; in the class, `this.opts` holds the same data.
- Test by running the existing `mcp/stdio/tests/opencode-*.test.js` files (if any) — they must continue to pass.

- [ ] **Step 5: Wire `OpencodeAdapter.controllerFor` to return the new class**

Edit `mcp/stdio/adapters/opencode.js`. Update the `controllerFor` override:

```javascript
import { OpencodeController } from "../controllers/opencode-controller.js";

// ... existing class ...

  controllerFor(opts) {
    // Opencode only supports managed dispatch today.
    if (opts?.executionMode === "managed") {
      return new OpencodeController(opts);
    }
    return null;
  }
```

- [ ] **Step 6: Update `launchRuntimeRun` to use the new adapter path for opencode only**

In `mcp/stdio/runtimes.js`, find the `launchRuntimeRun` switch and update the opencode branch from `createOpenCodeController(...)` to `adapterFor("opencode").controllerFor({...})`. Other runtimes still use their existing inline factories (extracted in Tasks 8-11).

Then remove the now-unreferenced `createOpenCodeController` function body from `runtimes.js`.

- [ ] **Step 7: Run controller tests + existing opencode tests**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/controllers/opencode-controller.test.js mcp/stdio/tests/opencode-*.test.js 2>&1 | tail -15` (skip if no opencode tests exist).

Per memory ([feedback-opencode-skip]): the operator's local opencode hits a GPU-tanking LLM. DO NOT spin up opencode for testing. Skip live e2e here; the unit tests + smoke imports are sufficient.

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/controllers/opencode-controller.js mcp/stdio/tests/controllers/opencode-controller.test.js mcp/stdio/adapters/opencode.js mcp/stdio/runtimes.js
git commit -m "feat(controllers): extract OpencodeController from runtimes.js"
```

If the OpencodeController file exceeds 400 lines, STOP and report — the controller probably needs splitting along an internal seam (e.g., spawn-and-wait vs streaming-loop). The plan's controller file budget is ≤400 lines per the 500-line rule.

---

## Task 8: Extract PiController

**Files:**
- Create: `mcp/stdio/controllers/pi-controller.js`
- Create: `mcp/stdio/tests/controllers/pi-controller.test.js`
- Modify: `mcp/stdio/adapters/pi.js` (wire controllerFor)
- Modify: `mcp/stdio/runtimes.js` (remove `createPiController`, `createPiControllerManaged`, `createPiControllerLegacy`)

Per Plan 2 pi flip, only `createPiControllerManaged` is reachable. `createPiControllerLegacy` is dead code that Plan 2 left behind. This task extracts the managed path and deletes the legacy.

- [ ] **Step 1: Read the existing pi controller factories**

Run: `cd C:/Docker/aify-comms && sed -n '2944,3550p' mcp/stdio/runtimes.js | head -200`

Then: `cd C:/Docker/aify-comms && sed -n '3372,3550p' mcp/stdio/runtimes.js | head -200`

Understand what `createPiControllerManaged` (line ~3372) does and how `createPiController` (line ~3448) routes by executionMode.

- [ ] **Step 2: Smoke test**

Create `mcp/stdio/tests/controllers/pi-controller.test.js`:

```javascript
import assert from "assert";
import test from "node:test";
import { PiController } from "../../controllers/pi-controller.js";
import { BaseController } from "../../controllers/base-controller.js";

test("PiController extends BaseController", () => {
  const c = new PiController({ agentId: "x", agentInfo: { agent_id: "x", runtime: "pi" }, run: {}, runtimeState: {}, callbacks: {}, executionMode: "managed" });
  assert.ok(c instanceof BaseController);
});

test("PiController exposes start/injectMessage/interrupt/steer", () => {
  const c = new PiController({ agentId: "x", agentInfo: { agent_id: "x", runtime: "pi" }, run: {}, runtimeState: {}, callbacks: {}, executionMode: "managed" });
  assert.strictEqual(typeof c.start, "function");
  assert.strictEqual(typeof c.injectMessage, "function");
  assert.strictEqual(typeof c.interrupt, "function");
  assert.strictEqual(typeof c.steer, "function");
});
```

- [ ] **Step 3: Run to verify fail**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/controllers/pi-controller.test.js`
Expected: FAIL.

- [ ] **Step 4: Create `mcp/stdio/controllers/pi-controller.js`**

Extract `createPiControllerManaged` (lines ~3372-3448) into a class. Follow the same translation pattern as Task 7. Drop `createPiControllerLegacy` — Plan 2 removed pi resident, the legacy path is dead.

File budget ≤400 lines.

- [ ] **Step 5: Wire `PiAdapter.controllerFor`**

```javascript
import { PiController } from "../controllers/pi-controller.js";

// ... existing class ...

  controllerFor(opts) {
    // Plan 2 pi flip: resident pi is no longer supported — return null
    // so launchRuntimeRun rejects with a clear error.
    if (opts?.executionMode === "resident") return null;
    return new PiController(opts);
  }
```

- [ ] **Step 6: Update `launchRuntimeRun` and remove dead pi factories**

In `runtimes.js`:
- Switch pi branch to `adapterFor("pi").controllerFor(opts)`.
- Delete `createPiControllerManaged`, `createPiController`, `createPiControllerLegacy` function bodies (all three are no longer referenced).

- [ ] **Step 7: Run tests**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/controllers/pi-controller.test.js mcp/stdio/tests/pi-runtime.test.js mcp/stdio/tests/pi-session-terminal.test.js 2>&1 | tail -20`
Expected: PASS. If a pi-runtime test fails because it called `createPiController` directly, update inline to use `adapterFor("pi").controllerFor(...)`.

- [ ] **Step 8: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/controllers/pi-controller.js mcp/stdio/tests/controllers/pi-controller.test.js mcp/stdio/adapters/pi.js mcp/stdio/runtimes.js
# include any existing test files updated
git commit -m "feat(controllers): extract PiController from runtimes.js (Plan 2 flip — managed only)"
```

---

## Task 9: Extract ClaudeController

**Files:**
- Create: `mcp/stdio/controllers/claude-controller.js`
- Create: `mcp/stdio/tests/controllers/claude-controller.test.js`
- Modify: `mcp/stdio/adapters/claude.js`
- Modify: `mcp/stdio/runtimes.js` (remove `createClaudeController`)

- [ ] **Step 1: Read the existing factory**

Run: `cd C:/Docker/aify-comms && sed -n '2045,2095p' mcp/stdio/runtimes.js`

ClaudeController is the SMALLEST of the three resident-capable runtimes — most of its work lives in claude-channel.js (the sidecar). The controller is mostly a thin orchestrator around the channel-notify path + managed wrapper PTY.

- [ ] **Step 2: Smoke test**

Create `mcp/stdio/tests/controllers/claude-controller.test.js`:

```javascript
import assert from "assert";
import test from "node:test";
import { ClaudeController } from "../../controllers/claude-controller.js";
import { BaseController } from "../../controllers/base-controller.js";

test("ClaudeController extends BaseController", () => {
  const c = new ClaudeController({ agentId: "x", agentInfo: { agent_id: "x", runtime: "claude-code" }, run: {}, runtimeState: {}, callbacks: {}, executionMode: "channel" });
  assert.ok(c instanceof BaseController);
});

test("ClaudeController exposes start/injectMessage/interrupt/steer", () => {
  const c = new ClaudeController({ agentId: "x", agentInfo: { agent_id: "x", runtime: "claude-code" }, run: {}, runtimeState: {}, callbacks: {}, executionMode: "channel" });
  assert.strictEqual(typeof c.start, "function");
  assert.strictEqual(typeof c.injectMessage, "function");
  assert.strictEqual(typeof c.interrupt, "function");
  assert.strictEqual(typeof c.steer, "function");
});
```

- [ ] **Step 3: Run to verify fail**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/controllers/claude-controller.test.js`
Expected: FAIL.

- [ ] **Step 4: Create `mcp/stdio/controllers/claude-controller.js`**

Extract `createClaudeController` (lines ~2045-2095). Keep ≤400 lines.

- [ ] **Step 5: Wire `ClaudeAdapter.controllerFor`**

```javascript
import { ClaudeController } from "../controllers/claude-controller.js";

  controllerFor(opts) {
    return new ClaudeController(opts);
  }
```

- [ ] **Step 6: Update `launchRuntimeRun` and remove `createClaudeController`**

- [ ] **Step 7: Run tests**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/controllers/claude-controller.test.js mcp/stdio/tests/claude-channel-marker-binding.test.js mcp/stdio/tests/claude-session-in-use.test.js mcp/stdio/tests/claude-print-disabled.test.js 2>&1 | tail -20`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/controllers/claude-controller.js mcp/stdio/tests/controllers/claude-controller.test.js mcp/stdio/adapters/claude.js mcp/stdio/runtimes.js
git commit -m "feat(controllers): extract ClaudeController from runtimes.js"
```

---

## Task 10: Extract HermesController

**Files:**
- Create: `mcp/stdio/controllers/hermes-controller.js`
- Create: `mcp/stdio/tests/controllers/hermes-controller.test.js`
- Modify: `mcp/stdio/adapters/hermes.js`
- Modify: `mcp/stdio/runtimes.js` (remove all `createHermes*` functions)

HermesController bundles 5 factories from runtimes.js:
- `createHermesController` (line ~3560)
- `createHermesResidentChannelController` (line ~3599) — resident gateway WS
- `createHermesControllerManaged` (line ~3821) — managed wrapper PTY
- `createHermesControllerManagedGateway` (line ~3866) — opt-in managed gateway
- `createHermesControllerSingleShot` (line ~3904) — legacy single-shot

Spans ~350 lines total. Extract all five into one HermesController class with mode-dispatch internally, OR split into per-mode subclasses if total exceeds 400 lines.

- [ ] **Step 1: Read the existing factories**

Run: `cd C:/Docker/aify-comms && sed -n '3560,3910p' mcp/stdio/runtimes.js | head -200`

Estimate total LOC. If extracting all five into one class would exceed 400 lines, plan to split into `hermes-controller.js` (factory/dispatcher) + `hermes-resident-controller.js` + `hermes-managed-controller.js` at most.

- [ ] **Step 2: Smoke test**

Create `mcp/stdio/tests/controllers/hermes-controller.test.js`:

```javascript
import assert from "assert";
import test from "node:test";
import { HermesController } from "../../controllers/hermes-controller.js";
import { BaseController } from "../../controllers/base-controller.js";

test("HermesController extends BaseController", () => {
  const c = new HermesController({ agentId: "x", agentInfo: { agent_id: "x", runtime: "hermes" }, run: {}, runtimeState: {}, callbacks: {}, executionMode: "managed" });
  assert.ok(c instanceof BaseController);
});

test("HermesController exposes start/injectMessage/interrupt/steer", () => {
  const c = new HermesController({ agentId: "x", agentInfo: { agent_id: "x", runtime: "hermes" }, run: {}, runtimeState: {}, callbacks: {}, executionMode: "managed" });
  assert.strictEqual(typeof c.start, "function");
  assert.strictEqual(typeof c.injectMessage, "function");
  assert.strictEqual(typeof c.interrupt, "function");
  assert.strictEqual(typeof c.steer, "function");
});
```

- [ ] **Step 3: Run to verify fail**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/controllers/hermes-controller.test.js`
Expected: FAIL.

- [ ] **Step 4: Create `mcp/stdio/controllers/hermes-controller.js`**

Translate all 5 hermes factories into a class. Internal mode dispatch:

```javascript
export class HermesController extends BaseController {
  constructor(opts) {
    super(opts);
    this._modeImpl = this._pickModeImpl(opts);
  }

  _pickModeImpl(opts) {
    if (opts.executionMode === "channel") {
      // resident gateway WS (createHermesResidentChannelController)
    } else if (opts.executionMode === "managed" && managedHermesUsesGateway(...)) {
      // managed-gateway opt-in (createHermesControllerManagedGateway)
    } else if (opts.executionMode === "managed") {
      // managed wrapper PTY (createHermesControllerManaged)
    } else {
      // single-shot legacy (createHermesControllerSingleShot)
    }
  }

  async start(ctx)             { return this._modeImpl.start(ctx); }
  async injectMessage(opts)    { return this._modeImpl.injectMessage(opts); }
  async interrupt(opts)        { return this._modeImpl.interrupt(opts); }
  async steer(opts)            { return this._modeImpl.steer(opts); }
  get terminalSink()           { return this._modeImpl.terminalSink; }
}
```

If the single file exceeds 400 lines, split into:
- `hermes-controller.js` — dispatcher + shared helpers
- `hermes-resident-controller.js` — channel/resident WS mode
- `hermes-managed-controller.js` — wrapper PTY + gateway-opt-in modes

- [ ] **Step 5: Wire `HermesAdapter.controllerFor`**

```javascript
import { HermesController } from "../controllers/hermes-controller.js";

  controllerFor(opts) {
    return new HermesController(opts);
  }
```

- [ ] **Step 6: Update `launchRuntimeRun` and remove hermes factories**

Remove from `runtimes.js`:
- `createHermesController`
- `createHermesResidentChannelController`
- `createHermesControllerManaged`
- `createHermesControllerManagedGateway`
- `createHermesControllerSingleShot`

Update `launchRuntimeRun` hermes branch to `adapterFor("hermes").controllerFor(opts)`.

- [ ] **Step 7: Run tests**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/controllers/hermes-controller.test.js mcp/stdio/tests/hermes-*.test.js 2>&1 | tail -20`
Expected: PASS. Update inline any hermes test that calls `createHermesController` directly.

- [ ] **Step 8: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/controllers/hermes-controller.js mcp/stdio/tests/controllers/hermes-controller.test.js mcp/stdio/adapters/hermes.js mcp/stdio/runtimes.js
# include hermes-{resident,managed}-controller.js if split
git commit -m "feat(controllers): extract HermesController from runtimes.js (5 factories collapsed)"
```

If the extraction reveals the controller is doing too much and needs further decomposition beyond resident+managed split, STOP and report — Plan 3 should not balloon.

---

## Task 11: Extract CodexController

**Files:**
- Create: `mcp/stdio/controllers/codex-controller.js`
- Create: `mcp/stdio/tests/controllers/codex-controller.test.js`
- Modify: `mcp/stdio/adapters/codex.js`
- Modify: `mcp/stdio/runtimes.js` (remove `createCodexController*`)

CodexController is the LARGEST extraction:
- `createCodexController` (line ~2056) — top-level dispatcher
- `createCodexControllerPooled` (line ~2098) — managed pooled controller
- `createCodexControllerLegacy` (line ~2141) — legacy resident path

Spans ~600 lines total. Almost certainly needs to split into multiple files to stay under the 400-line per-file budget.

- [ ] **Step 1: Read the existing factories**

Run: `cd C:/Docker/aify-comms && sed -n '2056,2700p' mcp/stdio/runtimes.js | wc -l`

Confirm the size. Plan ahead: if extracting all 3 into one file exceeds 400 lines, split into:
- `codex-controller.js` — dispatcher class (≤200 lines)
- `codex-resident-controller.js` — app-server WS resident path (≤400 lines)
- `codex-managed-controller.js` — pooled managed wrapper path (≤400 lines)

- [ ] **Step 2: Smoke test**

Create `mcp/stdio/tests/controllers/codex-controller.test.js`:

```javascript
import assert from "assert";
import test from "node:test";
import { CodexController } from "../../controllers/codex-controller.js";
import { BaseController } from "../../controllers/base-controller.js";

test("CodexController extends BaseController", () => {
  const c = new CodexController({ agentId: "x", agentInfo: { agent_id: "x", runtime: "codex" }, run: {}, runtimeState: {}, callbacks: {}, executionMode: "managed" });
  assert.ok(c instanceof BaseController);
});

test("CodexController exposes start/injectMessage/interrupt/steer", () => {
  const c = new CodexController({ agentId: "x", agentInfo: { agent_id: "x", runtime: "codex" }, run: {}, runtimeState: {}, callbacks: {}, executionMode: "managed" });
  assert.strictEqual(typeof c.start, "function");
  assert.strictEqual(typeof c.injectMessage, "function");
  assert.strictEqual(typeof c.interrupt, "function");
  assert.strictEqual(typeof c.steer, "function");
});
```

- [ ] **Step 3: Run to verify fail**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/controllers/codex-controller.test.js`
Expected: FAIL.

- [ ] **Step 4: Create the codex controller file(s)**

Translate the codex factories into class form. If splitting:

`mcp/stdio/controllers/codex-controller.js` (dispatcher):

```javascript
import { BaseController } from "./base-controller.js";
import { CodexResidentController } from "./codex-resident-controller.js";
import { CodexManagedController } from "./codex-managed-controller.js";

export class CodexController extends BaseController {
  constructor(opts) {
    super(opts);
    this._impl = this._pickImpl(opts);
  }

  _pickImpl(opts) {
    if (opts.executionMode === "resident") return new CodexResidentController(opts);
    return new CodexManagedController(opts);  // managed-via-wrapper or default
  }

  async start(ctx)          { return this._impl.start(ctx); }
  async injectMessage(opts) { return this._impl.injectMessage(opts); }
  async interrupt(opts)     { return this._impl.interrupt(opts); }
  async steer(opts)         { return this._impl.steer(opts); }
  get terminalSink()        { return this._impl.terminalSink; }
}
```

`codex-resident-controller.js` and `codex-managed-controller.js` contain the actual logic translated from the existing factories.

If a single `codex-controller.js` stays under 400 lines, fine to use one file. Plan ahead and split only if needed.

- [ ] **Step 5: Wire `CodexAdapter.controllerFor`**

```javascript
import { CodexController } from "../controllers/codex-controller.js";

  controllerFor(opts) {
    return new CodexController(opts);
  }
```

- [ ] **Step 6: Update `launchRuntimeRun` and remove codex factories**

Remove `createCodexController`, `createCodexControllerPooled`, `createCodexControllerLegacy` from `runtimes.js`. Update launchRuntimeRun codex branch.

- [ ] **Step 7: Run tests**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/controllers/codex-controller.test.js mcp/stdio/tests/codex-*.test.js 2>&1 | tail -25`
Expected: PASS. Codex has many tests (resident-dispatch, session, resume-failure, etc.). Update any test that calls `createCodexController` directly.

- [ ] **Step 8: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/controllers/codex-controller.js mcp/stdio/tests/controllers/codex-controller.test.js mcp/stdio/adapters/codex.js mcp/stdio/runtimes.js
# include codex-{resident,managed}-controller.js if split
git commit -m "feat(controllers): extract CodexController from runtimes.js (3 factories collapsed)"
```

---

## Task 12: launchRuntimeRun collapse to adapter dispatch

**Files:**
- Modify: `mcp/stdio/runtimes.js` (the `launchRuntimeRun` function)

By now, Tasks 7-11 have extracted all 5 runtime controllers and pointed their adapters at the new classes. `launchRuntimeRun`'s per-runtime if-branches should all just call `adapterFor(runtime).controllerFor(opts)`. This task verifies that and consolidates.

- [ ] **Step 1: Read current `launchRuntimeRun`**

Run: `cd C:/Docker/aify-comms && sed -n '3529,3560p' mcp/stdio/runtimes.js`

After Tasks 7-11, each branch should already be using the adapter form. Confirm that's the case.

- [ ] **Step 2: Collapse the if-branches**

Replace `launchRuntimeRun` with:

```javascript
export function launchRuntimeRun({ agentId, agentInfo, run, runtimeState, callbacks, managedViaWrapper = false }) {
  // Plan 3 (2026-05-25): per-runtime dispatch collapses to adapter.controllerFor.
  // The adapter owns the per-runtime controller selection — codex/hermes adapters
  // route by executionMode internally to their resident/managed implementations.
  const runtime = normalizeRuntime(agentInfo?.runtime || run?.runtime || "");
  let adapter;
  try {
    adapter = adapterFor(runtime);
  } catch {
    return failedRuntimeController(`Unknown runtime "${runtime}".`);
  }

  const opts = {
    agentId,
    agentInfo,
    run,
    runtimeState,
    callbacks,
    managedViaWrapper,
    executionMode: run?.executionMode || agentInfo?.session_mode || "managed",
  };

  const controller = adapter.controllerFor(opts);
  if (!controller) {
    return failedRuntimeController(
      `Runtime "${runtime}" does not support executionMode="${opts.executionMode}".`,
    );
  }
  return controller;
}
```

(`failedRuntimeController` is an existing helper that returns a rejected-promise wrapper — already in runtimes.js per Task 19 of Plan 2.)

- [ ] **Step 3: Run dispatch tests**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/dispatch-execution.test.js mcp/stdio/tests/dispatch-state.test.js mcp/stdio/tests/codex-resident-dispatch.test.js mcp/stdio/tests/hermes-resident-dispatch.test.js mcp/stdio/tests/managed-via-wrapper-routing.test.js 2>&1 | tail -25`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/runtimes.js
git commit -m "feat(bridge): launchRuntimeRun collapses to adapter.controllerFor"
```

---

## Task 13: runtimes.js cleanup — remove dead factories + pi-session-resume remnants

**Files:**
- Modify: `mcp/stdio/runtimes.js` — delete unused functions; target ≤350 lines

By Task 12, all `createXxxController*` functions have been moved to per-runtime controller files. This task deletes the dead code from runtimes.js + any remaining `pi-session-resume` references (Plan 2 task 19 may have left some).

- [ ] **Step 1: Inventory dead code**

Run: `cd C:/Docker/aify-comms && wc -l mcp/stdio/runtimes.js && grep -n "^function create\|pi-session-resume" mcp/stdio/runtimes.js | head -20`

Identify functions that are no longer called from anywhere. Run:

```bash
cd C:/Docker/aify-comms && for fn in createClaudeController createCodexController createCodexControllerPooled createCodexControllerLegacy createOpenCodeController createPiController createPiControllerManaged createPiControllerLegacy createHermesController createHermesResidentChannelController createHermesControllerManaged createHermesControllerManagedGateway createHermesControllerSingleShot createTerminalDeliveryController; do
  count=$(grep -rn "\\b$fn\\b" mcp/stdio service --include="*.js" --include="*.py" | grep -v "//" | wc -l)
  echo "$fn: $count refs"
done
```

Any function with 0 refs is dead. Any with >1 might still be referenced from a test — verify.

- [ ] **Step 2: Delete dead functions**

For each dead function, delete its body in `runtimes.js`. Keep imports/exports clean — `createTerminalDeliveryController` may still be needed by managed-via-wrapper routing; verify before deleting.

- [ ] **Step 3: Delete `pi-session-resume` references**

Run: `cd C:/Docker/aify-comms && grep -n "pi-session-resume" mcp/stdio/runtimes.js`
Delete any remaining references. The wake-mode shouldn't be in the codebase anymore.

- [ ] **Step 4: Verify file size**

Run: `cd C:/Docker/aify-comms && wc -l mcp/stdio/runtimes.js`
Expected: ≤350 lines. If higher, identify what's left that could move (long helper functions might belong in their own utility file).

- [ ] **Step 5: Run full bridge test suite**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/*.test.js mcp/stdio/tests/adapters/*.test.js mcp/stdio/tests/controllers/*.test.js 2>&1 | tail -30`
Expected: ALL pass. If anything breaks, the dead-code removal hit a still-used function — restore it and re-investigate.

- [ ] **Step 6: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/runtimes.js
git commit -m "refactor(bridge): runtimes.js cleanup — remove dead factories + pi-session-resume (~3700 → 350 lines)"
```

---

## Task 14: Docs + smoke + push

**Files:**
- Modify: `DECISIONS.md`, `README.md`

- [ ] **Step 1: Append to DECISIONS.md**

```markdown

## 2026-05-25 — Plan 3: Controllers + delivery migration

**Decision:** Complete the `RuntimeAdapter` foundation by adding the remaining Plan 3 methods (Python: `console_command`, `wrapper_name`, `is_resident_ready`; JS: `controllerFor` + delegate methods), extracting per-runtime controllers from `mcp/stdio/runtimes.js` into individual `mcp/stdio/controllers/<runtime>-controller.js` files, and migrating `_default_console_command`, `_default_capabilities_for`, and `launchRuntimeRun` to consume the adapter contract.

Closes #120 — restores the `channelEnabled` per-config resident gate that Plan 2 Task 14 simplification dropped. Claude resident agents now correctly require `runtime_config.channelEnabled=True` before advertising `resident-run`.

`mcp/stdio/runtimes.js` shrinks from ~4100 lines to ≤350 lines. Per-runtime controller code now lives in dedicated ≤400-line files. Adding a 6th runtime in the future becomes "write one adapter + one controller file."

**Why:** Operator-stated 500-line file rule + clean-architecture-always preference. `runtimes.js` was the worst monolith in the codebase; this plan resolves it. Adapter consumers (`_default_console_command` etc.) had per-runtime if-branches that grew quadratically with each new feature; Plan 3 collapses them to one rule.

**See:** `docs/superpowers/specs/2026-05-25-runtime-adapter-plan3-controllers-and-delivery-design.md`, `docs/superpowers/plans/2026-05-25-plan3-controllers-and-delivery.md`.
```

- [ ] **Step 2: Update README.md repo layout**

Add `mcp/stdio/controllers/` row. Match the existing format (table or list). Example:

```markdown
| `mcp/stdio/controllers/` | Per-runtime controllers extracted from `runtimes.js` (Plan 3). One file per runtime, ≤400 lines each, implements the `BaseController` contract for delivery + lifecycle. |
```

- [ ] **Step 3: Run full Node + Python suites**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/*.test.js mcp/stdio/tests/adapters/*.test.js mcp/stdio/tests/controllers/*.test.js 2>&1 | tail -15`
Expected: ALL PASS.

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/ -q 2>&1 | tail -10`
Expected: ALL PASS (or only the pre-existing test_new_dashboard_app.py UTF-8 failures noted in Plan 1 — those are out of scope).

- [ ] **Step 4: Rebuild container**

Run: `cd C:/Docker/aify-comms && docker compose up -d --build 2>&1 | tail -10`
Expected: `aify-comms-service Up X seconds (healthy)`.

- [ ] **Step 5: Confirm health**

Run: `cd C:/Docker/aify-comms && curl -4 -s http://127.0.0.1:8800/health`
Expected: `{"status":"healthy"}`.

- [ ] **Step 6: Commit docs + push**

```bash
cd C:/Docker/aify-comms
git add DECISIONS.md README.md
git commit -m "docs: record Plan 3 controllers + delivery migration decision"
git push origin feature/dashboard-console-mode 2>&1 | tail -10
```

Expected: branch updated on origin.

## Report

After all 14 tasks complete: announce "I'm using the finishing-a-development-branch skill to complete this work." Then invoke `superpowers:finishing-a-development-branch`.
