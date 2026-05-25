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
