"""The pre-split `_finalize_spawns_with_dead_terminals`, frozen.

Not imported by anything. It is the ONE true original that
`test_finalize_spawns_with_dead_terminals_split_is_inert.py` inlines every extraction back against.

Captured from `git show HEAD:service/reconcilers/spawn_lifecycle.py` at the commit before the
extraction, decoded as utf-8 rather than through the locale codec.
"""


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
