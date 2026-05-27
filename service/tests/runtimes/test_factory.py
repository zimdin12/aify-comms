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
