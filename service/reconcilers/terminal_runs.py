"""Terminal-bound dispatch runs and terminal controls: closing what a dead PTY left open.

v0.5 slice 6, extracted from `service/routers/api_v2.py`. Dependency table measured with the
every-name scan before the move.

BORROWED FROM THE ROUTER rather than moved, on measured caller count as in slices 4 and 5:
`_append_dispatch_event`, `_current_agent_session_row`, the two idle-prompt
hint helpers, and the constants `_TERMINAL_END_STATUSES`, `_TERMINAL_ACTIVE_STATUSES` and
`STUCK_STOPPING_GRACE_SECONDS`. Each read through exactly one owner so no second copy can drift —
`_TERMINAL_END_STATUSES` in particular is the set whose duplication produced finding N7.

No settings seam in this slice: none of these six read settings, which the scan confirmed before the
move rather than after.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

from service.api_core.runtime import _normalize_runtime  # v0.5.1e: the leaf owner, not via the router
from service.api_core.events import _append_dispatch_event  # v0.5.1i: the leaf owner
from service.clock import now as _now
from service.clock import iso_to_epoch as _iso_to_epoch
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state

logger = logging.getLogger(__name__)



async def _current_agent_session_row(*a, **k):
    from service.control_plane import _current_agent_session_row as _i
    return await _i(*a, **k)



def _terminal_idle_prompt_hint(output: str) -> str:
    clean = _borrowed_ansi_re().sub("", str(output or ""))
    clean = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", clean)
    tail = clean[-3000:].strip()
    if not tail or _terminal_awaiting_input_hint(tail):
        return ""
    marker_positions = [
        tail.lower().rfind("bypass permissions"),
        tail.lower().rfind("for agents"),
        tail.rfind("❯"),
    ]
    marker_at = max(marker_positions)
    if marker_at < 0:
        return ""
    suffix = tail[marker_at:]
    if re.search(r"(calling|cogitat|honking|thinking|running|undulating|press\s+esc|esc\s+to\s+interrupt)", suffix, re.I):
        return ""
    return "Claude PTY returned to an idle prompt without an explicit reply."


def _terminal_pi_idle_prompt_hint(output: str) -> str:
    """Detect Pi (omp) idle input prompt at the tail of terminal output.

    The omp interactive prompt renders a two-line input box:

        ╭── π  > ⬢ GPT-5.5 · ◕ high > 📁 C:\\tmp > ◫ 49.1%/272K ⟲ > $... ▶──╮
        ╰─                                                                ─╯

    When this idle box appears at the tail of the buffer and there is no
    active-thinking indicator below, pi is sitting at the input prompt
    waiting for new input — meaning whatever turn was in flight is done.
    Used by _close_idle_pi_terminal_run_without_reply the same way claude's
    idle-prompt detection closes PTY-delivered runs whose interactive
    runtime returned to ready state without a structured reply event.
    """
    clean = _borrowed_ansi_re().sub("", str(output or ""))
    clean = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", clean)
    tail = clean[-3000:]
    if not tail:
        return ""
    # The bottom-border of the omp input box. Distinctive enough that
    # plain log content won't false-positive. Both upper and lower box
    # corners must be present near the tail to confirm idle state.
    has_top = ("▶──╮" in tail) or ("π" in tail and "⬢" in tail)
    has_bottom = "╰─" in tail and "─╯" in tail
    if not (has_top and has_bottom):
        return ""
    # Bail if a streaming-thinking marker appears AFTER the idle box —
    # would mean pi went back to thinking after a momentary prompt flash.
    last_box_idx = tail.rfind("╰─")
    suffix = tail[last_box_idx:]
    if re.search(r"(thinking|cogitating|streaming|honking|press\s+esc|esc\s+to\s+interrupt)", suffix, re.I):
        return ""
    return "Pi PTY returned to an idle prompt without an explicit reply."


def _terminal_awaiting_input_hint(*a, **k):
    from service.control_plane import _terminal_awaiting_input_hint as _i
    return _i(*a, **k)


def _borrowed_ansi_re():
    """BORROWED constant: one owner, never a copy (finding N7).

    Six code readers outside this module, including `service/terminal_diagnostics.py`'s own
    separate pattern — so this one stays router-owned. Measured with
    scripts/constant_readership.py, not guessed.
    """
    from service.control_plane import _ANSI_RE
    return _ANSI_RE


def _terminal_end_statuses():
    from service.control_plane import _TERMINAL_END_STATUSES
    return _TERMINAL_END_STATUSES


def _terminal_active_statuses():
    from service.control_plane import _TERMINAL_ACTIVE_STATUSES
    return _TERMINAL_ACTIVE_STATUSES


def _stuck_stopping_grace_seconds():
    from service.control_plane import STUCK_STOPPING_GRACE_SECONDS
    return STUCK_STOPPING_GRACE_SECONDS


async def _close_active_terminal_runs_for_terminal(db, terminal, terminal_status: str, *, now: Optional[str] = None, reason: str = "") -> int:
    if not terminal:
        return 0
    status = str(terminal_status or "").strip().lower()
    if status not in _terminal_end_statuses():
        return 0
    terminal_id = str(terminal["id"] or "")
    agent_id = str(terminal["agent_id"] or "")
    if not terminal_id or not agent_id:
        return 0
    now = now or _now()
    terminal_label = status or "ended"
    run_status = "cancelled" if status in {"stopped", "cancelled"} else "failed"
    summary = reason or f"Terminal {terminal_label} before an explicit reply was recorded."
    cursor = await db.execute(
        """
        SELECT id
        FROM dispatch_runs
        WHERE target_agent = ?
          AND dispatch_mode = 'terminal'
          AND status IN ('claimed', 'running')
        """,
        (agent_id,),
    )
    rows = await cursor.fetchall()
    run_ids = [str(row["id"] or "") for row in rows if str(row["id"] or "")]
    for run_id in run_ids:
        await db.execute(
            """
            UPDATE dispatch_runs
            SET status = ?,
                summary = CASE WHEN COALESCE(summary, '') = '' THEN ? ELSE summary END,
                error_text = CASE WHEN ? = 'failed' AND COALESCE(error_text, '') = '' THEN ? ELSE error_text END,
                finished_at = COALESCE(finished_at, ?)
            WHERE id = ?
              AND status IN ('claimed', 'running')
            """,
            (run_status, summary, run_status, summary, now, run_id),
        )
        await _append_dispatch_event(db, run_id, "terminal_closed", f"{summary} terminalId={terminal_id}")
    await _fail_pending_terminal_controls(db, terminal_id, handled_at=now, response_text=summary)
    if run_ids:
        await _invalidate_agent_live_state(db, agent_id)
    queued_ids: list[str] = []
    current_session = await _current_agent_session_row(db, agent_id)
    current_terminal_id = str((current_session["terminal_id"] if current_session and "terminal_id" in current_session.keys() else "") or "").strip()
    if current_terminal_id == terminal_id:
        queued_summary = reason or f"Terminal {terminal_label} before the channel bridge claimed the run."
        queued_cursor = await db.execute(
            """
            SELECT id
            FROM dispatch_runs
            WHERE target_agent = ?
              AND execution_mode = 'channel'
              AND status = 'queued'
              AND dispatch_mode != 'message_only'
            ORDER BY requested_at ASC
            """,
            (agent_id,),
        )
        queued_rows = await queued_cursor.fetchall()
        queued_ids = [str(row["id"] or "") for row in queued_rows if str(row["id"] or "")]
        for run_id in queued_ids:
            await db.execute(
                """
                UPDATE dispatch_runs
                SET status = 'failed',
                    summary = CASE WHEN COALESCE(summary, '') = '' THEN ? ELSE summary END,
                    error_text = CASE WHEN COALESCE(error_text, '') = '' THEN ? ELSE error_text END,
                    finished_at = COALESCE(finished_at, ?)
                WHERE id = ?
                  AND status = 'queued'
                """,
                (queued_summary, queued_summary, now, run_id),
            )
            await _append_dispatch_event(db, run_id, "terminal_closed", f"{queued_summary} terminalId={terminal_id}")
        if queued_ids:
            await _invalidate_agent_live_state(db, agent_id)
    return len(run_ids) + len(queued_ids)


async def _close_idle_claude_terminal_run_without_reply(db, row, *, quiet_seconds: int = 20) -> bool:
    if not row:
        return False
    if str(row["dispatch_mode"] or "").strip().lower() != "terminal":
        return False
    if str(row["result_message_id"] or "").strip():
        return False
    agent_id = str(row["target_agent"] or "").strip()
    if not agent_id:
        return False
    session = await _current_agent_session_row(db, agent_id)
    runtime = str(row["runtime"] or "").strip()
    if not runtime and session and "runtime" in session.keys():
        runtime = str(session["runtime"] or "").strip()
    if _normalize_runtime(runtime) != "claude-code":
        return False
    terminal_id = str((session["terminal_id"] if session and "terminal_id" in session.keys() else "") or "").strip()
    if not terminal_id:
        return False
    terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
    if not terminal:
        return False
    terminal_status = str(terminal["status"] or "").strip().lower()
    if terminal_status not in _terminal_active_statuses():
        return False
    hint = _terminal_idle_prompt_hint(terminal["output"] or "")
    if not hint:
        return False
    updated_epoch = _iso_to_epoch(str(terminal["updated_at"] or "").strip())
    run_epoch = max(
        _iso_to_epoch(row["started_at"] if "started_at" in row.keys() else ""),
        _iso_to_epoch(row["claimed_at"] if "claimed_at" in row.keys() else ""),
        _iso_to_epoch(row["requested_at"] if "requested_at" in row.keys() else ""),
    )
    if updated_epoch and run_epoch and updated_epoch < run_epoch:
        return False
    if updated_epoch and time.time() - updated_epoch < max(0, int(quiet_seconds or 0)):
        return False
    now = _now()
    await db.execute(
        """
        UPDATE dispatch_runs
        SET status = 'completed',
            summary = CASE WHEN COALESCE(summary, '') = '' THEN ? ELSE summary END,
            finished_at = COALESCE(finished_at, ?)
        WHERE id = ?
          AND status IN ('claimed', 'running')
          AND COALESCE(result_message_id, '') = ''
        """,
        (hint, now, row["id"]),
    )
    await _append_dispatch_event(db, row["id"], "terminal_idle_reconciled", f"{hint} terminalId={terminal_id}")
    await _invalidate_agent_live_state(db, agent_id)
    return True


# Hot-read-path cap on the per-poll live-state re-derive burst. The re-derive is now an
# in-memory recompute (no DB writes — see _LIVE_STATE_CACHE), so this only bounds CPU per poll
# (each recompute does a handful of SELECTs). Most polls have few expired entries, so it rarely
# bites; right after a restart it caps how many of the ~28 agents recompute per poll, the rest
# caught by the next poll + the reconcile sweep.


async def _close_idle_pi_terminal_run_without_reply(db, row, *, quiet_seconds: int = 20) -> bool:
    """Pi analog of _close_idle_claude_terminal_run_without_reply.

    Pi's interactive omp wrapper does not emit a structured turn-end
    event when running under managed_terminal_backing. Without this
    detector, PTY-delivered runs to pi sit status='running' forever
    while pi is actually idle. The reconcile sweep (startup + periodic)
    calls this on each active run; when the pi terminal output shows
    the idle input box and the buffer has been quiet for quiet_seconds,
    the run is closed as completed.
    """
    if not row:
        return False
    if str(row["dispatch_mode"] or "").strip().lower() != "terminal":
        return False
    if str(row["result_message_id"] or "").strip():
        return False
    agent_id = str(row["target_agent"] or "").strip()
    if not agent_id:
        return False
    session = await _current_agent_session_row(db, agent_id)
    runtime = str(row["runtime"] or "").strip()
    if not runtime and session and "runtime" in session.keys():
        runtime = str(session["runtime"] or "").strip()
    if _normalize_runtime(runtime) != "pi":
        return False
    terminal_id = str((session["terminal_id"] if session and "terminal_id" in session.keys() else "") or "").strip()
    if not terminal_id:
        return False
    terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
    if not terminal:
        return False
    terminal_status = str(terminal["status"] or "").strip().lower()
    if terminal_status not in _terminal_active_statuses():
        return False
    hint = _terminal_pi_idle_prompt_hint(terminal["output"] or "")
    if not hint:
        return False
    updated_epoch = _iso_to_epoch(str(terminal["updated_at"] or "").strip())
    run_epoch = max(
        _iso_to_epoch(row["started_at"] if "started_at" in row.keys() else ""),
        _iso_to_epoch(row["claimed_at"] if "claimed_at" in row.keys() else ""),
        _iso_to_epoch(row["requested_at"] if "requested_at" in row.keys() else ""),
    )
    if updated_epoch and run_epoch and updated_epoch < run_epoch:
        return False
    if updated_epoch and time.time() - updated_epoch < max(0, int(quiet_seconds or 0)):
        return False
    now = _now()
    summary = hint
    await db.execute(
        """
        UPDATE dispatch_runs
        SET status = 'completed',
            summary = CASE WHEN COALESCE(summary, '') = '' THEN ? ELSE summary END,
            finished_at = COALESCE(finished_at, ?)
        WHERE id = ?
          AND status IN ('claimed', 'running')
          AND COALESCE(result_message_id, '') = ''
        """,
        (summary, now, row["id"]),
    )
    await _append_dispatch_event(db, row["id"], "terminal_closed", f"{summary} terminalId={terminal_id}")
    await _fail_pending_terminal_controls(db, terminal_id, handled_at=now, response_text=summary)
    await _invalidate_agent_live_state(db, agent_id)
    return True


async def _fail_pending_terminal_controls(
    db,
    terminal_id: str,
    *,
    handled_at: str,
    response_text: str,
    exclude_actions: tuple[str, ...] = (),
) -> int:
    """Fail this terminal's outstanding controls. `exclude_actions` spares specific actions.

    The exclusion exists for the liveness sweep, which must not cancel a queued `stop` (see
    _reconcile_ended_terminal_controls). It is NOT the default: the terminal-CLOSED callers below
    are right to fail everything, because once the process is genuinely gone a pending stop is moot.
    Needed as a parameter rather than relying on the caller's outer WHERE, since a terminal with
    BOTH an input and a stop outstanding is still selected by that query, and this helper would
    otherwise fail every pending row for it — taking the stop down with the input.
    """
    params: list[Any] = [terminal_id]
    exclusion_sql = ""
    normalized_exclusions = tuple(str(a or "").strip().lower() for a in exclude_actions if str(a or "").strip())
    if normalized_exclusions:
        placeholders = ", ".join("?" * len(normalized_exclusions))
        exclusion_sql = f" AND LOWER(COALESCE(action, '')) NOT IN ({placeholders})"
        params.extend(normalized_exclusions)
    cursor = await db.execute(
        f"""
        SELECT id
        FROM terminal_controls
        WHERE terminal_id = ?
          AND status IN ('pending', 'claimed')
          {exclusion_sql}
        """,
        tuple(params),
    )
    rows = await cursor.fetchall()
    control_ids = [str(row["id"] or "") for row in rows if str(row["id"] or "")]
    if not control_ids:
        return 0
    await db.executemany(
        """
        UPDATE terminal_controls
        SET status = 'failed',
            handled_at = COALESCE(handled_at, ?),
            error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
        WHERE id = ?
        """,
        [(handled_at, response_text, control_id) for control_id in control_ids],
    )
    return len(control_ids)


async def _reconcile_ended_terminal_controls(db, *, limit: int = 500) -> int:
    """Fail controls nobody will ever run, so a caller is not left waiting on a dead terminal.

    A `stop` is EXEMPT (review finding on `35cc646`, a regression). `stop_agent_worker` marks the
    terminal `'stopping'` — correct, the host has not acknowledged — and queues the stop control in
    the SAME transaction. `'stopping'` is not in the active set below, and this sweep runs on a timer
    while the bridge polls every ~3s, so whenever the sweep won the race it cancelled the very stop
    that was supposed to kill the process. The PTY then survived a "successful" Stop worker, and
    900s later the stuck-stopping reaper wrote `'stopped'` over it — a row asserting a death that
    never happened. Strictly worse than the state lie it replaced, because the process lived.

    The pre-existing VIRTUAL path had the same exposure for a different reason: it marks `'stopped'`
    and queues its stop together, and `'stopped'` is not in the active set either. So the fix is not
    "add 'stopping' to the set" — it is that a stop must never be cancelled on liveness grounds.
    Killing a process is idempotent and stays desirable on a dead-looking row; server.js carries an
    orphan-pid fallback for exactly the case where no bridge owns the PTY in memory any more.

    Everything else still fails fast, which is the whole point of this reconcile — keystrokes into a
    console that is gone cannot be honoured, and the caller should learn that instead of hanging.
    """
    cursor = await db.execute(
        """
        SELECT DISTINCT terminal.id
        FROM terminal_sessions terminal
        JOIN terminal_controls control ON control.terminal_id = terminal.id
        WHERE terminal.status NOT IN ('starting', 'attached', 'running', 'active', 'idle')
          AND control.status IN ('pending', 'claimed')
          AND LOWER(COALESCE(control.action, '')) != 'stop'
        LIMIT ?
        """,
        (max(1, int(limit or 500)),),
    )
    total = 0
    now = _now()
    for row in await cursor.fetchall():
        total += await _fail_pending_terminal_controls(
            db,
            str(row["id"] or ""),
            handled_at=now,
            response_text="terminal is not active",
            exclude_actions=("stop",),
        )
    return total


async def _reconcile_stuck_terminal_and_session_rows(db) -> dict[str, int]:
    """Self-heal two stuck-row patterns the other reapers miss (2026-06-18 fleet audit).

    1. terminal_sessions wedged in the TRANSITIONAL 'stopping' state: a stop was
       requested but the owning bridge died / never PATCHed the row to 'stopped'
       (observed: a PTY stuck 'stopping' for 17 days). The managed-worker-hygiene
       reaper only scans active states (attached/running/...), NOT 'stopping', so
       it never catches these. After a grace window, force 'stopped' so the
       dashboard stops rendering a phantom "stopping" console.
    2. agent_sessions marked status='ended' but with ended_at STILL NULL: any
       "live session" query keys on `ended_at IS NULL`, so such a row reads as
       active forever despite being ended (observed: a 3-week-old ghost). Backfill
       ended_at from last_seen.

    Both idempotent, both DB-only. Returns counts for the reconcile summary.
    """
    now = _now()
    result = {"stuck_stopping_terminals_closed": 0, "ended_sessions_backfilled": 0}
    cur = await db.execute(
        "UPDATE terminal_sessions SET status = 'stopped', stopped_at = COALESCE(stopped_at, ?) "
        "WHERE status = 'stopping' AND datetime(updated_at) < datetime('now', ? || ' seconds')",
        (now, f"-{_stuck_stopping_grace_seconds()}"),
    )
    result["stuck_stopping_terminals_closed"] = cur.rowcount or 0
    cur = await db.execute(
        "UPDATE agent_sessions SET ended_at = COALESCE(ended_at, last_seen, ?) "
        "WHERE status = 'ended' AND ended_at IS NULL",
        (now,),
    )
    result["ended_sessions_backfilled"] = cur.rowcount or 0
    return result
