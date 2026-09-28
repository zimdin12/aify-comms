"""Runtime-hook state events apply in the order the hooks fired, not the order they arrive.

The turn hooks (turn-start, turn-end, blocked, unblocked) run in the background (`async` in Claude
Code and codex, a detached `node` under hermes), so a prompt no longer waits on them. Measured
2026-09-26 on a saturated host: starting `sh` took 0.4-1.9 s and one turn-start hook 1.5-2.6 s, past
the 3 s hook timeout, so every prompt waited and the event could be lost. In the background, two
events can arrive out of order: a slow turn-start landing after its own turn-end would leave the agent
`working` with nothing left to end the turn.

So each hook sends `firedAtUs`, the host clock in microseconds when it fired (the shell's
`$EPOCHREALTIME`, taken before `node` starts), and `machineId`, the id its bridge registers with. For
one agent the service keeps the last applied event's time and host, and applies an event only when:

  * its host is the agent's REGISTERED host. A relaunch on another host re-registers there, so a
    delayed event from the previous host is refused whatever its clock says; times are never compared
    across hosts, whose clocks can disagree by minutes.
  * it is from a different host than the last applied event (the first event after a move), or it
    fired later than that event.
  * or it fired in the same microsecond and is a turn-end. The tie goes to the end of a turn because
    a wrong `idle` is corrected by the next tool call's turn-start, and a wrong `working` is not.

The bridge-side turn detectors (claude transcript, codex rollout, resident hermes gateway), hermes'
`clearTurn`, and a heartbeat that STARTS a turn (`api_core/turn_busy_signal.py`) stamp the same two
fields since the 0.7.6 review (O2), taking the time when they observed the state they report. Before
that they were outside the ordering, so a detector's end observed before the next turn started could
land after it and clear it. They share one host clock with the hooks, which is what makes the times
comparable. An event without `firedAtUs` (a bridge or hook installed before this) is outside the
ordering and applies as before.
"""

from __future__ import annotations

_MAX_FIRED_AT_US = 2 ** 53  # past this a JSON number is not an exact integer


def hook_event_stamp(body) -> tuple[int | None, str]:
    """The hook's (firedAtUs, machineId), with None when it sent no usable time."""
    if not isinstance(body, dict):
        return None, ""
    fired = body.get("firedAtUs")
    if isinstance(fired, bool) or not isinstance(fired, int) or not 0 < fired < _MAX_FIRED_AT_US:
        fired = None
    machine = body.get("machineId")
    return fired, (machine.strip().lower() if isinstance(machine, str) else "")


async def accept_hook_event(db, agent_id: str, *, fired_at_us: int | None, machine_id: str, kind: str) -> bool:
    """Record this event as the agent's latest and return True, or return False when it must not apply.
    One statement: the host check and the compare-and-set cannot be split by a concurrent event."""
    if fired_at_us is None:
        return True
    cursor = await db.execute(
        """
        INSERT INTO agent_hook_order (agent_id, last_at, machine_id)
        SELECT id, ?, ? FROM agents
        WHERE id = ? AND (? = '' OR COALESCE(machine_id, '') = '' OR lower(machine_id) = ?)
        ON CONFLICT(agent_id) DO UPDATE SET last_at = excluded.last_at, machine_id = excluded.machine_id
        WHERE excluded.machine_id != agent_hook_order.machine_id
           OR excluded.last_at > agent_hook_order.last_at
           OR (excluded.last_at = agent_hook_order.last_at AND ? = 'turn-end')
        """,
        (fired_at_us, machine_id, agent_id, machine_id, machine_id, kind),
    )
    return (cursor.rowcount or 0) > 0
