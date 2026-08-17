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


def test_claude_adapter_overrides_discover_session_id():
    from service.runtimes.claude import ClaudeAdapter
    base = ClaudeAdapter.__mro__[1]
    assert ClaudeAdapter.discover_session_id is not base.discover_session_id, (
        "ClaudeAdapter must override discover_session_id"
    )


def test_claude_adapter_discovery_is_UNCONDITIONALLY_none():
    """Was an `isinstance(result, str) or None` assertion, which no implementation can fail.

    The real contract is stronger and is the point of the method: claude session discovery is
    bridge-side ONLY, because the JS adapter scopes it to the agent's own cwd. Re-implementing a
    machine-global transcript scan here is what would cross-contaminate agents — so this returns
    None with a live session sitting in the environment, not just on an empty machine."""
    import asyncio
    from service.runtimes.claude import ClaudeAdapter
    assert asyncio.run(ClaudeAdapter().discover_session_id()) is None


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


def test_codex_adapter_discovers_nothing_in_a_SEALED_environment(monkeypatch, tmp_path):
    """Was an `isinstance(result, str) or None` assertion that read the developer's REAL
    `~/.codex/sessions` and their real `CODEX_THREAD_ID` — it passed against a live codex session
    and would have passed just as well against a discovery that had been deleted.

    Sealed: `Path.home()` points at an empty temp directory and the thread variable is unset, so
    "nothing to find" is an input this test controls rather than a fact about the machine."""
    import asyncio
    from pathlib import Path
    from service.runtimes.codex import CodexAdapter

    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert Path.home() == home, "the home seal did not take"

    assert asyncio.run(CodexAdapter().discover_session_id()) is None


def test_hermes_adapter():
    from service.runtimes.hermes import HermesAdapter
    a = HermesAdapter()
    assert a.name == "hermes"
    assert a.display_name == "Hermes"
    assert a.session_env_vars == ["HERMES_SESSION_ID", "HERMES_SESSION"]
    assert a.supports_resident is True
    assert a.supports_managed is True
    # Gateway-backed Hermes uses native session.steer; ACP fallback strips it per session.
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


def test_hermes_adapter_overrides_discover_session_id():
    from service.runtimes.hermes import HermesAdapter
    base = HermesAdapter.__mro__[1]
    assert HermesAdapter.discover_session_id is not base.discover_session_id, (
        "HermesAdapter must override discover_session_id"
    )


# REMOVED: `test_hermes_adapter_discover_session_id_returns_str_or_none`. It unset one variable,
# left the other four and `~/.hermes/sessions` reading the developer's live machine, and asserted
# only that the answer was a string or None — which no implementation can fail. The whole discovery
# chain is now covered against a sealed env and a sealed home in
# `test_hermes_session_discovery.py`; the three ordering tests below stay because they assert the
# ordering itself.


def test_hermes_adapter_discover_prefers_active_session_file(monkeypatch, tmp_path):
    import asyncio
    from service.runtimes.hermes import HermesAdapter

    active = tmp_path / "active-session.json"
    active.write_text('{"session_id":"visible-session"}', encoding="utf-8")
    monkeypatch.setenv("AIFY_HERMES_ACTIVE_SESSION_FILE", str(active))
    monkeypatch.setenv("HERMES_SESSION_ID", "stale-env-session")
    monkeypatch.setenv("AIFY_HERMES_GATEWAY_URL", "ws://127.0.0.1:9999/api/ws?token=x")

    assert asyncio.run(HermesAdapter().discover_session_id()) == "visible-session"


def test_hermes_adapter_discover_uses_env_before_gateway(monkeypatch):
    import asyncio
    from service.runtimes.hermes import HermesAdapter

    monkeypatch.delenv("AIFY_HERMES_ACTIVE_SESSION_FILE", raising=False)
    monkeypatch.setenv("HERMES_SESSION_ID", "env-session")
    monkeypatch.setenv("AIFY_HERMES_GATEWAY_URL", "ws://127.0.0.1:9999/api/ws?token=x")

    assert asyncio.run(HermesAdapter().discover_session_id()) == "env-session"


def test_hermes_adapter_discover_returns_none_when_only_gateway_is_present(monkeypatch):
    import asyncio
    from service.runtimes.hermes import HermesAdapter

    monkeypatch.delenv("AIFY_HERMES_ACTIVE_SESSION_FILE", raising=False)
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    monkeypatch.delenv("HERMES_SESSION", raising=False)
    monkeypatch.setenv("AIFY_HERMES_GATEWAY_URL", "ws://127.0.0.1:9999/api/ws?token=x")

    assert asyncio.run(HermesAdapter().discover_session_id()) is None


def test_pi_adapter():
    from service.runtimes.pi import PiAdapter
    a = PiAdapter()
    assert a.name == "pi"
    assert a.display_name == "Pi"
    assert a.session_env_vars == ["PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID"]
    # Pi is single-client RPC. Keep managed delivery native so chat and
    # Console share the same synthesized terminal stream.
    assert a.supports_resident is False, "pi is single-client RPC; resident impossible"
    assert a.supports_managed is True
    assert a.supports_steering is True
    assert a.supports_interrupt is True
    assert a.supports_multi_client is False
    assert a.preferred_delivery_mode == "managed"


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
    # Managed OpenCode steers through the per-run server's promptAsync endpoint.
    assert a.supports_resident is False
    assert a.supports_managed is True
    assert a.supports_steering is True
    assert a.supports_interrupt is True
    assert a.supports_multi_client is False
    assert a.preferred_delivery_mode == "managed"
