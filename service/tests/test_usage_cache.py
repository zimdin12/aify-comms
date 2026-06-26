"""Unit tests for the in-memory usage cache + consumption summarizer
(usage/quota feature, 2026-06-26). Pure module — no DB, no I/O."""
from service import usage_cache as uc


def test_set_get_all_roundtrip():
    uc._USAGE_CACHE.clear()
    uc.usage_set("anthropic-claude-max", {
        "weekly": {"used_pct": 81, "left_pct": 19},
        "five_hour": {"used_pct": 10, "left_pct": 90},
        "severity": "warning",
        "updated_at": "2026-06-26T17:00:00Z",
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
