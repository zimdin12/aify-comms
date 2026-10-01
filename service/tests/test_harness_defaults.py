"""The per-harness defaults have one owner, and it is derived from the settings (P0 C12).

The routes that use it are covered in test_api_v2_regressions.py
(`test_managed_spawn_uses_settings_defaults_and_persists_runtime_config`, one case per defaulted runtime).
"""

from __future__ import annotations

from service.api_core.harness_defaults import defaulted_runtimes, defaults_for, with_defaults
from service.api_core.settings import DEFAULT_SETTINGS
from service.runtimes import settings_name, supported_runtimes


def test_a_runtime_has_defaults_exactly_when_its_settings_are_declared():
    declared = [r for r in supported_runtimes()
                if f"managed_{settings_name(r)}_model" in DEFAULT_SETTINGS and f"managed_{settings_name(r)}_effort" in DEFAULT_SETTINGS]
    assert defaulted_runtimes() == declared
    assert "hermes" in declared and "claude-code" in declared, "positive control: the derivation finds what is there"
    assert "opencode" not in declared and defaults_for({}, "opencode") is None, "negative control: no settings, no defaults"
    assert defaults_for({}, "no-such-runtime") is None


def test_an_empty_value_is_filled_and_a_given_one_kept():
    settings = {"managed_codex_model": "gpt-d", "managed_codex_effort": "low"}
    assert with_defaults(settings, "codex", "", {}) == ("gpt-d", {"effort": "low"})
    assert with_defaults(settings, "codex", "mine", {"effort": "xhigh"}) == ("mine", {"effort": "xhigh"})
    assert with_defaults(settings, "codex", "", {"thinking": "high"}) == ("gpt-d", {"thinking": "high"}), \
        "thinking is an effort: the launch reads it as one"


def test_a_saved_empty_effort_falls_back_to_the_declared_default_and_an_empty_default_fills_nothing():
    assert defaults_for({"managed_claude_effort": ""}, "claude-code") == ("", DEFAULT_SETTINGS["managed_claude_effort"])
    assert with_defaults({}, "hermes", "", {}) == ("", {}), "hermes' own configuration decides"
    assert with_defaults({"managed_hermes_effort": "high"}, "hermes", "", {}) == ("", {"effort": "high"})


def test_an_unknown_runtime_is_left_alone():
    assert with_defaults({}, "generic", "m", {"x": 1}) == ("m", {"x": 1})


def test_every_defaulted_runtimes_two_settings_are_read():
    """The behavioural proof test_every_setting_has_a_reader.py leans on: these keys are built from the
    runtime's name, so no literal of them exists for a text scan to find."""
    for runtime in defaulted_runtimes():
        name = settings_name(runtime)
        settings = {f"managed_{name}_model": f"m-{name}", f"managed_{name}_effort": "xhigh"}
        assert defaults_for(settings, runtime) == (f"m-{name}", "xhigh"), runtime
