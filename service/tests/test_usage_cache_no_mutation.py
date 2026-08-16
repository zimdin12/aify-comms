"""Reading a quota snapshot must not damage the one in the cache.

`usage_get` suppresses misleading numbers in three tiers — stale, expired, reset-elapsed — by
blanking fields on the way out. The snapshot it blanks is the CACHED one, so the no-mutation
guarantee is what stops a single read from permanently destroying the numbers for every later read.

THAT GUARANTEE IS TWO-PART AND NEITHER HALF IS SUFFICIENT. `usage_get` takes a SHALLOW copy of the
entry, which protects the top-level flags; `_blank_expired_pool` copies each band dict before nulling
its fields, which protects the nested ones. Drop the band copies and a shallow copy still shares the
`weekly` and `five_hour` dicts, so blanking them writes straight through into the cache. Drop the
shallow copy and the top-level `expired`/`unknown` flags stick. Both are asserted here, separately,
because a test that only calls `usage_get` cannot tell which half is doing the work.

The failure is silent and permanent-looking: once a band is nulled in the cache, every subsequent
read returns an em-dash for a pool whose collector is posting fine, and only a restart clears it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from service import usage_cache
from service.usage_cache import (
    STALE_AFTER_SECONDS,
    STALE_EXPIRE_SECONDS,
    _blank_expired_pool,
    usage_get,
    usage_set,
)

SOURCE = "test-pool"


def iso(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat().replace("+00:00", "Z")


def snapshot(age_seconds=0.0, reset_in=3600):
    return {
        "updated_at": iso(age_seconds),
        "plan": "prolite",
        "weekly": {"used_pct": 40, "resets_at": iso(-reset_in), "resets_in": reset_in},
        "five_hour": {"used_pct": 12, "resets_at": iso(-reset_in), "resets_in": reset_in},
    }


@pytest.fixture(autouse=True)
def restore_the_cache():
    saved = dict(usage_cache._USAGE_CACHE)
    try:
        yield
    finally:
        usage_cache._USAGE_CACHE.clear()
        usage_cache._USAGE_CACHE.update(saved)


# ── the two halves of the no-mutation guarantee ──────────────────────────────────────────────
def test_blanking_an_expired_pool_does_not_touch_the_caller_s_band_dicts():
    """The nested half, tested directly. `_blank_expired_pool` mutates the dict it is handed at the
    TOP level by design — the caller passes a copy — but the band dicts must be replaced, not edited,
    or a shallow-copying caller writes straight through into the cache."""
    weekly = {"used_pct": 40, "resets_at": "later", "resets_in": 60}
    five_hour = {"used_pct": 12, "resets_at": "later", "resets_in": 60}
    out = _blank_expired_pool({"weekly": weekly, "five_hour": five_hour})

    assert out["weekly"]["used_pct"] is None, "the returned view is blanked"
    assert weekly == {"used_pct": 40, "resets_at": "later", "resets_in": 60}, (
        "the ORIGINAL band dict must be untouched — this is the half that protects the cache"
    )
    assert five_hour["used_pct"] == 12
    assert out["weekly"] is not weekly, "the band was replaced with a copy, not edited in place"


def test_reading_an_expired_pool_leaves_the_cache_intact():
    """Both halves together, through the real entry point. A read must be repeatable."""
    usage_set(SOURCE, snapshot(age_seconds=STALE_EXPIRE_SECONDS + 60))

    first = usage_get(SOURCE)
    assert first["expired"] is True
    assert first["weekly"]["used_pct"] is None

    cached = usage_cache._USAGE_CACHE[SOURCE]
    assert cached["weekly"]["used_pct"] == 40, "the cached numbers survive being read"
    assert "expired" not in cached, "and the flags were written to the copy, not the entry"

    second = usage_get(SOURCE)
    assert second == first, "a second read sees the same thing — the first did not consume it"


def test_a_pool_that_becomes_fresh_again_reports_its_numbers():
    """The consequence of the guarantee, stated end to end: if a read had nulled the cache, a fresh
    POST would be the only way back, and a pool whose collector merely hiccuped would stay blank."""
    usage_set(SOURCE, snapshot(age_seconds=STALE_EXPIRE_SECONDS + 60))
    assert usage_get(SOURCE)["weekly"]["used_pct"] is None

    usage_set(SOURCE, snapshot(age_seconds=1))
    fresh = usage_get(SOURCE)
    assert fresh["weekly"]["used_pct"] == 40
    assert "expired" not in fresh, "the flag is ABSENT when not expired, never present-and-false"
    assert fresh["stale"] is False


# ── the three suppression tiers ──────────────────────────────────────────────────────────────
def test_a_fresh_snapshot_is_neither_stale_nor_expired():
    usage_set(SOURCE, snapshot(age_seconds=1))
    out = usage_get(SOURCE)
    assert out["stale"] is False
    assert "expired" not in out
    assert out["five_hour"]["used_pct"] == 12, "a fresh number is shown"


def test_a_stale_snapshot_is_dimmed_but_keeps_its_numbers():
    """Stale is the hiccup tier: the collector missed a beat, and last-good beats nothing."""
    usage_set(SOURCE, snapshot(age_seconds=STALE_AFTER_SECONDS + 60))
    out = usage_get(SOURCE)
    assert out["stale"] is True
    assert "expired" not in out
    assert out["five_hour"]["used_pct"] == 12, "the number is kept, just marked stale"


def test_an_expired_snapshot_is_blanked_entirely():
    """Expired is the collector-stopped tier: after ~24h the number is not evidence of anything."""
    usage_set(SOURCE, snapshot(age_seconds=STALE_EXPIRE_SECONDS + 60))
    out = usage_get(SOURCE)
    assert out["stale"] is True and out["expired"] is True and out["unknown"] is True
    for band in ("weekly", "five_hour"):
        assert out[band]["used_pct"] is None
        assert out[band]["resets_at"] is None
        assert out[band]["resets_in"] is None
    assert out["plan"] == "prolite", "identifying fields survive — only the numbers are unknown"


def test_an_unparseable_timestamp_is_treated_as_maximally_stale():
    """An unknown age must fail toward suppression, not toward showing the number: a snapshot whose
    stamp cannot be read is a snapshot whose freshness nobody knows."""
    usage_set(SOURCE, {"updated_at": "not a date", "weekly": {"used_pct": 40}})
    out = usage_get(SOURCE)
    assert out["stale"] is True
    assert out["expired"] is True
    assert out["weekly"]["used_pct"] is None


def test_an_unknown_pool_is_none():
    assert usage_get("no-such-pool") is None


def test_the_source_id_is_stamped_on_write():
    usage_set(SOURCE, snapshot())
    assert usage_get(SOURCE)["source_id"] == SOURCE, "consumers key on this rather than on dict order"


def test_usage_set_copies_its_payload():
    """The caller's dict is somebody else's object — a collector that reuses a buffer must not be
    able to rewrite what is already cached."""
    payload = snapshot()
    usage_set(SOURCE, payload)
    payload["plan"] = "MUTATED"
    assert usage_get(SOURCE)["plan"] == "prolite"
