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

import json
import logging
import re
import time
from typing import Optional

from service.api_core.runtime import _normalize_runtime  # v0.5.1e: the leaf owner, not via the router
from service.api_core.events import _append_dispatch_event  # v0.5.1i: the leaf owner
from service.api_core.events import _append_terminal_event
from service.api_core.agent_sessions import _current_agent_session_row
from service.api_core.terminal_text import _ANSI_RE, _CTRL_RE, _terminal_awaiting_input_hint
from service.clock import now as _now
from service.terminal_diagnostics import without_reply_claim
from service.clock import iso_to_epoch as _iso_to_epoch
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
from service.reconcilers.terminal_controls import _fail_pending_terminal_controls
from service.api_core.terminal_status import _TERMINAL_ACTIVE_STATUSES, _TERMINAL_END_STATUSES
from service.api_core.tuning import STUCK_STOPPING_GRACE_SECONDS

logger = logging.getLogger(__name__)






def _terminal_idle_prompt_hint(output: str) -> str:
    clean = _ANSI_RE.sub("", str(output or ""))
    clean = _CTRL_RE.sub("", clean)
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
    clean = _ANSI_RE.sub("", str(output or ""))
    clean = _CTRL_RE.sub("", clean)
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



async def _close_active_terminal_runs_for_terminal(db, terminal, terminal_status: str, *, now: Optional[str] = None, reason: str = "") -> int:
    if not terminal:
        return 0
    status = str(terminal_status or "").strip().lower()
    if status not in _TERMINAL_END_STATUSES:
        return 0
    terminal_id = str(terminal["id"] or "")
    agent_id = str(terminal["agent_id"] or "")
    if not terminal_id or not agent_id:
        return 0
    now = now or _now()
    terminal_label = status or "ended"
    run_status = "cancelled" if status in {"stopped", "cancelled"} else "failed"
    base_summary = reason or f"Terminal {terminal_label} before an explicit reply was recorded."
    # THE STORED COLUMN IS ALREADY THE ANSWER, and re-deriving it was my mistake. `require_reply` is
    # written by `_dispatch_requires_reply(req.requireReply, default=_message_type_expects_reply(...))`
    # at creation, so the TYPE supplies a default only when the caller said nothing and an explicit
    # `requireReply=false` survives. `reply_expectation.py` states it outright: collapsing the two
    # "would lose the difference between 'did not ask' and 'asked for false'".
    #
    # A version of this re-derived from type and priority, which turned every explicit opt-out on a
    # request back into an obligation -- exactly what that docstring forbids. It looked right because
    # `test_a_terminal_records_how_it_ended` seeds a run by direct INSERT and gets the SCHEMA defaults
    # (require_reply 0, message_type 'request'), which is not a row the product would ever write.
    # This closes EVERY claimed or running terminal run for the agent, which is right -- the terminal
    # died and none of them finished -- but 5 of the 16 runs carrying that sentence on the live database
    # had `require_reply = 0`, so they were told a reply was missing that nobody ever asked for.
    cursor = await db.execute(
        """
        SELECT id, require_reply
        FROM dispatch_runs
        WHERE target_agent = ?
          AND dispatch_mode = 'terminal'
          AND status IN ('claimed', 'running')
        """,
        (agent_id,),
    )
    rows = await cursor.fetchall()
    owed_reply = {
        str(row["id"] or ""): bool(row["require_reply"])
        for row in rows if str(row["id"] or "")
    }
    run_ids = [str(row["id"] or "") for row in rows if str(row["id"] or "")]
    for run_id in run_ids:
        summary = base_summary if owed_reply.get(run_id, True) else without_reply_claim(base_summary)
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
    await _fail_pending_terminal_controls(db, terminal_id, handled_at=now, response_text=base_summary)
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
    if terminal_status not in _TERMINAL_ACTIVE_STATUSES:
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
    if terminal_status not in _TERMINAL_ACTIVE_STATUSES:
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
    # SELECTED FIRST so each closure can say who closed it. This was one set-based UPDATE, which
    # made it the only path in the service that can move SEVERAL terminals to `stopped` in a single
    # statement -- every one of them stamped with the same `stopped_at`, and none of them recording
    # anything but a count in the reconcile summary.
    #
    # That combination is what makes a simultaneous multi-terminal stop unattributable from the
    # outside: an operator sees two consoles die in the same second and there is nothing to read.
    # Every other function that stops or fails a terminal already appends an event; this was the one
    # that did not. Two extra statements per sweep, and only when there is something to close.
    stuck = await (await db.execute(
        "SELECT id FROM terminal_sessions "
        "WHERE status = 'stopping' AND datetime(updated_at) < datetime('now', ? || ' seconds')",
        (f"-{STUCK_STOPPING_GRACE_SECONDS}",),
    )).fetchall()
    stuck_ids = [str(row["id"]) for row in stuck if str(row["id"] or "").strip()]
    if stuck_ids:
        placeholders = ",".join("?" for _ in stuck_ids)
        reason = (
            "Closed by the stuck-stopping reconciler: a stop was requested and never confirmed "
            f"within {STUCK_STOPPING_GRACE_SECONDS}s, so the row was forced to stopped."
        )
        cur = await db.execute(
            f"UPDATE terminal_sessions SET status = 'stopped', stopped_at = COALESCE(stopped_at, ?), "
            f"error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END "
            f"WHERE id IN ({placeholders})",
            (now, reason, *stuck_ids),
        )
        result["stuck_stopping_terminals_closed"] = cur.rowcount or 0
        for terminal_id in stuck_ids:
            await _append_terminal_event(
                db, terminal_id, "terminal_stuck_stopping_closed", json.dumps({"reason": reason}),
            )
    cur = await db.execute(
        "UPDATE agent_sessions SET ended_at = COALESCE(ended_at, last_seen, ?) "
        "WHERE status = 'ended' AND ended_at IS NULL",
        (now,),
    )
    result["ended_sessions_backfilled"] = cur.rowcount or 0
    return result


# _fail_pending_terminal_controls and _reconcile_ended_terminal_controls moved to
# service/reconcilers/terminal_controls.py in v0.5.4 — they reconcile terminal_controls, not
# terminal RUNS, and the second calls the first, so the pair travelled together.
