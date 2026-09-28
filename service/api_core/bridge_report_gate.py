"""Whether a bridge may report an agent's session: only one registered for that agent and not superseded.

REVIEW OF 7f638a65 (comms-senior-dev, 2026-09-28). A `claude` started from an agent's own shell has a
bridge that registers its own session and is refused (409), so it never owns the agent. Its session-handle
heartbeat started anyway, independently of registration, and `PATCH /agents/{id}/session-handle` checked
nothing about who was asking: the reviewer's probe had a refused bridge rewrite the live parent's
`last_seen` and note, and, from a stale capture, replace the parent's pinned handle while ownership stayed
with the parent's bridge.

A bridge proves itself the way every other bridge report here does, by the `bridge_instances` row its
registration wrote. A refused registration writes none (the same-mode gate raises before the row is
recorded), and a superseded bridge has been replaced by a newer one for the same agent. No bridge id at all
is refused too: a guard that passes when its input is missing is decoration. A bridge started before this
change sends none, so its handle reports are ignored until it is relaunched; the handle it registered with
stands.
"""

from __future__ import annotations


async def bridge_may_report(db, agent_id: str, bridge_id: str) -> bool:
    """True when `bridge_id` is a current, non-superseded registration of `agent_id`."""
    if not bridge_id:
        return False
    row = await (await db.execute(
        "SELECT 1 FROM bridge_instances WHERE id = ? AND agent_id = ? AND COALESCE(superseded_by, '') = ''",
        (bridge_id, agent_id),
    )).fetchone()
    return row is not None
