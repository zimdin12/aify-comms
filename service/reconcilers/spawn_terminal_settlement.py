"""Ending a running spawn that the world has already moved past.

Extracted from `service/reconcilers/spawn_lifecycle.py` in v0.5.4, which kept the two repairs that
act on spawn REQUESTS. These two act on spawns that are already RUNNING and can no longer succeed.

TWO WAYS A RUNNING SPAWN BECOMES POINTLESS, and they are found differently. One is SUPERSEDED: the
agent already has a current session, so whatever this spawn is booting is a second copy nobody asked
for — detected by comparing the spawn against the live session, not by any timeout. The other is
DEAD-TERMINAL: the terminal the spawn was driving has ended, so nothing is going to finish it —
detected by the terminal's own end status.

THE GRACE WINDOWS ARE NOT INTERCHANGEABLE, which is why only one of them travelled here. The
dead-terminal grace (45s) is short because a terminal end status is a FACT and the only risk is
racing the row that records it. The orphan grace (180s, in `api_core/tuning.py`) is long because that
reaper INFERS abandonment from age, and the two reapers left in `spawn_lifecycle.py` share it.

`_count_spawns_masked_by_live_sibling` IS THE SAFETY RAIL on the dead-terminal path: a spawn whose
terminal died but whose agent has a live sibling terminal is not finished, it is being taken over,
and finalising it would kill a handover in progress.

Bodies byte-identical to what stood in `spawn_lifecycle.py`.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from service.api_core.dead_terminal_spawn_query import (
    _count_spawns_masked_by_live_sibling,
    _select_spawns_with_dead_terminals,
    _terminal_end_statuses_ordered,
)
from service.api_core.tuning import SPAWN_ORPHAN_GRACE_SECONDS
from service.clock import iso_to_epoch as _iso_to_epoch, now as _now
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
# ALIASED — the leaf calls it `meaningful_failure_line`; every reconciler that reads it has
# always spelled it `_terminal_failure_line`, and keeping that spelling is what makes the moved
# bodies byte-identical. Copying the reference without the alias resolves nowhere, and the
# undefined-name sweep is what caught it here — the third alias it has caught in this series.
from service.terminal_diagnostics import meaningful_failure_line as _terminal_failure_line

logger = logging.getLogger(__name__)

SPAWN_DEAD_TERMINAL_GRACE_SECONDS = 45


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
    rows = await _select_spawns_with_dead_terminals(db, end_statuses, limit)
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
    masked_row = await _count_spawns_masked_by_live_sibling(db, end_statuses)
    masked = int((masked_row["n"] if masked_row is not None else 0) or 0)
    if masked:
        logger.info(
            "dead-terminal spawn finalize: %d finalized, %d left alone (a live sibling terminal "
            "shares the session — rebind in progress, or a stale dead row needs pruning)",
            finalized,
            masked,
        )
    return finalized
