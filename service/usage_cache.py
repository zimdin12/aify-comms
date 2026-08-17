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

# A pool whose snapshot is older than this is shown dimmed/`stale` (never an error) —
# a brief collector hiccup keeps showing last-good numbers so a transient gap doesn't blank.
STALE_AFTER_SECONDS = 420  # ~2x the 3-min collector cadence
# Past this, the snapshot is too old to trust: the numbers are BLANKED (shown as unknown)
# rather than displayed, so a days-old quota (e.g. a pool that reset while the collector was
# down) can't mislead agents into thinking they're near a limit that already reset. Tunable.
STALE_EXPIRE_SECONDS = 86400  # 24h


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
        # hermes shares the codex pool ONLY when pointed at the chatgpt backend; any other
        # explicit base (ollama, a LAN model server, etc.) is a non-quota local pool.
        if base and "chatgpt" not in base:
            return "local-ollama"
        return "openai-chatgpt-codex"
    return None


# Latest per-(agent) consumption rows reported by the env-bridge collector.
_CONSUMPTION_ROWS: list[dict[str, Any]] = []


def consumption_set(rows: list[dict[str, Any]]) -> None:
    global _CONSUMPTION_ROWS
    _CONSUMPTION_ROWS = list(rows or [])


def consumption_summary() -> dict[str, Any]:
    return summarize_consumption(_CONSUMPTION_ROWS)


def usage_set(source_id: str, payload: dict[str, Any]) -> None:
    """Store the latest snapshot for a pool, stamping its source_id."""
    data = dict(payload or {})
    data["source_id"] = source_id
    _USAGE_CACHE[source_id] = data


def _age_seconds(updated_at: Any) -> Optional[float]:
    """Age of a snapshot in seconds, or None if the stamp is missing/unparseable
    (an unknown age is treated as maximally stale by the callers below)."""
    if not updated_at or not isinstance(updated_at, str):
        return None
    try:
        ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    return (datetime.now(timezone.utc) - ts).total_seconds()


# `_is_stale(updated_at)` stood here and was DEAD — no caller anywhere in the repo, while `usage_get`
# below computes the same rule inline from an `age` it already has. Two spellings of one rule with
# one of them unreachable is the shape this series keeps finding: an edit to the named helper (the
# one that reads canonical) would have changed nothing at all. Removed rather than wired in, because
# calling it from `usage_get` would re-parse the timestamp the caller has already parsed.


def _blank_expired_pool(out: dict[str, Any]) -> dict[str, Any]:
    """Null the numeric quota fields of a too-old snapshot so consumers render it as
    unknown (—) instead of a stale value. Copies each band dict so the cache entry is
    never mutated."""
    out["expired"] = True
    out["unknown"] = True
    for band_key in ("weekly", "five_hour"):
        band = out.get(band_key)
        if isinstance(band, dict):
            nb = dict(band)
            for k in list(nb.keys()):
                if k.endswith("_pct") or k in ("resets_at", "resets_in"):
                    nb[k] = None
            out[band_key] = nb
    return out


# Grace on a window's own `resets_at`: a live snapshot always carries a FUTURE reset time
# (the next reset). Once `resets_at` is in the past, that window already reset and the used/left
# % is from the dead cycle — even if the POST is fresh. The codex/hermes pool is sourced from the
# last codex rollout's `rate_limits`, so a stale rollout re-POSTs a month-old snapshot with a
# fresh `updated_at`; the reset-elapsed check is the only signal that catches THAT (2026-07).
RESET_ELAPSED_GRACE_SECONDS = 300


def _reset_elapsed(resets_at: Any) -> bool:
    age = _age_seconds(resets_at)  # >0 means resets_at is in the PAST
    return age is not None and age > RESET_ELAPSED_GRACE_SECONDS


def _blank_elapsed_reset_windows(out: dict[str, Any]) -> dict[str, Any]:
    """Blank any window whose own `resets_at` has already passed — its % is from a cycle that
    already reset, so it must read as unknown (—) rather than a live-looking number."""
    changed = False
    for band_key in ("weekly", "five_hour"):
        band = out.get(band_key)
        if isinstance(band, dict) and _reset_elapsed(band.get("resets_at")):
            nb = dict(band)
            for k in list(nb.keys()):
                if k.endswith("_pct") or k in ("resets_at", "resets_in"):
                    nb[k] = None
            out[band_key] = nb
            changed = True
    if changed:
        out["reset_elapsed"] = True
        out["stale"] = True
        out["unknown"] = True
    return out


def usage_get(source_id: str) -> Optional[dict[str, Any]]:
    """Return a copy of the pool snapshot with freshness flags, or None.

    Three ways a number is suppressed so a misleading value can't be shown as current:
      * `stale`  — POST older than STALE_AFTER_SECONDS: dim, but keep last-good through a hiccup.
      * `expired` — POST older than STALE_EXPIRE_SECONDS (~24h): BLANK (collector stopped).
      * `reset_elapsed` — a window's own `resets_at` is already in the past: BLANK that window
        (a fresh POST of a snapshot whose cycle already reset — the codex/hermes stale-rollout case)."""
    entry = _USAGE_CACHE.get(source_id)
    if entry is None:
        return None
    out = dict(entry)
    age = _age_seconds(entry.get("updated_at"))
    out["stale"] = age is None or age > STALE_AFTER_SECONDS
    if age is None or age > STALE_EXPIRE_SECONDS:
        out = _blank_expired_pool(out)
    else:
        out = _blank_elapsed_reset_windows(out)
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
