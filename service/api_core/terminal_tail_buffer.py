"""The stored terminal tail, held in memory and flushed on a slower cadence than the stream.

WHY, MEASURED 2026-09-04 rather than assumed. `_append_terminal_output` is a read-modify-write of the
whole `terminal_sessions.output` column, which sits at its 64 KB cap for any agent that has been
running a while. A mid-turn claude agent repainting its spinner ten times a second produces 440
chars/s of actual output and caused **640 KB/s of writes** -- 1490x amplification, per agent. The
write queue's coalescing catches BURSTS and does nothing for a steady drip, which is exactly what a
spinner is: `enqueue` cancels and reschedules a 4 ms idle flush per chunk.

WHAT MAKES IT SAFE TO SLOW DOWN. Nothing reads the stored tail on the status path any more -- both
readers take the live screen and fall back here only for a terminal this process has not seen since
it started. So the column is a RESTART FALLBACK, and a copy that lags the stream by a second costs at
most a second of scrollback if the service is killed abruptly. That is what the operator asked for:
"just as rolling memory of what was on screen. but we depend on it less thx to lazy right."

`output` AND `output_seq` LAG TOGETHER, AND THAT IS THE WHOLE DESIGN. My first note on this said only
the `output` column may lag; reading the dashboard proved that wrong and dangerous. `xterm-mount.mjs`
seeds `lastSeq` from the row's `outputSeq`, and `realtime-socket.mjs` drops any live frame with
`seq <= lastSeq`. So a seq written EAGERLY beside a lagging output tells the client it already has
content it has never seen, and the frames that would fill the gap are dropped -- missing output, a
desynchronised ANSI stream, and the scrambled console that is complaint B3. Lagging them together
keeps the row self-consistent: the client seeds an older screen and receives exactly the frames after
it.

WHAT NEVER LAGS: `status`, `updated_at` and `stopped_at`. `updated_at` is the freshness a reporting
host is recognised by -- `_active_terminal_for_agent` keys ownership on it -- and `status` is what
every liveness rule reads. Those stay on the original write.

THE BUFFER IS THE TRUTH BETWEEN FLUSHES. A skipped write means the row is stale, so the next append
must accumulate onto what is HELD here rather than re-reading the row -- otherwise every skipped chunk
would be dropped. That is also why both writers share this module rather than each keeping their own:
`append_outside_the_queue` and the queue's own flush are two paths to one column, which is the defect
D13 fixed and this must not reintroduce.
"""

from __future__ import annotations

import time
from typing import Optional

#: How long the database copy may lag the stream. One second bounds the loss on an abrupt kill to
#: about a second of scrollback, and takes the measured 640 KB/s per agent down to roughly 64 KB/s.
#: Not a tuning knob to raise casually: it IS the durability window.
TAIL_FLUSH_INTERVAL_SECONDS = 1.0

#: Overridden by tests that need every write to be durable immediately -- D13's two-writer proof, for
#: one, whose property is about bytes SURVIVING and not about when they land. Set to 0.0 there so the
#: assertion reads the column rather than the buffer. Production never touches it.
_interval_override: float | None = None


def set_flush_interval_for_tests(seconds: float | None) -> None:
    """A process-global cadence needs an explicit override, and an explicit way back."""
    global _interval_override
    _interval_override = seconds


def _interval() -> float:
    return TAIL_FLUSH_INTERVAL_SECONDS if _interval_override is None else _interval_override

#: terminal_id -> {"tail": str, "seq": int, "flushed_at": float, "dirty": bool}
_BUFFERS: dict[str, dict] = {}


def current_tail(terminal_id: str, stored: str) -> str:
    """The true tail for this terminal: what is held here, or the row when nothing is held.

    `stored` is the row's value and is the right answer for a terminal this process has not written
    yet -- including every terminal after a restart, which is the case the column exists for.
    """
    held = _BUFFERS.get(str(terminal_id or ""))
    return held["tail"] if held else str(stored or "")


def record(terminal_id: str, tail: str, seq: Optional[int], *, now: Optional[float] = None) -> bool:
    """Hold `tail` for this terminal and answer whether it should be WRITTEN now.

    True when nothing has been flushed yet (so a brand-new terminal reaches the database at once,
    and a console opened immediately after a spawn is not blank) or when the interval has elapsed.
    """
    key = str(terminal_id or "")
    if not key:
        return True
    at = time.monotonic() if now is None else now
    held = _BUFFERS.get(key)
    if held is None:
        _BUFFERS[key] = {"tail": str(tail or ""), "seq": seq, "flushed_at": at, "dirty": False}
        return True
    held["tail"] = str(tail or "")
    if seq is not None:
        held["seq"] = seq
    if at - held["flushed_at"] >= _interval():
        held["flushed_at"] = at
        held["dirty"] = False
        return True
    held["dirty"] = True
    return False


def pending(terminal_id: str) -> Optional[dict]:
    """What is held and not yet written, or None. Used to flush on the way out."""
    held = _BUFFERS.get(str(terminal_id or ""))
    if not held or not held["dirty"]:
        return None
    return {"tail": held["tail"], "seq": held["seq"]}


def mark_flushed(terminal_id: str, *, now: Optional[float] = None) -> None:
    held = _BUFFERS.get(str(terminal_id or ""))
    if held:
        held["flushed_at"] = time.monotonic() if now is None else now
        held["dirty"] = False


def forget(terminal_id: str) -> None:
    """Drop a terminal's buffer. Called when it ends, so a dead terminal holds no memory."""
    _BUFFERS.pop(str(terminal_id or ""), None)


def reset_for_tests() -> None:
    """A process-global needs an explicit reset, or one test's terminal leaks into the next."""
    _BUFFERS.clear()
    set_flush_interval_for_tests(None)


def held_count() -> int:
    """How many terminals are buffered. One 64 KB tail each is the memory this costs."""
    return len(_BUFFERS)
