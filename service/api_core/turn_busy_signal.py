"""The authoritative turn-busy signal: the bridge telling us a turn started or ended.

Extracted from `agent_heartbeat` in `service/routers/agents/liveness.py` in v0.5.4;
`test_agent_heartbeat_split_is_inert.py` inlines it back and AST-compares against the pre-split
fixture. The body is at its original 8-space column so the SQL literals are preserved byte-for-byte.

THIS EXTRACTION WAS BLOCKED FOR A RELEASE. It calls `_apply_status_event`, which was declared in
`service/routers/agents/shared.py`; an api_core leaf importing from a router is the cycle the
layering exists to prevent, and injecting the function as a parameter would have hidden that rather
than fixed it. Relocating `_apply_status_event` to `service/api_core/status_events.py` — where its
only dependencies, the clock and the pure status engine, already were — is what unblocked it.

ASYMMETRY IS THE WHOLE DESIGN, and it is not obvious from reading the code:

    turnBusy MISSING  -> liveness only. Old bridges that never send the field keep working.
    turnBusy TRUE     -> the LATEST bridge wins, unconditionally.
    turnBusy FALSE    -> ONLY the owning bridge, and only for the owning run, may clear.

The false case is guarded because a stale `false` from a superseded bridge or a finished run would
otherwise wipe a NEWER active turn, and the agent would report idle while it was still working. The
`in_turn` clear sits inside that same guard rather than beside it — deliberately, so it can never
clear where the `turn_busy = 0` write would not.

WHAT IS HANDED BACK. `turn_flip` says whether this beat actually CHANGED the turn state, and only a
real flip is worth pushing to dashboards; every 3-second liveness beat would otherwise broadcast.
"""
from __future__ import annotations

from service.api_core.status_events import _apply_status_event
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state


async def _apply_turn_busy_signal(db, agent_id, bridge_id, body, now, turn_flip):
        """Fold the bridge's turn report into `agent_turn_state`, and say whether it flipped.

        Guarded inside rather than at the call site, which is what the block looked like before it
        moved -- the extract-method gate splices this body back over its call verbatim. `turn_flip`
        is taken as a parameter and handed back rather than assigned, because after the split it
        would otherwise be a HELPER local the caller still reads: the live-out defect the gate
        refuses. Every argument is passed under the caller's own name; inline-back does not
        substitute arguments.
        """
        if "turnBusy" in body:
            turn_busy = bool(body.get("turnBusy"))
            turn_run_id = str(body.get("turnRunId", "") or "").strip()
            turn_runtime = str(body.get("turnRuntime", "") or "").strip()
            _prev_row = await (await db.execute(
                "SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?", (agent_id,))).fetchone()
            _prev_busy = bool(_prev_row and _prev_row["turn_busy"])
            if turn_busy:
                await db.execute(
                    """
                    INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at, turn_started_at)
                    VALUES (?, 1, ?, ?, ?, ?, ?)
                    ON CONFLICT(agent_id) DO UPDATE SET
                        turn_busy = 1,
                        turn_run_id = excluded.turn_run_id,
                        turn_bridge_id = excluded.turn_bridge_id,
                        turn_runtime = excluded.turn_runtime,
                        turn_updated_at = excluded.turn_updated_at,
                        -- Kept across a re-pulse, taken fresh only on the not-busy -> busy
                        -- transition. The delivery ceiling ages against this, so a heartbeat that
                        -- re-pulses turnBusy for a still-running turn must not push it forward.
                        turn_started_at = CASE
                            WHEN turn_busy = 1 AND COALESCE(turn_started_at, '') != ''
                            THEN turn_started_at
                            ELSE excluded.turn_started_at
                        END
                    """,
                    (agent_id, turn_run_id, bridge_id, turn_runtime, now, now),
                )
                turn_flip = not _prev_busy  # to-working transition
                # status v2 (Fix A, 2026-06-05): the /heartbeat turnBusy field is the
                # DOMINANT turn signal for MANAGED runtimes (hermes/codex/pi/opencode)
                # and claude channel-woken turns — the dispatch lifecycle pulses it,
                # but it only ever wrote agent_turn_state (OLD engine) and never fed
                # agent_status_state, so the `new` engine showed online/idle mid-turn.
                # Feed turn_start here too. Flag-agnostic at the write layer (only the
                # `new` read path consumes agent_status_state, so it is a no-op for
                # `old`); idempotent with any resident turn-start hook (turn_start just
                # sets in_turn=1). Mirrors the /turn-start endpoint's same pattern.
                await _apply_status_event(db, agent_id, {"kind": "turn_start", "runId": turn_run_id})
            else:
                cur = await (await db.execute(
                    "SELECT turn_bridge_id, turn_run_id FROM agent_turn_state WHERE agent_id = ?",
                    (agent_id,),
                )).fetchone()
                if cur:
                    stored_bridge = str(cur["turn_bridge_id"] or "").strip()
                    stored_run = str(cur["turn_run_id"] or "").strip()
                    if stored_bridge == bridge_id and (not stored_run or stored_run == turn_run_id):
                        await db.execute(
                            "UPDATE agent_turn_state SET turn_busy = 0, turn_updated_at = ? WHERE agent_id = ?",
                            (now, agent_id),
                        )
                        # status v2 (Fix A): clear in_turn ONLY inside the SAME
                        # ownership guard that gates the turn_busy=0 write, so a
                        # stale/superseded bridge or a non-owning run can never wipe
                        # a live turn's in_turn. Mirrors exactly the guard the
                        # turn_busy=0 write uses — never clears where the old code
                        # would not clear turn_busy.
                        await _apply_status_event(db, agent_id, {"kind": "turn_end", "runId": ""})
                        turn_flip = _prev_busy  # to-ready transition (only when we actually cleared)
            # A turn_busy flip changes derived status (working ⇄ idle). Invalidate
            # the live-state cache so the next read recomputes immediately, instead
            # of lagging up to the 60s reconcile sweep. Symmetric with the dedicated
            # /turn-start and /turn-end endpoints, which already invalidate.
            await _invalidate_agent_live_state(db, agent_id)
        return turn_flip
