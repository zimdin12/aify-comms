"""What a completed terminal control implies about the terminal itself.

Extracted from `update_terminal_control` in `service/routers/terminals.py` in v0.5.4;
`test_update_terminal_control_split_is_inert.py` inlines it back and AST-compares against the
pre-split fixture. The body is at its original 8-space column so the SQL literals are preserved
byte-for-byte.

THE BRIDGE REPORTS ON THE CONTROL; THIS DECIDES WHAT THAT MEANS FOR THE TERMINAL. Three sources, in
order: an explicit `terminalStatus` from the bridge wins; a FAILED control implies `failed`; a
completed STOP implies `stopped`. Anything else leaves the terminal alone, which is why a resize or
an input control does not touch its status.

AN END STATUS IS FIVE WRITES, NOT ONE, and dropping any of them leaves a different lie behind: close
the runs that will now never get a reply, stamp the terminal row, mirror the status onto the session
(returning ownership to `managed`), clear the console binding, and invalidate the live-status cache
so the agent stops reading as online with a dead terminal.

THE END-STATUS SET IS IMPORTED, not passed in. It has exactly one owner in
`service/api_core/terminal_status.py`, so importing it cannot fork it. Its sibling
`terminal_output_settlement.py` receives the same set as a PARAMETER instead -- that is not an
inconsistency to fix by making them match: the parameter there is what lets its own inline-back
proof close, and both read the one owner.
"""
from __future__ import annotations

from service.api_core.terminal_controls_io import _clear_console_terminal_binding
from service.api_core.terminal_status import _TERMINAL_END_STATUSES
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
from service.reconcilers.terminal_runs import _close_active_terminal_runs_for_terminal


async def _apply_terminal_status_from_control(db, req, control, terminal, status, now):
        """Derive the terminal status this control implies, apply it, and hand it back.

        Every argument is passed under the caller's own name: the extract-method gate splices this
        body back over its call without substituting arguments, so it refuses a call whose argument
        name differs from the parameter it fills.
        """
        terminal_status = str(req.terminalStatus or "").strip()
        if status == "failed":
            terminal_status = terminal_status or "failed"
        if control["action"] == "stop" and status == "completed":
            terminal_status = terminal_status or "stopped"
        if terminal_status:
            terminal_status_norm = terminal_status.strip().lower()
            if terminal_status_norm in _TERMINAL_END_STATUSES:
                await _close_active_terminal_runs_for_terminal(
                    db,
                    terminal,
                    terminal_status_norm,
                    now=now,
                    reason=f"Terminal {terminal_status_norm} before an explicit reply was recorded.",
                )
            await db.execute(
                """
                UPDATE terminal_sessions
                SET status = ?, updated_at = ?, stopped_at = CASE WHEN ? IN ('stopped','failed') THEN COALESCE(stopped_at, ?) ELSE stopped_at END,
                    error = CASE WHEN ? = 'failed' THEN ? ELSE error END
                WHERE id = ?
                """,
                # NORMALISED, because the statement itself compares against lowercase literals and so
                # does every reader. `terminal_status` is stripped but not lowered; the normalised
                # twin two lines up was built for the end-status membership check and then not used
                # for the writes. A `terminalStatus` of "Stopped" would be stored verbatim, fail the
                # `? IN ('stopped','failed')` CASE so `stopped_at` is never stamped, and then match
                # no reaper -- every one selects on the lowercase members. Same defect as the
                # dispatch-run status, one path over, and here with four consequences instead of one.
                (terminal_status_norm, now, terminal_status_norm, now, status, req.error or "", terminal["id"]),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET terminal_status = ?,
                    owner_mode = CASE WHEN ? IN ('stopped','failed') THEN 'managed' ELSE owner_mode END,
                    last_seen = ?
                WHERE id = ?
                """,
                # Same normalisation, and the second binding is why it matters here: `owner_mode`
                # only returns to 'managed' when this CASE matches, so a mixed-case stop left the
                # session owned by a console that has gone.
                (terminal_status_norm, terminal_status_norm, now, terminal["session_id"]),
            )
        if terminal_status in {"stopped", "failed"}:
            await _clear_console_terminal_binding(db, terminal["agent_id"], terminal["id"], now=now)
        if terminal_status.strip().lower() in _TERMINAL_END_STATUSES:
            await _invalidate_agent_live_state(db, terminal["agent_id"])
        return terminal_status
