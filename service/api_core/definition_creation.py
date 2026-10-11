"""D8: a spawn of an id no host defines asks a host to define it, then starts what the host defined.

There is no other way to start a managed agent: an undefined one is refused at `insert_spawn_request`. The
creation is a definition request (`definition_requests.CREATION`), applied by the host's definition store
only where no file names the id. Its `done` leaves the consequence `spawn pending`, and the spawn is queued
by whichever comes first to find the definition published here: the host's report or its next push.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Optional

from fastapi import HTTPException

from service.api_core import agent_lifecycle_requests as lifecycle
from service.api_core import definition_requests as requests
from service.api_core.definition_push import RUNTIME_HARNESS
from service.api_core.definition_schema import agent_problems
from service.api_core.serialization import _iso_add_seconds


def created_agent(agent_id: str, *, name: str, role: str, runtime: str, workspace: str, model: str, effort: str,
                  instructions: str, env: dict) -> dict:
    """PURE. The whole agent a spawn defines (C1), or 422 naming what C1 refuses in it."""
    if runtime not in RUNTIME_HARNESS:
        raise HTTPException(422, f'runtime "{runtime}" is not a harness a definition can name '
                                 f'({", ".join(sorted(RUNTIME_HARNESS))})')
    agent = {"name": name, "role": role, "harness": RUNTIME_HARNESS[runtime], "mode": "managed",
             "workspace": workspace, "model": model, "effort": effort, "instructions": instructions,
             "env": env, "herdrSpace": True}
    problems = agent_problems({**agent, "id": agent_id}, agent_id)
    if problems:
        raise HTTPException(422, f'"{agent_id}" cannot be defined as asked: {"; ".join(problems)}')
    return agent


async def admit_creation(db, agent_id: str, machine_id: str, agent: dict, brief: Optional[dict], requested_by: str,
                         now: str) -> dict:
    """Queue the creation of `agent_id` on `machine_id`'s current store. The caller holds the write lock and
    has checked that no host defines the id."""
    store = await (await db.execute(
        "SELECT store_id FROM definition_stores WHERE machine_id = ?", (machine_id,))).fetchone()
    if not store:
        raise HTTPException(409, f'the host on {machine_id or "an unknown machine"} has published no agent '
                                 f'definitions, so it cannot define "{agent_id}"; check `aify-env doctor` there')
    waiting = await (await db.execute(
        f"SELECT id FROM definition_requests WHERE agent_id = ? AND status IN {requests.OPEN_SQL}",
        (agent_id,))).fetchone()
    if waiting:
        raise HTTPException(409, f'"{agent_id}" already has a request waiting for its host ({waiting["id"]})')
    request_id = f"defreq_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    await db.execute(
        "INSERT INTO definition_requests (id, agent_id, machine_id, store_id, expected_incarnation, expected_revision, "
        "patch, requested_by, created_at, expires_at, brief) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (request_id, agent_id, machine_id, store["store_id"], *requests.CREATION, json.dumps(agent), requested_by,
         now, _iso_add_seconds(now, requests.REQUEST_TTL_SECONDS), json.dumps(brief) if brief else ""))
    return await requests.request_by_id(db, request_id)


async def spawn_created(db, machine_id: str, now: str) -> None:
    """Queue the spawn of every creation `machine_id`'s host has done and published here. One not yet
    published stays owed; one whose definition has since gone or been replaced spawns nothing, and says why."""
    owed = await (await db.execute(
        "SELECT * FROM definition_requests WHERE machine_id = ? AND status = 'done' AND consequence = ?",
        (machine_id, requests.SPAWN_OWED))).fetchall()
    for row in owed:
        held = await (await db.execute(
            "SELECT * FROM agent_definitions WHERE agent_id = ?", (row["agent_id"],))).fetchone()
        if held is None:
            continue
        settled = await _spawn(db, row, held, now)
        await db.execute("UPDATE definition_requests SET consequence = ? WHERE id = ?", (settled, row["id"]))


async def _spawn(db, row, held, now: str) -> str:
    created = (row["machine_id"], row["store_id"], row["result_incarnation"])
    if (held["machine_id"], held["store_id"], held["incarnation"]) != created:
        return f"nothing spawned: {row['agent_id']} is now defined by {held['machine_id']} at another lifetime"
    request_id = f"spawn-{row['id']}"
    intent = json.dumps({"agentId": row["agent_id"], "action": "spawn", "requestedBy": row["requested_by"],
                         "createdBy": row["id"]}, sort_keys=True)
    try:
        await lifecycle.queue_lifecycle(db, request_id, held, action="spawn", actor=row["requested_by"],
                                        expected_lifetime=None, fresh_context=True, intent=intent, now=now,
                                        brief=row["brief"] or "")
    except HTTPException as refused:
        return f"nothing spawned: {refused.detail}"
    return f"spawn queued {request_id}"
