"""EXPIRE keeps a stale-but-true status readable; DROP is a cache miss that serves a fresh falsehood.

`_LIVE_STATE_CACHE` holds each agent's DERIVED status. The five accessors around it look
interchangeable, and two of them are not: `_live_state_expire` marks an entry stale while leaving the
value readable, `_live_state_drop` removes it. Collapsing that distinction is the natural tidy-up
("why keep an entry we have declared stale?") and it reintroduces a bug an operator reported in these
words: "all working agents turn online for a second and then back to working".

The chain, from the function's own docstring: a dropped entry is a cache MISS; the `list_agents`
miss-path falls back to the raw `agents.status` column; every heartbeat stamps that to 'active'; and
`_LEGACY_RAW_STATUS_TO_CANONICAL` coerces 'active' to **online**. That value never passes through
`derive()`, so it can contradict `in_turn=1` — a genuinely working agent is served `online` for one
poll and a status-sorted dashboard yanks the row down the list and back. Working agents heartbeat
most, so they lose the race most, which is why *all* of them flicker.

Invalidation therefore EXPIRES. Real eviction — the agent is gone — still drops. Nothing else in the
suite holds those two apart, and neither raises when confused.

THE CACHE IS A PROCESS GLOBAL, so every test here restores it. That is not politeness: the module's
own docstring records that a duplicated or leaked module-global is the failure mode that survives
every other gate, and a test that leaves entries behind is exactly that leak in miniature.
"""
from __future__ import annotations

import pytest

from service.api_core.serialization import _iso_add_seconds
from service.clock import now as _now
from service.reconcilers import status_cache
from service.reconcilers.status_cache import (
    _live_state_drop,
    _live_state_expire,
    _live_state_fresh,
    _live_state_get,
    _live_state_set,
)

AGENT = "sc-coder"


@pytest.fixture(autouse=True)
def restore_the_process_global():
    """Snapshot and restore, rather than clear: another test's live entry is not this test's to bin."""
    saved = dict(status_cache._LIVE_STATE_CACHE)
    try:
        yield
    finally:
        status_cache._LIVE_STATE_CACHE.clear()
        status_cache._LIVE_STATE_CACHE.update(saved)


def entry(status="working", *, fresh_for=60, now=None):
    base = now or _now()
    return {"status": status, "refresh_after": _iso_add_seconds(base, fresh_for)}


# ── the accessors ────────────────────────────────────────────────────────────────────────────
def test_set_then_get_round_trips():
    _live_state_set(AGENT, entry())
    assert _live_state_get(AGENT)["status"] == "working"


def test_a_missing_agent_is_none_everywhere_rather_than_a_keyerror():
    assert _live_state_get("nobody") is None
    assert _live_state_fresh("nobody") is None
    _live_state_drop("nobody")          # no-op, not a raise
    _live_state_expire("nobody")        # no-op, not a raise
    assert "nobody" not in status_cache._LIVE_STATE_CACHE, (
        "expiring an absent agent must not CREATE an entry — that would cache a status nobody derived"
    )


@pytest.mark.parametrize("spelling", [AGENT, f"  {AGENT}  ", f"\t{AGENT}\n"])
def test_the_agent_id_is_trimmed_on_every_accessor(spelling):
    """A padded id on one path and a clean one on another would be two entries for one agent."""
    _live_state_set(spelling, entry())
    assert _live_state_get(AGENT) is not None
    assert _live_state_fresh(spelling) is not None
    _live_state_drop(spelling)
    assert _live_state_get(AGENT) is None


def test_a_blank_or_none_id_does_not_raise():
    for bad in ("", "   ", None):
        assert _live_state_get(bad) is None
        assert _live_state_fresh(bad) is None
        _live_state_expire(bad)
        _live_state_drop(bad)
    assert "" not in status_cache._LIVE_STATE_CACHE, (
        "all three blanks normalise to the empty key, and none of them may create it"
    )


# ── freshness is a lexical comparison of same-format timestamps ──────────────────────────────
def test_fresh_only_while_refresh_after_is_in_the_future():
    base = _now()
    _live_state_set(AGENT, entry(fresh_for=60, now=base))
    assert _live_state_fresh(AGENT, now=base) is not None
    assert _live_state_fresh(AGENT, now=_iso_add_seconds(base, 30)) is not None
    assert _live_state_fresh(AGENT, now=_iso_add_seconds(base, 120)) is None, "past its refresh_after"


def test_the_boundary_is_strictly_greater_than():
    base = _now()
    _live_state_set(AGENT, entry(fresh_for=60, now=base))
    at = _iso_add_seconds(base, 60)
    assert _live_state_fresh(AGENT, now=at) is None, "equal is NOT fresh — the comparison is >"


def test_an_empty_refresh_after_is_never_fresh():
    _live_state_set(AGENT, {"status": "working", "refresh_after": ""})
    assert _live_state_fresh(AGENT) is None
    _live_state_set(AGENT, {"status": "working"})
    assert _live_state_fresh(AGENT) is None, "a missing key is stale, not an error"


def test_the_comparison_is_lexical_which_only_works_because_the_format_is_fixed():
    """`refresh_after > now` compares STRINGS. `service/clock.now()` documents that changing its
    format is a data migration, not a formatting choice; this is that assumption written down on the
    cache side, where a mixed format would silently make every entry fresh or every entry stale."""
    base = _now()
    later = _iso_add_seconds(base, 60)
    assert len(base) == len(later) == 20 and base.endswith("Z"), f"{base!r} is not the fixed format"
    assert later > base, "same-format UTC seconds sort lexically, which is what freshness relies on"


# ── the distinction this file exists for ─────────────────────────────────────────────────────
def test_expire_keeps_the_derived_value_readable():
    _live_state_set(AGENT, entry("working"))
    _live_state_expire(AGENT)

    still_there = _live_state_get(AGENT)
    assert still_there is not None, "THE POINT: the last DERIVED status survives invalidation"
    assert still_there["status"] == "working", "and it is still the true one"
    assert _live_state_fresh(AGENT) is None, "while a recompute is forced on the next refresh"


def test_drop_removes_the_entry_entirely():
    _live_state_set(AGENT, entry("working"))
    _live_state_drop(AGENT)
    assert _live_state_get(AGENT) is None, "a MISS — this is what falls back to the raw status column"
    assert _live_state_fresh(AGENT) is None


def test_expire_and_drop_are_not_interchangeable():
    """Both make the entry unfresh; only one leaves a readable status. If this ever passes with the
    two swapped, the flicker is back."""
    _live_state_set(AGENT, entry("working"))
    _live_state_expire(AGENT)
    expired = _live_state_get(AGENT)

    _live_state_set(AGENT, entry("working"))
    _live_state_drop(AGENT)
    dropped = _live_state_get(AGENT)

    assert _live_state_fresh(AGENT) is None
    assert expired is not None and dropped is None, (
        "expire and drop agree about freshness and disagree about readability — that is the contract"
    )


def test_expiring_twice_is_stable():
    _live_state_set(AGENT, entry("working"))
    _live_state_expire(AGENT)
    _live_state_expire(AGENT)
    assert _live_state_get(AGENT)["status"] == "working"
    assert _live_state_get(AGENT)["refresh_after"] == ""


def test_a_set_after_an_expire_makes_it_fresh_again():
    """The recompute path: expire marks it for refresh, the refresh writes a new entry."""
    _live_state_set(AGENT, entry("working"))
    _live_state_expire(AGENT)
    assert _live_state_fresh(AGENT) is None
    _live_state_set(AGENT, entry("online"))
    assert _live_state_fresh(AGENT)["status"] == "online"


def test_expire_touches_only_the_named_agent():
    _live_state_set("a", entry("working"))
    _live_state_set("b", entry("online"))
    _live_state_expire("a")
    assert _live_state_fresh("a") is None
    assert _live_state_fresh("b") is not None, "one agent's invalidation is not the fleet's"
