"""Runtime-hook state events apply in the order the hooks fired, not the order they arrive.

The turn hooks (turn-start, turn-end, blocked, unblocked) run in the background (`async` in Claude
Code and codex, a detached `node` under hermes), so a prompt no longer waits on them. Measured
2026-09-26 on a saturated host: starting `sh` took 0.4-1.9 s and one turn-start hook 1.5-2.6 s, past
the 3 s hook timeout, so every prompt waited and the event could be lost. In the background, two
events can arrive out of order: a slow turn-start landing after its own turn-end would leave the agent
`working` with nothing left to end the turn.

So each hook sends `at`, the host's clock in milliseconds when it fired, and an event older than the
last one applied for that agent is refused. The comparison is between hook times from the agent's own
host, never against the service's clock. Registration clears the record, so an agent relaunched on a
host whose clock is behind is not refused until that clock catches up. An event without `at` (a
bridge-side detector, an older hook) is outside this ordering and applies as before.
"""

from __future__ import annotations


def hook_event_at(body: dict) -> int | None:
    """The hook's `at`, when it sent a usable one."""
    at = body.get("at") if isinstance(body, dict) else None
    if isinstance(at, bool) or not isinstance(at, int) or at <= 0:
        return None
    return at


async def accept_hook_event(db, agent_id: str, at: int | None) -> bool:
    """Record `at` as the agent's latest hook event and return True, or return False when a later one
    was already applied. One statement, so two concurrent events cannot both pass."""
    if at is None:
        return True
    cursor = await db.execute(
        "INSERT INTO agent_hook_order (agent_id, last_at) VALUES (?, ?) "
        "ON CONFLICT(agent_id) DO UPDATE SET last_at = excluded.last_at "
        "WHERE excluded.last_at >= agent_hook_order.last_at",
        (agent_id, at),
    )
    return (cursor.rowcount or 0) > 0


async def forget_hook_order(db, agent_id: str) -> None:
    """At registration: the next hook event may come from another host's clock."""
    await db.execute("DELETE FROM agent_hook_order WHERE agent_id = ?", (agent_id,))
