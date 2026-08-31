"""One write of the proof-based status engine's per-agent turn state.

RELOCATED, not rewritten, in v0.5.4 — byte-identical from `service/routers/agents/shared.py`, where
it had SEVEN router importers and no reason to live. It reads `agent_status_state`, folds one event
through `apply_event`, and upserts the result; its only dependencies are the clock and the pure
status engine, which makes it a layer-0 leaf that had been sitting a layer too high.

THE MOVE WAS FORCED BY A REAL BLOCK, which is worth recording because "it belongs lower" on its own
would not have been enough to justify touching seven files. `agent_heartbeat`'s turn-busy branch
could not be extracted at all while this lived in a router: an api_core leaf importing from
`service.routers` is the cycle the layering exists to prevent, and injecting the function as a
parameter would have hidden that rather than fixed it. See
`service/api_core/turn_busy_signal.py`, which is the extraction this unblocked.

IT COMMITS. That is inherited behaviour, not a new decision, and callers depend on it: the heartbeat
path relies on this write being durable before its own later commit. A caller that needs the write
to be part of a larger atomic unit cannot use this function as-is.
"""
from __future__ import annotations

from service.clock import now as _now
from service.status_engine import apply_event


async def _apply_status_event(db, agent_id: str, event: dict) -> dict:
    now = _now()
    row = await (await db.execute(
        "SELECT in_turn, awaiting_input, turn_run_id, turn_started_at "
        "FROM agent_status_state WHERE agent_id = ?",
        (agent_id,))).fetchone()
    cur = {"in_turn": (row["in_turn"] if row else 0),
           "awaiting_input": (row["awaiting_input"] if row else 0),
           "turn_run_id": (row["turn_run_id"] if row else "")}
    new = apply_event(cur, event)
    # WHEN THIS TURN BEGAN, stamped on the not-busy -> busy transition and then LEFT ALONE.
    #
    # The whole point is that it must not move while the turn runs. The in_turn ceiling used to age
    # against `last_event_at`, and the hermes hook path calls this function with `turn_start` before
    # EVERY model call -- so the ceiling that exists to un-latch a stuck `working` was measured
    # against a clock the latch itself keeps winding. Anchoring to the START retires the class
    # rather than naming re-stamping posters one at a time, which was tried once and held only for
    # the poster it named.
    was_in_turn = bool(cur["in_turn"])
    now_in_turn = bool(new["in_turn"])
    if now_in_turn and not was_in_turn:
        turn_started_at = now
    elif now_in_turn:
        # PRESERVED. A row already busy keeps the anchor it has, whatever this event was.
        turn_started_at = str((row["turn_started_at"] if row else "") or "") or now
    else:
        turn_started_at = ""
    await db.execute("""
        INSERT INTO agent_status_state (agent_id, in_turn, awaiting_input, turn_run_id,
                                        last_event, last_event_at, turn_started_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(agent_id) DO UPDATE SET
            in_turn=excluded.in_turn, awaiting_input=excluded.awaiting_input,
            turn_run_id=excluded.turn_run_id, last_event=excluded.last_event,
            last_event_at=excluded.last_event_at, turn_started_at=excluded.turn_started_at,
            updated_at=excluded.updated_at
    """, (agent_id, new["in_turn"], new["awaiting_input"], new["turn_run_id"],
          str(event.get("kind") or ""), now, turn_started_at, now))
    await db.commit()
    return new
