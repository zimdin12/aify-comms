"""A bridge that took over its session from a bridge that is still alive hands the session back when it goes.

THE INCIDENT, 2026-09-26 (sc-manager). An agent ran `claude mcp list` and `claude -p` from its own shell.
Each child `claude` inherited the agent's identity and session id, so its aify-comms bridge registered as
that agent with the same session handle, and the same-session relaunch rule (`same_mode_bridge_gate.py`)
let it supersede the real bridge. The child exited seconds later, its bridge reported resident-lost as the
current owner, and the agent was set `stopped`. The real bridge kept beating and was ignored as
superseded, so for 2.5 hours no message created a run, until the operator had the agent re-register.

THE RULE. When the current bridge of a resident agent is lost, and the bridge it superseded holds the same
session handle and has beaten within the resident lease, that bridge is the session's living process: it
gets the session back and the agent is not stopped. A real relaunch looks different, because kill-prior
killed the superseded bridge, which never beats again; by the time the relaunched session closes, it is
outside the lease and the agent stops as before. One case reads wrong: a relaunched session that closes
within the lease of the kill hands back to a dead bridge, and the agent then ages to offline through the
lease instead of reading stopped at once.

`superseded_beat_at` is written only by beats from a superseded bridge and read only here, so a long nested
run (a `claude -p` that outlives the lease) is still recognised, while `last_seen`, which a dozen readers
treat as liveness, stays untouched for a superseded bridge.
"""

from __future__ import annotations

import json
import time

from service.clock import iso_to_epoch


async def record_beat_while_superseded(db, *, agent_id: str, bridge_id: str, now: str) -> None:
    await db.execute(
        "UPDATE bridge_instances SET superseded_beat_at = ? WHERE id = ? AND agent_id = ?",
        (now, bridge_id, agent_id),
    )


async def live_bridge_it_took_over(db, *, agent_id: str, lost_bridge_id: str, lease_seconds: float) -> str:
    """The same-session bridge `lost_bridge_id` superseded that has beaten within the lease, or ""."""
    lost = await (await db.execute(
        "SELECT session_handle FROM bridge_instances WHERE id = ? AND agent_id = ?", (lost_bridge_id, agent_id),
    )).fetchone()
    handle = str((lost["session_handle"] if lost else "") or "").strip()
    if not handle:
        return ""
    rows = await (await db.execute(
        "SELECT id, last_seen, superseded_beat_at FROM bridge_instances"
        " WHERE agent_id = ? AND superseded_by = ? AND session_handle = ?",
        (agent_id, lost_bridge_id, handle),
    )).fetchall()
    now = time.time()
    fresh = []
    for row in rows:
        seen = max(iso_to_epoch(row["last_seen"] or "") or 0, iso_to_epoch(row["superseded_beat_at"] or "") or 0)
        if seen and now - seen <= lease_seconds:
            fresh.append((seen, row["id"]))
    return max(fresh)[1] if fresh else ""


async def hand_back(db, *, agent_id: str, bridge_id: str, runtime_state: dict) -> None:
    """`bridge_id` owns the session again. The agent's status is left as it is: it was never stopped."""
    await db.execute(
        "UPDATE bridge_instances SET superseded_by = '', superseded_at = NULL, superseded_beat_at = NULL"
        " WHERE id = ? AND agent_id = ?",
        (bridge_id, agent_id),
    )
    await db.execute(
        "UPDATE agents SET runtime_state = ? WHERE id = ?",
        (json.dumps(dict(runtime_state, bridgeInstanceId=bridge_id)), agent_id),
    )
