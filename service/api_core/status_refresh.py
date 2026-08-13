"""Refresh, cache and serve one agent's derived status.

The layer above `status_inputs.py`: that module DERIVES a status for an agent, these four decide when
to derive it again, write the result into the process-global cache, and hand it to callers.

  _refresh_agent_live_state           derive now, store the result
  _refresh_expired_agent_live_states  re-derive only the entries whose TTL has passed
  _compute_agent_status               serve from cache when fresh, otherwise derive
  _get_recipient_info                 a recipient's record with its status attached

Separate from `status_inputs.py` because the names would stop being true otherwise: nothing here is an
input, and `_compute_live_status_cache` next door does not touch the cache it is named for — the
caching is here. Separate from `service/reconcilers/status_cache.py` for the opposite reason: that
module OWNS `_LIVE_STATE_CACHE` and twenty-five modules import it, so it must stay low in the graph
and cannot import the derivation it would need.

THE LOGGER NAME IS DELIBERATELY NOT `__name__`. `_refresh_agent_live_state` logs a derive failure, and
it is the only logger call the control plane had left, so the logger came with it. Operators filter
logs by that name, which makes it observable contract rather than an implementation detail: keeping
`aify_comms.api_v2` means a refactor does not silently move somebody's log filter. `logging.getLogger`
returns the same singleton for a given name, so this is the same logger object the carrier used, not a
lookalike. Renaming it is a separate, operator-visible decision.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from service.api_core.capabilities import _has_live_rpc_controller
from service.api_core.dispatch_state import _get_dispatch_state_map
from service.api_core.liveness import _has_live_terminal_session
from service.api_core.manual_status import _MANUAL_STATUSES
from service.api_core.message_store import _get_unread_count_map
from service.api_core.records import _agent_record_to_dict
from service.api_core.serialization import _row_get
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings
from service.api_core.status_inputs import _compute_live_status_cache
from service.clock import now as _now
from service.reconcilers import status_cache
from service.reconcilers.status_cache import _live_state_fresh, _live_state_get, _live_state_set
from service.status_engine import derive

logger = logging.getLogger("aify_comms.api_v2")


async def _refresh_agent_live_state(db, agent_id: str, *, settings: Optional[dict[str, Any]] = None, now: Optional[str] = None):
    row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
    if not row:
        return None
    settings = settings or await _load_settings(db)
    cache = await _compute_live_status_cache(db, row, settings=settings, now=now)
    # status v2 flag-branch (2026-06-04). The served status is the cache `status`.
    # Under `status_engine=new` the event-driven engine becomes authoritative for
    # the served value; under `old` (default) the legacy derivation is unchanged.
    # Disagreements are always logged so the new engine can be validated before
    # the flip. Manual statuses (stop/disable) short-circuit the engine too — they
    # are operator overrides that both paths must honor identically.
    # Proof-based engine is the ONE authority (2026-06-18: the status_engine old|new flag is
    # gone). Manual statuses (stop/disable) are an operator override derive() already encodes
    # via the `disabled` input; we keep the short-circuit so a stopped agent never depends on
    # the rest of the input gather. The served status is derive() of the assembled inputs (a
    # PURE call on the byproduct _compute_live_status_cache already built — no second gather).
    if cache["status"] not in _MANUAL_STATUSES:
        try:
            _legacy_status = cache["status"]
            _derived = derive(cache["status_inputs"])
            # derive() is the ONE authority for the served status. `cache["reason"]`
            # (served as statusNote) was computed by the legacy cascade for the
            # legacy status; when derive() DISAGREES, that reason describes the
            # superseded status and contradicts what the operator sees (e.g. a
            # dead-worker-mid-turn: derive→"available" but reason="Active run: X").
            # Drop the stale reason on disagreement so the note never mismatches the
            # status. (Cosmetic-only: dispatch keys on worker_present, not reason.)
            if _derived != _legacy_status:
                cache["reason"] = ""
            cache["status"] = _derived
        except Exception:
            logger.exception("status derive failed for agent=%s; keeping computed status", agent_id)
    # Store in the in-memory cache — NOT the DB (was the write-storm source). No lock possible.
    _live_state_set(agent_id, cache)
    return cache


async def _refresh_expired_agent_live_states(db, *, settings: Optional[dict[str, Any]] = None, agent_ids: Optional[list[str]] = None, limit: Optional[int] = None) -> int:
    """Recompute expired/missing live-status entries INTO THE IN-MEMORY CACHE. Returns how many
    were refreshed. No DB writes happen here anymore — the status cache lives in _LIVE_STATE_CACHE
    (2026-06-18), so there is nothing to commit and a read can never take SQLite's write lock.

    `limit` bounds the per-call recompute count (CPU only) for the hot GET /agents path; the
    reconcile sweep calls it unbounded (limit=None). Missing entries are refreshed first, then
    the oldest, so the most-stale agents recompute soonest under the cap."""
    settings = settings or await _load_settings(db)
    now = _now()
    if agent_ids:
        ids = [str(a or "").strip() for a in agent_ids if str(a or "").strip()]
    else:
        rows = await (await db.execute("SELECT id FROM agents")).fetchall()
        ids = [r["id"] for r in rows]
    # Order: missing-from-cache first, then by oldest refresh_after — so the most-stale recompute
    # soonest when `limit` caps the batch.
    def _sort_key(aid: str):
        entry = status_cache._LIVE_STATE_CACHE.get(aid)
        if not entry:
            return (0, "")
        return (1, str(entry.get("refresh_after") or ""))
    ids.sort(key=_sort_key)
    refreshed = 0
    for aid in ids:
        if limit is not None and refreshed >= limit:
            break
        if _live_state_fresh(aid, now=now) is None:
            await _refresh_agent_live_state(db, aid, settings=settings, now=now)
            refreshed += 1
    return refreshed


async def _compute_agent_status(row, db=None):
    # Single source of truth: delegate to the live-state engine that
    # list_agents/get_agent already use, so write endpoints (heartbeat,
    # register, dispatch status) never disagree with the dashboard about
    # whether an agent is active/idle/offline. The db-less fallback below is
    # only the minimal heartbeat heuristic for callers without a connection.
    status = row["status"]
    if status in _MANUAL_STATUSES:
        return status
    if db is not None:
        # The CPU fix: the in-memory live-status entry is kept fresh by push events
        # (status-event ingest invalidates it) + the reconcile backstop, so a hot read
        # serves the cached status directly instead of recomputing on EVERY call (claim
        # deliverability / write endpoints / send preflight all funnel through here).
        # Only recompute when the cache entry is missing or expired.
        settings = await _load_settings(db)
        cached = _live_state_fresh(row["id"])
        if cached:
            return cached["status"]
        cache = await _refresh_agent_live_state(db, row["id"], settings=settings)
        if cache:
            return cache["status"]

    # Plan 4 (2026-05-25): db-less fallback. With a db, `_compute_live_status_cache`
    # already gates `online` on `has_live_worker` (wrapper PTY or RPC child) and
    # falls back to `available`. Without a db we cannot inspect terminal_sessions,
    # so a managed agent's persisted `status` column (likely `online`) is a lie
    # — degrade to `available` so the taxonomy stays honest. The db-less branch
    # is informational only (used by callers without a connection); db-backed
    # callers go through _compute_live_status_cache above, which DOES layer the
    # offline-via-stale-heartbeat check on top.
    session_mode = str(_row_get(row, "session_mode", "") or "")
    if session_mode == "managed":
        agent_id = _row_get(row, "id", "")
        if agent_id:
            has_terminal = await _has_live_terminal_session(db, agent_id)
            has_rpc = _has_live_rpc_controller(agent_id)
            if not has_terminal and not has_rpc:
                return "available"

    # Proof-based (2026-06-18): no idle/offline MINUTE decay. The only time element is the
    # short liveness window — heartbeat older than it = offline (gone). Otherwise online.
    try:
        last = datetime.fromisoformat(str(row["last_seen"] or "").replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - last
        liveness = int(DEFAULT_SETTINGS.get("agent_liveness_seconds", 90) or 90)
        if age > timedelta(seconds=liveness):
            status = "offline"
        elif status in ("idle", "active", "ready"):
            status = "online"  # legacy raw values are not engine statuses
    except Exception:
        pass
    return status


async def _get_recipient_info(db, recipient_id: str):
    if recipient_id == "dashboard":
        return {
            "status": "active",
            "unread": 0,
            "runtime": "dashboard",
            "machineId": "dashboard",
        }
    settings = await _load_settings(db)
    await _refresh_expired_agent_live_states(db, settings=settings, agent_ids=[recipient_id])
    c = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
    row = await c.fetchone()
    if not row:
        return None
    unread_map = await _get_unread_count_map(db, [recipient_id])
    dispatch_state = await _get_dispatch_state_map(db, [recipient_id])
    entry = _live_state_get(recipient_id) or {}
    return _agent_record_to_dict(
        row, entry.get("status") or row["status"], unread_map.get(recipient_id, 0),
        dispatch_state.get(recipient_id), live_reason=entry.get("reason"),
    )
