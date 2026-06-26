"""In-memory per-pool usage/quota cache + consumption summarizer.

Mirrors the `_LIVE_STATE_CACHE` pattern: a process-global dict, valid ONLY with a
single uvicorn worker (the service's hard constraint). No SQLite — usage is hot,
ephemeral live state. The env-bridge collector POSTs normalized pool snapshots; the
dashboards + comms_usage read them. See
docs/superpowers/specs/2026-06-26-usage-quota-stats-design.md.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

# source_id -> normalized pool snapshot (see usage-collector.normalizeUsage shape,
# plus a server-stamped `updated_at`).
_USAGE_CACHE: dict[str, dict[str, Any]] = {}

# A pool whose snapshot is older than this is shown dimmed/`stale` (never an error).
STALE_AFTER_SECONDS = 420  # ~2x the 3-min collector cadence


def derive_usage_source(runtime: Any, runtime_config: Any = None) -> Optional[str]:
    """Map a runtime (+ its config) to its quota pool id. Auto-bound at register;
    overridable later. hermes shares the codex pool (same chatgpt backend) unless it
    is pointed at a local model."""
    rt = str(runtime or "").strip().lower()
    rc = runtime_config if isinstance(runtime_config, dict) else {}
    if rt in ("claude-code", "claude", "claude_code"):
        return "anthropic-claude-max"
    if rt == "codex":
        return "openai-chatgpt-codex"
    if rt == "hermes":
        base = str(rc.get("modelBaseUrl") or "").lower()
        if base and "chatgpt" not in base and any(k in base for k in ("ollama", "11434", "localhost", "127.0.0.1")):
            return "local-ollama"
        return "openai-chatgpt-codex"
    return None


def usage_set(source_id: str, payload: dict[str, Any]) -> None:
    """Store the latest snapshot for a pool, stamping its source_id."""
    data = dict(payload or {})
    data["source_id"] = source_id
    _USAGE_CACHE[source_id] = data


def _is_stale(updated_at: Any) -> bool:
    if not updated_at or not isinstance(updated_at, str):
        return True
    try:
        ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return True
    return (datetime.now(timezone.utc) - ts).total_seconds() > STALE_AFTER_SECONDS


def usage_get(source_id: str) -> Optional[dict[str, Any]]:
    """Return a copy of the pool snapshot with a computed `stale` flag, or None."""
    entry = _USAGE_CACHE.get(source_id)
    if entry is None:
        return None
    out = dict(entry)
    out["stale"] = _is_stale(entry.get("updated_at"))
    return out


def usage_all() -> list[dict[str, Any]]:
    """All pool snapshots (each with `stale`), for the dashboard + comms_usage."""
    return [usage_get(sid) for sid in _USAGE_CACHE]  # type: ignore[misc]


def _add(bucket: dict[str, dict[str, int]], key: str, row: dict[str, Any]) -> None:
    if not key:
        return
    agg = bucket.setdefault(key, {"input_tokens": 0, "output_tokens": 0, "cache_tokens": 0})
    agg["input_tokens"] += int(row.get("input_tokens") or 0)
    agg["output_tokens"] += int(row.get("output_tokens") or 0)
    agg["cache_tokens"] += int(row.get("cache_tokens") or 0)


def summarize_consumption(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold per-(agent,turn) token rows into by_agent / by_model / by_source / totals."""
    by_agent: dict[str, dict[str, int]] = {}
    by_model: dict[str, dict[str, int]] = {}
    by_source: dict[str, dict[str, int]] = {}
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_tokens": 0}
    for row in rows or []:
        _add(by_agent, str(row.get("agent_id") or ""), row)
        _add(by_model, str(row.get("model") or ""), row)
        _add(by_source, str(row.get("source_id") or ""), row)
        totals["input_tokens"] += int(row.get("input_tokens") or 0)
        totals["output_tokens"] += int(row.get("output_tokens") or 0)
        totals["cache_tokens"] += int(row.get("cache_tokens") or 0)
    return {"by_agent": by_agent, "by_model": by_model, "by_source": by_source, "totals": totals}
