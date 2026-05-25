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


def test_codex_adapter_overrides_discover_session_id():
    from service.runtimes.codex import CodexAdapter
    base = CodexAdapter.__mro__[1]
    assert CodexAdapter.discover_session_id is not base.discover_session_id, (
        "CodexAdapter must override discover_session_id"
    )


def test_codex_adapter_discover_session_id_returns_str_or_none():
    import asyncio
    from service.runtimes.codex import CodexAdapter
    result = asyncio.run(CodexAdapter().discover_session_id())
    if result is not None:
        assert isinstance(result, str) and len(result) > 0


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


def test_pi_adapter_overrides_discover_session_id():
    from service.runtimes.pi import PiAdapter
    base = PiAdapter.__mro__[1]
    assert PiAdapter.discover_session_id is not base.discover_session_id, (
        "PiAdapter must override discover_session_id"
    )


def test_pi_adapter_discover_session_id_returns_str_or_none():
    """Functional smoke test — if ~/.omp/agent/sessions/ exists on host with
    files, returns a string; otherwise returns None gracefully."""
    import asyncio
    from service.runtimes.pi import PiAdapter
    result = asyncio.run(PiAdapter().discover_session_id())
    if result is not None:
        assert isinstance(result, str) and len(result) > 0


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
