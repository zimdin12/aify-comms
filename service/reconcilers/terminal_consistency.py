"""Terminal/session correlation repair: when the two tables disagree about who owns what.

v0.5 slice 4, extracted from `service/routers/api_v2.py`. One function, 230 lines — the reviewer's
bound says a body this size anchors its own slice, and this one does.

NAMES WERE BORROWED FROM THE ROUTER rather than moved, and the reason was caller count, measured
before deciding: `_append_terminal_event` had 36 call sites in the router and
`_clear_console_terminal_binding` had 9. Dragging those across was a migration of its own, not part of
moving one reconciler — the same judgement that deferred `_agent_liveness` in slice 3a.

`_clear_console_terminal_binding` IS NO LONGER ONE OF THEM. v0.5.4 moved it, with the two helpers it
sits beside, to `service/api_core/terminal_controls_io.py` — this module imports it from there now,
so that upward edge is gone rather than merely tolerated. This was the "later slice" the paragraph
above anticipated; what remains is `_append_terminal_event`, which the api_core events leaf already
owns.
"""

from __future__ import annotations

import json
import logging

from service.api_core.events import _append_terminal_event  # v0.5.1i: the leaf owner
from service.api_core.terminal_controls_io import _clear_console_terminal_binding
from service.api_core.virtual_rpc import VIRTUAL_RPC_COMMAND_SET
from service.clock import now as _now

logger = logging.getLogger(__name__)






async def _repair_terminal_session_consistency(db) -> int:
    now = _now()
    active_statuses = ("starting", "attached", "running", "active", "idle")
    repaired = 0

    stale_binding_cursor = await db.execute(
        """
        SELECT stale.id AS session_id, stale.terminal_id AS terminal_id
        FROM agent_sessions stale
        JOIN terminal_sessions terminal ON terminal.id = stale.terminal_id
        JOIN agent_sessions current
          ON current.id = terminal.session_id
         AND current.terminal_id = stale.terminal_id
        WHERE LOWER(COALESCE(stale.status, '')) IN
              ('ended', 'completed', 'cancelled', 'failed', 'lost', 'stopped', 'exited')
          AND COALESCE(stale.terminal_id, '') != ''
          AND terminal.session_id != stale.id
          AND LOWER(COALESCE(current.status, '')) IN
              ('starting', 'recovering', 'restarting', 'running', 'active', 'idle')
        """
    )
    for row in await stale_binding_cursor.fetchall():
        await db.execute(
            """
            UPDATE agent_sessions
            SET owner_bridge_id = '',
                terminal_id = '',
                terminal_status = '',
                terminal_command = '',
                terminal_workspace = ''
            WHERE id = ? AND terminal_id = ?
            """,
            (row["session_id"], row["terminal_id"]),
        )
        await _append_terminal_event(
            db,
            row["terminal_id"],
            "stale_session_terminal_binding_cleared",
            json.dumps({"sessionId": row["session_id"]}),
        )
        repaired += 1

    legacy_cursor = await db.execute(
        f"""
        SELECT id, agent_id
        FROM terminal_sessions
        WHERE runtime = 'claude-code'
          AND status IN ({",".join("?" for _ in active_statuses)})
          AND COALESCE(command, '') != ''
          AND command NOT LIKE '%claude-aify%'
        """,
        active_statuses,
    )
    legacy_rows = await legacy_cursor.fetchall()
    for row in legacy_rows:
        terminal_id = str(row["id"] or "").strip()
        agent_id = str(row["agent_id"] or "").strip()
        if not terminal_id:
            continue
        reason = "Released legacy raw Claude terminal during session reconciliation; Claude backing must start through claude-aify."
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'failed',
                updated_at = ?,
                stopped_at = COALESCE(stopped_at, ?),
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ?
            """,
            (now, now, reason, terminal_id),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET owner_mode = 'managed',
                terminal_status = 'failed',
                last_seen = ?
            WHERE terminal_id = ?
            """,
            (now, terminal_id),
        )
        if agent_id:
            await _clear_console_terminal_binding(db, agent_id, terminal_id, now=now)
        await _append_terminal_event(
            db,
            terminal_id,
            "terminal_consistency_repaired",
            json.dumps({"reason": reason}),
        )
        repaired += 1

    # Exclude virtual rpc terminals from PTY-status mirroring. The
    # synth feed for managed pi/hermes/codex/opencode has a different
    # lifecycle from a real node-pty wrapper: it survives across
    # dispatch boundaries as the operator-visibility surface, while
    # the agent_sessions.terminal_status field can carry stale state
    # from a previous wrapper PTY for the same agent. Operator-
    # reported 2026-05-22: hermes synth terminal got marked stopped
    # within seconds of creation because agent_sessions.terminal_status
    # had a leftover 'stopped' from earlier hermes-aify wrapper PTYs.
    mismatch_cursor = await db.execute(
        f"""
        SELECT t.id, t.agent_id, s.terminal_status
        FROM terminal_sessions t
        JOIN agent_sessions s ON s.terminal_id = t.id
        WHERE t.status IN ({",".join("?" for _ in active_statuses)})
          AND s.terminal_status IN ('stopped', 'failed')
          AND t.command NOT IN ({",".join("?" for _ in VIRTUAL_RPC_COMMAND_SET)})
        """,
        (*active_statuses, *VIRTUAL_RPC_COMMAND_SET),
    )
    mismatch_rows = await mismatch_cursor.fetchall()
    for row in mismatch_rows:
        terminal_id = str(row["id"] or "").strip()
        agent_id = str(row["agent_id"] or "").strip()
        terminal_status = str(row["terminal_status"] or "").strip().lower()
        if not terminal_id or terminal_status not in {"stopped", "failed"}:
            continue
        reason = f"Terminal reconciled because owner session is {terminal_status}."
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = ?,
                updated_at = ?,
                stopped_at = COALESCE(stopped_at, ?),
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ?
            """,
            (terminal_status, now, now, reason, terminal_id),
        )
        if agent_id:
            await _clear_console_terminal_binding(db, agent_id, terminal_id, now=now)
        await _append_terminal_event(
            db,
            terminal_id,
            "terminal_consistency_repaired",
            json.dumps({"reason": reason}),
        )
        repaired += 1

    orphan_cursor = await db.execute(
        f"""
        SELECT t.id, t.agent_id
        FROM terminal_sessions t
        LEFT JOIN agent_sessions s ON s.terminal_id = t.id
        WHERE t.status IN ({",".join("?" for _ in active_statuses)})
          AND s.id IS NULL
        """,
        active_statuses,
    )
    orphan_rows = await orphan_cursor.fetchall()
    for row in orphan_rows:
        terminal_id = str(row["id"] or "").strip()
        agent_id = str(row["agent_id"] or "").strip()
        if not terminal_id:
            continue
        reason = "Terminal reconciled because it is not referenced by any current session."
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'stopped',
                updated_at = ?,
                stopped_at = COALESCE(stopped_at, ?),
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ?
            """,
            (now, now, reason, terminal_id),
        )
        if agent_id:
            await _clear_console_terminal_binding(db, agent_id, terminal_id, now=now)
        await _append_terminal_event(
            db,
            terminal_id,
            "terminal_consistency_repaired",
            json.dumps({"reason": reason}),
        )
        repaired += 1

    inactive_binding_cursor = await db.execute(
        """
        SELECT s.id AS session_id,
               s.agent_id AS agent_id,
               s.terminal_id AS terminal_id,
               s.terminal_status AS session_terminal_status,
               t.status AS terminal_status
        FROM agent_sessions s
        JOIN terminal_sessions t ON t.id = s.terminal_id
        WHERE COALESCE(s.terminal_id, '') != ''
          AND (
            LOWER(COALESCE(s.terminal_status, '')) IN ('stopped', 'failed')
            OR LOWER(COALESCE(t.status, '')) IN ('stopped', 'failed')
          )
        """
    )
    inactive_binding_rows = await inactive_binding_cursor.fetchall()
    for row in inactive_binding_rows:
        session_id = str(row["session_id"] or "").strip()
        agent_id = str(row["agent_id"] or "").strip()
        terminal_id = str(row["terminal_id"] or "").strip()
        if not session_id or not terminal_id:
            continue
        reason = "Cleared stopped Console terminal as current session binding."
        if agent_id:
            await _clear_console_terminal_binding(db, agent_id, terminal_id, now=now)
        await db.execute(
            """
            UPDATE agent_sessions
            SET owner_mode = 'managed',
                owner_bridge_id = '',
                terminal_id = '',
                terminal_status = '',
                terminal_command = '',
                terminal_workspace = '',
                last_seen = ?
            WHERE id = ?
              AND terminal_id = ?
            """,
            (now, session_id, terminal_id),
        )
        await _append_terminal_event(
            db,
            terminal_id,
            "terminal_consistency_repaired",
            json.dumps({"reason": reason}),
        )
        repaired += 1

    if repaired:
        await db.commit()
    return repaired
