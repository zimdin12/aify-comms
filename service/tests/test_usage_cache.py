"""Unit tests for the in-memory usage cache + consumption summarizer
(usage/quota feature, 2026-06-26). Pure module — no DB, no I/O."""
from datetime import datetime, timedelta, timezone

from service import usage_cache as uc


def _iso_ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_fresh_pool_keeps_numbers():
    uc._USAGE_CACHE.clear()
    uc.usage_set("openai-chatgpt-codex", {"weekly": {"used_pct": 70, "left_pct": 30}, "updated_at": _iso_ago(60)})
    g = uc.usage_get("openai-chatgpt-codex")
    assert g["stale"] is False and not g.get("expired")
    assert g["weekly"]["left_pct"] == 30


def test_stale_pool_dims_but_keeps_last_good_numbers():
    # 7min–24h: transient collector gap → flagged stale but still shows last-good numbers.
    uc._USAGE_CACHE.clear()
    uc.usage_set("openai-chatgpt-codex", {"weekly": {"used_pct": 70, "left_pct": 30},
                                          "updated_at": _iso_ago(uc.STALE_AFTER_SECONDS + 60)})
    g = uc.usage_get("openai-chatgpt-codex")
    assert g["stale"] is True and not g.get("expired")
    assert g["weekly"]["left_pct"] == 30


def test_expired_pool_blanks_numbers():
    # >24h without a fresh snapshot: numbers are nulled so a days-old quota can't mislead.
    uc._USAGE_CACHE.clear()
    uc.usage_set("openai-chatgpt-codex", {
        "weekly": {"used_pct": 70, "left_pct": 30, "resets_at": "2026-06-01T00:00:00Z"},
        "five_hour": {"used_pct": 10, "left_pct": 90},
        "updated_at": _iso_ago(uc.STALE_EXPIRE_SECONDS + 3600),
    })
    g = uc.usage_get("openai-chatgpt-codex")
    assert g["stale"] is True and g["expired"] is True and g["unknown"] is True
    assert g["weekly"]["left_pct"] is None and g["weekly"]["used_pct"] is None
    assert g["weekly"]["resets_at"] is None
    assert g["five_hour"]["left_pct"] is None
    # Blanking must work on a COPY — the cached entry keeps its real values.
    assert uc._USAGE_CACHE["openai-chatgpt-codex"]["weekly"]["left_pct"] == 30


def test_missing_timestamp_is_expired():
    uc._USAGE_CACHE.clear()
    uc.usage_set("openai-chatgpt-codex", {"weekly": {"left_pct": 30}})  # no updated_at
    g = uc.usage_get("openai-chatgpt-codex")
    assert g["stale"] is True and g["expired"] is True
    assert g["weekly"]["left_pct"] is None


def test_elapsed_reset_window_blanked_even_with_fresh_post():
    # The real codex/hermes symptom: fresh POST (updated_at now) but the window's resets_at is
    # weeks in the past → a stale rollout snapshot. Blank ONLY the elapsed window; keep the other.
    uc._USAGE_CACHE.clear()
    uc.usage_set("openai-chatgpt-codex", {
        "weekly": {"used_pct": 70, "left_pct": 30, "resets_at": _iso_ago(32 * 86400)},  # reset 32d ago
        "five_hour": {"used_pct": 20, "left_pct": 80, "resets_at": "2099-01-01T00:00:00Z"},  # future = live
        "updated_at": _iso_ago(60),  # POST is FRESH — the 24h guard would NOT fire
    })
    g = uc.usage_get("openai-chatgpt-codex")
    assert g["reset_elapsed"] is True and g["unknown"] is True and g["stale"] is True
    assert not g.get("expired")  # fresh POST → not the 24h path
    assert g["weekly"]["left_pct"] is None and g["weekly"]["resets_at"] is None  # dead cycle blanked
    assert g["five_hour"]["left_pct"] == 80  # still-live window untouched


def test_set_get_all_roundtrip():
    uc._USAGE_CACHE.clear()
    uc.usage_set("anthropic-claude-max", {
        "weekly": {"used_pct": 81, "left_pct": 19},
        "five_hour": {"used_pct": 10, "left_pct": 90},
        "severity": "warning",
        "updated_at": _iso_ago(60),  # fresh — a fixed past date would now trip the 24h expiry blank
    })
    g = uc.usage_get("anthropic-claude-max")
    assert g["weekly"]["left_pct"] == 19
    assert g["source_id"] == "anthropic-claude-max"
    assert "stale" in g  # computed, never raises
    pools = uc.usage_all()
    assert any(p["source_id"] == "anthropic-claude-max" for p in pools)


def test_get_missing_returns_none():
    uc._USAGE_CACHE.clear()
    assert uc.usage_get("nope") is None


def test_derive_usage_source():
    assert uc.derive_usage_source("claude-code") == "anthropic-claude-max"
    assert uc.derive_usage_source("claude") == "anthropic-claude-max"
    assert uc.derive_usage_source("codex") == "openai-chatgpt-codex"
    assert uc.derive_usage_source("hermes") == "openai-chatgpt-codex"  # shares codex pool
    assert uc.derive_usage_source("hermes", {"modelBaseUrl": "http://192.0.2.10:11434/v1"}) == "local-ollama"
    assert uc.derive_usage_source("hermes", {"modelBaseUrl": "https://chatgpt.com/backend-api/codex"}) == "openai-chatgpt-codex"
    # any non-chatgpt base (incl. a bare LAN IP with no keyword) is the local pool, not codex
    assert uc.derive_usage_source("hermes", {"modelBaseUrl": "http://192.168.1.5:8080/v1"}) == "local-ollama"
    assert uc.derive_usage_source("opencode") is None


def test_consumption_summary():
    rows = [
        {"agent_id": "a", "source_id": "anthropic-claude-max", "model": "claude-opus-4-8",
         "input_tokens": 100, "output_tokens": 10, "cache_tokens": 5},
        {"agent_id": "b", "source_id": "openai-chatgpt-codex", "model": "gpt-5.5",
         "input_tokens": 200, "output_tokens": 20, "cache_tokens": 0},
        {"agent_id": "a", "source_id": "anthropic-claude-max", "model": "claude-opus-4-8",
         "input_tokens": 50, "output_tokens": 5, "cache_tokens": 1},
    ]
    s = uc.summarize_consumption(rows)
    assert s["totals"]["input_tokens"] == 350
    assert s["by_agent"]["a"]["input_tokens"] == 150  # folded across rows
    assert s["by_agent"]["a"]["output_tokens"] == 15
    assert s["by_source"]["openai-chatgpt-codex"]["input_tokens"] == 200
    assert s["by_model"]["claude-opus-4-8"]["cache_tokens"] == 6
