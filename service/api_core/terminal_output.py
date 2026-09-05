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

from service.api_core.console_prompts import (
    answer_for_screen,
    needs_resume_policy,
    should_answer,
)
from service.api_core.events import _append_terminal_control, _append_terminal_event
from service.api_core.terminal_status import _TERMINAL_END_STATUSES, _terminal_status_transition
from service.clock import now as _now
from service.api_core.terminal_tail_buffer import (
    pending,
    current_tail,
    forget,
    mark_flushed,
    record,
)
from service.api_core.serialization import _json_loads_or
from service.terminal_snapshot import feed_live_screen as _feed_live_terminal_screen
from service.terminal_snapshot import render_live_screen

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


async def _append_terminal_output(
    db, terminal, output: str, *, status: str = "", seq: Optional[int] = None, settle: bool = False,
):
    chunk = str(output or "")
    if not chunk and not status and not settle:
        return
    # A SETTLE IS A WRITE WITH NO NEW BYTES, for a terminal that has gone quiet. The held tail was
    # going to be written by the next chunk and no next chunk is coming; `current_tail` below
    # returns it, so nothing here needs special-casing except knowing to proceed.
    #
    # NOTHING HELD MEANS NOTHING TO DO. Output that arrived after the settle was scheduled has
    # already written it, which is the common case on a busy terminal.
    if settle and pending(str(terminal["id"])) is None:
        return
    # THE BUFFER IS THE TRUTH BETWEEN FLUSHES. The tail is written on a slower cadence than the
    # stream (see `terminal_tail_buffer`), so the ROW is stale whenever a write was skipped --
    # accumulating onto it would drop every chunk since the last flush. `current_tail` answers with
    # what is held, and falls back to the row for a terminal this process has not written yet, which
    # is every terminal after a restart and exactly what the column exists for.
    stored = terminal["output"] if "output" in terminal.keys() else ""
    current = current_tail(str(terminal["id"]), stored)
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
    next_status = _terminal_status_transition(terminal["status"] if "status" in terminal.keys() else "", status)
    # AN ENDING TERMINAL FLUSHES, whatever the cadence says: the last screen of a worker that died is
    # the one an operator reads to find out why, and it is the one the tail's whole 24-hour TTL is
    # about. `terminal_diagnostics` reads it to say which line explains the death.
    # DERIVED, NOT RETYPED. This read `{"stopped", "failed"}` while the vocabulary has six
    # members, so a terminal ending as `ended`, `cancelled`, `lost` or `completed` got neither the
    # forced final write nor the `forget()` that releases its buffer -- and `routers/terminals.py`
    # closes such a terminal out using the full set on the same request, so the two disagreed
    # about what "ended" means.
    ending = next_status in _TERMINAL_END_STATUSES
    write_tail = record(str(terminal["id"]), next_output, seq) or ending or settle

    # `updated_at` NEVER LAGS. It is the freshness a reporting host is recognised by --
    # `_active_terminal_for_agent` keys ownership on it -- so a lazy tail must not make a live worker
    # look unowned. Same for `status` below.
    updates = ["updated_at = ?"]
    params: list[Any] = [_now()]
    if write_tail:
        # `output` AND `output_seq` TOGETHER, AND THAT PAIRING IS THE DESIGN. `xterm-mount.mjs` seeds
        # `lastSeq` from this row's `outputSeq` and `realtime-socket.mjs` drops any live frame with
        # `seq <= lastSeq`. A seq written EAGERLY beside a lagging output would tell the client it
        # already holds content it has never seen, and the frames filling the gap would be dropped --
        # missing output, a desynchronised ANSI stream, and the scrambled console of complaint B3.
        # Lagging them together keeps the row self-consistent.
        updates.append("output = ?")
        params.append(next_output)
        if seq is not None:
            updates.append("output_seq = ?")
            params.append(int(seq))
        mark_flushed(str(terminal["id"]))
    if next_status:
        updates.append("status = ?")
        params.append(next_status)
        if ending:
            updates.append("stopped_at = COALESCE(stopped_at, ?)")
            params.append(_now())
            # A DEAD TERMINAL HOLDS NO MEMORY. Its final tail has just been written above, so the
            # buffer has nothing left to protect and keeping it would leak 64 KB per ended terminal
            # for the life of the process.
            forget(str(terminal["id"]))
    params.append(terminal["id"])
    await db.execute(
        f"UPDATE terminal_sessions SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )
    if chunk:
        await _append_terminal_event(db, terminal["id"], "terminal_output", chunk[-2000:])
        # ANSWERED HERE, WHERE THE SCREEN IS CURRENT, and that placement is the whole fix.
        #
        # It was first done in the route, right after enqueueing the chunk -- and it never fired
        # once. The write is COALESCED and deferred, so at that moment the live screen did not yet
        # include the chunk being reacted to. Checking one chunk behind is harmless for a busy
        # terminal and useless for the only case that matters: a worker PARKED at a dialog sends
        # nothing more, so the chunk that drew it is never followed by one that would trigger the
        # check. The screen is fed a few lines above; this is the first moment it is true.
        await _answer_console_prompt(db, terminal)


async def _answer_console_prompt(db, terminal) -> None:
    """Press the key a parked worker is waiting for, once, and say why.

    THE FAILURE IT ENDS. A managed claude worker launched with
    `--dangerously-load-development-channels` stops at a first-run acknowledgment and waits. It
    registers `online`, claims nothing, and every signal reads healthy -- "up but deaf", which cost
    the operator's fleet a night on 2026-09-03. Nothing could press Enter for it once the aify-comms
    bridge stopped being the thing that started workers.

    THE SERVICE DECIDES AND THE HOST TYPES. The rule is a model of a claude SCREEN, which is this
    tier's business; the host runs the `input` control and stays ignorant of what any runtime looks
    like, because it is about to run processes for other services too.

    NEVER THROWS. Every chunk from every worker passes through here, and an exception would stop the
    console stream the operator reads -- trading a stuck prompt for a blind one.
    """
    try:
        terminal_id = str(terminal["id"])
        rendered = render_live_screen(terminal_id)
        if not rendered:
            return
        screen = rendered[0]
        keys = terminal.keys()
        # THE FACT ONLY THIS SERVICE HOLDS, and the reason a host cannot decide this at all: "keep
        # the context" and "start fresh" want opposite answers to the same dialog. Fetched only for
        # the screens that turn on it -- see `needs_resume_policy` for why a query per chunk is the
        # wrong trade.
        resume_policy = ""
        if needs_resume_policy(screen):
            resume_policy = await _resume_policy_for_agent(
                db, str(terminal["agent_id"] if "agent_id" in keys else ""),
            )
        answer = answer_for_screen(screen, resume_policy=resume_policy)
        if not should_answer(terminal_id, answer):
            return
        await _append_terminal_control(
            db,
            terminal_id=terminal_id,
            environment_id=str(terminal["environment_id"] if "environment_id" in keys else ""),
            bridge_id=str(terminal["bridge_id"] if "bridge_id" in keys else ""),
            action="input",
            requested_by="console-prompt",
            body=answer.keys,
        )
        # ATTRIBUTED TO ITS RULE. A keystroke nobody can account for is worse than a stuck prompt:
        # the next person to read this console sees an answer arrive from nowhere.
        logger.info(
            "answered console prompt on terminal=%s rule=%s: %s",
            terminal_id, answer.rule, answer.why,
        )
    except Exception:
        logger.debug("console prompt check failed", exc_info=True)


async def _resume_policy_for_agent(db, agent_id: str) -> str:
    """The agent's resume policy, or "" when it has none.

    ON THE AGENT ROW, NOT THE TERMINAL, and this function exists because that was got wrong once:
    the first version of the caller read `terminal["runtime_state"]`, which `terminal_sessions` has
    no column for. Nothing raised -- the read was guarded on the column being present -- so it
    answered "" for every terminal for ever, and the guard that was supposed to make it safe is
    exactly what made the mistake invisible. `session_restart.py` writes `resumePolicy` here, and
    `mcp/stdio/terminal-env.js` reads it from the same place.
    """
    if not agent_id:
        return ""
    row = await (await db.execute(
        "SELECT runtime_state FROM agents WHERE id = ?", (agent_id,),
    )).fetchone()
    if not row:
        return ""
    state = _json_loads_or(row["runtime_state"] or "", {})
    return str((state or {}).get("resumePolicy") or "")


async def _record_host_reported_alive(db, terminal) -> None:
    """The host says it is still running this terminal. Write down that it said so.

    THIS IS THE WHOLE LIVENESS MECHANISM, and until 2026-09-03 it wrote NOTHING. aify-env posts an
    empty frame per terminal per control pass -- no output, no status, deliberately, because a status
    would let a heartbeat REOPEN a terminal an operator or a reconciler had closed. Both guards on
    that path then dropped it: `TerminalOutputWriteQueue.enqueue` returns 0 for a frame with neither,
    and `_append_terminal_output` returns before its UPDATE. The service answered 200 and changed
    nothing.

    WHAT THAT COST, measured on the operator's fleet. `_active_terminal_for_agent` releases a
    terminal whose `bridge_id` no longer matches the environment row -- and every aify-env start
    mints a fresh bridge id, so after a restart every terminal mismatches. The guard against that is
    "was this terminal REPORTED recently", read from `updated_at`. With nothing refreshing
    `updated_at` the guard could never be true, so it released `sc-coder`'s terminal at 06:27:52
    while the host was running and streaming it. An ended terminal cannot go back to active by
    design, so the worker's own output could never undo it: a live claude session, unaddressable and
    unrestartable, and three refused restarts behind it.

    NOT THROUGH THE OUTPUT QUEUE, on purpose. That queue exists to COALESCE a high-frequency byte
    stream; a liveness touch is one tiny row write with nothing to batch, and routing it through the
    queue is what made it invisible. It writes ONE column: no status, so it cannot reopen anything;
    no output, so it cannot disturb the stream or its sequence numbers.
    """
    await db.execute(
        "UPDATE terminal_sessions SET updated_at = ? WHERE id = ?",
        (_now(), str(terminal["id"])),
    )
