"""Process-global status cache and the bridge-row reconcilers that share its lifetime.

v0.5 slice 1a — the first extraction out of `service/routers/api_v2.py` (23k lines). Slice 1 was
chosen to go first because it OWNS `_LIVE_STATE_CACHE`: the failure mode that survives every other
gate is a duplicated module-global, so the smallest slice is the right one to prove the
import-identity gate against.

WHAT IS HERE AND WHY TOGETHER: the derived-status cache, its five accessors, and the two reconcilers
that maintain `bridge_instances` rows. They share a lifetime — bridge supersession is what makes a
cached agent status stale — and, measured before the move, they depend on nothing in the router
except a UTC timestamp (`service.clock.now`) and, formerly, a settings read.

NOT A PURE MOVE, and it is labelled that way deliberately. `_reap_stale_orphan_bridges` used to load
settings itself; that derivation now happens in the caller (see `stale_seconds_from_settings`) with
identical inputs and formula. Only the MOMENT of the settings read changed — per-sweep-pass instead
of per-step — which is the correct unit for a reconciler sweep: one pass should be internally
coherent rather than split across two policy epochs by a mid-sweep edit.

SINGLE-WORKER INVARIANT: `_LIVE_STATE_CACHE` is a process-global dict. It is correct ONLY with one
uvicorn worker / one event loop. `service/tests/test_process_global_identity.py` asserts this module
is its one owner and that nobody imports it by value.
"""

from __future__ import annotations

from typing import Any, Optional

from service.clock import now as _now

# ---- In-memory live-status cache (2026-06-18) --------------------------------------------
# The derived agent status is a CACHE, not durable state: it is recomputed from inputs and is
# rebuilt from scratch on restart. Storing it in SQLite made every dashboard poll refresh-WRITE
# it (the single-writer `database is locked` storm) AND kept readers hammering the DB (blocking
# the WAL checkpoint → WAL bloat → slow commits). It now lives in this process-global dict.
# SAFE: the service is ONE uvicorn process / one event loop (the dashboard-next container only
# proxies in, it never opens the DB), so dict access between `await`s is atomic — no mutex
# needed. Lost on restart = fine (recomputed in a single reconcile pass — that's what a cache
# is). NOTE: if the service is ever run multi-worker, this must move to a shared store (Redis)
# or the workers need sticky routing. The agent_live_state TABLE is retained only for schema
# compatibility; it is no longer read or written on any path.


_LIVE_STATE_CACHE: dict[str, dict[str, Any]] = {}


def _live_state_get(agent_id: str) -> Optional[dict[str, Any]]:
    return _LIVE_STATE_CACHE.get(str(agent_id or "").strip())


def _live_state_fresh(agent_id: str, *, now: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Return the cached entry only if its refresh_after is still in the future."""
    entry = _LIVE_STATE_CACHE.get(str(agent_id or "").strip())
    if not entry:
        return None
    refresh_after = str(entry.get("refresh_after") or "").strip()
    return entry if (refresh_after and refresh_after > (now or _now())) else None


def _live_state_set(agent_id: str, data: dict[str, Any]) -> None:
    _LIVE_STATE_CACHE[str(agent_id or "").strip()] = data


def _live_state_drop(agent_id: str) -> None:
    _LIVE_STATE_CACHE.pop(str(agent_id or "").strip(), None)


def _live_state_expire(agent_id: str) -> None:
    """Mark a cached entry STALE without dropping it (2026-07-14, the status-flicker fix).

    A DROPPED entry is a cache MISS, and the `list_agents` miss-path falls back to the raw
    `agents.status` column — which every heartbeat stamps to 'active', and which
    `_LEGACY_RAW_STATUS_TO_CANONICAL` coerces to **`online`**. That value never passes through
    `derive()`, so it can contradict `in_turn=1`: a genuinely WORKING agent is served `online`
    for one poll, and a status-sorted dashboard yanks the row down the list and back. Operator
    report: "all working agents turn online for a second and then back to working".

    The race is real because `GET /agents` takes seconds (per-agent liveness/env gates inside
    the row loop) while the event loop is single-threaded: a heartbeat landing mid-request
    invalidates AFTER the top-of-request refresh pass and BEFORE the per-agent read. Working
    agents heartbeat the most, so they lose that race the most — hence *all* of them flicker.

    Expiring instead keeps the last DERIVED value readable while forcing a recompute on the
    next refresh (`_live_state_fresh` gates on `refresh_after`). Worst case becomes a slightly
    stale but TRUE status rather than a fresh falsehood. Real eviction (agent deleted) still
    calls `_live_state_drop` directly.
    """
    entry = _LIVE_STATE_CACHE.get(str(agent_id or "").strip())
    if entry is not None:
        entry["refresh_after"] = ""


BRIDGE_ORPHAN_STALE_SECONDS = 300


def stale_seconds_from_settings(settings: dict) -> int:
    """The window `_reap_stale_orphan_bridges` must not reap inside, derived from settings.

    Lifted verbatim out of that reconciler in slice 1a so the settings read can happen once per
    sweep pass in the caller instead of once per step inside the helper. The formula and its inputs
    are unchanged:

    ALWAYS beyond every configured freshness window (+60s margin), even if an operator raises
    resident_lease_seconds (<=3600) or agent_liveness_seconds (<=600) above the 300s floor —
    otherwise the reaper could supersede a bridge that `_resident_bridge_is_fresh` / the liveness
    gate still treats as live (2026-07-11 review). The non-configurable stale constants
    (channel-sidecar 180 / claimer 240 / active-run 120) all sit under the floor.
    """
    lease = int((settings or {}).get("resident_lease_seconds", 150) or 150)
    liveness = int((settings or {}).get("agent_liveness_seconds", 90) or 90)
    return max(BRIDGE_ORPHAN_STALE_SECONDS, lease + 60, liveness + 60)


async def _prune_superseded_bridges(
    db,
    *,
    ttl_hours: int = 24,
    chunk: int = 2000,
    max_chunks: int = 50,
) -> int:
    """Reclaim superseded bridge_instances rows (holistic-review F4, 2026-05-31).

    Supersession sets `superseded_by` but nothing ever deleted the row, so the
    table grew monotonically with every wrapper relaunch (observed: 83/98 rows
    superseded). LIVE (non-superseded) rows are NEVER touched — only rows that
    have been superseded for longer than `ttl_hours` (keyed on superseded_at,
    falling back to last_seen). claim_bridge_id on dispatch_runs is a plain
    string (no FK), and any in-flight run owned by a superseded bridge was failed
    at supersession time, so deleting aged superseded rows orphans nothing.
    Chunked so a live control plane is never locked for long.
    """
    removed = 0
    for _ in range(max_chunks):
        cur = await db.execute(
            """
            DELETE FROM bridge_instances WHERE id IN (
                SELECT id FROM bridge_instances
                WHERE COALESCE(superseded_by, '') != ''
                  AND datetime(COALESCE(superseded_at, last_seen, '1970-01-01')) < datetime('now', ?)
                ORDER BY datetime(COALESCE(superseded_at, last_seen, '1970-01-01')) ASC
                LIMIT ?
            )
            """,
            (f"-{max(1, int(ttl_hours))} hours", int(chunk)),
        )
        await db.commit()
        n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        removed += n
        if n < chunk:
            break
    return removed


# A bridge whose process died WITHOUT a graceful supersede (crash / host restart /
# wrapper kill) is dead once its last_seen is this far past every liveness window.
# The liveness beat (__HEARTBEAT_MS) fires unconditionally every ~60s while the bridge
# process is alive, and the longest freshness window is the 150s resident lease, so
# 300s = ~5 missed beats — a value only a dead process can reach. Kept generous on
# purpose: superseding a still-live bridge would wrongly flip its agent offline.


async def _reap_stale_orphan_bridges(db, *, stale_seconds: Optional[int] = None, limit: int = 500) -> int:
    """Supersede bridge_instances rows whose owner died without a clean supersede.

    THE GAP (2026-07-11, other-PC perf report): supersession only happens on a clean
    relaunch/register, and `_prune_superseded_bridges` only DELETEs rows that are ALREADY
    superseded. So a bridge whose process crashed/was-killed lingers `superseded_by=''`
    with an old last_seen FOREVER — counted as "live" by every status-derivation and
    dispatch-claim scan (all keyed `WHERE superseded_by=''`), taxing idle CPU and never
    reaped (observed re-accumulating to dozens of orphans across the fleet).

    This marks such rows `superseded_by='reaper:stale-orphan'` so they drop out of the hot
    scans immediately; `_prune_superseded_bridges` then DELETEs them after its TTL. NEVER
    touches a row seen within `stale_seconds` — a live bridge always beats inside that
    window, so this can only match a dead process. Idempotent (a re-run re-selects nothing:
    superseded rows are excluded); LIMIT-bounded; single UPDATE; commit by the caller.
    """
    if stale_seconds is None:
        # SEAM NORMALIZATION, v0.5 slice 1a — NOT a data-plane change. This used to call
        # `_load_settings(db)` itself; that lives in the router, and importing it back here would
        # create the cycle the extraction exists to remove. The derivation moved to the caller
        # (`stale_seconds_from_settings` below, called from main.py's sweep with the settings that
        # pass already loads), so the FORMULA and its inputs are identical and only the moment of
        # the settings read changed: per-pass instead of per-step. Reviewed and named as such.
        #
        # Reaching here with None now means a caller that did not derive it. Falling back to the
        # bare floor is the conservative direction: a SMALLER window than an operator-raised lease
        # would risk superseding a live bridge, so the max() below keeps the 180s hard floor and
        # the caller is responsible for widening it.
        stale_seconds = BRIDGE_ORPHAN_STALE_SECONDS
    stale_seconds = max(180, int(stale_seconds or BRIDGE_ORPHAN_STALE_SECONDS))
    cur = await db.execute(
        """
        UPDATE bridge_instances
        SET superseded_by = 'reaper:stale-orphan',
            superseded_at = ?
        WHERE id IN (
            SELECT id FROM bridge_instances
            WHERE COALESCE(superseded_by, '') = ''
              AND datetime(COALESCE(last_seen, '1970-01-01')) < datetime('now', ?)
            ORDER BY datetime(COALESCE(last_seen, '1970-01-01')) ASC
            LIMIT ?
        )
        """,
        (_now(), f"-{stale_seconds} seconds", int(limit)),
    )
    return cur.rowcount if cur.rowcount is not None and cur.rowcount > 0 else 0
