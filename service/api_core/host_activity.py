"""What a managed agent's HOST sees on its worker's screen: recorded from liveness frames, read back
for the status engine.

WHY IT EXISTS. The status of a managed agent came from hook events, the turn bookkeeping and the
console lease. A lost turn-end left an agent `working` while its screen sat at an idle prompt, and
Herdr's pane dot -- reading that screen with per-runtime rules -- was right. aify-env keeps a headless
screen per PTY, evaluates the same rules (Herdr's manifests, vendored there), and reports
`activity: {state, rule, observedAt}` on the terminal's liveness frames: at once on a change, and
again on every control pass.

FRESHNESS IS THE SERVICE'S OWN CLOCK. `activity_reported_at` is when this service last HEARD the
observation, and it is what `HOST_ACTIVITY_FRESH_SECONDS` is judged against -- never the host's
`observedAt`, so a skewed host clock cannot make an observation look fresh. A host that stops
reporting (restart, crash, older aify-env) lets the observation go stale, and the status falls back
to exactly what it read before this existed.

ONLY AN ACTIVE TERMINAL COUNTS. An ended terminal's last observation is kept on its row for the
record and never read here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from service.api_core.status_signal_prefetch import status_signals_or_live
from service.clock import iso_to_epoch as _iso_to_epoch, now as _now
from service.reconcilers.status_cache import invalidate_agent_live_state

#: The states a host may report. Anything else is ignored rather than refused, so a newer host with a
#: state this service does not know still has its liveness frame recorded.
HOST_ACTIVITY_STATES = ("working", "idle", "blocked")

#: How long one report stays fresh. aify-env repeats the observation on every control pass, and a pass
#: is gated by its claim long-poll (`CLAIM_WAIT_MS`, 25 s) plus a 250 ms floor -- so one frame per
#: terminal about every 25 s. MEASURED end to end: see the proof recorded with this change. Three
#: missed frames make it stale, which tolerates one slow pass without letting a host that has gone
#: away go on deciding a status for more than about a minute.
HOST_ACTIVITY_FRESH_SECONDS = 75


def _fresh(reported_at, now_epoch: float) -> bool:
    epoch = _iso_to_epoch(reported_at)
    # A FUTURE stamp is not fresh: a negative age trivially satisfies `<=`, the same trap the turn
    # clamps close.
    return bool(epoch) and 0 <= now_epoch - epoch <= HOST_ACTIVITY_FRESH_SECONDS


async def record_host_activity(db, terminal, activity) -> str:
    """Record the observation a liveness frame carried, on the caller's transaction.

    Returns the agent id when the status may have moved -- a new state, or a state that had gone
    stale -- so the caller can broadcast after its commit; "" otherwise. The live-status cache is
    expired here, before the commit, the way every other status writer does it.
    """
    state = str(getattr(activity, "state", "") or "").strip().lower()
    if state not in HOST_ACTIVITY_STATES:
        return ""
    terminal_id = str(terminal["id"])
    prior = await (await db.execute(
        "SELECT activity_state, activity_observed_at, activity_reported_at FROM terminal_sessions WHERE id = ?",
        (terminal_id,),
    )).fetchone()
    observed_at = str(getattr(activity, "observedAt", "") or "")[:64]
    # OLDER THAN WHAT IS STORED IS STALE NEWS. A liveness frame and a transition can cross in flight, so
    # arrival order is not observation order; the host's own `observedAt` is. Compared only when both
    # parse -- an observation that carries no usable time is judged by arrival, as before.
    incoming_epoch = _iso_to_epoch(observed_at)
    stored_epoch = _iso_to_epoch(prior["activity_observed_at"]) if prior else 0
    if incoming_epoch and stored_epoch and incoming_epoch < stored_epoch:
        return ""
    await db.execute(
        "UPDATE terminal_sessions SET activity_state = ?, activity_rule = ?, activity_observed_at = ?,"
        " activity_reported_at = ? WHERE id = ?",
        (state, str(getattr(activity, "rule", "") or "")[:200],
         observed_at, _now(), terminal_id),
    )
    now_epoch = datetime.now(timezone.utc).timestamp()
    if prior and str(prior["activity_state"] or "") == state and _fresh(prior["activity_reported_at"], now_epoch):
        return ""
    agent_id = str(terminal["agent_id"] or "")
    if agent_id:
        await invalidate_agent_live_state(db, agent_id)
    return agent_id


async def host_activity_for(db, agent_id: str, *, now_epoch: float | None = None,
                            status_signals=None) -> tuple[str, bool, str]:
    """(state, fresh, reported_at) of the newest observation on this agent's active terminals.

    ("", False, "") when there is none. Both StatusInputs producers call this, so they cannot
    disagree about it; the reconcile sweep passes its prefetched signals so the fleet is read once.
    """
    row = await status_signals_or_live(status_signals).host_activity(db, str(agent_id))
    if not row:
        return "", False, ""
    if now_epoch is None:
        now_epoch = datetime.now(timezone.utc).timestamp()
    reported_at = str(row["activity_reported_at"] or "")
    return str(row["activity_state"] or ""), _fresh(reported_at, now_epoch), reported_at
