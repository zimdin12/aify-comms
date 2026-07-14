"""OpenAI/ChatGPT quota — collected BY THE SERVICE, and reporting every constraint.

WHY THIS LIVES IN THE SERVICE (2026-07-14). The collector used to run only in the environment
bridge, so every fix to it required restarting the bridge — which cycles the operator's managed
agents. They were rightly fed up: "I cannot restart the team after every 5 minutes." Quota is a
READ of a host file plus one HTTP GET; there is no reason it needs an agent-cycling deploy. The
service mounts the codex/hermes auth stores read-only and does it itself, so a usage fix is now a
container rebuild and nothing else. Bridge posts are still accepted (other hosts, back-compat).

WHY IT REPORTS EVERYTHING, NOT ONE NUMBER. The dashboard showed "100% left" while the operator had
nothing left — worse than showing nothing, and they said so. It was not inventing that figure: it
faithfully echoed `rate_limit.primary_window.used_percent = 0`. But the SAME response said
`credits.balance = "0"`, `has_credits = false`, and `approx_local_messages = [0, 0]` — OpenAI's own
estimate of how many messages the account can actually send: ZERO. We published the one metric that
looked healthy and dropped the ones that contradicted it.

So this returns every constraint the endpoint exposes, and the pool carries a `disagreement` flag
whenever the window says "free" while another signal says "empty". A number we cannot stand behind
must never be shown as if we can.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx

CHATGPT_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
SOURCE_ID = "openai-chatgpt-codex"

# Searched, never guessed by platform — the same lesson as the bridge collector: every bug here was
# a wrong guess about a path, and each failed SILENTLY (no token -> stale numbers forever).
# Hermes' auth.json is often just a POINTER ({"active_provider": "openai-codex"}) because it
# delegates to the codex CLI's store, so codex is searched FIRST.
def _auth_candidates() -> list[Path]:
    homes: list[Path] = []
    for env in ("SERVICE_HOME", "HOME"):
        v = os.environ.get(env)
        if v:
            homes.append(Path(v))
    homes += [Path("/home/service"), Path("/root"), Path.home()]
    out: list[Path] = []
    for home in homes:
        for tool in ("codex", "hermes"):
            for p in (home / f".{tool}" / "auth.json", home / ".config" / tool / "auth.json"):
                if p not in out:
                    out.append(p)
    return out


def _is_openai_jwt(token: str) -> bool:
    try:
        import base64

        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return "openai.com" in str(claims.get("iss") or "")
    except Exception:
        return False


def _extract_token(data: Any) -> str:
    """Walk any auth store for an OpenAI access token (ignores nous/anthropic tokens)."""
    found = ""

    def walk(obj: Any) -> None:
        nonlocal found
        if found or not isinstance(obj, dict):
            return
        for k, v in obj.items():
            if found:
                return
            if k == "access_token" and isinstance(v, str) and v.startswith("ey") and _is_openai_jwt(v):
                found = v
                return
            if isinstance(v, dict):
                walk(v)

    walk(data)
    return found


def read_openai_token() -> tuple[str, list[str]]:
    """(token, searched_paths). Empty token = codex not installed / not signed in."""
    searched: list[str] = []
    for path in _auth_candidates():
        searched.append(str(path))
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        token = _extract_token(data)
        if token:
            return token, searched
    return "", searched


def classify_windows(rate_limit: dict[str, Any]) -> tuple[Optional[dict], Optional[dict]]:
    """(five_hour, weekly) picked by DURATION, never by position.

    `primary_window` is NOT always the 5-hour one: on this operator's `prolite` plan the only
    window returned is the WEEKLY one (604800s), and reading it positionally published the weekly
    figure as "5h" — a 5-hour window whose reset was six days out.
    """
    windows = [w for w in (rate_limit.get("primary_window"), rate_limit.get("secondary_window")) if isinstance(w, dict)]
    five = week = None
    for w in windows:
        secs = int(w.get("limit_window_seconds") or 0)
        if secs <= 0:
            continue
        if secs <= 86400:
            five = five or w
        else:
            week = week or w
    if not five and not week and windows:
        five = windows[0]
        week = windows[1] if len(windows) > 1 else None
    return five, week


def _win(w: Optional[dict]) -> dict[str, Any]:
    # An ABSENT window stays UNKNOWN — never 0. `int(None or 0)` would publish "0% used / 100%
    # left": a confident all-clear for a limit we know nothing about.
    if not isinstance(w, dict) or w.get("used_percent") is None:
        return {"used_pct": None, "left_pct": None, "resets_at": None}
    used = float(w["used_percent"])
    reset_at = w.get("reset_at")
    return {
        "used_pct": used,
        "left_pct": max(0.0, min(100.0, 100.0 - used)),
        "resets_at": (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(reset_at))) if reset_at else None
        ),
    }


def build_pool(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the live response into the pool, carrying EVERY constraint."""
    rl = payload.get("rate_limit") or {}
    five, week = classify_windows(rl)
    credits = payload.get("credits") or {}

    approx = []
    for key in ("approx_local_messages", "approx_cloud_messages"):
        v = credits.get(key)
        if isinstance(v, list):
            approx += [int(x) for x in v if isinstance(x, (int, float))]
    messages_left = max(approx) if approx else None

    has_credits = bool(credits.get("has_credits"))
    balance = str(credits.get("balance") or "0")
    unlimited = bool(credits.get("unlimited"))

    pool = {
        "source_id": SOURCE_ID,
        "five_hour": _win(five),
        "weekly": _win(week),
        "plan_type": payload.get("plan_type") or None,
        "allowed": bool(rl.get("allowed", True)),
        "limit_reached": bool(rl.get("limit_reached", False)),
        "credits": {
            "has_credits": has_credits,
            "unlimited": unlimited,
            "balance": balance,
            "messages_left": messages_left,
        },
    }

    # NOT AUTHORITATIVE FOR THE USAGE THE OPERATOR ACTUALLY BURNS (2026-07-14).
    #
    # `wham/usage` reports ONE window and, for this account, `used_percent: 0`. Codex's OWN meter —
    # the `rate_limits` object that comes back with every API response and is recorded in its
    # rollouts — reports TWO windows and real consumption (46% on a 300-minute window, 70% on a
    # 10080-minute one). The operator confirms the truth is ~64% weekly, not 0%. So this endpoint
    # is metering something else (Codex cloud), NOT the API usage their hermes agents burn.
    #
    # Publishing its number produced exactly the failure the operator called out: "says 100% left
    # but I actually have [64% used] ... it lies. it is worse than not showing." They are right —
    # a confident wrong number is worse than a blank. So the pool is marked UNVERIFIED: the
    # dashboard renders "—" and says why, instead of a reassuring figure nobody can stand behind.
    # The raw readings are still carried, so the panel can show them as evidence rather than truth.
    pool["verified"] = False
    pool["unknown"] = True
    pool["source"] = "chatgpt wham/usage"
    pool["unverified_reason"] = (
        "This endpoint reports account-level Codex limits and disagrees with codex's own meter "
        "(which tracks a 5-hour AND a weekly window). It is not the meter your hermes/codex agents "
        "consume, so its numbers are shown as evidence, not as your quota."
    )

    # THE DISAGREEMENT FLAG — the whole point of this module.
    # The window can read "0% used / 100% left" while the account can send ZERO messages. Publishing
    # only the window is how the dashboard came to claim "100% left" to an operator who had nothing.
    window_says_free = (pool["weekly"]["used_pct"] is not None and pool["weekly"]["used_pct"] < 100) or (
        pool["five_hour"]["used_pct"] is not None and pool["five_hour"]["used_pct"] < 100
    )
    credits_say_empty = (not unlimited) and (not has_credits) and (messages_left == 0)
    pool["disagreement"] = bool(window_says_free and credits_say_empty)
    pool["blocked"] = bool(pool["limit_reached"] or not pool["allowed"] or credits_say_empty)

    if pool["blocked"]:
        pool["severity"] = "critical"
    else:
        worst = max([w["used_pct"] for w in (pool["five_hour"], pool["weekly"]) if w["used_pct"] is not None] or [0])
        pool["severity"] = "critical" if worst >= 98 else ("warning" if worst >= 90 else "normal")
    return pool


async def collect_openai_pool(*, timeout: float = 8.0) -> Optional[dict[str, Any]]:
    """Fetch + normalize, or None when we cannot know (no token / unreachable / refused).

    None means UNKNOWN. It must never be turned into a reassuring zero by a caller.
    """
    token, _searched = read_openai_token()
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.get(
                CHATGPT_USAGE_URL,
                headers={
                    "authorization": f"Bearer {token}",
                    "accept": "application/json",
                    "user-agent": "codex-cli",
                },
            )
        if res.status_code != 200:
            return None
        return build_pool(res.json())
    except Exception:
        return None
