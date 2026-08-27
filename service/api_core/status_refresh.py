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
from service.api_core.live_process_probes import _has_live_terminal_session
from service.api_core.manual_status import _MANUAL_STATUSES
from service.api_core.message_store import _get_unread_count_map
from service.api_core.records import _agent_record_to_dict
from service.api_core.serialization import _row_get
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings
from service.api_core.status_inputs import _compute_live_status_cache
from service.clock import now as _now
from service.api_core.status_signal_prefetch import PrefetchedStatusSignals
from service.reconcilers import status_cache
from service.reconcilers.status_cache import _live_state_fresh, _live_state_get, _live_state_set
from service.status_engine import derive

logger = logging.getLogger("aify_comms.api_v2")


async def _refresh_agent_live_state(db, agent_id: str, *, settings: Optional[dict[str, Any]] = None, now: Optional[str] = None, environments_by_machine=None, session_environment_by_agent=None, agent_row=None, status_signals=None):
    # `agent_row` is the row the CALLER already holds, the same move `5c45ab44` made on the roster.
    # The batch caller below reads every agent row to decide who is stale; re-selecting each one here
    # is 1.0N round-trips for rows it is holding. Optional and falling back, because this function is
    # also called with an id and nothing else.
    #
    # The id is CHECKED rather than trusted. This function writes the live-state cache under
    # `agent_id`, so a caller handing over a row for a different agent would file one agent's derived
    # status under another's key -- wrong data, silently, with no error anywhere. Every caller today
    # passes a correctly-keyed row (the batch below uses `rows_by_id[aid]`, and the two analytics
    # loops pass the row they are iterating), so this guards a future caller, not a present bug. It
    # fails toward the QUERY rather than raising: re-reading is always correct, and a status endpoint
    # is a poor place to turn a caller's mistake into a 500.
    if agent_row is not None and str(_row_get(agent_row, "id", "")) != str(agent_id):
        agent_row = None
    row = agent_row if agent_row is not None else await (
        await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
    ).fetchone()
    if not row:
        return None
    settings = settings or await _load_settings(db)
    cache = await _compute_live_status_cache(db, row, settings=settings, now=now,
                                            status_signals=status_signals,
                                            environments_by_machine=environments_by_machine,
                                            session_environment_by_agent=session_environment_by_agent)
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


async def _refresh_expired_agent_live_states(db, *, settings: Optional[dict[str, Any]] = None, agent_ids: Optional[list[str]] = None, limit: Optional[int] = None, environments_by_machine=None, session_environment_by_agent=None) -> int:
    """Recompute expired/missing live-status entries INTO THE IN-MEMORY CACHE. Returns how many
    were refreshed. No DB writes happen here anymore — the status cache lives in _LIVE_STATE_CACHE
    (2026-06-18), so there is nothing to commit and a read can never take SQLite's write lock.

    `limit` bounds the per-call recompute count (CPU only) for the hot GET /agents path; the
    reconcile sweep calls it unbounded (limit=None). Missing entries are refreshed first, then
    the oldest, so the most-stale agents recompute soonest under the cap."""
    settings = settings or await _load_settings(db)
    now = _now()
    rows_by_id: dict = {}
    if agent_ids:
        ids = [str(a or "").strip() for a in agent_ids if str(a or "").strip()]
    else:
        # `*` rather than `id`: the same one query, and it carries the seven columns
        # `_compute_live_status_cache` reads, so the per-agent re-select below disappears.
        rows = await (await db.execute("SELECT * FROM agents")).fetchall()
        ids = [r["id"] for r in rows]
        rows_by_id = {str(r["id"]): r for r in rows}
    # Order: missing-from-cache first, then by oldest refresh_after — so the most-stale recompute
    # soonest when `limit` caps the batch.
    def _sort_key(aid: str):
        entry = status_cache._LIVE_STATE_CACHE.get(aid)
        if not entry:
            return (0, "")
        return (1, str(entry.get("refresh_after") or ""))
    ids.sort(key=_sort_key)
    # WHICH AGENTS WILL ACTUALLY BE RECOMPUTED, decided before any of them are, so the prefetch below
    # reads exactly those and no more. Computing this twice is not a risk: `_live_state_fresh` reads
    # the in-memory cache and `now` is fixed above, so the second pass cannot disagree with the first.
    due = [aid for aid in ids if _live_state_fresh(aid, now=now) is None]
    if limit is not None:
        due = due[:limit]
    # TWO QUERIES FOR THE WHOLE BATCH instead of two per agent. Measured: the per-agent refresh is 7.0
    # round-trips per agent and 61% of a whole reconcile pass at 40 agents, a share that GROWS with
    # fleet size. Skipped for a single agent, where a prefetch is the same two reads with an IN clause
    # around them -- the hot single-agent callers must not pay for a batch of one.
    status_signals = None
    if len(due) > 1:
        status_signals = await PrefetchedStatusSignals.load(db, due)
    refreshed = 0
    for aid in due:
        await _refresh_agent_live_state(db, aid, settings=settings, now=now,
                                        environments_by_machine=environments_by_machine,
                                        session_environment_by_agent=session_environment_by_agent,
                                        agent_row=rows_by_id.get(aid),
                                        status_signals=status_signals)
        refreshed += 1
    return refreshed


async def _compute_agent_status(
    row,
    db=None,
    *,
    environments_by_machine=None,
    session_environment_by_agent=None,
    agent_row=None,
    status_signals=None,
):
    # The three kwargs are OPT-IN and default to None, which is the pre-existing behaviour exactly.
    # They exist for callers that compute a status for EVERY agent in a loop: without them each
    # iteration re-reads the agent row it already holds, re-reads `environments` by machine_id (an
    # answer that depends on machine_id alone) and re-reads the session environment. `GET
    # /api/v1/analytics` was doing all three per agent. Eleven other call sites compute ONE status
    # after a write and pass nothing, so they are unchanged -- and `agent_row` is opt-in rather than
    # `row` itself precisely because those callers pass a row re-read after an update, which is not
    # always the same shape as `SELECT * FROM agents`.
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
        cache = await _refresh_agent_live_state(
            db,
            row["id"],
            settings=settings,
            environments_by_machine=environments_by_machine,
            session_environment_by_agent=session_environment_by_agent,
            agent_row=agent_row,
            status_signals=status_signals,
        )
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
