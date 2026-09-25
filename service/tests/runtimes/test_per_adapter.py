"""Per-adapter capability + identity assertions. The expected values are
locked by the Plan 2 spec (docs/superpowers/specs/2026-05-25-runtime-adapter-plan2-capabilities-design.md)."""

def test_claude_adapter():
    from service.runtimes.claude import ClaudeAdapter
    a = ClaudeAdapter()
    assert a.name == "claude-code"
    assert a.session_env_vars == ["CLAUDE_SESSION_ID"]
    assert a.supports_resident is True
    assert a.supports_managed is True
    assert a.supports_steering is True
    assert a.supports_interrupt is True
    assert a.preferred_delivery_mode == "managed-via-wrapper"


def test_codex_adapter():
    from service.runtimes.codex import CodexAdapter
    a = CodexAdapter()
    assert a.name == "codex"
    assert a.session_env_vars == ["CODEX_THREAD_ID"]
    assert a.supports_resident is True
    assert a.supports_managed is True
    assert a.supports_steering is True
    assert a.supports_interrupt is True
    assert a.preferred_delivery_mode == "managed-via-wrapper"


def test_hermes_adapter():
    from service.runtimes.hermes import HermesAdapter
    a = HermesAdapter()
    assert a.name == "hermes"
    assert a.session_env_vars == ["HERMES_SESSION_ID", "HERMES_SESSION"]
    assert a.supports_resident is True
    assert a.supports_managed is True
    # Gateway-backed Hermes uses native session.steer; ACP fallback strips it per session.
    assert a.supports_steering is True
    assert a.supports_interrupt is True
    assert a.preferred_delivery_mode == "managed-via-wrapper"


def test_pi_adapter():
    from service.runtimes.pi import PiAdapter
    a = PiAdapter()
    assert a.name == "pi"
    assert a.session_env_vars == ["PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID"]
    # Pi is single-client RPC. Keep managed delivery native so chat and
    # Console share the same synthesized terminal stream.
    assert a.supports_resident is False, "pi is single-client RPC; resident impossible"
    assert a.supports_managed is True
    assert a.supports_steering is True
    assert a.supports_interrupt is True
    assert a.preferred_delivery_mode == "managed"


def test_opencode_adapter():
    from service.runtimes.opencode import OpencodeAdapter
    a = OpencodeAdapter()
    assert a.name == "opencode"
    assert a.session_env_vars == ["OPENCODE_SESSION_ID", "OPENCODE_SESSION"]
    # Managed OpenCode steers through the per-run server's promptAsync endpoint.
    assert a.supports_resident is False
    assert a.supports_managed is True
    assert a.supports_steering is True
    assert a.supports_interrupt is True
    assert a.preferred_delivery_mode == "managed"
