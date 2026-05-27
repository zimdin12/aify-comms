# Plan 2 — Runtime Capabilities + Pi Delivery Flip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill the six capability stubs on the existing JS `RuntimeAdapter`, mirror the contract in a new Python `service/runtimes/` package, then route pi delivery away from `pi-session-resume` and into `managed_via_wrapper` via a graceful drain.

**Architecture:** Capability values become per-runtime constants on each adapter (class attrs in Python, getter overrides in JS). A small subprocess-based test guarantees both languages agree. The pi flip is a server-side state machine: detect → mark pending → drain → flip on next bridge launch.

**Tech Stack:** Node 20 + ES modules (`node --test`), Python 3 + FastAPI + pytest, SQLite for `agents.runtime_state`.

---

## File Structure

### Create

| Path | Responsibility |
|---|---|
| `service/runtimes/__init__.py` | `adapter_for(name)` factory + `supported_runtimes()` listing |
| `service/runtimes/base.py` | `RuntimeAdapter` base with class-attribute contract + concrete normalizers + Plan 3 stubs raising NotImplementedError |
| `service/runtimes/claude.py` | `ClaudeAdapter` class |
| `service/runtimes/codex.py` | `CodexAdapter` class |
| `service/runtimes/hermes.py` | `HermesAdapter` class |
| `service/runtimes/pi.py` | `PiAdapter` class |
| `service/runtimes/opencode.py` | `OpencodeAdapter` class |
| `service/tests/runtimes/__init__.py` | empty marker |
| `service/tests/runtimes/test_base.py` | Base normalizer + abstract contract behavior |
| `service/tests/runtimes/test_per_adapter.py` | Per-adapter capability + identity assertions |
| `service/tests/runtimes/test_factory.py` | adapter_for + supported_runtimes + alias resolution |
| `service/tests/test_runtime_adapter_consistency.py` | Cross-language consistency via `node mcp/stdio/scripts/dump-capabilities.mjs` |
| `mcp/stdio/scripts/dump-capabilities.mjs` | Dump all JS adapter capabilities as JSON to stdout |
| `service/tests/test_pi_resident_flip.py` | Pi flip mechanics (pending flag + drain helper + 409 during pending) |

### Modify

| Path | Change |
|---|---|
| `mcp/stdio/adapters/claude.js` | Add 6 capability getter overrides |
| `mcp/stdio/adapters/codex.js` | Add 6 capability getter overrides |
| `mcp/stdio/adapters/hermes.js` | Add 6 capability getter overrides |
| `mcp/stdio/adapters/pi.js` | Add 6 capability getter overrides |
| `mcp/stdio/adapters/opencode.js` | Add 6 capability getter overrides |
| `mcp/stdio/tests/adapters/claude.test.js` | Add capability assertions |
| `mcp/stdio/tests/adapters/codex.test.js` | Add capability assertions |
| `mcp/stdio/tests/adapters/hermes.test.js` | Add capability assertions |
| `mcp/stdio/tests/adapters/pi.test.js` | Add capability assertions |
| `mcp/stdio/tests/adapters/opencode.test.js` | Add capability assertions |
| `service/routers/api_v2.py` | `_managed_via_wrapper_for_runtime` drops pi exclusion + consults adapter; `_default_capabilities_for` derives from adapter; new `_drain_and_flip_pi_resident_agents` helper + 5s loop; new 409 path for resident pi during pending-flip window |
| `mcp/stdio/runtimes.js` | `defaultCapabilitiesForRuntime` + `controlCapabilitiesForRuntime` derive from `adapterFor()`; remove `pi-session-resume` resident branch from dispatch table |
| `DECISIONS.md` | Append Plan 2 entry |
| `README.md` | Add `service/runtimes/` to repo layout |

### Out of scope (Plan 3)

- Filling in `wrapper_name` / `console_command` / `inject_message` / `interrupt` / `steer` adapter methods. Plan 3.
- Migrating `_default_console_command`, dispatch dispatcher, delivery shims. Plan 3.
- Wiring opencode through `opencode serve` for multi-client (separate follow-up).

---

## Task 1: Python adapter base + factory + contract tests

**Files:**
- Create: `service/runtimes/__init__.py`
- Create: `service/runtimes/base.py`
- Create: `service/tests/runtimes/__init__.py`
- Create: `service/tests/runtimes/test_base.py`
- Create: `service/tests/runtimes/test_factory.py`

- [ ] **Step 1: Write the failing base contract tests**

Create `service/tests/runtimes/__init__.py` as an empty file.

Create `service/tests/runtimes/test_base.py`:

```python
"""Contract tests for the Python RuntimeAdapter base class. Verifies the
normalizer behavior + Plan 3 stub semantics. Per-adapter capability
assertions live in test_per_adapter.py."""

import os
import pytest

from service.runtimes.base import (
    RuntimeAdapter,
    HANDLE_PLACEHOLDERS,
    MODEL_PLACEHOLDERS,
)


class _TestAdapter(RuntimeAdapter):
    """Concrete fill-in so we can exercise the base class without depending on
    a real runtime. Matches the JS contract.test.js TestAdapter pattern."""
    name = "test-runtime"
    display_name = "Test Runtime"
    session_env_vars = ["TEST_SESSION_ID", "TEST_SESSION_ALT"]
    supports_resident = True
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    supports_multi_client = True
    preferred_delivery_mode = "managed"


def _clear_test_env(monkeypatch):
    monkeypatch.delenv("TEST_SESSION_ID", raising=False)
    monkeypatch.delenv("TEST_SESSION_ALT", raising=False)


def test_get_current_session_id_returns_none_when_unset(monkeypatch):
    _clear_test_env(monkeypatch)
    assert _TestAdapter().get_current_session_id() is None


def test_get_current_session_id_returns_first_var(monkeypatch):
    _clear_test_env(monkeypatch)
    monkeypatch.setenv("TEST_SESSION_ID", "abc-123")
    assert _TestAdapter().get_current_session_id() == "abc-123"


def test_get_current_session_id_falls_back(monkeypatch):
    _clear_test_env(monkeypatch)
    monkeypatch.setenv("TEST_SESSION_ALT", "fallback-id")
    assert _TestAdapter().get_current_session_id() == "fallback-id"


def test_get_current_session_id_rejects_placeholders(monkeypatch):
    _clear_test_env(monkeypatch)
    for placeholder in ("unknown", "default", "none", "null"):
        monkeypatch.setenv("TEST_SESSION_ID", placeholder)
        assert _TestAdapter().get_current_session_id() is None


def test_normalize_session_handle_strips_placeholders():
    a = _TestAdapter()
    assert a.normalize_session_handle("unknown") == ""
    assert a.normalize_session_handle("Default") == ""
    assert a.normalize_session_handle("") == ""
    assert a.normalize_session_handle(None) == ""
    assert a.normalize_session_handle("  real-handle  ") == "real-handle"


def test_resume_args():
    a = _TestAdapter()
    assert a.resume_args("real-handle") == ["--resume", "real-handle"]
    assert a.resume_args("") == []
    assert a.resume_args("unknown") == []
    assert a.resume_args(None) == []


def test_normalize_model_override_strips_placeholders():
    a = _TestAdapter()
    assert a.normalize_model_override("unknown") == ""
    assert a.normalize_model_override("default") == ""
    assert a.normalize_model_override("auto") == ""
    assert a.normalize_model_override("") == ""
    assert a.normalize_model_override("gpt-5.5") == "gpt-5.5"


def test_diagnostic_env(monkeypatch):
    _clear_test_env(monkeypatch)
    monkeypatch.setenv("TEST_SESSION_ALT", "captured")
    env = _TestAdapter().diagnostic_env()
    assert env["TEST_SESSION_ID"] == "(unset)"
    assert env["TEST_SESSION_ALT"] == "captured"


def test_plan_3_methods_raise_not_implemented():
    a = _TestAdapter()
    with pytest.raises(NotImplementedError):
        _ = a.wrapper_name
    with pytest.raises(NotImplementedError):
        a.console_command(agent_id="x", handle="", interactive=True)


@pytest.mark.asyncio
async def test_plan_3_async_methods_raise_not_implemented():
    a = _TestAdapter()
    with pytest.raises(NotImplementedError):
        await a.inject_message(text="hi")
    with pytest.raises(NotImplementedError):
        await a.interrupt(reason="x")
    with pytest.raises(NotImplementedError):
        await a.steer(text="x")


def test_placeholder_sets():
    assert HANDLE_PLACEHOLDERS == {"unknown", "default", "none", "null"}
    assert MODEL_PLACEHOLDERS == {"unknown", "default", "auto"}


def test_subclass_must_define_class_attrs():
    class Bad(RuntimeAdapter):
        pass  # forgets to set name/session_env_vars/etc.

    with pytest.raises(AttributeError):
        _ = Bad().name
```

Create `service/tests/runtimes/test_factory.py`:

```python
"""Factory + alias resolution + supported_runtimes() listing."""

import pytest


def test_adapter_for_returns_concrete_classes():
    from service.runtimes import adapter_for
    from service.runtimes.claude import ClaudeAdapter
    from service.runtimes.codex import CodexAdapter
    from service.runtimes.hermes import HermesAdapter
    from service.runtimes.pi import PiAdapter
    from service.runtimes.opencode import OpencodeAdapter

    assert isinstance(adapter_for("claude-code"), ClaudeAdapter)
    assert isinstance(adapter_for("codex"), CodexAdapter)
    assert isinstance(adapter_for("hermes"), HermesAdapter)
    assert isinstance(adapter_for("pi"), PiAdapter)
    assert isinstance(adapter_for("opencode"), OpencodeAdapter)


def test_adapter_for_resolves_aliases():
    from service.runtimes import adapter_for
    from service.runtimes.claude import ClaudeAdapter
    from service.runtimes.pi import PiAdapter
    from service.runtimes.hermes import HermesAdapter

    assert isinstance(adapter_for("claude"), ClaudeAdapter)
    assert isinstance(adapter_for("omp"), PiAdapter)
    assert isinstance(adapter_for("oh-my-pi"), PiAdapter)
    assert isinstance(adapter_for("hermes-agent"), HermesAdapter)


def test_adapter_for_is_case_insensitive_and_trims():
    from service.runtimes import adapter_for
    from service.runtimes.claude import ClaudeAdapter
    from service.runtimes.codex import CodexAdapter

    assert isinstance(adapter_for("  CLAUDE-CODE  "), ClaudeAdapter)
    assert isinstance(adapter_for("Codex"), CodexAdapter)


def test_adapter_for_unknown_raises():
    from service.runtimes import adapter_for

    with pytest.raises(ValueError, match="Unknown runtime"):
        adapter_for("not-a-real-runtime")
    with pytest.raises(ValueError, match="Unknown runtime"):
        adapter_for("")
    with pytest.raises(ValueError, match="Unknown runtime"):
        adapter_for(None)


def test_supported_runtimes_lists_five():
    from service.runtimes import supported_runtimes
    names = supported_runtimes()
    assert sorted(names) == ["claude-code", "codex", "hermes", "opencode", "pi"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/ -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.runtimes'`.

- [ ] **Step 3: Implement `service/runtimes/base.py`**

Create `service/runtimes/base.py`:

```python
"""Abstract runtime adapter — Python mirror of mcp/stdio/adapters/base.js.

Every supported runtime (claude-code, codex, hermes, pi, opencode) ships a
subclass that fills in the following class attributes:

    name: str
    display_name: str  (defaults to name if unset)
    session_env_vars: list[str]
    supports_resident: bool
    supports_managed: bool
    supports_steering: bool
    supports_interrupt: bool
    supports_multi_client: bool
    preferred_delivery_mode: str  # "resident" | "managed" | "managed-via-wrapper"

The base supplies shared session-handle normalization, model-override
normalization, default diagnostic_env() implementation, and stubs for the
Plan 3 (console + delivery) methods so the contract surface is defined
upfront.
"""

from __future__ import annotations

import os
from typing import Any

HANDLE_PLACEHOLDERS = {"unknown", "default", "none", "null"}
MODEL_PLACEHOLDERS = {"unknown", "default", "auto"}


class RuntimeAdapter:
    # Class attributes set by subclasses. Accessing on the base class raises
    # AttributeError, which surfaces missing overrides loudly in tests.

    @property
    def display_name(self) -> str:
        # Default to `name` if subclass doesn't override.
        cls_attr = type(self).__dict__.get("display_name")
        if isinstance(cls_attr, str):
            return cls_attr
        return self.name

    # ─────────────────── SESSION LIFECYCLE (Plan 1) ───────────────────

    def get_current_session_id(self) -> str | None:
        for var in self.session_env_vars:
            raw = os.environ.get(var, "")
            normalized = self.normalize_session_handle(raw)
            if normalized:
                return normalized
        return None

    def normalize_session_handle(self, raw: Any) -> str:
        text = str(raw if raw is not None else "").strip()
        if not text:
            return ""
        if text.lower() in HANDLE_PLACEHOLDERS:
            return ""
        return text

    def resume_args(self, handle: Any) -> list[str]:
        h = self.normalize_session_handle(handle)
        return ["--resume", h] if h else []

    def normalize_model_override(self, raw: Any) -> str:
        text = str(raw if raw is not None else "").strip()
        if not text:
            return ""
        if text.lower() in MODEL_PLACEHOLDERS:
            return ""
        return text

    def diagnostic_env(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for var in self.session_env_vars:
            val = os.environ.get(var, "").strip()
            out[var] = val if val else "(unset)"
        return out

    # ─────────────────── CONSOLE / WRAPPER (Plan 3 — stubbed) ───────────────────

    @property
    def wrapper_name(self) -> str:
        raise NotImplementedError("Plan 3 — not yet implemented")

    def console_command(self, **opts: Any) -> str:
        raise NotImplementedError("Plan 3 — not yet implemented")

    # ─────────────────── DELIVERY (Plan 3 — stubbed) ───────────────────

    async def inject_message(self, **opts: Any) -> Any:
        raise NotImplementedError("Plan 3 — not yet implemented")

    async def interrupt(self, **opts: Any) -> Any:
        raise NotImplementedError("Plan 3 — not yet implemented")

    async def steer(self, **opts: Any) -> Any:
        raise NotImplementedError("Plan 3 — not yet implemented")
```

Create `service/runtimes/__init__.py`:

```python
"""Python RuntimeAdapter package — parallel to mcp/stdio/adapters/.

Plan 2 introduces this so the server (Python) and bridge (JS) can both
own per-runtime capabilities through their own adapter classes. Plan 3
extends with console_command / inject_message / interrupt / steer.
"""

from __future__ import annotations

from .base import RuntimeAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .hermes import HermesAdapter
from .pi import PiAdapter
from .opencode import OpencodeAdapter

_REGISTRY: dict[str, type[RuntimeAdapter]] = {
    "claude-code": ClaudeAdapter,
    "codex": CodexAdapter,
    "hermes": HermesAdapter,
    "pi": PiAdapter,
    "opencode": OpencodeAdapter,
}

_ALIASES: dict[str, str] = {
    "claude": "claude-code",
    "claude_code": "claude-code",
    "hermes-agent": "hermes",
    "hermes_agent": "hermes",
    "oh-my-pi": "pi",
    "oh_my_pi": "pi",
    "omp": "pi",
    "pi-agent": "pi",
    "pi_agent": "pi",
}


def adapter_for(name: str | None) -> RuntimeAdapter:
    key = str(name if name is not None else "").strip().lower()
    canonical = _ALIASES.get(key, key)
    cls = _REGISTRY.get(canonical)
    if cls is None:
        raise ValueError(
            f'Unknown runtime "{name}". Known: {", ".join(_REGISTRY.keys())}'
        )
    return cls()


def supported_runtimes() -> list[str]:
    return list(_REGISTRY.keys())


__all__ = [
    "RuntimeAdapter",
    "ClaudeAdapter",
    "CodexAdapter",
    "HermesAdapter",
    "PiAdapter",
    "OpencodeAdapter",
    "adapter_for",
    "supported_runtimes",
]
```

Note: this Step 3 creates `base.py` and `__init__.py` BUT the per-adapter files are created in Tasks 2-6. Tests in this Task 1 will still fail until those land. That's fine — the contract test passes in Task 1 because it only uses `_TestAdapter`; the factory test depends on the concrete adapters and will go green in Task 7 after all subclasses exist.

Actually, the simplest way to keep Task 1 self-contained: split factory test out. Update Task 1's failing-test step to only run `test_base.py`.

- [ ] **Step 4: Run base tests only to verify they pass**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/test_base.py -v`
Expected: PASS — 11 tests green.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add service/runtimes/__init__.py service/runtimes/base.py \
        service/tests/runtimes/__init__.py service/tests/runtimes/test_base.py \
        service/tests/runtimes/test_factory.py
git commit -m "feat(runtimes/py): RuntimeAdapter base + factory + contract tests"
```

Note: `service/runtimes/__init__.py` imports from per-adapter files that don't exist yet — but `service/tests/runtimes/test_base.py` only imports from `service.runtimes.base`, not `service.runtimes`, so the test suite is green. The factory test stays present-but-failing until Task 7 lands.

---

## Task 2: ClaudePythonAdapter

**Files:**
- Create: `service/runtimes/claude.py`
- Modify: `service/tests/runtimes/test_per_adapter.py` (create on first use; add ClaudeAdapter assertions)

- [ ] **Step 1: Write failing per-adapter test**

Create `service/tests/runtimes/test_per_adapter.py`:

```python
"""Per-adapter capability + identity assertions. The expected values are
locked by the Plan 2 spec (docs/superpowers/specs/2026-05-25-runtime-adapter-plan2-capabilities-design.md)."""

import pytest


def test_claude_adapter():
    from service.runtimes.claude import ClaudeAdapter
    a = ClaudeAdapter()
    assert a.name == "claude-code"
    assert a.display_name == "Claude Code"
    assert a.session_env_vars == ["CLAUDE_SESSION_ID"]
    assert a.supports_resident is True
    assert a.supports_managed is True
    assert a.supports_steering is True
    assert a.supports_interrupt is True
    assert a.supports_multi_client is True
    assert a.preferred_delivery_mode == "managed-via-wrapper"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/test_per_adapter.py::test_claude_adapter -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.runtimes.claude'`.

- [ ] **Step 3: Implement `service/runtimes/claude.py`**

```python
"""ClaudeAdapter — Python mirror of mcp/stdio/adapters/claude.js.

Capability values per Plan 2 spec; everything else inherited from the base.
"""

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/test_per_adapter.py::test_claude_adapter -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add service/runtimes/claude.py service/tests/runtimes/test_per_adapter.py
git commit -m "feat(runtimes/py): ClaudeAdapter"
```

---

## Task 3: CodexPythonAdapter

**Files:**
- Create: `service/runtimes/codex.py`
- Modify: `service/tests/runtimes/test_per_adapter.py` (append assertions)

- [ ] **Step 1: Append failing test**

Append to `service/tests/runtimes/test_per_adapter.py`:

```python


def test_codex_adapter():
    from service.runtimes.codex import CodexAdapter
    a = CodexAdapter()
    assert a.name == "codex"
    assert a.display_name == "Codex"
    assert a.session_env_vars == ["CODEX_THREAD_ID"]
    assert a.supports_resident is True
    assert a.supports_managed is True
    assert a.supports_steering is True
    assert a.supports_interrupt is True
    assert a.supports_multi_client is True
    assert a.preferred_delivery_mode == "managed-via-wrapper"


def test_codex_adapter_diagnostic_env_includes_app_server(monkeypatch):
    from service.runtimes.codex import CodexAdapter
    monkeypatch.setenv("CODEX_THREAD_ID", "thread-x")
    monkeypatch.setenv("AIFY_CODEX_APP_SERVER_URL", "ws://127.0.0.1:1234")
    env = CodexAdapter().diagnostic_env()
    assert env["CODEX_THREAD_ID"] == "thread-x"
    assert env["AIFY_CODEX_APP_SERVER_URL"] == "ws://127.0.0.1:1234"


def test_codex_adapter_diagnostic_env_unset_app_server(monkeypatch):
    from service.runtimes.codex import CodexAdapter
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("AIFY_CODEX_APP_SERVER_URL", raising=False)
    env = CodexAdapter().diagnostic_env()
    assert env["AIFY_CODEX_APP_SERVER_URL"] == "(unset)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/test_per_adapter.py::test_codex_adapter -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `service/runtimes/codex.py`**

```python
"""CodexAdapter — Python mirror of mcp/stdio/adapters/codex.js.

Adds AIFY_CODEX_APP_SERVER_URL to diagnostic_env() for parity with the JS side.
"""

from __future__ import annotations

import os

from .base import RuntimeAdapter


class CodexAdapter(RuntimeAdapter):
    name = "codex"
    display_name = "Codex"
    session_env_vars = ["CODEX_THREAD_ID"]
    supports_resident = True
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    supports_multi_client = True
    preferred_delivery_mode = "managed-via-wrapper"

    def diagnostic_env(self) -> dict[str, str]:
        env = super().diagnostic_env()
        val = os.environ.get("AIFY_CODEX_APP_SERVER_URL", "").strip()
        env["AIFY_CODEX_APP_SERVER_URL"] = val if val else "(unset)"
        return env
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/test_per_adapter.py::test_codex_adapter service/tests/runtimes/test_per_adapter.py::test_codex_adapter_diagnostic_env_includes_app_server service/tests/runtimes/test_per_adapter.py::test_codex_adapter_diagnostic_env_unset_app_server -v`
Expected: PASS — 3 tests green.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add service/runtimes/codex.py service/tests/runtimes/test_per_adapter.py
git commit -m "feat(runtimes/py): CodexAdapter with app-server URL in diagnostic_env"
```

---

## Task 4: HermesPythonAdapter

**Files:**
- Create: `service/runtimes/hermes.py`
- Modify: `service/tests/runtimes/test_per_adapter.py`

- [ ] **Step 1: Append failing test**

Append to `service/tests/runtimes/test_per_adapter.py`:

```python


def test_hermes_adapter():
    from service.runtimes.hermes import HermesAdapter
    a = HermesAdapter()
    assert a.name == "hermes"
    assert a.display_name == "Hermes"
    assert a.session_env_vars == ["HERMES_SESSION_ID", "HERMES_SESSION"]
    assert a.supports_resident is True
    assert a.supports_managed is True
    assert a.supports_steering is True
    assert a.supports_interrupt is True
    assert a.supports_multi_client is True
    assert a.preferred_delivery_mode == "managed-via-wrapper"


def test_hermes_adapter_diagnostic_env_includes_gateway(monkeypatch):
    from service.runtimes.hermes import HermesAdapter
    monkeypatch.setenv("AIFY_HERMES_GATEWAY_URL", "ws://127.0.0.1:9999/api/ws?token=x")
    env = HermesAdapter().diagnostic_env()
    assert env["AIFY_HERMES_GATEWAY_URL"] == "ws://127.0.0.1:9999/api/ws?token=x"


def test_hermes_adapter_falls_back_to_HERMES_SESSION(monkeypatch):
    from service.runtimes.hermes import HermesAdapter
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    monkeypatch.setenv("HERMES_SESSION", "fallback-id")
    assert HermesAdapter().get_current_session_id() == "fallback-id"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/test_per_adapter.py::test_hermes_adapter -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `service/runtimes/hermes.py`**

```python
"""HermesAdapter — Python mirror of mcp/stdio/adapters/hermes.js."""

from __future__ import annotations

import os

from .base import RuntimeAdapter


class HermesAdapter(RuntimeAdapter):
    name = "hermes"
    display_name = "Hermes"
    session_env_vars = ["HERMES_SESSION_ID", "HERMES_SESSION"]
    supports_resident = True
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    supports_multi_client = True
    preferred_delivery_mode = "managed-via-wrapper"

    def diagnostic_env(self) -> dict[str, str]:
        env = super().diagnostic_env()
        val = os.environ.get("AIFY_HERMES_GATEWAY_URL", "").strip()
        env["AIFY_HERMES_GATEWAY_URL"] = val if val else "(unset)"
        return env
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/test_per_adapter.py::test_hermes_adapter service/tests/runtimes/test_per_adapter.py::test_hermes_adapter_diagnostic_env_includes_gateway service/tests/runtimes/test_per_adapter.py::test_hermes_adapter_falls_back_to_HERMES_SESSION -v`
Expected: PASS — 3 tests green.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add service/runtimes/hermes.py service/tests/runtimes/test_per_adapter.py
git commit -m "feat(runtimes/py): HermesAdapter with gateway URL in diagnostic_env"
```

---

## Task 5: PiPythonAdapter

**Files:**
- Create: `service/runtimes/pi.py`
- Modify: `service/tests/runtimes/test_per_adapter.py`

- [ ] **Step 1: Append failing test**

Append to `service/tests/runtimes/test_per_adapter.py`:

```python


def test_pi_adapter():
    from service.runtimes.pi import PiAdapter
    a = PiAdapter()
    assert a.name == "pi"
    assert a.display_name == "Pi"
    assert a.session_env_vars == ["PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID"]
    # Plan 2 capability matrix — the critical pi flip declaration:
    assert a.supports_resident is False, "pi is single-client RPC; resident impossible"
    assert a.supports_managed is True
    assert a.supports_steering is True
    assert a.supports_interrupt is True
    assert a.supports_multi_client is False
    assert a.preferred_delivery_mode == "managed-via-wrapper"


def test_pi_adapter_session_var_fallback_order(monkeypatch):
    from service.runtimes.pi import PiAdapter
    monkeypatch.delenv("PI_SESSION_ID", raising=False)
    monkeypatch.setenv("OMP_SESSION_ID", "omp-x")
    monkeypatch.setenv("AIFY_PI_SESSION_ID", "aify-y")
    assert PiAdapter().get_current_session_id() == "omp-x"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/test_per_adapter.py::test_pi_adapter -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `service/runtimes/pi.py`**

```python
"""PiAdapter — Python mirror of mcp/stdio/adapters/pi.js.

Capability declarations encode the Plan 2 pi delivery flip: resident is
False because omp --mode rpc is single-client stdio (no multi-client
gateway). preferred_delivery_mode is managed-via-wrapper so the dispatch
router pins pi to the unified wrapper-backing path.
"""

from __future__ import annotations

from .base import RuntimeAdapter


class PiAdapter(RuntimeAdapter):
    name = "pi"
    display_name = "Pi"
    session_env_vars = ["PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID"]
    supports_resident = False
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    supports_multi_client = False
    preferred_delivery_mode = "managed-via-wrapper"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/test_per_adapter.py::test_pi_adapter service/tests/runtimes/test_per_adapter.py::test_pi_adapter_session_var_fallback_order -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add service/runtimes/pi.py service/tests/runtimes/test_per_adapter.py
git commit -m "feat(runtimes/py): PiAdapter (resident=false; preferredDeliveryMode=managed-via-wrapper)"
```

---

## Task 6: OpencodePythonAdapter

**Files:**
- Create: `service/runtimes/opencode.py`
- Modify: `service/tests/runtimes/test_per_adapter.py`

- [ ] **Step 1: Append failing test**

Append to `service/tests/runtimes/test_per_adapter.py`:

```python


def test_opencode_adapter():
    from service.runtimes.opencode import OpencodeAdapter
    a = OpencodeAdapter()
    assert a.name == "opencode"
    assert a.display_name == "OpenCode"
    assert a.session_env_vars == ["OPENCODE_SESSION_ID", "OPENCODE_SESSION"]
    # aify-comms doesn't wire `opencode serve` today — tracked as separate
    # follow-up. Capability flags reflect aify-comms's current delivery
    # surface, not what opencode CAN do in principle.
    assert a.supports_resident is False
    assert a.supports_managed is True
    assert a.supports_steering is False
    assert a.supports_interrupt is True
    assert a.supports_multi_client is False
    assert a.preferred_delivery_mode == "managed"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/test_per_adapter.py::test_opencode_adapter -v`
Expected: FAIL.

- [ ] **Step 3: Implement `service/runtimes/opencode.py`**

```python
"""OpencodeAdapter — Python mirror of mcp/stdio/adapters/opencode.js.

aify-comms currently spawns the opencode CLI directly without using
`opencode serve` (the multi-client HTTP+ACP server). Capability flags
reflect aify-comms's current delivery surface, not what opencode supports
in principle. Wiring opencode through `serve` is tracked as a separate
follow-up.
"""

from __future__ import annotations

from .base import RuntimeAdapter


class OpencodeAdapter(RuntimeAdapter):
    name = "opencode"
    display_name = "OpenCode"
    session_env_vars = ["OPENCODE_SESSION_ID", "OPENCODE_SESSION"]
    supports_resident = False
    supports_managed = True
    supports_steering = False
    supports_interrupt = True
    supports_multi_client = False
    preferred_delivery_mode = "managed"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/test_per_adapter.py::test_opencode_adapter -v`
Expected: PASS.

- [ ] **Step 5: Run the whole runtimes test directory to confirm everything's green**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/runtimes/ -v`
Expected: All tests pass (test_base + test_per_adapter + test_factory). 26+ tests green.

- [ ] **Step 6: Commit**

```bash
cd C:/Docker/aify-comms
git add service/runtimes/opencode.py service/tests/runtimes/test_per_adapter.py
git commit -m "feat(runtimes/py): OpencodeAdapter"
```

---

## Task 7: Cross-language consistency test

**Files:**
- Create: `mcp/stdio/scripts/dump-capabilities.mjs`
- Create: `service/tests/test_runtime_adapter_consistency.py`

- [ ] **Step 1: Write the failing consistency test**

Create `service/tests/test_runtime_adapter_consistency.py`:

```python
"""Cross-language consistency: every per-runtime capability value must match
between the JS adapter (mcp/stdio/adapters/*.js) and the Python adapter
(service/runtimes/*.py). Catches drift before it ships.

Runs `node mcp/stdio/scripts/dump-capabilities.mjs` and compares to the
Python adapter values. Skips cleanly if Node isn't on PATH.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _js_to_py_key(js_key: str) -> str:
    """Convert JS camelCase to Python snake_case."""
    out = []
    for ch in js_key:
        if ch.isupper():
            out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


def test_js_and_python_adapters_agree_on_capabilities():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH — cross-language consistency check skipped")

    script = ROOT / "mcp" / "stdio" / "scripts" / "dump-capabilities.mjs"
    proc = subprocess.run(
        [node, str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    js_caps = json.loads(proc.stdout)

    from service.runtimes import adapter_for, supported_runtimes

    assert sorted(js_caps.keys()) == sorted(supported_runtimes()), (
        f"JS and Python disagree on which runtimes are supported. "
        f"JS: {sorted(js_caps.keys())}, Py: {sorted(supported_runtimes())}"
    )

    drifts: list[str] = []
    for name in supported_runtimes():
        py = adapter_for(name)
        for js_key, js_value in js_caps[name].items():
            py_key = _js_to_py_key(js_key)
            py_value = getattr(py, py_key)
            if py_value != js_value:
                drifts.append(
                    f"{name}.{js_key} (py: {py_key}): JS={js_value!r}, Py={py_value!r}"
                )

    assert not drifts, "Capability drift between JS and Python adapters:\n  - " + "\n  - ".join(drifts)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_runtime_adapter_consistency.py -v`
Expected: FAIL — `[Errno 2] No such file or directory: '.../mcp/stdio/scripts/dump-capabilities.mjs'` (the script doesn't exist yet).

- [ ] **Step 3: Create the dump-capabilities script**

Create `mcp/stdio/scripts/dump-capabilities.mjs`:

```javascript
// Prints all JS adapter capability values as JSON to stdout. Used by
// service/tests/test_runtime_adapter_consistency.py to verify JS and
// Python adapters agree.
//
// Output shape:
//   {
//     "claude-code": {
//       "supportsResident": true,
//       "supportsManaged": true,
//       "supportsSteering": true,
//       "supportsInterrupt": true,
//       "supportsMultiClient": true,
//       "preferredDeliveryMode": "managed-via-wrapper"
//     },
//     ...
//   }

import { adapterFor, supportedRuntimes } from "../adapters/index.js";

const out = {};
for (const name of supportedRuntimes()) {
  const a = adapterFor(name);
  out[name] = {
    supportsResident: a.supportsResident,
    supportsManaged: a.supportsManaged,
    supportsSteering: a.supportsSteering,
    supportsInterrupt: a.supportsInterrupt,
    supportsMultiClient: a.supportsMultiClient,
    preferredDeliveryMode: a.preferredDeliveryMode,
  };
}

process.stdout.write(JSON.stringify(out, null, 2));
```

- [ ] **Step 4: Run test to confirm next-stage failure (JS adapters don't have capability getters yet)**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_runtime_adapter_consistency.py -v`
Expected: FAIL — `subprocess.CalledProcessError` because the JS script invocation throws "not yet implemented: Plan 2" when reading `a.supportsResident`.

This is expected — Tasks 8-12 add the JS capability getters. The consistency test will pass once Task 12 lands.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/scripts/dump-capabilities.mjs service/tests/test_runtime_adapter_consistency.py
git commit -m "feat(runtimes): cross-language consistency test via dump-capabilities.mjs"
```

---

## Task 8: JS ClaudeAdapter capability overrides

**Files:**
- Modify: `mcp/stdio/adapters/claude.js`
- Modify: `mcp/stdio/tests/adapters/claude.test.js`

- [ ] **Step 1: Append failing test**

Append to `mcp/stdio/tests/adapters/claude.test.js`:

```javascript

test("ClaudeAdapter Plan 2 capabilities", () => {
  const a = new ClaudeAdapter();
  assert.strictEqual(a.supportsResident, true);
  assert.strictEqual(a.supportsManaged, true);
  assert.strictEqual(a.supportsSteering, true);
  assert.strictEqual(a.supportsInterrupt, true);
  assert.strictEqual(a.supportsMultiClient, true);
  assert.strictEqual(a.preferredDeliveryMode, "managed-via-wrapper");
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/claude.test.js`
Expected: FAIL — `Error: not yet implemented: Plan 2`.

- [ ] **Step 3: Implement capability overrides in `mcp/stdio/adapters/claude.js`**

Update `mcp/stdio/adapters/claude.js` to add the 6 capability getters AFTER the existing identity getters:

```javascript
import { RuntimeAdapter } from "./base.js";

export class ClaudeAdapter extends RuntimeAdapter {
  get name() { return "claude-code"; }
  get displayName() { return "Claude Code"; }
  get sessionEnvVars() { return ["CLAUDE_SESSION_ID"]; }

  // Plan 2 capability matrix — see
  // docs/superpowers/specs/2026-05-25-runtime-adapter-plan2-capabilities-design.md
  get supportsResident() { return true; }
  get supportsManaged() { return true; }
  get supportsSteering() { return true; }
  get supportsInterrupt() { return true; }
  get supportsMultiClient() { return true; }
  get preferredDeliveryMode() { return "managed-via-wrapper"; }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/claude.test.js`
Expected: PASS — 5 tests green.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/adapters/claude.js mcp/stdio/tests/adapters/claude.test.js
git commit -m "feat(adapters): ClaudeAdapter Plan 2 capability getters"
```

---

## Task 9: JS CodexAdapter capability overrides

**Files:**
- Modify: `mcp/stdio/adapters/codex.js`
- Modify: `mcp/stdio/tests/adapters/codex.test.js`

- [ ] **Step 1: Append failing test**

Append to `mcp/stdio/tests/adapters/codex.test.js`:

```javascript

test("CodexAdapter Plan 2 capabilities", () => {
  const a = new CodexAdapter();
  assert.strictEqual(a.supportsResident, true);
  assert.strictEqual(a.supportsManaged, true);
  assert.strictEqual(a.supportsSteering, true);
  assert.strictEqual(a.supportsInterrupt, true);
  assert.strictEqual(a.supportsMultiClient, true);
  assert.strictEqual(a.preferredDeliveryMode, "managed-via-wrapper");
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/codex.test.js`
Expected: FAIL — `Error: not yet implemented: Plan 2`.

- [ ] **Step 3: Implement capability overrides in `mcp/stdio/adapters/codex.js`**

Update `mcp/stdio/adapters/codex.js` to add 6 capability getters (keep the existing diagnosticEnv override):

```javascript
import { RuntimeAdapter } from "./base.js";

export class CodexAdapter extends RuntimeAdapter {
  get name() { return "codex"; }
  get displayName() { return "Codex"; }
  get sessionEnvVars() { return ["CODEX_THREAD_ID"]; }

  // Plan 2 capability matrix
  get supportsResident() { return true; }
  get supportsManaged() { return true; }
  get supportsSteering() { return true; }
  get supportsInterrupt() { return true; }
  get supportsMultiClient() { return true; }
  get preferredDeliveryMode() { return "managed-via-wrapper"; }

  diagnosticEnv() {
    const env = super.diagnosticEnv();
    env.AIFY_CODEX_APP_SERVER_URL = String(process.env.AIFY_CODEX_APP_SERVER_URL || "").trim() || "(unset)";
    return env;
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/codex.test.js`
Expected: PASS — 5 tests green.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/adapters/codex.js mcp/stdio/tests/adapters/codex.test.js
git commit -m "feat(adapters): CodexAdapter Plan 2 capability getters"
```

---

## Task 10: JS HermesAdapter capability overrides

**Files:**
- Modify: `mcp/stdio/adapters/hermes.js`
- Modify: `mcp/stdio/tests/adapters/hermes.test.js`

- [ ] **Step 1: Append failing test**

Append to `mcp/stdio/tests/adapters/hermes.test.js`:

```javascript

test("HermesAdapter Plan 2 capabilities", () => {
  const a = new HermesAdapter();
  assert.strictEqual(a.supportsResident, true);
  assert.strictEqual(a.supportsManaged, true);
  assert.strictEqual(a.supportsSteering, true);
  assert.strictEqual(a.supportsInterrupt, true);
  assert.strictEqual(a.supportsMultiClient, true);
  assert.strictEqual(a.preferredDeliveryMode, "managed-via-wrapper");
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/hermes.test.js`
Expected: FAIL — `Error: not yet implemented: Plan 2`.

- [ ] **Step 3: Implement capability overrides in `mcp/stdio/adapters/hermes.js`**

Update `mcp/stdio/adapters/hermes.js` to add 6 capability getters (keep the existing diagnosticEnv override):

```javascript
import { RuntimeAdapter } from "./base.js";

export class HermesAdapter extends RuntimeAdapter {
  get name() { return "hermes"; }
  get displayName() { return "Hermes"; }
  get sessionEnvVars() { return ["HERMES_SESSION_ID", "HERMES_SESSION"]; }

  // Plan 2 capability matrix
  get supportsResident() { return true; }
  get supportsManaged() { return true; }
  get supportsSteering() { return true; }
  get supportsInterrupt() { return true; }
  get supportsMultiClient() { return true; }
  get preferredDeliveryMode() { return "managed-via-wrapper"; }

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
git commit -m "feat(adapters): HermesAdapter Plan 2 capability getters"
```

---

## Task 11: JS PiAdapter capability overrides

**Files:**
- Modify: `mcp/stdio/adapters/pi.js`
- Modify: `mcp/stdio/tests/adapters/pi.test.js`

- [ ] **Step 1: Append failing test**

Append to `mcp/stdio/tests/adapters/pi.test.js`:

```javascript

test("PiAdapter Plan 2 capabilities — pi flip key declarations", () => {
  const a = new PiAdapter();
  // pi is single-client RPC; resident impossible
  assert.strictEqual(a.supportsResident, false);
  assert.strictEqual(a.supportsManaged, true);
  assert.strictEqual(a.supportsSteering, true);
  assert.strictEqual(a.supportsInterrupt, true);
  assert.strictEqual(a.supportsMultiClient, false);
  assert.strictEqual(a.preferredDeliveryMode, "managed-via-wrapper");
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/pi.test.js`
Expected: FAIL.

- [ ] **Step 3: Implement capability overrides in `mcp/stdio/adapters/pi.js`**

```javascript
import { RuntimeAdapter } from "./base.js";

export class PiAdapter extends RuntimeAdapter {
  get name() { return "pi"; }
  get displayName() { return "Pi"; }
  get sessionEnvVars() { return ["PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID"]; }

  // Plan 2 capability matrix — the pi delivery flip:
  //   resident=false because omp --mode rpc is single-client stdio.
  //   preferredDeliveryMode pins pi to the unified wrapper-backing path.
  get supportsResident() { return false; }
  get supportsManaged() { return true; }
  get supportsSteering() { return true; }
  get supportsInterrupt() { return true; }
  get supportsMultiClient() { return false; }
  get preferredDeliveryMode() { return "managed-via-wrapper"; }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/pi.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/adapters/pi.js mcp/stdio/tests/adapters/pi.test.js
git commit -m "feat(adapters): PiAdapter Plan 2 capability getters (resident=false; pi flip)"
```

---

## Task 12: JS OpencodeAdapter capability overrides + cross-language test passes

**Files:**
- Modify: `mcp/stdio/adapters/opencode.js`
- Modify: `mcp/stdio/tests/adapters/opencode.test.js`

- [ ] **Step 1: Append failing test**

Append to `mcp/stdio/tests/adapters/opencode.test.js`:

```javascript

test("OpencodeAdapter Plan 2 capabilities", () => {
  const a = new OpencodeAdapter();
  // aify-comms doesn't wire `opencode serve` today — capabilities reflect
  // current aify-comms delivery surface, not what opencode CAN do.
  assert.strictEqual(a.supportsResident, false);
  assert.strictEqual(a.supportsManaged, true);
  assert.strictEqual(a.supportsSteering, false);
  assert.strictEqual(a.supportsInterrupt, true);
  assert.strictEqual(a.supportsMultiClient, false);
  assert.strictEqual(a.preferredDeliveryMode, "managed");
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/opencode.test.js`
Expected: FAIL.

- [ ] **Step 3: Implement capability overrides in `mcp/stdio/adapters/opencode.js`**

```javascript
import { RuntimeAdapter } from "./base.js";

export class OpencodeAdapter extends RuntimeAdapter {
  get name() { return "opencode"; }
  get displayName() { return "OpenCode"; }
  get sessionEnvVars() { return ["OPENCODE_SESSION_ID", "OPENCODE_SESSION"]; }

  // Plan 2 capability matrix. aify-comms doesn't wire `opencode serve`
  // today — capabilities describe current aify-comms delivery surface.
  // Wiring serve is tracked as separate follow-up.
  get supportsResident() { return false; }
  get supportsManaged() { return true; }
  get supportsSteering() { return false; }
  get supportsInterrupt() { return true; }
  get supportsMultiClient() { return false; }
  get preferredDeliveryMode() { return "managed"; }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/opencode.test.js`
Expected: PASS.

- [ ] **Step 5: Now the consistency test should pass — run it**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_runtime_adapter_consistency.py -v`
Expected: PASS — Tasks 7 + 8-12 now align JS and Python capabilities.

- [ ] **Step 6: Run the full adapter test directory + Python runtimes suite**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/*.test.js && python -m pytest service/tests/runtimes/ service/tests/test_runtime_adapter_consistency.py -v`
Expected: All green.

- [ ] **Step 7: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/adapters/opencode.js mcp/stdio/tests/adapters/opencode.test.js
git commit -m "feat(adapters): OpencodeAdapter Plan 2 capability getters (consistency test green)"
```

---

## Task 13: Server consumer migration — `_managed_via_wrapper_for_runtime`

**Files:**
- Modify: `service/routers/api_v2.py:266-293`
- Create: `service/tests/test_managed_via_wrapper_adapter.py`

- [ ] **Step 1: Write failing test**

Create `service/tests/test_managed_via_wrapper_adapter.py`:

```python
"""Regression: _managed_via_wrapper_for_runtime now consults the adapter's
preferred_delivery_mode. Pi is no longer hardcoded-excluded (Plan 2 pi flip).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.routers.api_v2 import _managed_via_wrapper_for_runtime


def test_pi_is_now_eligible_for_managed_via_wrapper():
    # Setting respects True; pi adapter declares managed-via-wrapper as preferred
    settings = {"managed_via_wrapper": True}
    assert _managed_via_wrapper_for_runtime(settings, "pi") is True


def test_pi_list_form_includes_pi():
    settings = {"managed_via_wrapper": ["pi"]}
    assert _managed_via_wrapper_for_runtime(settings, "pi") is True


def test_pi_setting_off_still_returns_false():
    settings = {"managed_via_wrapper": False}
    assert _managed_via_wrapper_for_runtime(settings, "pi") is False


def test_claude_still_excluded():
    # Claude is wrapper-backed via claude-channel.js — not via this flag
    settings = {"managed_via_wrapper": True}
    assert _managed_via_wrapper_for_runtime(settings, "claude-code") is False


def test_codex_hermes_unchanged():
    settings = {"managed_via_wrapper": True}
    assert _managed_via_wrapper_for_runtime(settings, "codex") is True
    assert _managed_via_wrapper_for_runtime(settings, "hermes") is True


def test_runtime_with_preferred_managed_not_wrapper():
    # opencode adapter declares preferred_delivery_mode = "managed" (not
    # "managed-via-wrapper"), so it stays out even when the setting is True.
    settings = {"managed_via_wrapper": True}
    assert _managed_via_wrapper_for_runtime(settings, "opencode") is False
```

- [ ] **Step 2: Run to verify failures**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_managed_via_wrapper_adapter.py -v`
Expected: FAIL on `test_pi_is_now_eligible_for_managed_via_wrapper` and `test_pi_list_form_includes_pi` because today's code returns False unconditionally for pi.

- [ ] **Step 3: Update `_managed_via_wrapper_for_runtime` in `service/routers/api_v2.py`**

Find the function at line 266 and replace its body. The current implementation hardcodes "exclude pi" and "include {codex, hermes, opencode}". Replace with adapter-driven logic:

```python
def _managed_via_wrapper_for_runtime(settings: dict[str, Any], runtime: str) -> bool:
    """True when managed dispatches for this runtime should route through a
    *-aify wrapper PTY (the wrapper's child bridge claims and delivers) instead
    of the bridge's native RPC adapter. Unified-backing refactor 2026-05-24,
    extended in Plan 2 (2026-05-25) to consult the runtime adapter.

    claude-code is excluded — it's already wrapper-backed via claude-channel.js
    inside claude-aify regardless of this flag.

    For all other runtimes, eligibility is now driven by the adapter's
    preferred_delivery_mode == "managed-via-wrapper". Pi adopts this in Plan 2
    (was hardcoded-excluded prior; the structural mismatch was fixed when
    pi-session-resume was removed from the dispatch entry table).
    """
    from service.runtimes import adapter_for

    val = settings.get("managed_via_wrapper", DEFAULT_SETTINGS.get("managed_via_wrapper", False))
    runtime_n = _normalize_runtime(runtime or "")
    if runtime_n == "claude-code":
        return False
    try:
        adapter = adapter_for(runtime_n)
    except ValueError:
        return False
    if adapter.preferred_delivery_mode != "managed-via-wrapper":
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, list):
        return runtime_n in {str(item).strip().lower() for item in val if item}
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_managed_via_wrapper_adapter.py -v`
Expected: PASS — 6 tests green.

- [ ] **Step 5: Run existing regression tests to catch unintended breakage**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_api_v2_regressions.py -v -k "managed_via_wrapper or via_wrapper or pi"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd C:/Docker/aify-comms
git add service/routers/api_v2.py service/tests/test_managed_via_wrapper_adapter.py
git commit -m "feat(server): _managed_via_wrapper_for_runtime consults adapter (pi eligible)"
```

---

## Task 14: Server consumer migration — `_default_capabilities_for`

**Files:**
- Modify: `service/routers/api_v2.py:831-...` (find function body)
- Create: `service/tests/test_default_capabilities_adapter.py`

- [ ] **Step 1: Read current implementation**

Run: `cd C:/Docker/aify-comms && sed -n '825,900p' service/routers/api_v2.py`

Expected: Look at the current `_default_capabilities_for(runtime, session_mode, session_handle, runtime_config)` body. It returns a list of capability strings like `["managed-run", "resume", "interrupt", "steer", "spawn"]` based on hardcoded per-runtime branches.

- [ ] **Step 2: Write failing test**

Create `service/tests/test_default_capabilities_adapter.py`:

```python
"""Regression: _default_capabilities_for derives the capability list from the
runtime adapter. Pi no longer claims `resident-run` capability because
PiAdapter.supports_resident == False.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.routers.api_v2 import _default_capabilities_for


def test_pi_resident_no_longer_advertises_resident_run():
    # Plan 2 pi flip: pi adapter.supports_resident == False, so the resident
    # session mode should not produce `resident-run` capability for pi.
    caps = _default_capabilities_for("pi", "resident", "session-x", {})
    assert "resident-run" not in caps, (
        f"pi resident must not advertise resident-run after Plan 2. caps={caps}"
    )


def test_pi_managed_still_advertises_managed_run_and_steer():
    caps = _default_capabilities_for("pi", "managed", "", {})
    assert "managed-run" in caps
    assert "steer" in caps
    assert "interrupt" in caps


def test_claude_resident_still_has_resident_run():
    caps = _default_capabilities_for("claude-code", "resident", "session-x", {})
    assert "resident-run" in caps


def test_codex_managed_has_full_set():
    caps = _default_capabilities_for("codex", "managed", "", {})
    assert "managed-run" in caps
    assert "interrupt" in caps
    assert "steer" in caps


def test_opencode_managed_no_steer():
    # OpencodeAdapter.supports_steering == False
    caps = _default_capabilities_for("opencode", "managed", "", {})
    assert "managed-run" in caps
    assert "interrupt" in caps
    assert "steer" not in caps
```

- [ ] **Step 3: Run to verify some failures**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_default_capabilities_adapter.py -v`
Expected: FAIL — at least `test_pi_resident_no_longer_advertises_resident_run` fails because today's hardcoded branch returns `["resident-run", "resume", "interrupt", "steer"]` for pi resident.

- [ ] **Step 4: Update `_default_capabilities_for` to derive from adapter**

Replace the function body in `service/routers/api_v2.py:831-...`:

```python
def _default_capabilities_for(
    runtime: str,
    session_mode: str,
    session_handle: str,
    runtime_config: dict[str, Any],
) -> list[str]:
    """Build the default capability list for an agent registration.

    Plan 2 (2026-05-25): derives from the runtime adapter's supports_* flags
    so the per-runtime hardcoded branches collapse into one rule. The session
    mode + handle + runtime_config still gate per-agent capability (e.g. a
    hermes resident without a live gatewayUrl gets no `resident-run`).
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
        # Resident-capable only when the adapter declares it AND, for
        # gateway-backed runtimes, the gateway URL is present.
        gateway_ok = True
        if runtime_n == "hermes":
            gw = str((runtime_config or {}).get("gatewayUrl", "")).strip()
            gateway_ok = bool(gw)
        if adapter.supports_resident and gateway_ok:
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

    # `spawn` capability is independent — every aify-comms managed-capable
    # runtime supports being spawned by another agent's environment.
    if session_mode_n != "resident" and adapter.supports_managed:
        caps.append("spawn")

    return caps
```

CAREFUL: the existing `_default_capabilities_for` function may have additional behavior (e.g., `native-managed-run` for pi). Read the existing function thoroughly before replacing. If it sets `native-managed-run` for pi managed, **drop that** — Plan 2's pi flip removes native managed for pi entirely. If it sets `spawn` more selectively, preserve that nuance.

If the existing implementation has shape this replacement doesn't capture, STOP and report BLOCKED with the diff so the controller can adjust.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_default_capabilities_adapter.py -v`
Expected: PASS — 5 tests green.

- [ ] **Step 6: Run the existing regression suite**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_api_v2_regressions.py -v -k "capabilities or capability"`
Expected: PASS. If any existing test pins hardcoded values like `assert caps == ["managed-run", "native-managed-run", "resume", "interrupt", "steer", "spawn"]` for pi managed, it'll fail because `native-managed-run` is gone. UPDATE those tests inline to match the new adapter-derived shape (similar to how Task 11 of Plan 1 inverted the codex carve-out test).

- [ ] **Step 7: Commit**

```bash
cd C:/Docker/aify-comms
git add service/routers/api_v2.py service/tests/test_default_capabilities_adapter.py service/tests/test_api_v2_regressions.py
git commit -m "feat(server): _default_capabilities_for derives from RuntimeAdapter"
```

---

## Task 15: Bridge consumer migration — `defaultCapabilitiesForRuntime` + `controlCapabilitiesForRuntime`

**Files:**
- Modify: `mcp/stdio/runtimes.js` — find `defaultCapabilitiesForRuntime` and `controlCapabilitiesForRuntime` exports
- Modify: `mcp/stdio/tests/pi-runtime.test.js` (and similar) — verify they still pass
- Create: `mcp/stdio/tests/capabilities-from-adapter.test.js`

- [ ] **Step 1: Find existing implementations**

Run: `cd C:/Docker/aify-comms && grep -n "defaultCapabilitiesForRuntime\|controlCapabilitiesForRuntime" mcp/stdio/runtimes.js | head -10`

Expected: identifies the exports (around line 80-120 per earlier task references).

Read them with: `cd C:/Docker/aify-comms && sed -n '60,180p' mcp/stdio/runtimes.js`

Expected: hardcoded per-runtime branches similar to the Python side.

- [ ] **Step 2: Write failing test**

Create `mcp/stdio/tests/capabilities-from-adapter.test.js`:

```javascript
// Regression: bridge-side capability helpers now derive from the adapter.
// Pi resident no longer advertises `resident-run` (Plan 2 pi flip).
import assert from "assert";
import test from "node:test";
import {
  defaultCapabilitiesForRuntime,
  controlCapabilitiesForRuntime,
} from "../runtimes.js";

test("pi resident no longer advertises resident-run", () => {
  const caps = defaultCapabilitiesForRuntime("pi", "resident", "session-x", {});
  assert.ok(!caps.includes("resident-run"),
    `pi resident must not have resident-run after Plan 2 — got ${JSON.stringify(caps)}`);
});

test("pi managed still has managed-run + steer + interrupt", () => {
  const caps = defaultCapabilitiesForRuntime("pi", "managed", "", {});
  assert.ok(caps.includes("managed-run"));
  assert.ok(caps.includes("steer"));
  assert.ok(caps.includes("interrupt"));
});

test("claude resident still has resident-run", () => {
  const caps = defaultCapabilitiesForRuntime("claude-code", "resident", "session-x", {});
  assert.ok(caps.includes("resident-run"));
});

test("opencode managed has no steer", () => {
  const caps = defaultCapabilitiesForRuntime("opencode", "managed", "", {});
  assert.ok(caps.includes("managed-run"));
  assert.ok(caps.includes("interrupt"));
  assert.ok(!caps.includes("steer"));
});

test("controlCapabilitiesForRuntime derives from adapter for pi", () => {
  const caps = controlCapabilitiesForRuntime("pi");
  // PiAdapter.supports_steering == true, supports_interrupt == true
  assert.strictEqual(caps.interrupt, true);
  assert.strictEqual(caps.steer, true);
});

test("controlCapabilitiesForRuntime derives from adapter for opencode", () => {
  const caps = controlCapabilitiesForRuntime("opencode");
  // OpencodeAdapter.supports_steering == false, supports_interrupt == true
  assert.strictEqual(caps.interrupt, true);
  assert.strictEqual(caps.steer, false);
});
```

- [ ] **Step 3: Run to verify failures**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/capabilities-from-adapter.test.js`
Expected: FAIL — pi resident still has resident-run.

- [ ] **Step 4: Update both helpers in `mcp/stdio/runtimes.js`**

Find `export function defaultCapabilitiesForRuntime(...)` and replace its body with the adapter-driven derivation:

```javascript
import { adapterFor } from "./adapters/index.js";

export function defaultCapabilitiesForRuntime(runtime, sessionMode, sessionHandle, runtimeConfig) {
  // Plan 2 (2026-05-25): derive from RuntimeAdapter instead of hardcoded
  // per-runtime branches.
  const runtimeN = normalizeRuntime(runtime || "");
  let adapter;
  try { adapter = adapterFor(runtimeN); } catch { return []; }

  const caps = [];
  const sessionModeN = String(sessionMode || "").toLowerCase();

  if (sessionModeN === "resident") {
    // Resident-capable only when adapter declares it AND, for gateway-backed
    // runtimes, the gateway URL is present.
    let gatewayOk = true;
    if (runtimeN === "hermes") {
      const gw = String((runtimeConfig || {}).gatewayUrl || "").trim();
      gatewayOk = !!gw;
    }
    if (adapter.supportsResident && gatewayOk) caps.push("resident-run");
  } else {
    if (adapter.supportsManaged) caps.push("managed-run");
  }

  if (adapter.supportsResident || adapter.supportsManaged) caps.push("resume");
  if (adapter.supportsInterrupt) caps.push("interrupt");
  if (adapter.supportsSteering) caps.push("steer");

  if (sessionModeN !== "resident" && adapter.supportsManaged) caps.push("spawn");

  return caps;
}

export function controlCapabilitiesForRuntime(runtime) {
  const runtimeN = normalizeRuntime(runtime || "");
  try {
    const a = adapterFor(runtimeN);
    return { interrupt: a.supportsInterrupt, steer: a.supportsSteering };
  } catch {
    return { interrupt: false, steer: false };
  }
}
```

Make sure the new `import { adapterFor } from "./adapters/index.js";` line is added near the top of `runtimes.js` if it isn't already (Plan 1 may have already added it).

- [ ] **Step 5: Run new test + existing pi-runtime test**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/capabilities-from-adapter.test.js mcp/stdio/tests/pi-runtime.test.js`
Expected: New test green. Pi-runtime test may need updates if it asserts old hardcoded values like `defaultCapabilitiesForRuntime("pi", "resident", "session-123")` returns `["resident-run","resume","interrupt","steer"]`. Update the existing assertions to match the new pi-resident-empty shape: `assert.deepEqual(defaultCapabilitiesForRuntime("pi", "resident", "session-123"), []);` if that's what comes out, or accept whatever the adapter-derived result is.

If the existing pi-runtime.test.js asserts pi-resident capabilities that no longer hold, update inline.

- [ ] **Step 6: Run the broader bridge suite**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/pi-runtime.test.js mcp/stdio/tests/environment-runtimes.test.js mcp/stdio/tests/dispatch-state.test.js`
Expected: PASS, with any pi-resident-capability assertions updated to match the new shape.

- [ ] **Step 7: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/runtimes.js mcp/stdio/tests/capabilities-from-adapter.test.js mcp/stdio/tests/pi-runtime.test.js
git commit -m "feat(bridge): defaultCapabilitiesForRuntime + controlCapabilitiesForRuntime derive from adapter"
```

---

## Task 16: Pi flip pending-flag detection at registration time

**Files:**
- Modify: `service/routers/api_v2.py` — `register_agent` handler around line 8252
- Create: `service/tests/test_pi_resident_flip.py`

- [ ] **Step 1: Write failing test**

Create `service/tests/test_pi_resident_flip.py`:

```python
"""Pi flip mechanics — Plan 2.

When a pi agent attempts to register as sessionMode=resident, the server
sets agents.runtime_state.pi_resident_pending_flip = true. The
_drain_and_flip_pi_resident_agents helper flips it to managed once active
runs drain.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import json
import pytest
from fastapi.testclient import TestClient

# Test-fixture imports — same conftest pattern used elsewhere in service/tests/
from service.tests.conftest_helpers import build_test_app, register_agent, get_agent_row  # noqa: E402


@pytest.fixture
def app():
    return build_test_app()


@pytest.fixture
def client(app):
    return TestClient(app)


def test_pi_resident_registration_marks_pending_flip(client):
    # Register a pi agent claiming sessionMode=resident
    resp = client.post("/api/v1/register", json={
        "agentId": "test-pi-flip",
        "role": "tester",
        "runtime": "pi",
        "sessionMode": "resident",
        "sessionHandle": "session-handle-x",
    })
    assert resp.status_code == 200
    # Check the resulting runtime_state has the pending flip flag
    row = get_agent_row(client, "test-pi-flip")
    rs = json.loads(row["runtime_state"] or "{}")
    assert rs.get("pi_resident_pending_flip") is True, (
        f"pi resident registration must mark pending flip; got runtime_state={rs}"
    )


def test_pi_managed_registration_does_not_mark_pending_flip(client):
    resp = client.post("/api/v1/register", json={
        "agentId": "test-pi-managed",
        "role": "tester",
        "runtime": "pi",
        "sessionMode": "managed",
    })
    assert resp.status_code == 200
    row = get_agent_row(client, "test-pi-managed")
    rs = json.loads(row["runtime_state"] or "{}")
    assert rs.get("pi_resident_pending_flip") is None
```

NOTE: This test relies on `service/tests/conftest_helpers.py` providing `build_test_app`, `register_agent`, `get_agent_row`. If those don't exist, the implementer may need to create them mirroring the pattern in `service/tests/test_api_v2_regressions.py`. STOP and ask if conftest_helpers doesn't exist and the pattern is unclear.

Alternative simpler test approach if conftest_helpers is absent: write a focused unit-test of the helper function directly (`_apply_pi_resident_pending_flip_flag`), bypassing the FastAPI register handler.

- [ ] **Step 2: Run to verify failure**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_pi_resident_flip.py::test_pi_resident_registration_marks_pending_flip -v`
Expected: FAIL — the flag isn't set today.

- [ ] **Step 3: Add the pending-flip detection in `register_agent`**

Find the `register_agent` handler at `service/routers/api_v2.py:8252`. Read enough context to find where the agent's `runtime_state` is computed/serialized before the UPSERT.

Add this logic BEFORE the runtime_state serialization. Roughly (locate the right place by reading the surrounding code):

```python
# Plan 2 (2026-05-25) pi flip: detect a pi resident registration and mark
# it pending-flip so the drain loop can migrate it once active runs drain.
# Once flipped, agents.session_mode becomes "managed" and capabilities are
# recomputed from PiAdapter (supports_resident=False).
if normalized_runtime == "pi" and normalized_session_mode == "resident":
    if isinstance(runtime_state, dict):
        runtime_state["pi_resident_pending_flip"] = True
```

(`runtime_state` is the local variable holding the dict before `json.dumps`. Adapt to the actual variable name in your reading.)

- [ ] **Step 4: Run test to verify pass**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_pi_resident_flip.py::test_pi_resident_registration_marks_pending_flip service/tests/test_pi_resident_flip.py::test_pi_managed_registration_does_not_mark_pending_flip -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add service/routers/api_v2.py service/tests/test_pi_resident_flip.py
git commit -m "feat(server): pi resident registration marks runtime_state.pi_resident_pending_flip"
```

---

## Task 17: Pi flip drain helper + ~5s loop

**Files:**
- Modify: `service/routers/api_v2.py` — add `_drain_and_flip_pi_resident_agents` helper + background task
- Modify: `service/tests/test_pi_resident_flip.py` — add drain assertions

- [ ] **Step 1: Append failing drain test**

Append to `service/tests/test_pi_resident_flip.py`:

```python
import asyncio


@pytest.mark.asyncio
async def test_drain_and_flip_when_no_active_run(client, app):
    # Register pi resident, no active runs.
    client.post("/api/v1/register", json={
        "agentId": "test-pi-flip-2",
        "role": "tester",
        "runtime": "pi",
        "sessionMode": "resident",
        "sessionHandle": "session-handle-y",
    })

    # Manually invoke the drain helper
    from service.routers.api_v2 import _drain_and_flip_pi_resident_agents
    await _drain_and_flip_pi_resident_agents()

    row = get_agent_row(client, "test-pi-flip-2")
    assert row["session_mode"] == "managed", (
        f"pi resident agent without active runs should flip to managed. row={row}"
    )
    rs = json.loads(row["runtime_state"] or "{}")
    assert rs.get("pi_resident_pending_flip") is False or rs.get("pi_resident_pending_flip") is None
    assert rs.get("flipped_at"), "flipped_at timestamp should be recorded"
    # Session handle preserved
    assert row["session_handle"] == "session-handle-y"


@pytest.mark.asyncio
async def test_drain_skips_when_active_run(client, app):
    # Register pi resident with an active run
    client.post("/api/v1/register", json={
        "agentId": "test-pi-flip-3",
        "role": "tester",
        "runtime": "pi",
        "sessionMode": "resident",
        "sessionHandle": "session-handle-z",
    })
    # Simulate an active run for this agent — direct DB row insert into runs
    # table with status='running'.
    # (Detail of how to do this depends on test fixture helpers.)
    from service.tests.conftest_helpers import insert_running_run
    insert_running_run(client, agent_id="test-pi-flip-3", run_id="run-active")

    from service.routers.api_v2 import _drain_and_flip_pi_resident_agents
    await _drain_and_flip_pi_resident_agents()

    row = get_agent_row(client, "test-pi-flip-3")
    assert row["session_mode"] == "resident", "should still be resident while run active"
    rs = json.loads(row["runtime_state"] or "{}")
    assert rs.get("pi_resident_pending_flip") is True
```

- [ ] **Step 2: Run to verify failures**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_pi_resident_flip.py -v`
Expected: FAIL — the helper doesn't exist.

- [ ] **Step 3: Implement `_drain_and_flip_pi_resident_agents`**

Add to `service/routers/api_v2.py` (placement: near the other `_drain_*` or background-task helpers; if none, near the top of the file alongside `_apply_managed_runtime_defaults`):

```python
async def _drain_and_flip_pi_resident_agents() -> None:
    """Pi delivery flip (Plan 2, 2026-05-25). Every ~5s the server checks
    for pi agents marked with runtime_state.pi_resident_pending_flip = True.
    If no active or queued dispatch run blocks the flip, the agent is
    migrated from sessionMode=resident to sessionMode=managed. Existing
    session_handle is preserved. capabilities are recomputed from PiAdapter
    (supports_resident=False)."""
    from service.runtimes import adapter_for

    db = await get_db()
    try:
        now_iso = _now()
        cursor = await db.execute(
            """
            SELECT id, session_handle, runtime_state, runtime_config
            FROM agents
            WHERE runtime = 'pi'
              AND session_mode = 'resident'
            """
        )
        rows = await cursor.fetchall()
        if not rows:
            return

        pi_adapter = adapter_for("pi")

        for row in rows:
            runtime_state = _json_loads_or(row["runtime_state"], {})
            if not runtime_state.get("pi_resident_pending_flip"):
                continue

            # Check for any active or queued run blocking the flip
            run_cursor = await db.execute(
                """
                SELECT COUNT(*) AS cnt FROM runs
                WHERE target_agent_id = ?
                  AND status IN ('queued', 'claimed', 'running')
                """,
                (row["id"],),
            )
            run_row = await run_cursor.fetchone()
            if run_row and int(run_row["cnt"] or 0) > 0:
                continue  # wait until next tick

            # Drain complete — flip
            runtime_state["pi_resident_pending_flip"] = False
            runtime_state["flipped_at"] = now_iso

            new_caps = _default_capabilities_for(
                "pi", "managed",
                str(row["session_handle"] or ""),
                _json_loads_or(row["runtime_config"], {}),
            )

            await db.execute(
                """
                UPDATE agents
                SET session_mode = 'managed',
                    runtime_state = ?,
                    capabilities = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    json.dumps(runtime_state),
                    json.dumps(new_caps),
                    now_iso,
                    row["id"],
                ),
            )
            await db.commit()
    finally:
        await db.close()
```

Also wire the helper into the FastAPI startup so it runs every 5 seconds. Find the existing background-task setup in api_v2.py (search for `asyncio.create_task` or `@router.on_event`). Add:

```python
async def _pi_resident_flip_loop():
    while True:
        try:
            await _drain_and_flip_pi_resident_agents()
        except Exception:
            pass  # best effort; next tick retries
        await asyncio.sleep(5.0)


@router.on_event("startup")
async def _start_pi_resident_flip_loop():
    asyncio.create_task(_pi_resident_flip_loop())
```

If `@router.on_event` isn't used elsewhere, integrate however the codebase already kicks off long-running tasks. STOP and report BLOCKED if the existing async-task lifecycle isn't clear.

- [ ] **Step 4: Run tests to verify pass**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_pi_resident_flip.py -v`
Expected: PASS — drain helper works.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add service/routers/api_v2.py service/tests/test_pi_resident_flip.py
git commit -m "feat(server): _drain_and_flip_pi_resident_agents + 5s background loop"
```

---

## Task 18: Reject new resident pi dispatches with 409 during pending-flip window

**Files:**
- Modify: `service/routers/api_v2.py` — dispatch creation handler
- Modify: `service/tests/test_pi_resident_flip.py`

- [ ] **Step 1: Append failing test**

Append to `service/tests/test_pi_resident_flip.py`:

```python


def test_resident_pi_dispatch_rejected_during_pending_flip(client):
    # Register pi resident → pending-flip flag is set
    client.post("/api/v1/register", json={
        "agentId": "test-pi-flip-4",
        "role": "tester",
        "runtime": "pi",
        "sessionMode": "resident",
        "sessionHandle": "session-handle-w",
    })
    # Attempt a resident dispatch to this agent
    resp = client.post("/api/v1/dispatch", json={
        "targetAgentId": "test-pi-flip-4",
        "executionMode": "resident",
        "message": {"subject": "test", "body": "hello"},
        "from": "operator",
    })
    assert resp.status_code == 409, (
        f"expected 409 during pending pi flip, got {resp.status_code} body={resp.text}"
    )
    assert "migrating" in resp.text.lower() or "pending" in resp.text.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_pi_resident_flip.py::test_resident_pi_dispatch_rejected_during_pending_flip -v`
Expected: FAIL — currently the dispatch goes through (or fails for a different reason).

- [ ] **Step 3: Add the 409 gate in the dispatch creator**

Find the dispatch creation handler (likely `create_dispatch` or `_create_dispatch_runs` in `service/routers/api_v2.py`). Add a pre-flight check:

```python
# Plan 2 pi flip: reject resident dispatches to a pi agent currently
# marked pending-flip. Operator should retry once the drain completes
# (a few seconds at most).
if execution_mode == "resident":
    target_row = ...  # get target agent row (use existing query)
    runtime_state = _json_loads_or(target_row["runtime_state"], {}) if target_row else {}
    if runtime_state.get("pi_resident_pending_flip"):
        raise HTTPException(
            409,
            f"Agent '{target_agent_id}' is migrating from resident to managed "
            f"(Plan 2 pi flip). Retry in a few seconds — the drain loop will "
            f"flip it once active runs complete."
        )
```

Adapt to wherever the dispatch handler reads the target agent row.

- [ ] **Step 4: Run test to verify pass**

Run: `cd C:/Docker/aify-comms && python -m pytest service/tests/test_pi_resident_flip.py -v`
Expected: PASS — all flip-related tests green.

- [ ] **Step 5: Commit**

```bash
cd C:/Docker/aify-comms
git add service/routers/api_v2.py service/tests/test_pi_resident_flip.py
git commit -m "feat(server): reject resident pi dispatches with 409 during pending flip"
```

---

## Task 19: Remove `pi-session-resume` from the bridge dispatch entry table

**Files:**
- Modify: `mcp/stdio/runtimes.js` — remove the resident-pi branch from the dispatch dispatcher
- Modify: `mcp/stdio/tests/pi-runtime.test.js` — drop/update tests asserting pi-session-resume behavior

- [ ] **Step 1: Find the dispatch entry table**

Run: `cd C:/Docker/aify-comms && grep -n "pi-session-resume\|createPiController\|launchRuntimeRun" mcp/stdio/runtimes.js | head -20`

Expected: identifies where `createPiController` is invoked for resident-mode pi and any references to `pi-session-resume` wake-mode handling.

- [ ] **Step 2: Write failing test**

Append to `mcp/stdio/tests/pi-runtime.test.js` (or create a new `mcp/stdio/tests/pi-no-resident-dispatch.test.js`):

```javascript

test("createPiController returns null/no-op for resident-mode pi (Plan 2 pi flip)", () => {
  const { createPiController } = await import("../runtimes.js");
  const result = createPiController({
    runtime: "pi",
    executionMode: "resident",
    sessionHandle: "h",
    agentInfo: { agent_id: "x", runtime: "pi" },
  });
  // The exact contract depends on existing impl; either the controller
  // returns null, returns an object with a no-op start(), or throws a clear
  // "not supported" error. Pick whichever shape matches the existing code
  // path's "this runtime mode is rejected" return value.
  assert.ok(result === null || result?.disabled === true,
    "pi resident dispatch must be rejected after Plan 2 — got " + JSON.stringify(result));
});
```

NOTE: this test's assertion may need to be adjusted based on what the existing createPiController returns when the new code-path falls through. The implementer should read the existing createPiController to pick the right rejection shape.

- [ ] **Step 3: Run to verify failure**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/pi-runtime.test.js`
Expected: FAIL (test new, code old).

- [ ] **Step 4: Update `createPiController` and the dispatch entry table**

In `mcp/stdio/runtimes.js`, find `createPiController` (or wherever the bridge picks pi controllers based on executionMode). Update the resident branch to return null/no-op so the dispatch loop doesn't claim resident pi runs:

```javascript
export function createPiController(opts) {
  if (opts.executionMode === "resident") {
    // Plan 2 (2026-05-25): pi resident is removed in favor of
    // managed-via-wrapper. PiAdapter.supportsResident == false. The server
    // graceful drain migrates existing resident-pi agents on next bridge
    // launch; any race-stragglers fall through here and are rejected.
    return null;
  }
  // ... existing managed-mode body unchanged ...
}
```

Also remove `pi-session-resume` from any wake-mode lookup tables in `runtimes.js` if it's still listed (search and remove).

- [ ] **Step 5: Run test to verify pass**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/pi-runtime.test.js mcp/stdio/tests/capabilities-from-adapter.test.js`
Expected: PASS.

- [ ] **Step 6: Run broader bridge tests for regressions**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/environment-runtimes.test.js mcp/stdio/tests/dispatch-state.test.js mcp/stdio/tests/dispatch-execution.test.js`
Expected: PASS, updating any test that asserts pi resident dispatch goes through to match the new rejection shape.

- [ ] **Step 7: Commit**

```bash
cd C:/Docker/aify-comms
git add mcp/stdio/runtimes.js mcp/stdio/tests/pi-runtime.test.js
git commit -m "feat(bridge): remove pi-session-resume dispatch path (Plan 2 pi flip)"
```

---

## Task 20: Docs + smoke + push

**Files:**
- Modify: `DECISIONS.md` — append Plan 2 entry
- Modify: `README.md` — add `service/runtimes/` to repo layout

- [ ] **Step 1: Append to DECISIONS.md**

Append:

```markdown

## 2026-05-25 — Plan 2: Runtime capabilities + pi delivery flip

**Decision:** Extend the Plan 1 `RuntimeAdapter` foundation with six capability properties (`supportsResident`, `supportsManaged`, `supportsSteering`, `supportsInterrupt`, `supportsMultiClient`, `preferredDeliveryMode`) implemented in both languages — JS adapter classes in `mcp/stdio/adapters/` (Plan 1 location) and a new mirror Python package at `service/runtimes/`. Cross-language consistency enforced by `service/tests/test_runtime_adapter_consistency.py` running `node mcp/stdio/scripts/dump-capabilities.mjs`.

Drop the `pi-session-resume` spawn-fresh-worker delivery pattern. Pi resident agents auto-migrate to managed-via-wrapper on next bridge launch via a graceful drain (waits for active runs to complete; `runtime_state.pi_resident_pending_flip` flag visible to dashboard). New resident pi dispatches during the pending-flip window are rejected with 409 + clear explanation.

Migrate the consumer call sites pi-flip touches: `_managed_via_wrapper_for_runtime`, `_default_capabilities_for`, `defaultCapabilitiesForRuntime`, `controlCapabilitiesForRuntime`. Remaining per-runtime branches in api_v2.py (`_default_console_command`, dispatch dispatcher, delivery shims) defer to Plan 3.

**Why:** Operator-reported recurring pi pain (`--model unknown`, `No API key for cloudflare-ai-gateway`) traced to the spawn-fresh-worker pattern. Pi's single-client RPC mutex makes resident impossible without a multi-client gateway omp doesn't provide. The fix is structural: pi joins the same wrapper-backing pattern as managed claude/hermes/codex.

**Plan 3 (next):** Fill in `consoleCommand`, `injectMessage`, `interrupt`, `steer` on both adapter packages and migrate the remaining per-runtime branches.

**See:** `docs/superpowers/specs/2026-05-25-runtime-adapter-plan2-capabilities-design.md`, `docs/superpowers/plans/2026-05-25-plan2-runtime-capabilities-and-pi-flip.md`.
```

- [ ] **Step 2: Update README.md**

Find the Repo layout section (introduced in Plan 1). Add an entry under `mcp/stdio/adapters/`:

```markdown
| `service/runtimes/` | Python mirror of `mcp/stdio/adapters/` — runtime capabilities + Plan 3 console/delivery (per-language adapter packages so server and bridge can each own their concerns). See `docs/superpowers/specs/2026-05-25-runtime-adapter-plan2-capabilities-design.md`. |
```

- [ ] **Step 3: Full Node + Python smoke suite**

Run: `cd C:/Docker/aify-comms && node --test mcp/stdio/tests/adapters/*.test.js mcp/stdio/tests/capabilities-from-adapter.test.js mcp/stdio/tests/pi-runtime.test.js && python -m pytest service/tests/runtimes/ service/tests/test_runtime_adapter_consistency.py service/tests/test_managed_via_wrapper_adapter.py service/tests/test_default_capabilities_adapter.py service/tests/test_pi_resident_flip.py -v`
Expected: ALL GREEN.

- [ ] **Step 4: Rebuild container**

Run: `cd C:/Docker/aify-comms && docker compose up -d --build 2>&1 | tail -10`
Expected: `aify-comms-service` `Up X seconds (healthy)`.

- [ ] **Step 5: Confirm health + git status**

Run: `cd C:/Docker/aify-comms && curl -4 -s http://127.0.0.1:8800/health && echo "" && git status --short && git log --oneline -22`
Expected: `{"status":"healthy"}`, working tree clean except pre-existing unstaged files, last ~20 commits are Plan 2 work.

- [ ] **Step 6: Push**

Run: `cd C:/Docker/aify-comms && git add DECISIONS.md README.md && git commit -m "docs: record Plan 2 runtime capabilities + pi flip decision" && git push origin feature/dashboard-console-mode 2>&1 | tail -10`
Expected: branch updated on origin.

---

## After all tasks complete

Announce: "I'm using the finishing-a-development-branch skill to complete this work."

REQUIRED SUB-SKILL: Use `superpowers:finishing-a-development-branch`.
