"""Anthropic (Claude Max) quota, collected by the service, the way usage_openai.py collects OpenAI's.

WHY THE SERVICE (v0.7.4). The Anthropic pool reached the service only through `POST /usage`, whose one
caller was the environment bridge deleted in v0.6.3, so it read `?` or a stale figure. The operator asked
for both pools without agent cost: no per-agent collection and no runtime started to ask. The service
already mounts `~/.claude` read-only (docker-compose.yml), and `api/oauth/usage` is the free endpoint the
interactive `/usage` command calls. One read serves every agent.

The token is only READ. The running claude processes refresh `.credentials.json`; an expired or absent
token, or any refusal, is UNKNOWN (None), never a reassuring zero.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import httpx

from service.usage_cache import SOURCE_ANTHROPIC_CLAUDE_MAX

ANTHROPIC_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
SOURCE_ID = SOURCE_ANTHROPIC_CLAUDE_MAX


def _credential_candidates() -> list[Path]:
    homes: list[Path] = []
    for env in ("SERVICE_HOME", "HOME"):
        value = os.environ.get(env)
        if value:
            homes.append(Path(value))
    homes += [Path("/home/service"), Path("/root"), Path.home()]
    out: list[Path] = []
    for home in homes:
        path = home / ".claude" / ".credentials.json"
        if path not in out:
            out.append(path)
    return out


def read_claude_oauth() -> dict[str, Any]:
    """The `claudeAiOauth` block of the first credentials file found, or {}."""
    for path in _credential_candidates():
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        block = data.get("claudeAiOauth") if isinstance(data, dict) else None
        if isinstance(block, dict) and block.get("accessToken"):
            return block
    return {}


def _win(window: Any) -> dict[str, Any]:
    # An ABSENT window stays UNKNOWN, never 0: "0% used" would be a confident all-clear about a limit
    # nobody measured (the same rule as usage_openai._win).
    used = window.get("utilization") if isinstance(window, dict) else None
    if used is None:
        return {"used_pct": None, "left_pct": None, "resets_at": None}
    used = float(used)
    return {"used_pct": used, "left_pct": max(0.0, min(100.0, 100.0 - used)), "resets_at": window.get("resets_at")}


def build_pool(payload: dict[str, Any], oauth: dict[str, Any]) -> dict[str, Any]:
    """Normalize `api/oauth/usage` into the pool shape the dashboard and comms_usage read."""
    five, week = _win(payload.get("five_hour")), _win(payload.get("seven_day"))
    limits = payload.get("limits") if isinstance(payload.get("limits"), list) else []
    weekly_limit = next((l for l in limits if isinstance(l, dict) and l.get("group") == "weekly" and l.get("is_active")), None)
    known = [w["used_pct"] for w in (five, week) if w["used_pct"] is not None]
    worst = max(known or [0])
    severity = "critical" if worst >= 98 else ("warning" if worst >= 90 else "normal")
    provider = str((weekly_limit or {}).get("severity") or "")
    if provider == "critical" or (provider == "warning" and severity == "normal"):
        severity = provider
    return {
        "source_id": SOURCE_ID,
        "five_hour": five,
        "weekly": week,
        "severity": severity,
        "plan_type": oauth.get("subscriptionType") or oauth.get("rateLimitTier") or None,
        "verified": bool(known),
        "unknown": not known,
        "source": "Claude account usage (api/oauth/usage)",
    }


async def collect_anthropic_pool(*, timeout: float = 8.0) -> Optional[dict[str, Any]]:
    """Fetch and normalize, or None when it cannot be known (no token, refused, unreachable)."""
    oauth = read_claude_oauth()
    token = str(oauth.get("accessToken") or "")
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            res = await client.get(
                ANTHROPIC_USAGE_URL,
                headers={"authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20", "accept": "application/json"},
            )
        if res.status_code != 200:
            return None
        return build_pool(res.json(), oauth)
    except Exception:
        return None
