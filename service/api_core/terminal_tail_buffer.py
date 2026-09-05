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


def current_seq(terminal_id: str, stored: Optional[int]) -> int:
    """The seq DESCRIBING what `current_tail` would return for this terminal.

    THE HALF THAT WAS MISSING, and its absence was a repaint storm on every busy console. The read
    path served the live screen as `snapshot` while taking `outputSeq` from the ROW, which the lazy
    tail only writes once a second. `xterm-mount.mjs` seeds `lastSeq` from that value and
    `realtime-socket.mjs` resyncs on `seq > lastSeq + 1` -- STRICT CONTIGUITY, not just monotonicity
    -- so a seq even one frame behind made the very next live frame look like a gap. The resync
    re-fetched, got the same stale seq, and the console reset() and fully rewrote itself at frame
    rate until the terminal fell quiet for a flush interval.

    `_append_terminal_output`'s comment argues that `output` and `output_seq` must lag TOGETHER, and
    that is still true of the ROW. What it missed is that the client is not seeded from the row: the
    snapshot comes from the live screen, which every chunk feeds. Pairing has to hold across what is
    actually SERVED, so the read path now answers with the live tail and the live seq or with the
    row's pair, never one of each.

    Falls back to `stored` for a terminal this process holds nothing for -- every terminal after a
    restart, which is the case the column exists for.
    """
    held = _BUFFERS.get(str(terminal_id or ""))
    if held and held.get("seq") is not None:
        return int(held["seq"])
    return int(stored or 0)


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
    """What is held and not yet written, or None.

    THE SETTLE WRITE'S SEAM. This existed with no caller for two days, and its absence was the
    regression: a terminal that stops producing output has a held tail that the next chunk was
    supposed to write, and there is no next chunk. `terminal_write_queue` now asks this after every
    flush and schedules one write when the answer is not None.
    """
    held = _BUFFERS.get(str(terminal_id or ""))
    if not held or not held["dirty"]:
        return None
    return {"tail": held["tail"], "seq": held["seq"]}


def snapshot(terminal_id: str) -> Optional[dict]:
    """A copy of everything held for this terminal, for restoring after a failed write.

    R9-M2, external review 2026-09-06. `_append_terminal_output` folds the chunk into the held tail,
    marks it flushed and may `forget()` it, and only THEN runs the UPDATE. When that UPDATE throws --
    the `database is locked` family past the 5s busy timeout is the live trigger -- the write queue
    requeues the SAME chunk at the front. By then `current_tail` already includes it, so the retry
    appends it a SECOND time, and `record()` answers False because the interval was just marked, so
    the retry writes nothing at all. Duplicated bytes in the stored tail, and no write to carry them.

    On the ending path the same failure loses the buffer outright: `forget()` has already run, so the
    final screen of a worker that died -- the one an operator reads to find out why -- is gone.

    RESTORING BEATS REORDERING. Moving every mutation after the UPDATE would put the live-screen feed
    after `_answer_console_prompt`, which reads that screen, and would split `record`'s hold from its
    due-decision on the hottest path in the service. A snapshot and a rollback touch one call site.
    """
    held = _BUFFERS.get(str(terminal_id or ""))
    return dict(held) if held else None


def restore(terminal_id: str, snap: Optional[dict]) -> None:
    """Put a terminal's buffer back exactly as `snapshot` found it, including having held nothing."""
    key = str(terminal_id or "")
    if not key:
        return
    if snap is None:
        _BUFFERS.pop(key, None)
    else:
        _BUFFERS[key] = dict(snap)


def mark_flushed(terminal_id: str, *, now: Optional[float] = None) -> None:
    held = _BUFFERS.get(str(terminal_id or ""))
    if held:
        held["flushed_at"] = time.monotonic() if now is None else now
        held["dirty"] = False


def forget(terminal_id: str) -> None:
    """Drop a terminal's buffer. Called when it ends, so a dead terminal holds no memory."""
    _BUFFERS.pop(str(terminal_id or ""), None)


def held_ids() -> set[str]:
    """Every terminal this process is holding a tail for.

    FOR STATE-BASED CLEANUP, which is the only kind that can work here (R9-M4, external review
    2026-09-06). `forget()` has exactly ONE caller -- the ending branch of `_append_terminal_output`
    -- while 108 places in the service write a terminal status. Every terminal ended by a reaper, a
    supersede, a lifecycle stop or a session teardown therefore left its 64 KB held for the life of
    the process.

    Calling `forget()` at each of those sites is the fix that rots: it holds until somebody adds the
    109th. DECISIONS.md already carries the rule this follows -- cleanup that must hold for ALL paths
    keys on the STATE, not on an event -- so the sweep asks which terminals are still active and
    releases the rest.
    """
    return set(_BUFFERS.keys())


def reset_for_tests() -> None:
    """A process-global needs an explicit reset, or one test's terminal leaks into the next."""
    _BUFFERS.clear()
    set_flush_interval_for_tests(None)


def held_count() -> int:
    """How many terminals are buffered. One 64 KB tail each is the memory this costs."""
    return len(_BUFFERS)
