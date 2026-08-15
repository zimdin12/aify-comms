"""The four channel runtime sets must agree, and the flag gate must apply to exactly their union.

`channel_delivery.py` declares four sets that answer nearly the same question, and its own docstring
records that they have drifted:

    _CHANNEL_MANAGED_RUNTIMES          may a managed agent be woken over the channel
    _CHANNEL_CLAIM_RUNTIMES            may its bridge CLAIM a run          (route vs claim!)
    _CHANNEL_FLAG_GATED_RUNTIMES       is that gated behind the wrapper flag
    _CHANNEL_SIDECAR_DELIVERY_RUNTIMES is delivery done by a standalone sidecar

THE DRIFT THAT ALREADY HAPPENED: Plan 4 set the server ROUTE for wrapper-backed runtimes while the
CLAIM whitelist still held only claude-code. Bridges for codex/hermes/pi never requested the mode,
and the server would have rejected them if they had — a route added without a claim added. Nothing
failed; the runtimes simply never used the path.

That is the shape an agreement test exists for. These are four editable literals with a relationship
between them that lives only in prose, and prose is not read by the suite. Each assertion below names
the consequence of breaking it, so a deliberate change updates a rule rather than deleting a
tripwire.

The sets are deliberately NOT re-derived here — the point is to compare the real ones, not to keep a
second copy of the answer.
"""
from __future__ import annotations

import pytest

from service.api_core.channel_delivery import (
    _CHANNEL_CLAIM_RUNTIMES,
    _CHANNEL_FLAG_GATED_RUNTIMES,
    _CHANNEL_MANAGED_RUNTIMES,
    _CHANNEL_SIDECAR_DELIVERY_RUNTIMES,
    _channel_flag_enabled,
    _channel_managed_eligible,
)

KNOWN_RUNTIMES = ["claude-code", "codex", "hermes", "pi", "opencode", "generic"]


# ── the relationships ────────────────────────────────────────────────────────────────────────
def test_every_routable_runtime_can_also_claim():
    """THE PLAN 4 BUG, as an assertion. A runtime the server routes to the channel whose bridge may
    not CLAIM a channel run gets a route it can never use — silently, because nothing errors."""
    assert _CHANNEL_MANAGED_RUNTIMES <= _CHANNEL_CLAIM_RUNTIMES
    assert _CHANNEL_FLAG_GATED_RUNTIMES <= _CHANNEL_CLAIM_RUNTIMES, (
        "hermes is routed to the channel by the flag gate, so its bridge must be allowed to claim"
    )


def test_the_two_eligibility_sets_do_not_overlap():
    """They mean different things: claude ALWAYS routes to channel once it clears the cap check
    (it has no native managed run), hermes routes there ONLY via the flag and otherwise keeps its
    native managed path. A runtime in both would make that asymmetry unreadable."""
    assert not (_CHANNEL_MANAGED_RUNTIMES & _CHANNEL_FLAG_GATED_RUNTIMES)


def test_sidecar_delivery_is_exactly_the_channel_eligible_union():
    """Delivery is done by a sidecar for precisely the runtimes that can reach the channel path. A
    runtime in the sidecar set but neither eligibility set has a delivery mechanism and no way to be
    routed to it; the reverse is a route to a path with nothing to deliver it."""
    assert _CHANNEL_SIDECAR_DELIVERY_RUNTIMES == (
        _CHANNEL_MANAGED_RUNTIMES | _CHANNEL_FLAG_GATED_RUNTIMES
    )


def test_claim_is_a_superset_and_may_legitimately_be_wider():
    """codex claims without being channel-eligible, which is intentional — it is recorded so a
    future reader does not "fix" the asymmetry by deleting it."""
    extra = _CHANNEL_CLAIM_RUNTIMES - (_CHANNEL_MANAGED_RUNTIMES | _CHANNEL_FLAG_GATED_RUNTIMES)
    assert extra == {"codex"}, (
        f"claim-only runtimes changed to {sorted(extra)}; that is a routing decision, not a typo"
    )


def test_the_sets_are_normalised_runtime_names():
    """`_channel_managed_eligible` normalises its input before comparing, so a set holding an alias
    (e.g. "claude" rather than "claude-code") would never match anything."""
    from service.api_core.runtime import _normalize_runtime

    for name, runtimes in (
        ("managed", _CHANNEL_MANAGED_RUNTIMES),
        ("flag-gated", _CHANNEL_FLAG_GATED_RUNTIMES),
        ("claim", _CHANNEL_CLAIM_RUNTIMES),
        ("sidecar", _CHANNEL_SIDECAR_DELIVERY_RUNTIMES),
    ):
        for runtime in runtimes:
            assert _normalize_runtime(runtime) == runtime, f"{name} set holds un-normalised {runtime!r}"


# ── the gate itself ──────────────────────────────────────────────────────────────────────────
def test_eligibility_is_exactly_the_union_and_only_with_the_flag():
    eligible = {r for r in KNOWN_RUNTIMES if _channel_managed_eligible(r, {"channelEnabled": True})}
    assert eligible == (_CHANNEL_MANAGED_RUNTIMES | _CHANNEL_FLAG_GATED_RUNTIMES)
    assert eligible == {"claude-code", "hermes"}, "recorded explicitly so a set edit is visible here"


@pytest.mark.parametrize("runtime", ["claude-code", "hermes"])
def test_an_eligible_runtime_still_needs_the_wrapper_flag(runtime):
    """Preserves the prior claude contract — no flag and no managed-run capability means REJECTED,
    never a silent channel path — and extends it symmetrically to hermes. Both wrappers export
    AIFY_CHANNELS_ENABLED=1 through the same mechanism."""
    assert _channel_managed_eligible(runtime, {"channelEnabled": True}) is True
    assert _channel_managed_eligible(runtime, {"channelEnabled": False}) is False
    assert _channel_managed_eligible(runtime, {}) is False
    assert _channel_managed_eligible(runtime, None) is False


@pytest.mark.parametrize("runtime", ["codex", "pi", "opencode", "generic", "", None])
def test_an_ineligible_runtime_is_refused_even_with_the_flag_set(runtime):
    """The flag is a wrapper's claim about itself; it must not create eligibility on its own."""
    assert _channel_managed_eligible(runtime, {"channelEnabled": True}) is False


def test_runtime_aliases_reach_the_same_answer():
    """`claude` and `claude-code` are the same runtime — the gate normalises, so both must pass."""
    assert _channel_managed_eligible("claude", {"channelEnabled": True}) is True
    assert _channel_managed_eligible("  HERMES  ", {"channelEnabled": True}) is True


# ── the flag reader ──────────────────────────────────────────────────────────────────────────
def test_the_flag_reader_survives_a_non_dict():
    """`runtime_config` is free-form and arrives from an agent's own registration."""
    for value in (None, "", "channelEnabled", 1, [], ["channelEnabled"]):
        assert _channel_flag_enabled(value) is False


def test_the_flag_is_read_truthily():
    for truthy in (True, 1, "yes", "1"):
        assert _channel_flag_enabled({"channelEnabled": truthy}) is True
    for falsy in (False, 0, "", None):
        assert _channel_flag_enabled({"channelEnabled": falsy}) is False
    assert _channel_flag_enabled({"other": True}) is False
