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

from service.terminal_diagnostics import meaningful_failure_line as _terminal_failure_line
from service.clock import now as _now
from service.clock import iso_to_epoch as _iso_to_epoch
from service.env_status import environment_effective_status as _environment_effective_status
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state

logger = logging.getLogger(__name__)

# Moved WITH the reconcilers that read them. `_TERMINAL_END_STATUSES_ORDERED` keeps its ordering
# contract: a set gives no ordering guarantee across builds, and an inline literal in a query is how
# the two managed-worker sweeps came to disagree about `degraded` (finding N7). The router still owns
# `_TERMINAL_END_STATUSES`; `test_terminal_status_sets_agree` fails the suite if the two diverge.
SPAWN_ORPHAN_GRACE_SECONDS = 180  # matches the dispatch queued-run backstop window


def _terminal_end_statuses_ordered() -> tuple[str, ...]:
    """Imported LAZILY from the router, deliberately.

    `_TERMINAL_END_STATUSES_ORDERED` derives from `_TERMINAL_END_STATUSES`, which the router still
    owns and which `test_terminal_status_sets_agree` pins there. Forking a second copy here is
    exactly the divergence that produced finding N7 (two sweeps disagreeing about `degraded`), so
    this module borrows the one owner instead. A module-level import would be a cycle; a function
    call at use time is not, because the router is fully loaded by then."""
    from service.api_core.terminal_status import _TERMINAL_END_STATUSES_ORDERED

    return _TERMINAL_END_STATUSES_ORDERED

SPAWN_DEAD_TERMINAL_GRACE_SECONDS = 45


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


async def _fail_running_spawns_superseded_by_current_session(db) -> int:
    """Fail only running spawns proven older than the agent's current live backing.

    ``running`` is the normal lifetime state of a managed spawn, so a live terminal alone cannot
    prove that its request is stale. The current ``agent_sessions.spawn_request_id`` is the
    correlation authority: when a live managed session and terminal both reference a newer running
    request, older running requests for that agent are superseded. The current request is never
    changed.
    """
    live_cutoff = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - SPAWN_ORPHAN_GRACE_SECONDS)
    )
    cursor = await db.execute(
        """
        SELECT s.id AS session_id, s.agent_id, s.spawn_request_id, s.started_at
        FROM agent_sessions s
        JOIN terminal_sessions t
          ON t.id = s.terminal_id
         AND t.agent_id = s.agent_id
         AND t.session_id = s.id
        JOIN spawn_requests current_spawn
          ON current_spawn.id = s.spawn_request_id
         AND current_spawn.agent_id = s.agent_id
         AND current_spawn.status = 'running'
         AND COALESCE(current_spawn.finished_at, '') = ''
        WHERE s.mode IN ('managed', 'managed-warm')
          AND s.status IN ('starting','running','active','idle','recovering')
          AND COALESCE(s.spawn_request_id, '') <> ''
          AND t.status IN ('starting','attached','running','active','idle','recovering')
          AND COALESCE(NULLIF(t.updated_at, ''), '') >= ?
        ORDER BY s.started_at DESC
        """,
        (live_cutoff,),
    )
    current_by_agent = {}
    for row in await cursor.fetchall():
        agent_id = str(row["agent_id"] or "").strip()
        if agent_id and agent_id not in current_by_agent:
            current_by_agent[agent_id] = row

    failed = 0
    now = _now()
    for agent_id, current in current_by_agent.items():
        current_spawn_id = str(current["spawn_request_id"] or "").strip()
        current_started_epoch = _iso_to_epoch(str(current["started_at"] or ""))
        if not current_spawn_id or not current_started_epoch:
            continue
        old_cursor = await db.execute(
            """
            SELECT id, claimed_at, created_at
            FROM spawn_requests
            WHERE agent_id = ?
              AND status = 'running'
              AND COALESCE(finished_at, '') = ''
              AND id <> ?
            """,
            (agent_id, current_spawn_id),
        )
        for old in await old_cursor.fetchall():
            old_epoch = _iso_to_epoch(str(old["claimed_at"] or old["created_at"] or ""))
            if not old_epoch or old_epoch >= current_started_epoch:
                continue
            update_cursor = await db.execute(
                """
                UPDATE spawn_requests
                SET status = 'failed',
                    error = COALESCE(
                        NULLIF(error, ''),
                        'Superseded by a newer live managed session tied to a different spawn request.'
                    ),
                    finished_at = COALESCE(finished_at, ?),
                    updated_at = ?
                WHERE id = ?
                  AND status = 'running'
                  AND COALESCE(finished_at, '') = ''
                  AND EXISTS (
                    SELECT 1
                    FROM agent_sessions s
                    JOIN terminal_sessions t
                      ON t.id = s.terminal_id
                     AND t.agent_id = s.agent_id
                     AND t.session_id = s.id
                    JOIN spawn_requests current_spawn
                      ON current_spawn.id = s.spawn_request_id
                     AND current_spawn.agent_id = s.agent_id
                     AND current_spawn.status = 'running'
                     AND COALESCE(current_spawn.finished_at, '') = ''
                    WHERE s.id = ?
                      AND s.agent_id = ?
                      AND s.spawn_request_id = ?
                      AND s.mode IN ('managed', 'managed-warm')
                      AND s.status IN ('starting','running','active','idle','recovering')
                      AND t.status IN ('starting','attached','running','active','idle','recovering')
                      AND COALESCE(NULLIF(t.updated_at, ''), '') >= ?
                  )
                """,
                (
                    now,
                    now,
                    old["id"],
                    current["session_id"],
                    agent_id,
                    current_spawn_id,
                    live_cutoff,
                ),
            )
            if update_cursor.rowcount > 0:
                failed += 1
    return failed


async def _finalize_spawns_with_dead_terminals(
    db, *, grace_seconds: int = SPAWN_DEAD_TERMINAL_GRACE_SECONDS, limit: int = 200
) -> int:
    """Finalize a `running`/`starting` spawn_request whose bound terminal is DEAD.

    PROVEN LIVE (2026-08-07, v0.2 WS-2). Spawn `spawn_1786109794441_a620d173` was
    claimed 13:36:34; its terminal `term_1786109794427_0f32fd75` reached `stopped` at
    13:37:39 after a 65s failed hermes launch. The spawn then sat `running` with an
    empty `finished_at` for **97 minutes**, and was cleared at 15:14:38 only by an
    unrelated "superseded by a newer live managed session" — no reconciler ever
    touched it.

    WHY the existing reapers missed it, both times for the same structural reason:

    - `_fail_orphaned_running_spawn_requests` skips any spawn whose
      `claimed_by_bridge_id` is a currently-ONLINE env bridge. The env bridge stayed
      online the whole time — only the worker died — so that reaper is correct to
      skip it and can never cover this shape.
    - `report_terminal_dead` DOES finalize the spawn (and its comment explains
      exactly why that matters). But it is ONE of the ~26 sites that write
      `terminal_sessions`, and it was never called here: the row carries no
      `console_dead_reported` event and an EMPTY `error`, so whichever path stopped
      it at 13:37:39 was a different one. Finalization was bolted onto a single
      death path out of many.

    So this reconciler is deliberately **state-based, not event-based**: it keys on
    the terminal's own recorded status rather than on any death path remembering to
    call something. A new way for a terminal to die cannot defeat it, which is the
    property the event-based version lacked.

    Why finalizing matters beyond tidiness — `_has_pending_or_booting_spawn_request`
    treats a `running` spawn with empty `finished_at` as "worker mid-boot" for 5
    minutes, so for those 5 minutes the DEAD worker suppresses the very respawn its
    death made necessary, and the requester is told a spawn is already in flight.

    SAFETY — DB rows only, never a process:
    - Only status IN ('starting','running') with an empty `finished_at`.
    - Requires a session-bound terminal in a TERMINAL status (`_TERMINAL_END_STATUSES`).
    - LEAVES the spawn alone if ANY terminal on the same session is still live, so a
      rebind/respawn race (new terminal created before the session is re-pointed)
      cannot fail a healthy worker.
    - Requires the death to be older than `grace_seconds`; an undeterminable death
      time is treated as too fresh and left alone (conservative).
    - Records the terminal's own recorded cause as the spawn error, so the refusal an
      agent later reads names what actually happened (WS-1).
    """
    now = _now()
    end_statuses = ",".join("?" for _ in _terminal_end_statuses_ordered())
    cursor = await db.execute(
        f"""
        SELECT s.id AS spawn_id,
               s.agent_id AS agent_id,
               t.id AS terminal_id,
               t.status AS terminal_status,
               t.output AS terminal_output,
               t.error AS terminal_error,
               COALESCE(NULLIF(t.stopped_at, ''), t.updated_at) AS died_at
        FROM spawn_requests s
        JOIN terminal_sessions t ON t.session_id = s.session_id
        WHERE s.status IN ('starting', 'running')
          AND COALESCE(s.finished_at, '') = ''
          AND COALESCE(s.session_id, '') != ''
          AND LOWER(COALESCE(t.status, '')) IN ({end_statuses})
          AND NOT EXISTS (
            SELECT 1 FROM terminal_sessions live
            WHERE live.session_id = s.session_id
              AND LOWER(COALESCE(live.status, '')) NOT IN ({end_statuses})
          )
        ORDER BY s.created_at ASC
        LIMIT ?
        """,
        (*_terminal_end_statuses_ordered(), *_terminal_end_statuses_ordered(), max(1, int(limit or 200))),
    )
    rows = await cursor.fetchall()
    now_epoch = datetime.now(timezone.utc).timestamp()
    grace = max(1, int(grace_seconds or SPAWN_DEAD_TERMINAL_GRACE_SECONDS))
    finalized = 0
    for row in rows:
        died_epoch = _iso_to_epoch(str(row["died_at"] or ""))
        if not died_epoch or (now_epoch - died_epoch) < grace:
            continue  # too fresh, or death time undeterminable → leave it (conservative)
        cause = _terminal_failure_line(str(row["terminal_output"] or "")) or str(row["terminal_error"] or "").strip()
        detail = f": {cause}" if cause else " (no output was recorded)"
        message = (
            f"Worker terminal {row['terminal_id']} is {str(row['terminal_status'] or 'dead').lower()}"
            f"{detail}"
        )
        cursor = await db.execute(
            """
            UPDATE spawn_requests
            SET status = 'failed',
                finished_at = ?,
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END,
                updated_at = ?
            WHERE id = ?
              AND status IN ('starting', 'running')
              AND COALESCE(finished_at, '') = ''
            """,
            (now, message, now, row["spawn_id"]),
        )
        if cursor.rowcount > 0:
            finalized += 1
            agent_id = str(row["agent_id"] or "").strip()
            if agent_id:
                await _invalidate_agent_live_state(db, agent_id)
    if finalized:
        await db.commit()
    # Name what the live-sibling guard held back (reviewer suggestion, 2026-08-07). The
    # guard is the right call — a rebind race must not fail a healthy worker — but it is
    # also SILENT, and a masked row is indistinguishable from "nothing was dead". This
    # repo has been bitten by exactly that ambiguity: on the day this shipped, "0
    # finalized" could not be told apart from "the sweep never ran" without reading the
    # container's imports. Logged, not returned, so the tested int contract is unchanged.
    masked_row = await (await db.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM spawn_requests s
        WHERE s.status IN ('starting', 'running')
          AND COALESCE(s.finished_at, '') = ''
          AND COALESCE(s.session_id, '') != ''
          AND EXISTS (
            SELECT 1 FROM terminal_sessions dead
            WHERE dead.session_id = s.session_id
              AND LOWER(COALESCE(dead.status, '')) IN ({end_statuses})
          )
          AND EXISTS (
            SELECT 1 FROM terminal_sessions live
            WHERE live.session_id = s.session_id
              AND LOWER(COALESCE(live.status, '')) NOT IN ({end_statuses})
          )
        """,
        (*_terminal_end_statuses_ordered(), *_terminal_end_statuses_ordered()),
    )).fetchone()
    masked = int((masked_row["n"] if masked_row is not None else 0) or 0)
    if masked:
        logger.info(
            "dead-terminal spawn finalize: %d finalized, %d left alone (a live sibling terminal "
            "shares the session — rebind in progress, or a stale dead row needs pruning)",
            finalized,
            masked,
        )
    return finalized
