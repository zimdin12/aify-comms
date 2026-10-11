"""Defining an agent on its host, and starting it the ways the service still starts one (D8).

Since D8 an agent no host defines is never started, so a test that needs a managed worker, a spawn request or
a start defines the agent first, as a host's push would. `define` keeps every definition a test has pushed
for a machine, because a push is the machine's whole snapshot and one that left an agent out would withdraw it.

`cold_start` is the only producer of a legacy spawn request left: a defined agent woken on the send path.
"""
from __future__ import annotations

import asyncio
from typing import Any

from service.api_core.definition_push import RUNTIME_HARNESS
from service.api_core.definition_snapshot import definition_digest, snapshot_digest
from service.api_core.dispatch_start import _coldstart_spawn_request_for_dispatch
from service.api_core.settings import _load_settings
from service.db import get_db

STORE = "s-test"


def definition_entry(agent_id: str, *, runtime: str = "claude-code", workspace: str = "/workspace", incarnation: int = 1,
                     revision: int = 1, **fields: Any) -> dict:
    """The snapshot entry a host publishes for a valid, runnable definition of `agent_id`."""
    agent = {"id": agent_id, "name": agent_id, "role": "coder", "harness": RUNTIME_HARNESS[runtime],
             "mode": "managed", "workspace": workspace, "model": "", "effort": "", "instructions": "", "env": {},
             "herdrSpace": True, **fields}
    return {"id": agent_id, "state": "valid", "incarnation": incarnation, "revision": revision,
            "definitionDigest": definition_digest(agent), "definition": agent, "available": True}


def define(test, agent_id: str, *, environment_id: str, machine_id: str, bridge_id: str, **fields: Any) -> dict:
    """Publish `agent_id` defined on `machine_id`, beside everything this test defined there before."""
    entries = _defined_on(test, machine_id)
    entries[agent_id] = definition_entry(agent_id, incarnation=len(entries) + 1, **fields)
    publish(test, environment_id=environment_id, machine_id=machine_id, bridge_id=bridge_id)
    return entries[agent_id]


def _defined_on(test, machine_id: str) -> dict:
    return test.__dict__.setdefault("_defined_by_machine", {}).setdefault(machine_id, {})


def publish(test, *, environment_id: str, machine_id: str, bridge_id: str) -> None:
    """Push `machine_id`'s snapshot: everything this test defined there, which may be nothing. A machine whose
    host has published a store is one a spawn can ask to define an agent (D8)."""
    published = list(_defined_on(test, machine_id).values())
    revision = test.__dict__["_store_revision"] = test.__dict__.get("_store_revision", 0) + 1
    answer = test.client.put(f"/api/v1/environments/{environment_id}/agent-definitions", json={
        "bridgeId": bridge_id, "machineId": machine_id, "storeId": STORE, "revision": revision,
        "snapshotDigest": snapshot_digest(published), "entries": published})
    test.assertEqual(answer.status_code, 200, answer.text)
    test.assertEqual(answer.json().get("refused") or [], [], "the service refused a definition")


def cold_start(test, agent_id: str, *, runtime: str = "claude-code", requested_by: str = "dashboard") -> list[str]:
    """Wake `agent_id` as a message would. Returns the warnings, which name why nothing was queued."""
    warnings: list[str] = []

    async def run():
        db = await get_db()
        try:
            await _coldstart_spawn_request_for_dispatch(db, agent_id, runtime=runtime, settings=await _load_settings(db),
                                                        requested_by=requested_by, warnings=warnings)
            await db.commit()
        finally:
            await db.close()

    asyncio.run(run())
    return warnings


def spawn_defined(test, agent_id: str, *, environment_id: str, machine_id: str, bridge_id: str,
                  runtime: str = "claude-code", requested_by: str = "dashboard", **fields: Any) -> dict:
    """Define `agent_id` and cold-start it: the spawn request a test used to get from POST /spawn-requests."""
    define(test, agent_id, environment_id=environment_id, machine_id=machine_id, bridge_id=bridge_id,
           runtime=runtime, **fields)
    before = {row["id"] for row in _spawn_requests(test, agent_id)}
    warnings = cold_start(test, agent_id, runtime=runtime, requested_by=requested_by)
    queued = [row for row in _spawn_requests(test, agent_id) if row["id"] not in before]
    test.assertEqual(len(queued), 1, f"no spawn request was queued for {agent_id}: {warnings}")
    return queued[0]


def _spawn_requests(test, agent_id: str) -> list[dict]:
    return test.client.get(f"/api/v1/spawn-requests?agentId={agent_id}").json()["spawnRequests"]


def undefine(test, agent_id: str) -> None:
    """Make `agent_id` one no host defines, as an agent already running before D8 is: its definition row goes,
    and this test's later pushes leave it out. Nothing withdraws it, so it reads as never defined."""
    import sqlite3

    for entries in test.__dict__.get("_defined_by_machine", {}).values():
        entries.pop(agent_id, None)
    db = sqlite3.connect(test._db_path)
    try:
        db.execute("DELETE FROM agent_definitions WHERE agent_id = ?", (agent_id,))
        db.execute("UPDATE agents SET definition_state = '', definition_withdrawn = '' WHERE id = ?", (agent_id,))
        db.commit()
    finally:
        db.close()
