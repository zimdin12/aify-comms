"""Honesty gates for the service-side OpenAI/Codex quota collector."""

from service.usage_openai import build_pool


def test_subscription_window_is_verified_without_optional_extra_credits():
    pool = build_pool({
        "plan_type": "prolite",
        "rate_limit": {
            "allowed": True,
            "limit_reached": False,
            "primary_window": {
                "used_percent": 92,
                "limit_window_seconds": 604800,
                "reset_at": 1784668550,
            },
        },
        # Codex exposes purchased/overage credits separately from the subscription
        # window. A zero balance must not turn an allowed subscription into blocked.
        "credits": {
            "has_credits": False,
            "unlimited": False,
            "balance": "0",
            "approx_local_messages": [0, 0],
        },
    })

    assert pool["weekly"]["used_pct"] == 92
    assert pool["weekly"]["left_pct"] == 8
    assert pool["verified"] is True
    assert pool["unknown"] is False
    assert pool["blocked"] is False
    assert pool["severity"] == "warning"


def test_missing_windows_remain_unknown():
    pool = build_pool({"rate_limit": {"allowed": True}, "credits": {}})

    assert pool["verified"] is False
    assert pool["unknown"] is True
    assert pool["weekly"]["left_pct"] is None


def test_provider_limit_reached_is_blocked_and_critical():
    pool = build_pool({
        "rate_limit": {
            "allowed": False,
            "limit_reached": True,
            "primary_window": {
                "used_percent": 100,
                "limit_window_seconds": 604800,
            },
        },
    })

    assert pool["verified"] is True
    assert pool["blocked"] is True
    assert pool["severity"] == "critical"
