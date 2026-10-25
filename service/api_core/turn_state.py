"""Turn state: is a turn open, and when may it be closed. Leaf module.

Layer-0 slice of the v0.5.4 decomposition. `agent_turn_state` is the raw harness signal that an agent
is mid-turn, and these three are the only reads and writes of it outside the delivery gates.

WHY THIS IS WORTH ISOLATING. The bugs here have all been the same shape: a turn that never closes.
`turn_busy=1` latches when a turn-END is missed — a killed harness, a hook error, a transcript
classifier still reading in-flight — and the dead-bridge sweeper deliberately skips hook-driven
resident turns, so nothing clears it. Queued work then strands forever and an agent without `steer`
goes permanently deaf. Every fix in that area has been a BOUND on how long a raw signal may hold, so
the code that reads the signal and the code that ages it belong in one readable place.

THE READS ARE GONE (0.9.9 S3b). The turn-busy hold, the status engine's `in_turn` signal and the two
ceilings that aged them went with the legacy derivation: since the 0.9.8 switch the word and the delivery
hold are aify-env's. What remains are the two clears, writers of a row nothing reads, which go with the
table in S3c.

DB ACCESS: `db` passed in, no connection opened, no commit, no rollback — each joins its caller's
transaction.
"""

from __future__ import annotations


import time

from service.clock import now as _now


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
