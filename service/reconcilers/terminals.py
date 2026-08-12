"""Terminal reconcilers, group A: history pruning, resurrection, idle RPC workers, stale rebinding.

v0.5 slice 5, extracted from `service/routers/api_v2.py`. Dependency table measured with the
every-name scan before the move (see docs/V0.5_SLICE3.md for why a call-only scan is not enough).

BORROWED, NOT MOVED — decided on measured caller count, the same rule as slice 4:
`_append_terminal_event` (36 call sites in the router), `_append_terminal_control`,
`_has_live_channel_sidecar`, `_has_live_terminal_session`, and the constants
`MANAGED_ORPHAN_GRACE_SECONDS` / `VIRTUAL_RPC_COMMAND_SET`. Each is read through a function-scope
import so there is exactly one owner and no second copy that can drift. Safe in this direction: the
router is fully loaded by the time any reconciler runs.

Those borrows are the visible debt of this slice. A later terminal-helper consolidation is what
deletes them.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from service.api_core.serialization import _json_loads_or  # v0.5.1c: the leaf owner, not via the router
from service.api_core.events import _append_terminal_control, _append_terminal_event  # v0.5.1i: the leaf owner
from service.clock import now as _now
from service.clock import iso_to_epoch as _iso_to_epoch
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state

logger = logging.getLogger(__name__)




async def _has_live_channel_sidecar(*a, **k):
    from service.control_plane import _has_live_channel_sidecar as _i
    return await _i(*a, **k)


async def _has_live_terminal_session(*a, **k):
    from service.control_plane import _has_live_terminal_session as _i
    return await _i(*a, **k)



def _managed_orphan_grace_seconds():
    from service.control_plane import MANAGED_ORPHAN_GRACE_SECONDS
    return MANAGED_ORPHAN_GRACE_SECONDS


def _virtual_rpc_command_set():
    from service.control_plane import VIRTUAL_RPC_COMMAND_SET
    return VIRTUAL_RPC_COMMAND_SET


async def _prune_terminal_history(
    db,
    *,
    terminal_event_ttl_hours: int = 24,
    dispatch_event_ttl_hours: int = 72,
    ended_output_ttl_hours: int = 24,
    terminal_control_ttl_hours: int = 24,
    keep_terminal_rows_per_agent: int = 8,
    chunk: int = 5000,
    max_chunks: int = 200,
) -> dict[str, int]:
    """Bounded history retention so the DB does not grow forever.

    The live console scrollback is the (already 64KB-capped)
    terminal_sessions.output column — that is what the dashboard reads and is
    NOT touched for active sessions. This only trims redundant audit history:
    per-chunk terminal_events past a TTL, dispatch_events past a TTL, and the
    output blob of long-ended terminals. Chunked deletes keep each statement
    short so a live control plane is never locked for long.
    """
    counts = {"terminal_events": 0, "terminal_events_capped": 0, "dispatch_events": 0, "ended_output_cleared": 0, "terminal_controls": 0, "terminal_sessions": 0}
    keep_events_per_terminal = 200

    async def _chunked_delete(sql: str, params: tuple) -> int:
        removed = 0
        for _ in range(max_chunks):
            cur = await db.execute(sql, params)
            await db.commit()
            n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
            removed += n
            if n < chunk:
                break
        return removed

    counts["terminal_events"] = await _chunked_delete(
        f"DELETE FROM terminal_events WHERE id IN ("
        f"SELECT id FROM terminal_events WHERE datetime(created_at) < datetime('now', ?) "
        f"ORDER BY id ASC LIMIT {int(chunk)})",
        (f"-{max(1, int(terminal_event_ttl_hours))} hours",),
    )
    counts["dispatch_events"] = await _chunked_delete(
        f"DELETE FROM dispatch_events WHERE id IN ("
        f"SELECT id FROM dispatch_events WHERE datetime(created_at) < datetime('now', ?) "
        f"ORDER BY id ASC LIMIT {int(chunk)})",
        (f"-{max(1, int(dispatch_event_ttl_hours))} hours",),
    )
    # Per-terminal cap: chatty long-lived consoles produce hundreds of
    # thousands of event rows *within* the TTL window, so age alone cannot
    # bound them. Keep only the most recent N per terminal. Per-terminal
    # indexed deletes (idx_terminal_events_terminal on terminal_id,id) stay
    # fast and short even on a large table.
    term_ids = [
        r["terminal_id"]
        for r in await (await db.execute("SELECT DISTINCT terminal_id FROM terminal_events")).fetchall()
    ]
    for tid in term_ids:
        cutoff_row = await (await db.execute(
            "SELECT id FROM terminal_events WHERE terminal_id = ? ORDER BY id DESC LIMIT 1 OFFSET ?",
            (tid, keep_events_per_terminal),
        )).fetchone()
        if not cutoff_row:
            continue
        cutoff_id = cutoff_row["id"]
        for _ in range(max_chunks):
            cur = await db.execute(
                f"DELETE FROM terminal_events WHERE id IN ("
                f"SELECT id FROM terminal_events WHERE terminal_id = ? AND id <= ? "
                f"ORDER BY id ASC LIMIT {int(chunk)})",
                (tid, cutoff_id),
            )
            await db.commit()
            n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            counts["terminal_events_capped"] += n
            if n < chunk:
                break

    cur = await db.execute(
        "UPDATE terminal_sessions SET output = '' "
        "WHERE status IN ('stopped', 'failed', 'ended', 'cancelled') "
        "AND COALESCE(output, '') != '' "
        "AND datetime(updated_at) < datetime('now', ?)",
        (f"-{max(1, int(ended_output_ttl_hours))} hours",),
    )
    await db.commit()
    counts["ended_output_cleared"] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    # terminal_controls retention (2026-06-07): this is the runtime command QUEUE — once a
    # control is HANDLED (handled_at set) it is pure delivered-keystroke audit history. It was
    # never pruned, so it grew unbounded (13k+ rows over 4 days, dominated by per-keystroke
    # dashboard input). Delete ONLY handled controls past the TTL — a control with handled_at
    # IS NULL is still PENDING (a bridge has not claimed/executed it yet) and MUST never be
    # touched here, or a queued keystroke/resize/stop would be silently dropped. Chunked +
    # indexed on id so a live control plane is never locked for long.
    counts["terminal_controls"] = await _chunked_delete(
        f"DELETE FROM terminal_controls WHERE id IN ("
        f"SELECT id FROM terminal_controls "
        f"WHERE handled_at IS NOT NULL AND datetime(handled_at) < datetime('now', ?) "
        f"ORDER BY id ASC LIMIT {int(chunk)})",
        (f"-{max(1, int(terminal_control_ttl_hours))} hours",),
    )
    # terminal_sessions ROW retention (2026-06-17): the rows themselves were never pruned
    # — only their events/output blobs — so ENDED consoles accumulated forever (one managed
    # claude had 184 rows; 99% of the table was stopped/failed cruft). Keep the newest N per
    # agent (any status, so every LIVE console and recent history survives) and delete only
    # the OLDER ended (stopped/failed/ended/cancelled) rows. The status filter guarantees a
    # live console is NEVER deleted; the per-agent keep window guarantees recent debugging
    # history survives. Chunked so the control plane is never locked for long.
    keep_n = max(1, int(keep_terminal_rows_per_agent))
    counts["terminal_sessions"] = await _chunked_delete(
        f"DELETE FROM terminal_sessions WHERE id IN ("
        f"  SELECT t.id FROM terminal_sessions t"
        f"  WHERE LOWER(COALESCE(t.status,'')) IN ('stopped','failed','ended','cancelled')"
        f"    AND (SELECT COUNT(*) FROM terminal_sessions t2"
        f"         WHERE t2.agent_id = t.agent_id AND t2.updated_at > t.updated_at) >= {keep_n}"
        f"  ORDER BY t.updated_at ASC LIMIT {int(chunk)})",
        (),
    )
    return counts


async def _reconcile_resurrected_managed_consoles(db) -> int:
    """Self-heal the INVERSE of the ghost reap: a managed console reaped as
    `reconciled_managed_ghost_console_dead_worker` (an INFERRED death — all three liveness
    signals lapsed at once, e.g. host/WSL starvation paused the heartbeats) whose worker is now
    provably ALIVE again. The reap is monotonic (a `stopped` row can't go active via output —
    `_terminal_status_transition`), so a starved-but-alive worker that resumes stays stranded
    `available` while it works HEADLESS, claiming and completing channel runs, until a console is
    manually re-attached (the next-manager incident, 2026-06-08). Re-activate the row so the agent
    recovers `online`/`working`.

    STRICTLY scoped to never resurrect a genuinely-dead or operator-stopped console:
      - only rows stopped with the ghost-reap error (an explicit Stop / real PTY exit / any other
        reason is NEVER touched — those are authoritative);
      - only when the worker is UNAMBIGUOUSLY alive RIGHT NOW: a live channel-sidecar AND fresh
        terminal output (updated_at within _managed_orphan_grace_seconds()) — a trailing frame from a
        dying process or a stale ghost that never came back is left dead;
      - only when the agent has NO OTHER live terminal (`_has_live_terminal_session`): if a new
        console was already attached the agent has already recovered, so re-activating the old row
        would create a duplicate console + clobber the newer binding. Idempotent: once a row is
        re-activated it counts as live, so the next pass skips the agent.
    """
    healed = 0
    rows = await (await db.execute(
        """
        SELECT t.id AS terminal_id, t.agent_id AS agent_id, t.session_id AS session_id,
               t.updated_at AS updated_at, t.stopped_at AS stopped_at
        FROM terminal_sessions t
        JOIN agents a ON a.id = t.agent_id
        WHERE a.session_mode = 'managed'
          AND a.status != 'stopped'
          AND t.status IN ('stopped', 'failed')
          AND t.error = 'reconciled_managed_ghost_console_dead_worker'
          AND t.id NOT LIKE 'vterm_%'
        ORDER BY t.updated_at DESC
        """
    )).fetchall()
    now = _now()
    for row in (rows or []):
        agent_id = row["agent_id"]
        terminal_id = row["terminal_id"]
        # Already has a live console (a new one attached, or a sibling row already healed) → the
        # agent has recovered; do NOT resurrect this old row (avoids a duplicate + binding clobber).
        if await _has_live_terminal_session(db, agent_id):
            continue
        # Output must be FRESH — the worker is streaming right now, not a trailing/stale frame.
        updated = _iso_to_epoch(str(row["updated_at"] or ""))
        if not updated or (datetime.now(timezone.utc).timestamp() - updated) > _managed_orphan_grace_seconds():
            continue
        # And it must be REAL output SINCE the reap: the reap itself wrote updated_at = stopped_at
        # = now, so for ~90s after a reap the freshness gate above is satisfied by the reap's own
        # write. A genuine post-reap output frame bumps updated_at PAST stopped_at; a reap-only row
        # has them equal — without this, a dead PTY whose (separate) sidecar recovered would be
        # resurrected and then permanently shield the dead console from both reapers (review M2,
        # 2026-06-10).
        stopped = _iso_to_epoch(str(row["stopped_at"] or "")) if "stopped_at" in row.keys() else 0
        if stopped and updated <= stopped:
            continue
        # AND the worker must be reachable now (live, non-superseded channel-sidecar).
        if not await _has_live_channel_sidecar(db, agent_id):
            continue
        # Provably alive → un-reap (guard the UPDATE to the still-stopped row).
        await db.execute(
            "UPDATE terminal_sessions SET status = 'attached', stopped_at = NULL, error = '' "
            "WHERE id = ? AND status IN ('stopped', 'failed')",
            (terminal_id,),
        )
        # Restore the session→terminal binding so the dashboard re-attaches the live console.
        # Safe from clobber: we only reach here when the agent has NO other live terminal.
        session_id = str(row["session_id"] or "").strip()
        if session_id:
            await db.execute(
                "UPDATE agent_sessions SET terminal_id = ?, terminal_status = 'attached', "
                # Re-binding the resurrected live console is a "backing is running" event — also
                # promote a dead-state session denorm (same rule as the other bind sites), else
                # the Console label reads "Console stopped" over a live attached terminal.
                "status = CASE WHEN status IN ('stopped','ended','failed','lost','cancelled','completed') THEN 'running' ELSE status END, "
                "ended_at = CASE WHEN status IN ('stopped','ended','failed','lost','cancelled','completed') THEN NULL ELSE ended_at END, "
                "last_seen = ? WHERE id = ?",
                (terminal_id, now, session_id),
            )
        await _append_terminal_event(
            db,
            terminal_id,
            "reconciled_managed_console_resurrected",
            json.dumps({
                "agentId": agent_id,
                "reason": "ghost-reaped console is alive again (live channel-sidecar + fresh output); re-activated",
            }),
        )
        await _invalidate_agent_live_state(db, agent_id)
        healed += 1
    return healed


async def _close_idle_virtual_rpc_workers(db, *, idle_close_enabled: bool, idle_close_minutes: int, limit: int = 200) -> list[dict[str, str]]:
    """Auto-close managed worker terminals idle longer than configured."""
    # SEAM NORMALIZATION, v0.5 slice 5 (declared). Two keys, supplied by the caller from its pass
    # settings as required scalars — same keys, same defaults, same use. Narrow scalars rather than
    # the whole dict, which is the shape the reviewer preferred in slice 1a.
    minutes = int(idle_close_minutes or 0)
    if minutes <= 0 or not bool(idle_close_enabled):
        return []
    cursor = await db.execute(
        f"""
        SELECT
          t.id,
          t.agent_id,
          t.command,
          t.environment_id,
          t.bridge_id,
          s.id AS agent_session_id
        FROM terminal_sessions t
        LEFT JOIN agent_sessions s ON s.id = t.session_id
        LEFT JOIN agents a ON a.id = t.agent_id
        WHERE t.status IN ('starting', 'attached', 'running', 'recovering', 'active', 'idle')
          AND (
            t.command IN ({",".join("?" for _ in _virtual_rpc_command_set())})
            OR t.command LIKE '%-aify%'
            OR t.command LIKE 'opencode%'
          )
          AND (
            COALESCE(a.session_mode, '') = 'managed'
            OR COALESCE(s.owner_mode, '') = 'managed'
            OR COALESCE(s.mode, '') LIKE 'managed%'
          )
          AND datetime(t.updated_at) <= datetime('now', ?)
          AND NOT EXISTS (
            SELECT 1 FROM dispatch_runs r
            WHERE r.target_agent = t.agent_id
              AND (
                r.status IN ('queued', 'claimed', 'running')
                OR (r.status = 'delivered' AND COALESCE(r.require_reply, 0) = 1)
              )
          )
        ORDER BY t.updated_at ASC
        LIMIT ?
        """,
        (*_virtual_rpc_command_set(), f"-{minutes} minutes", limit),
    )
    rows = await cursor.fetchall()
    now = _now()
    closed: list[dict[str, str]] = []
    for row in rows:
        terminal_id = str(row["id"] or "").strip()
        owner_agent = str(row["agent_id"] or "").strip()
        command = str(row["command"] or "").strip()
        if not terminal_id:
            continue
        is_virtual_rpc = command in _virtual_rpc_command_set()
        has_bridge_owner = bool(str(row["environment_id"] or "").strip() and str(row["bridge_id"] or "").strip())
        next_status = "stopped" if is_virtual_rpc or not has_bridge_owner else "stopping"
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = ?,
                stopped_at = CASE WHEN ? = 'stopped' THEN COALESCE(stopped_at, ?) ELSE stopped_at END,
                updated_at = ?,
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ?
            """,
            (
                next_status,
                next_status,
                now,
                now,
                f"Auto-closed: idle longer than worker_idle_close_minutes={minutes}.",
                terminal_id,
            ),
        )
        if not is_virtual_rpc and has_bridge_owner:
            await _append_terminal_control(
                db,
                terminal_id=terminal_id,
                environment_id=str(row["environment_id"] or "").strip(),
                bridge_id=str(row["bridge_id"] or "").strip(),
                action="stop",
                requested_by="auto-close-idle-worker",
            )
        await _append_terminal_event(
            db,
            terminal_id,
            "managed_worker_auto_closed_idle",
            json.dumps({"agentId": owner_agent, "idleMinutes": minutes, "status": next_status}),
        )
        session_id = str(row["agent_session_id"] or "").strip()
        if session_id:
            await db.execute(
                """
                UPDATE agent_sessions
                SET terminal_status = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (next_status, now, session_id),
            )
        if owner_agent:
            agent_row = await (await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (owner_agent,))).fetchone()
            if agent_row:
                rs = _json_loads_or(agent_row["runtime_state"], {}) or {}
                changed = False
                if str(rs.get("virtualTerminalId") or "").strip() == terminal_id:
                    rs.pop("virtualTerminal", None)
                    rs.pop("virtualTerminalId", None)
                    changed = True
                if str(rs.get("terminalId") or "").strip() == terminal_id:
                    rs.pop("terminalId", None)
                    changed = True
                if changed:
                    await db.execute(
                        "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
                        (json.dumps(rs), now, owner_agent),
                    )
            await _invalidate_agent_live_state(db, owner_agent)
        closed.append({"terminalId": terminal_id, "agentId": owner_agent})
    return closed


async def _reconcile_stale_managed_terminals_for_resident_agents(db) -> int:
    """Service-start event-driven cleanup.

    When the service container restarts, any in-flight managed wrapper
    PTYs are dead (their bridge process died with the previous service).
    For agents that are currently registered as resident (operator's
    *-aify wrapper owns the terminal), the existing managed PTY rows
    must NOT be displayed as live consoles — the dashboard would show
    ghosts and users get confused.

    This sweep fires once at service startup (an event, not a timer).
    For each resident agent, mark any terminal_sessions in active
    states as stopped and clear the agent_sessions.terminal_id binding
    so the dashboard renders the resident-owned state cleanly.

    Returns the number of terminal_sessions that were reconciled.
    """
    cursor = await db.execute(
        """
        SELECT t.id AS terminal_id, t.agent_id
        FROM terminal_sessions t
        JOIN agents a ON a.id = t.agent_id
        WHERE a.session_mode = 'resident'
          AND t.status IN ('starting','attached','running','active','idle','recovering')
        """
    )
    rows = await cursor.fetchall()
    if not rows:
        return 0
    now = _now()
    for row in rows:
        terminal_id = row["terminal_id"]
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'stopped',
                stopped_at = ?,
                updated_at = ?,
                error = COALESCE(NULLIF(error, ''), 'reconciled_at_service_startup_resident_owns_agent')
            WHERE id = ?
            """,
            (now, now, terminal_id),
        )
        await _append_terminal_event(
            db,
            terminal_id,
            "reconciled_at_service_startup",
            json.dumps({
                "agentId": row["agent_id"],
                "reason": "agent is registered as resident; bridge-spawned managed PTY rows from before service-restart are dead",
            }),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET terminal_id = '',
                terminal_status = ''
            WHERE terminal_id = ?
            """,
            (terminal_id,),
        )
    return len(rows)
