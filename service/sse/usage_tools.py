"""`comms_usage`: subscription quota left per pool (port of `mcp/stdio/usage-tool.mjs`).

Advisory: a pool near 0% is a reason to hand work elsewhere, not a gate. A percentage nobody knows prints
`?`, never `0%`, which would report an exhausted pool when nothing is known about it; `stale` and
`unknown` print as their own tags. The caller's own line is best-effort: the pool table is the answer.
"""

from __future__ import annotations

from urllib.parse import quote

from mcp.server.fastmcp import Context

from service.sse.api_client import api as _api
from service.sse.caller import caller_or_none


def _left(window) -> str:
    pct = (window or {}).get("left_pct")
    return "?" if pct is None else f"{pct}%"


def _pool_line(pool: dict) -> str:
    severity = pool.get("severity")
    sev = f" [{severity}]" if severity and severity != "normal" else ""
    tags = f"{' (stale)' if pool.get('stale') else ''}{' (unknown)' if pool.get('unknown') else ''}"
    return f"- {pool.get('source_id')}: weekly {_left(pool.get('weekly'))} left, 5h {_left(pool.get('five_hour'))} left{sev}{tags}"


def personal_quota_line(agent_id: str, agent: dict) -> str:
    """The caller's own pool. Severity, not `quotaCritical` alone, so a warning on the five-hour window shows."""
    source = str(agent.get("usageSource") or "").strip()
    if not agent_id or not source:
        return ""
    weekly = agent.get("poolWeeklyPctLeft")
    left = "?" if weekly is None else f"{weekly}%"
    severity = str(agent.get("poolSeverity") or "").strip().lower() or ("critical" if agent.get("quotaCritical") else "")
    tag = f" [{severity}]" if severity and severity != "normal" else ""
    return f"\nYou ({agent_id}) → {source}: {left} weekly left{tag}"


async def comms_usage(ctx: Context):
    """Remaining subscription quota per pool (Anthropic Claude, OpenAI ChatGPT-Codex) and which pool you draw on. Advisory: on a pool near 0%, hand work to a pool with headroom."""
    pools = (await _api("GET", "/usage")).get("pools") or []
    if not pools:
        return "No usage data yet (collector warming up)."
    mine = ""
    me = caller_or_none(ctx)
    if me:
        info = await _api("GET", f"/agents/{quote(me, safe='')}")
        if "detail" not in info:
            mine = personal_quota_line(me, info.get("agent") or {})
    return "Quota pools (% remaining):\n" + "\n".join(_pool_line(p) for p in pools) + mine


TOOLS = (comms_usage,)


def register(mcp_server) -> None:
    for tool in TOOLS:
        mcp_server.tool(structured_output=False)(tool)
