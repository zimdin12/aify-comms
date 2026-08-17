"""Spawn-request lifecycle reconcilers: the paths that close a spawn nobody will finish.

v0.5 slice 2, extracted from `service/routers/api_v2.py`. Unlike slice 1 this moved as ONE slice —
nothing here reaches the status-computation core; see `docs/V0.5_SLICE2.md` for the measured
dependency table taken before the move.

MOVE PLUS ONE DECLARED SEAM, not a pure move. `_fail_orphaned_running_spawn_requests` used to load
settings itself for a single key (`environment_offline_seconds`); the caller now passes that scalar
from the settings its sweep pass already loads. Same key, same default, same use — only the moment of
the read changed, per-step to per-pass. Identical treatment to slice 1a, and approved as the shape to
repeat.

ORDER STILL MATTERS AND IS NOT ENCODED HERE. `_finalize_spawns_with_dead_terminals` must run BEFORE
`_fail_running_spawns_superseded_by_current_session`: the superseded reaper only clears a dead spawn
once a NEWER live session exists, which is what left a spawn `running` for 97 minutes on 2026-08-07,
while the dead-terminal path needs no successor. That ordering lives in `main.py`'s sweep and is
asserted by `service/tests/test_reconcile_sweep_ordering.py`.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from service.clock import now as _now
from service.clock import iso_to_epoch as _iso_to_epoch
from service.env_status import environment_effective_status as _environment_effective_status

logger = logging.getLogger(__name__)

# The orphan grace is shared with `spawn_terminal_settlement.py`, which split off in v0.5.4;
# `api_core/tuning.py` imports nothing, so a constant read by both cannot create a cycle there.
from service.api_core.tuning import SPAWN_ORPHAN_GRACE_SECONDS

# _terminal_end_statuses_ordered moved to service/api_core/dead_terminal_spawn_query.py in
# v0.5.4 - it travelled with the two queries that were its only callers.

async def _repair_spawn_requests_from_initial_dispatch_failures(db) -> int:
    cursor = await db.execute(
        """
        SELECT *
        FROM spawn_requests
        WHERE status = 'running'
          AND COALESCE(initial_message, '') != ''
          AND COALESCE(error, '') = ''
        """
    )
    repaired = 0
    for spawn in await cursor.fetchall():
        started_at = spawn["started_at"] or spawn["updated_at"] or spawn["created_at"]
        # AUDIT FINDING 3. This used to take the FIRST run to the agent at or after started_at,
        # which is time proximity, not identity: a manager's unrelated question landing a second
        # after the spawn started WAS the spawn's brief as far as this query could tell, and if it
        # failed the healthy spawn was killed with "Initial brief failed: are you up?".
        #
        # The identity was available all along — the brief is created from the spawn row itself
        # (see the dispatch at `_create_dispatch_runs` in the spawn-claim path), so from_agent,
        # subject and body reconstruct it exactly. The time bound stays, but as a BOUND now rather
        # than as the thing doing the identifying.
        #
        # The two defaults below must track that call site: `created_by or "dashboard"` and
        # `subject or f"Spawn {agent_id}"`. If either drifts, this silently matches nothing and the
        # reconciler stops repairing rather than repairing wrongly — quieter, still broken.
        # test_spawn_initial_dispatch_identity.py fails on that drift.
        run_cursor = await db.execute(
            """
            SELECT *
            FROM dispatch_runs
            WHERE target_agent = ?
              AND requested_at >= ?
              AND from_agent = ?
              AND subject = ?
              AND body = ?
            ORDER BY requested_at ASC
            LIMIT 1
            """,
            (
                spawn["agent_id"],
                started_at,
                spawn["created_by"] or "dashboard",
                spawn["subject"] or f"Spawn {spawn['agent_id']}",
                spawn["initial_message"],
            ),
        )
        run = await run_cursor.fetchone()
        if not run or str(run["status"] or "").lower() not in {"failed", "cancelled"}:
            continue
        error = (run["error_text"] or run["summary"] or f"Initial dispatch {run['status']}").strip()
        now = _now()
        await db.execute(
            """
            UPDATE spawn_requests
            SET status = 'failed',
                error = ?,
                finished_at = COALESCE(finished_at, ?),
                updated_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (f"Initial brief failed: {error}", run["finished_at"] or now, now, spawn["id"]),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = 'failed',
                ended_at = COALESCE(ended_at, ?),
                last_seen = ?
            WHERE spawn_request_id = ?
              AND status IN ('starting', 'running')
            """,
            (run["finished_at"] or now, now, spawn["id"]),
        )
        repaired += 1
    if repaired:
        await db.commit()
    return repaired


async def _fail_orphaned_running_spawn_requests(db, *, offline_seconds: int) -> int:
    """Fail spawn_requests stuck in 'running' whose claiming environment bridge is
    no longer the current live env bridge.

    These orphan when an env bridge restarts / is superseded (e.g. `aify-comms`
    restarted) BEFORE the worker it was spawning finished coming up: the claiming
    bridge is gone, so nothing will ever PATCH the spawn to completed/failed, and
    it lingers 'running' forever (clutters state, masks real spawn activity, and
    accumulates across restarts). Complements
    `_repair_spawn_requests_from_initial_dispatch_failures`, which only covers the
    case where the initial-brief dispatch itself failed.

    SAFETY — this ONLY touches the stale DB record, never any process:
    - Targets ONLY status='running' with empty finished_at.
    - NEVER fails a spawn whose `claimed_by_bridge_id` is a CURRENTLY-online
      environment bridge — a worker actively (even slowly) booting on the live
      bridge is left alone regardless of how long it has been booting, because
      its claiming bridge stays in the live set.
    - Requires a DETERMINABLE claim/create age > SPAWN_ORPHAN_GRACE_SECONDS, so a
      just-claimed spawn whose env heartbeat may briefly lag gets grace; unknown
      age → left alone (conservative).
    - The coldstart idempotency gate only inspects queued/claimed spawns, so
      failing a 'running' orphan never blocks a future autostart — it frees state.
      A live worker (if one exists) keeps delivering via its own sidecar bridge.
    """
    # SEAM NORMALIZATION, v0.5 slice 2 (declared, as in slice 1a). This used to call
    # `_load_settings(db)` for ONE key, `environment_offline_seconds`. `_load_settings` lives in the
    # router; importing it back here would be the cycle this release removes. The caller passes the
    # scalar from the settings its sweep pass already loads — same key, same default, same use.
    environment_rows = await (await db.execute(
        "SELECT * FROM environments WHERE COALESCE(bridge_id, '') != ''"
    )).fetchall()
    live_bridge_ids = {
        str(row["bridge_id"]).strip()
        for row in (environment_rows or [])
        if _environment_effective_status(
            row,
            offline_seconds=offline_seconds,
        ) == "online"
    }

    now_epoch = datetime.now(timezone.utc).timestamp()
    now = _now()
    cursor = await db.execute(
        """
        SELECT id, claimed_by_bridge_id, claimed_at, created_at
        FROM spawn_requests
        WHERE status = 'running' AND COALESCE(finished_at, '') = ''
        """
    )
    failed = 0
    for row in await cursor.fetchall():
        bid = str(row["claimed_by_bridge_id"] or "").strip()
        if bid and bid in live_bridge_ids:
            continue  # claiming bridge is live → genuinely in progress, leave it
        age_epoch = _iso_to_epoch(str(row["claimed_at"] or row["created_at"] or ""))
        if not age_epoch or (now_epoch - age_epoch) < SPAWN_ORPHAN_GRACE_SECONDS:
            continue  # too fresh, or age undeterminable → leave it (conservative)
        await db.execute(
            """
            UPDATE spawn_requests
            SET status = 'failed',
                error = COALESCE(NULLIF(error, ''), 'Orphaned: claiming environment bridge is no longer live (env bridge restart/supersede); failed by reconcile.'),
                finished_at = COALESCE(finished_at, ?),
                updated_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (now, now, row["id"]),
        )
        failed += 1
    if failed:
        await db.commit()
    return failed
