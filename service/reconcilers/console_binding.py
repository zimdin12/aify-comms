"""Rebind a LIVE console that warm rotation left attached to an ended session.

REPORTED 2026-08-11 by another team's tech-lead, with the rows, the root cause and a suggested fix —
one of the better bug reports this project has had. Verified here before fixing: the healer they named
(`_reconcile_resurrected_managed_consoles`) does only match `stopped`/`failed` terminals carrying
`reconciled_managed_ghost_console_dead_worker`, so an `attached` terminal is never a candidate for it.

THE SYMPTOM. A managed agent reads `working`, `GET /agents/{id}/console` says `live: true`, and the
dashboard offers **"Start console"** — because `console-chooser.js` derives the terminal from the
CURRENT session row's binding, and that binding is empty. Clicking it would spawn a SECOND console
beside the live one.

THE CAUSE. Managed-warm agents reuse one PTY across many `agent_sessions` generations. When a new
generation is created the reused terminal stays bound to the previous row — which is then marked
ended — and the new `running` row gets `terminal_id = ''`:

    agent_sessions   sess_…322d1758  ended    terminal_id=term_…c6b602a5
                     sess_…2806ab2a  running  terminal_id=''            <- current
    terminal_sessions term_…c6b602a5 session_id=sess_…322d1758  attached (live, fresh)

Three surfaces then disagree, and the one the operator clicks is the one that is wrong.

WHY A RECONCILER RATHER THAN FIXING THE ROTATION. The reporter offered both. This takes the
reconciler, for the reason this repo keeps relearning: carrying the binding forward at rotation time
fixes the ONE rotation path that was found, and `agent_sessions` is written from many sites. Keying on
the STATE — a live terminal whose owning session has ended, beside a current session with no terminal
— cannot be defeated by a new rotation path. Same argument as
`_finalize_spawns_with_dead_terminals` (v0.2.0), which replaced an event-based fix for exactly this
reason.

FAIL-CLOSED, mirroring the resurrect healer's guards. It only ever moves a binding when every one of
these holds:
  - the terminal is `attached` (never a dead or unknown one),
  - its owning session is genuinely ENDED (never a live one — two live sessions is a different bug
    and must not be papered over),
  - the current session is `running` and has NO terminal (never steals from a bound row),
  - both rows belong to the same agent.

Anything else is left exactly as it is, because a wrong rebind points the operator's console at
another agent's process.
"""

from __future__ import annotations

import logging

from service.clock import now as _now

logger = logging.getLogger(__name__)

# The statuses that mean "this session is over". Deliberately narrow: `recovering`/`restarting` are
# NOT here, because a session mid-recovery may legitimately reclaim its own terminal.
_ENDED_SESSION_STATUSES = ("ended", "stopped", "failed")


async def rebind_orphaned_live_consoles(db, *, limit: int = 50) -> int:
    """Move a live console's binding from an ended session onto the agent's current running one.

    Returns how many bindings were repaired. Commit is the caller's, matching the sweep's other
    steps.
    """
    placeholders = ",".join("?" for _ in _ENDED_SESSION_STATUSES)
    cursor = await db.execute(
        f"""
        SELECT current_session.id   AS current_session_id,
               current_session.agent_id AS agent_id,
               old_session.id       AS old_session_id,
               t.id                 AS terminal_id
        FROM agent_sessions current_session
        JOIN agent_sessions old_session
          ON old_session.agent_id = current_session.agent_id
         AND old_session.id <> current_session.id
         AND LOWER(COALESCE(old_session.status, '')) IN ({placeholders})
         AND COALESCE(old_session.terminal_id, '') <> ''
        JOIN terminal_sessions t
          ON t.id = old_session.terminal_id
         AND t.agent_id = current_session.agent_id
         AND LOWER(COALESCE(t.status, '')) = 'attached'
        WHERE LOWER(COALESCE(current_session.status, '')) = 'running'
          AND COALESCE(current_session.terminal_id, '') = ''
          -- Never touch an agent that has ANOTHER live session already holding a terminal: that is
          -- a duplicate-owner problem, not an orphaned binding, and rebinding would hide it.
          AND NOT EXISTS (
            SELECT 1 FROM agent_sessions other
            WHERE other.agent_id = current_session.agent_id
              AND other.id <> current_session.id
              AND LOWER(COALESCE(other.status, '')) NOT IN ({placeholders})
              AND COALESCE(other.terminal_id, '') <> ''
          )
        ORDER BY current_session.started_at DESC
        LIMIT ?
        """,
        (*_ENDED_SESSION_STATUSES, *_ENDED_SESSION_STATUSES, max(1, int(limit or 50))),
    )
    rows = await cursor.fetchall()
    repaired = 0
    now = _now()
    for row in rows:
        # Each UPDATE re-asserts the precondition it depends on, so a concurrent write between the
        # SELECT and here can only make the statement a no-op — never a wrong rebind.
        moved = await db.execute(
            """
            UPDATE agent_sessions
            SET terminal_id = ?, terminal_status = 'attached', last_seen = ?
            WHERE id = ? AND COALESCE(terminal_id, '') = ''
            """,
            (row["terminal_id"], now, row["current_session_id"]),
        )
        if not moved.rowcount:
            continue
        await db.execute(
            """
            UPDATE agent_sessions
            SET terminal_id = '', terminal_status = ''
            WHERE id = ? AND terminal_id = ?
            """,
            (row["old_session_id"], row["terminal_id"]),
        )
        await db.execute(
            """
            UPDATE terminal_sessions
            SET session_id = ?, updated_at = ?
            WHERE id = ? AND LOWER(COALESCE(status, '')) = 'attached'
            """,
            (row["current_session_id"], now, row["terminal_id"]),
        )
        repaired += 1
        logger.info(
            "rebound live console %s from ended session %s onto running session %s (agent %s) — "
            "warm rotation left the binding behind, so the dashboard was offering 'Start console' "
            "for an agent whose PTY is alive",
            row["terminal_id"], row["old_session_id"], row["current_session_id"], row["agent_id"],
        )
    return repaired
