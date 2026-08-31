"""Turn state: is a turn open, and when may it be closed. Leaf module.

Layer-0 slice of the v0.5.4 decomposition. `agent_turn_state` is the raw harness signal that an agent
is mid-turn, and these three are the only reads and writes of it outside the delivery gates.

WHY THIS IS WORTH ISOLATING. The bugs here have all been the same shape: a turn that never closes.
`turn_busy=1` latches when a turn-END is missed — a killed harness, a hook error, a transcript
classifier still reading in-flight — and the dead-bridge sweeper deliberately skips hook-driven
resident turns, so nothing clears it. Queued work then strands forever and an agent without `steer`
goes permanently deaf. Every fix in that area has been a BOUND on how long a raw signal may hold, so
the code that reads the signal and the code that ages it belong in one readable place.

`TURN_BUSY_STALE_SECONDS` came with `_turn_busy_state` — its only reader, measured. Note it is NOT the
same bound as `TURN_BUSY_BACKSTOP_SECONDS`: the status engine's `in_turn` clamp reads that one and the
delivery gate must agree with the clamp exactly. This line used to add "which stays in the control
plane", which stopped being true when that constant landed in `api_core/liveness.py` — and the two
bounds now meet in this file, because v0.5.4 moved the status engine's READ of `agent_turn_state`
here as `_status_turn_signals`. Both readers of the same row, ageing it against different ceilings,
are finally adjacent.

DB ACCESS: `db` passed in, no connection opened, no commit, no rollback — each joins its caller's
transaction.
"""

from __future__ import annotations

from service.api_core.status_signal_prefetch import status_signals_or_live

import time
from datetime import datetime, timezone

from service.api_core.liveness import TURN_BUSY_BACKSTOP_SECONDS
from service.api_core.turn_liveness_policy import turn_is_still_live
# `claim_gating` does not import this module, so this is a plain edge and not a cycle -- checked
# rather than assumed, because a function-scope import here would hide the dependency from the
# layering tests that read module imports.
from service.api_core.claim_gating import (
    TURN_LEASE_ABSOLUTE_MAX_SECONDS, _turn_lease_is_renewable,
)
from service.clock import iso_to_epoch as _iso_to_epoch
from service.clock import now as _now


TURN_BUSY_STALE_SECONDS = 120


async def _turn_busy_state(db, agent_id: str) -> tuple[bool, str]:
    """Return (fresh, turn_run_id) for the agent's agent_turn_state row.

    `fresh` is True when turn_busy=1 was updated within TURN_BUSY_STALE_SECONDS.
    `turn_run_id` is the run the bridge attributed that turn-busy pulse to (''
    when unknown). Callers wanting only the boolean ignore the run id;
    the reminder loop needs the run id so it can tell a GENUINE other-work
    turn_busy apart from a delivered-run's OWN delivery re-pulse (which would
    otherwise make a handoff skip its own reminder forever — deadlock)."""
    try:
        row = await (await db.execute(
            "SELECT turn_busy, turn_run_id, turn_updated_at FROM agent_turn_state WHERE agent_id = ?",
            (agent_id,),
        )).fetchone()
    except Exception:
        return (False, "")
    if not row or not int((row["turn_busy"] if "turn_busy" in row.keys() else 0) or 0):
        return (False, "")
    seen = _iso_to_epoch(str(row["turn_updated_at"] or ""))
    fresh = bool(seen and time.time() - seen <= TURN_BUSY_STALE_SECONDS)
    run_id = str((row["turn_run_id"] if "turn_run_id" in row.keys() else "") or "")
    return (fresh, run_id)


async def _clear_turn_busy_if_no_open_reply_owing_run(db, target_agent: str, exclude_run_id: str) -> bool:
    """Clear turn_busy for a channel/resident target ONLY when no OTHER
    require_reply=1 channel/resident run is still open for it.

    Event-based working-state clear shared by two completion paths:

      * a reply landing for an rr=1 run (_mark_dispatch_run_answered); and
      * an rr=0 channel/resident delivery being marked completed by the bridge
        (PATCH /dispatch/runs) — an info/response wake is NOT sustained work, so
        leaving turn_busy stamped from its delivery re-pulse (claude-channel.js
        re-pulses turn_busy on every delivery) was the send-deadlock: the next
        queued send saw a fresh phantom turn_busy and waited out the 120s window.

    The "no other open rr=1 run" guard is the anti-feedback-loop safety: we only
    clear when the agent is NOT owing a reply on some other in-flight turn, so we
    never race a legitimate, still-running reply turn to 0. We never RE-ARM
    turn_busy here (anti-loop invariant) — only ever clear it.
    """
    if not target_agent:
        return False
    remaining_cursor = await db.execute(
        """
        SELECT COUNT(*) AS open_count
        FROM dispatch_runs
        WHERE target_agent = ?
          AND id != ?
          AND status IN ('claimed', 'running', 'delivered')
          AND execution_mode IN ('channel', 'resident')
          AND COALESCE(require_reply, 0) = 1
        """,
        (target_agent, exclude_run_id),
    )
    remaining = await remaining_cursor.fetchone()
    if not remaining or int(remaining["open_count"] or 0) != 0:
        return False
    await db.execute(
        """
        INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
        VALUES (?, 0, '', '', '', ?)
        ON CONFLICT(agent_id) DO UPDATE SET
            turn_busy = 0,
            turn_run_id = '',
            turn_bridge_id = '',
            turn_runtime = '',
            turn_updated_at = excluded.turn_updated_at
        """,
        (target_agent, _now()),
    )
    # PURE-EVENT FIX (2026-06-19): do NOT clear the v2 status signal (agent_status_state.in_turn)
    # here. A reply landing is NOT a turn-end — the channel contract REQUIRES the agent to send
    # its reply mid-turn, before it finishes, so clearing in_turn on reply-landed flipped the
    # derived status to `online` while the agent was still working, then the bridge's turn
    # detector re-asserted → the working→online→working flicker the operator reported on BOTH
    # hermes (sc-coder) and claude (sc-claude). The 20s grace used to mask this; with the grace
    # gone (pure-event), the premature clear was exposed.
    #
    # We STILL clear agent_turn_state.turn_busy above — that releases the claim/send-queue gate
    # (the original send-deadlock fix: the queue gate reads turn_busy freshness, and a phantom
    # turn_busy from the delivery re-pulse stranded the next queued send for 120s). The two
    # consumers are now decoupled: turn_busy (queue gate) clears on reply-landed; in_turn
    # (status) clears ONLY on a real turn-end (the bridge turn detector / Stop / heartbeat-false
    # / dead-bridge sweep / 30-min backstop). This matches the file's anti-feedback invariant
    # ("only a bridge/event clears turn state") with ZERO new time-based logic.
    return True


async def _clear_status_state_in_turn(db, agent_id: str) -> None:
    """Clear the v2 engine's in_turn alongside an agent_turn_state turn_busy clear.

    Dual-table drift guard (review M3, 2026-06-10): the busy SETTERS feed both tables, but
    several reaper/clear paths cleared only agent_turn_state — under status_engine=new the
    agent stayed `working` until the 30-min backstop (and under `old` the disagreement log
    spammed). Commit-free on purpose: callers (reapers inside the reconcile transaction,
    endpoints with their own commit) own the transaction boundary.
    """
    now = _now()
    await db.execute(
        # `turn_started_at` is cleared HERE TOO, for the same reason the docstring above exists: the
        # busy setters feed both tables and a clear path that skips one leaves the row describing a
        # turn that ended. A stale anchor is only latent -- the clamp reads it only while in_turn is
        # 1, and the next turn stamps a fresh one -- but "latent" is what the drift this function
        # was written for looked like before it cost thirty minutes of a working agent's status.
        "UPDATE agent_status_state SET in_turn = 0, turn_run_id = '', turn_started_at = '', "
        "last_event = 'turn_end', last_event_at = ?, updated_at = ? "
        "WHERE agent_id = ? AND in_turn = 1",
        (now, now, agent_id),
    )


async def _status_turn_signals(db, agent_row, *, status_signals=None):
    """Read `agent_turn_state` for the STATUS engine: is a turn open, and is the agent ready.

    Extracted from `_compute_live_status_cache` (`api_core/status_inputs.py`) in v0.5.4, which
    was the largest function in the repo at 432 lines.

    IT IS A TWIN OF `_turn_busy_state` ABOVE AND THE DIFFERENCE IS THE POINT. Both read the same
    row and both age the signal, but against DIFFERENT bounds: that one uses
    `TURN_BUSY_STALE_SECONDS`, this one `TURN_BUSY_BACKSTOP_SECONDS`, which is the long
    wall-clock ceiling for a DROPPED turn-end event rather than a re-pulse cadence. This
    module's own docstring already warned the two bounds are not interchangeable; putting the
    readers side by side is what makes that visible instead of a sentence nobody reaches.

    All four values are initialised here, so the caller passes none of them in — the block
    returns the defaults unchanged on every path where the row is missing or the signal stale.
    """
    # Authoritative mid-turn signal pushed by the bridge (contract). Fresh
    # turn_busy=1 means the runtime is executing a turn right now → working,
    # even when the dispatch row is delivered/ambiguous. Stale is treated as
    # not-busy, and the bound is TURN_BUSY_BACKSTOP_SECONDS — see below, and see
    # the docstring above on why it is NOT TURN_BUSY_STALE_SECONDS. This comment
    # named the short bound until 2026-08-16: it travelled here with the block
    # when `_status_turn_signals` was extracted from `_compute_live_status_cache`,
    # where the surrounding prose was about `_turn_busy_state`. Naming the wrong
    # one of the pair, four lines under a docstring warning they are not
    # interchangeable, points the next reader at exactly the swap the suite
    # rejects (test_status_is_pure_event_long_ceiling_not_short_window).
    turn_busy = False
    turn_runtime = ""
    turn_updated_at = ""
    # Plan 4 task 12 (2026-05-25): `ready` is the bridge-pushed
    # handshake-complete signal. It remains an internal readiness bit; the
    # public idle-live status is `online` so operators do not see both
    # `ready` and `available` as competing positive states.
    turn_state_ready = False
    try:
        # Read through the signal source rather than inline, so a batch refresh can hand this the
        # row it already loaded. An absent source READS -- `status_signals_or_live` fails closed, and
        # the live reader issues exactly the query that used to be here.
        _tb = await status_signals_or_live(status_signals).turn_state(db, agent_row["id"])
        if _tb:
            if int(_tb["turn_busy"] or 0) == 1:
                # THE SAME POLICY DELIVERY USES. This aged `turn_updated_at` -- the MOVING column --
                # against the 30-minute ceiling and had no start anchor at all, so the hermes hook
                # re-stamping before every model call kept `working` alive for ever here even after
                # both anchored readers had let go. It was the third reader of this question and the
                # last one still carrying the original defect.
                _keys = _tb.keys()
                _started = _iso_to_epoch(str(
                    (_tb["turn_started_at"] if "turn_started_at" in _keys else "") or ""))
                _touched = _iso_to_epoch(str(_tb["turn_updated_at"] or ""))
                _now = datetime.now(timezone.utc).timestamp()

                def _verdict(renewable):
                    return turn_is_still_live(
                        started_epoch=_started, touched_epoch=_touched, renewable=renewable,
                        now_epoch=_now, strict_seconds=TURN_BUSY_BACKSTOP_SECONDS,
                        absolute_max_seconds=TURN_LEASE_ABSOLUTE_MAX_SECONDS)

                # STRICT FIRST, and pay for the ownership query ONLY when it would change the
                # answer. This runs per agent on a batch status refresh, and the prefetch module
                # beside it exists precisely to stop per-agent round-trips. The shortcut is exact
                # because the policy is monotone: verifying a lease can add liveness and never
                # remove it, which is asserted directly over its input grid.
                _live = _verdict(False)
                if not _live:
                    _owner = str((_tb["turn_bridge_id"] if "turn_bridge_id" in _keys else "") or "")
                    if _owner:
                        _live = _verdict(await _turn_lease_is_renewable(db, agent_row["id"], _owner))
                if _live:
                    turn_busy = True
                    turn_runtime = str(_tb["turn_runtime"] or "").strip()
                    turn_updated_at = str(_tb["turn_updated_at"] or "").strip()
            # PURE-EVENT (2026-06-19): the turn-end GRACE (#224, 20s) was REMOVED. It held
            # `working` for 20s after turn_busy cleared to mask a managed claude's premature/
            # duplicate Stop hooks — a TIME-BASED hold that (a) stacked on the hermes bridge's
            # 9s idle-debounce to show "working" ~30s after a real idle (operator-reported), and
            # (b) is exactly the time-decay the status engine must not have. The flap is now
            # fixed AT THE SOURCE: the bridge turn detectors (hermes gateway / claude transcript)
            # only clear turn_busy on EVENT-confirmed end, and run fast enough to re-assert a
            # premature clear within a tick. Status here is pure-event: turn_busy=1 (within the
            # far 30-min wedged-bridge backstop) AND live → working; otherwise online.
            try:
                turn_state_ready = int(_tb["ready"] or 0) == 1
            except (IndexError, KeyError):
                # Pre-migration row (column absent on a foreign DB schema).
                turn_state_ready = False
    except Exception:
        turn_busy = False
        turn_state_ready = False
    return turn_busy, turn_runtime, turn_updated_at, turn_state_ready
