"""Appending output to a terminal row: trim the stored tail, feed the live screen, write the row.

WHY THIS IS A LEAF NOW. `TerminalOutputWriteQueue` in the control plane calls
`_append_terminal_output`, which lived in `routers/terminals.py` — so the queue depended UPWARD on a
router and could not be extracted at all. That is the reason this slice comes before the queue's:
the queue is not blocked by its own size, it is blocked by the direction of this one call. Moving a
5,000-line carrier is not a sequence of big moves, it is a sequence of small ones in dependency order.

DB ACCESS: `db` is passed in. No connection is opened, no commit, no rollback — the caller owns the
transaction, which is what makes this movable at all.

LOGGER: this module logs under its own name, so the one best-effort `logger.debug` for a failed live
screen feed now appears on `aify_comms.api_core.terminal_output` instead of
`aify_comms.routers.terminals`. No functional change; an observable one, recorded rather than
smuggled.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from service.api_core.events import _append_terminal_event
from service.api_core.terminal_status import _terminal_status_transition
from service.clock import now as _now
from service.terminal_snapshot import feed_live_screen as _feed_live_terminal_screen

logger = logging.getLogger("aify_comms.api_core.terminal_output")


def _trim_terminal_output(text: str, max_chars: int = 65536) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    tail = value[-max_chars:]
    # Start the kept tail at a clean LINE boundary (2026-06-07). A raw char-count slice
    # routinely cuts mid-line or — worse — mid-ANSI-escape-sequence, so when the dashboard
    # seeds a FRESH xterm with this buffer the leading bytes are a broken escape that xterm
    # misparses into on-screen garbage (part of the "glitchy console" report). Dropping at
    # most the first partial line makes the seed parse cleanly. If the whole window is one
    # huge line (no newline), fall back to the raw tail rather than return empty.
    newline = tail.find("\n")
    if 0 <= newline < len(tail) - 1:
        return tail[newline + 1:]
    return tail


async def _append_terminal_output(db, terminal, output: str, *, status: str = "", seq: Optional[int] = None):
    chunk = str(output or "")
    if not chunk and not status:
        return
    current = terminal["output"] if "output" in terminal.keys() else ""
    next_output = _trim_terminal_output(f"{current or ''}{chunk}")

    # LIVE SCREEN (2026-07-14). Feed this chunk into the terminal's persistent screen, the way a
    # real terminal consumes bytes. The console renders from THAT, not from a replay of the
    # stored log — because the stored log is a 64KB TAIL and claude never clears the screen (zero
    # ESC[2J fleet-wide; it paints one line per frame), so replaying a suffix into a blank screen
    # can only ever rebuild part of it. That is the scrambled / half-empty / "old state stuck on
    # screen" console, and why Refresh did not help: it re-rendered the same broken buffer.
    # Best-effort and non-fatal: on any failure the screen is dropped and the reader falls back to
    # the replay path, i.e. exactly today's behaviour. Never worse.
    if chunk:
        try:
            keys = terminal.keys()
            _feed_live_terminal_screen(
                str(terminal["id"]),
                chunk,
                cols=(terminal["cols"] if "cols" in keys else 0),
                rows=(terminal["rows"] if "rows" in keys else 0),
                seed=str(current or ""),  # only used when the screen does not exist yet
            )
        except Exception:
            logger.debug("live screen feed failed for terminal=%s", terminal["id"], exc_info=True)
    updates = ["output = ?", "updated_at = ?"]
    params: list[Any] = [next_output, _now()]
    if seq is not None:
        updates.append("output_seq = ?")
        params.append(int(seq))
    next_status = _terminal_status_transition(terminal["status"] if "status" in terminal.keys() else "", status)
    if next_status:
        updates.append("status = ?")
        params.append(next_status)
        if next_status in {"stopped", "failed"}:
            updates.append("stopped_at = COALESCE(stopped_at, ?)")
            params.append(_now())
    params.append(terminal["id"])
    await db.execute(
        f"UPDATE terminal_sessions SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )
    if chunk:
        await _append_terminal_event(db, terminal["id"], "terminal_output", chunk[-2000:])
