"""An agent's definition, as the agent list and detail carry it (P0 D13: dashboard-manager's mirror).

`definitionState` comes from the agents row on every serialized agent; the owner, store, lifetime and
revision come from `agent_definitions`, read once for a whole list.
"""
from __future__ import annotations

import json
from typing import Any, Optional


async def definition_rows(db, agent_ids: list[str]) -> dict[str, Any]:
    """The `agent_definitions` rows for these agents, by id, in one read."""
    if not agent_ids:
        return {}
    marks = ",".join("?" * len(agent_ids))
    rows = await (await db.execute(
        f"SELECT agent_id, machine_id, store_id, incarnation, revision, available, unavailable_reason, "
        f"host_state, host_problems FROM agent_definitions WHERE agent_id IN ({marks})", tuple(agent_ids),
    )).fetchall()
    return {row["agent_id"]: row for row in rows}


def definition_of(agent_row, definition_row: Optional[Any]) -> dict[str, Any]:
    """PURE. `state` is "defined" while a host's definition stands, "withdrawn" once its owner dropped
    it, "" for an agent no host ever defined; the rest is null unless a definition stands."""
    state = str((agent_row["definition_state"] if "definition_state" in agent_row.keys() else "") or "")
    if definition_row is None:
        return {"state": state, "ownerMachineId": None, "storeId": None, "incarnation": None, "revision": None,
                "hostState": None, "hostProblems": [], "available": None, "unavailableReason": ""}
    return {
        "state": state,
        "ownerMachineId": definition_row["machine_id"],
        "storeId": definition_row["store_id"],
        "incarnation": definition_row["incarnation"],
        "revision": definition_row["revision"],
        "hostState": definition_row["host_state"],
        "hostProblems": json.loads(definition_row["host_problems"] or "[]"),
        "available": bool(definition_row["available"]),
        "unavailableReason": definition_row["unavailable_reason"] or "",
    }
