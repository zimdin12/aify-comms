"""Starting a defined agent: from its definition, at one revision, or not at all (P0 C6, C7).

`start_binding` answers, for one agent, what a start may be built from:

- None: the agent was never defined. Since D8 it does not start at all (`undefined_refusal`): a spawn of a new
  id defines it first (`definition_creation.py`), and an existing one is defined by `aify-env agents import`;
- a `StartBinding`: the definition the spawn is built from. Its store, incarnation and revision are
  recorded on the spawn request and carried by the launch, so the host can refuse to start a worker
  from any other;
- `StartRefused`: withdrawn (C6), resident (started through its launcher, C7), or a definition its
  host has said it cannot run as it stands.

THE READ IS REPEATED BY THE WRITE. `spawn_request_guard` is the same question as a WHERE clause on the
spawn request's INSERT, so a push or withdrawal that lands between this read and that write leaves the
INSERT with nothing to insert, and the start is refused rather than built from the state it replaced.
Some callers hold the write lock and some do not; the guard does not depend on which.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from service.api_core.definition_push import HARNESS_RUNTIME


class StartRefused(Exception):
    """A defined or withdrawn agent that may not be started now; the message says why and what to do."""


def undefined_refusal(agent_id: str) -> str:
    """D8: why an agent no host defines is not started, and what to do."""
    return (f'no host defines "{agent_id}", so it cannot be started; define it on its host '
            f'(`aify-env agents import --write` adopts an existing agent), then start it')


@dataclass(frozen=True)
class StartBinding:
    machine_id: str
    store_id: str
    incarnation: int
    revision: int
    runtime: str
    workspace: str
    model: str
    effort: str
    role: str
    name: str
    instructions: str
    env: dict[str, str]


def binding_for(agent_id: str, definition, agent) -> Optional[StartBinding]:
    """PURE: what a start of `agent_id` is built from, given its `agent_definitions` row (or None) and
    its `agents` row (or None). Raises `StartRefused`."""
    if definition is None:
        withdrawn = agent is not None and str(agent["definition_state"] or "") == "withdrawn"
        if not withdrawn:
            return None
        machine = str(json.loads(agent["definition_withdrawn"] or "{}").get("machineId") or "")
        where = f" on {machine}" if machine else ""
        raise StartRefused(f'Agent "{agent_id}" was withdrawn in aify-env{where}; define it again to start it')
    machine = definition["machine_id"]
    if definition["host_state"] != "valid":
        problems = ", ".join(json.loads(definition["host_problems"] or "[]")) or "unreadable"
        raise StartRefused(f'Agent "{agent_id}" has an invalid definition on {machine} ({problems}); '
                           f'fix it there to start it')
    if not definition["available"]:
        raise StartRefused(f'Agent "{agent_id}" is defined on {machine}, which cannot run it: '
                           f'{definition["unavailable_reason"]}')
    body = json.loads(definition["body"])
    if body["mode"] == "resident":
        raise StartRefused(f'Agent "{agent_id}" is defined in aify-env on {machine} as resident; '
                           f'start it through its launcher on that host')
    return StartBinding(
        machine_id=machine, store_id=definition["store_id"], incarnation=int(definition["incarnation"]),
        revision=int(definition["revision"]), runtime=HARNESS_RUNTIME[body["harness"]],
        workspace=body["workspace"], model=body["model"], effort=body["effort"], role=body["role"],
        name=body["name"], instructions=body["instructions"], env=dict(body["env"]))


async def start_binding(db, agent_id: str) -> Optional[StartBinding]:
    definition = await (await db.execute(
        "SELECT * FROM agent_definitions WHERE agent_id = ?", (agent_id,))).fetchone()
    agent = await (await db.execute(
        "SELECT definition_state, definition_withdrawn FROM agents WHERE id = ?", (agent_id,))).fetchone()
    return binding_for(agent_id, definition, agent)


def spec_columns(binding: StartBinding) -> dict[str, Any]:
    """PURE: every spawn spec column but its identity, workspace and timestamps, from the definition.
    Nothing comes from an earlier spec or from the service's managed defaults: an empty model or effort
    leaves the harness to choose, as it would for the operator who launched the definition by hand."""
    runtime_config = {"effort": binding.effort} if binding.effort else {}
    return {
        "runtime": binding.runtime, "model": binding.model, "profile": "", "mode": "managed-warm",
        "system_prompt": "", "standing_instructions": binding.instructions, "env_vars": json.dumps(binding.env),
        "channel_ids": "[]", "budget_policy": "{}", "context_policy": "{}", "restart_policy": "{}",
        "metadata": json.dumps({"runtimeConfig": runtime_config} if runtime_config else {}),
    }


def request_columns(binding: Optional[StartBinding]) -> dict[str, Any]:
    """PURE: the spawn request's record of what it was built from; '' and 0 for an undefined agent."""
    if binding is None:
        return {"definition_store_id": "", "definition_incarnation": 0, "definition_revision": 0}
    return {"definition_store_id": binding.store_id, "definition_incarnation": binding.incarnation,
            "definition_revision": binding.revision}


def spawn_request_guard(agent_id: str, binding: Optional[StartBinding]) -> tuple[str, tuple]:
    """The WHERE that holds only while the agent is as `start_binding` read it."""
    if binding is None:
        return ("NOT EXISTS (SELECT 1 FROM agent_definitions WHERE agent_id = ?) AND NOT EXISTS "
                "(SELECT 1 FROM agents WHERE id = ? AND definition_state = 'withdrawn')", (agent_id, agent_id))
    return ("EXISTS (SELECT 1 FROM agent_definitions WHERE agent_id = ? AND machine_id = ? AND store_id = ? "
            "AND incarnation = ? AND revision = ? AND host_state = 'valid' AND available = 1)",
            (agent_id, binding.machine_id, binding.store_id, binding.incarnation, binding.revision))


async def insert_spawn_request(db, columns: dict[str, Any], binding: Optional[StartBinding]) -> bool:
    """Insert the spawn request `columns` describe, recording `binding`, only while the guard holds.
    True when it was inserted. An undefined agent is refused (D8): every caller refuses it first, with its
    own answer, and this keeps a caller that forgets from starting one."""
    if binding is None:
        raise StartRefused(undefined_refusal(columns["agent_id"]))
    row = {**columns, **request_columns(binding)}
    guard, guard_params = spawn_request_guard(row["agent_id"], binding)
    names = ", ".join(row)
    cursor = await db.execute(
        f"INSERT INTO spawn_requests ({names}) SELECT {', '.join('?' * len(row))} WHERE {guard}",
        (*row.values(), *guard_params))
    return cursor.rowcount == 1


CHANGED_WHILE_STARTING = "its definition changed while this start was being made; start it again"
