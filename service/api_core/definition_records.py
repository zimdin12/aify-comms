"""An agent's definition, as the agent list and detail carry it (P0 D13: dashboard-manager's mirror).

`definitionState` comes from the agents row on every serialized agent; the owner, store, lifetime and
revision come from `agent_definitions`, read once for a whole list.
"""
from __future__ import annotations

from typing import Any, Optional

from service.api_core.model_effort import record_effort, record_model
from service.api_core.runtime import _normalize_session_mode
from service.api_core.serialization import _json_loads_or


async def definition_rows(db, agent_ids: list[str]) -> dict[str, Any]:
    """The `agent_definitions` rows for these agents, by id, in one read."""
    if not agent_ids:
        return {}
    marks = ",".join("?" * len(agent_ids))
    rows = await (await db.execute(
        f"SELECT agent_id, machine_id, store_id, incarnation, revision, available, unavailable_reason, "
        f"host_state, host_problems, body FROM agent_definitions WHERE agent_id IN ({marks})", tuple(agent_ids),
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
        "hostProblems": _json_loads_or(definition_row["host_problems"], []),
        "available": bool(definition_row["available"]),
        "unavailableReason": definition_row["unavailable_reason"] or "",
    }


def runs_with(agent_row, definition_row: Optional[Any]) -> dict[str, Any]:
    """PURE. The model and effort this agent's next start uses, each with where it comes from (P0 C12).

    `from` is "definition" for a defined agent (aify-env's file, which its launcher and its host read),
    "agent" for an undefined managed agent (the record its managed start reads), and "runtime" when
    nothing applies a value, so the runtime's own configuration decides; `value` is then "". An undefined
    resident's launcher reads no record, so a value on its record is not what it runs with. What a
    runtime is actually running is not observed: no bridge reports it.

    A managed record is read by `model_effort`, the reader its launch uses. Stored JSON is decoded
    tolerantly: one damaged row must not take down the list of every agent.
    """
    state = str((agent_row["definition_state"] if "definition_state" in agent_row.keys() else "") or "")
    if state == "defined" and definition_row is not None:
        body = _json_loads_or(definition_row["body"], {})
        body = body if isinstance(body, dict) else {}
        model, effort, source = record_model(body.get("model"), {}), record_effort({"effort": body.get("effort")}), "definition"
    elif _normalize_session_mode(agent_row["session_mode"] or "resident") == "managed":
        config = _json_loads_or(agent_row["runtime_config"], {})
        model, effort, source = record_model(agent_row["model"], config), record_effort(config), "agent"
    else:
        model, effort, source = "", "", "runtime"
    said = lambda value: {"value": value, "from": source} if value else {"value": "", "from": "runtime"}
    return {"model": said(model), "effort": said(effort)}
