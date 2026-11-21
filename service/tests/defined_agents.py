"""Defining an agent on its host, and starting it the ways the service still starts one (D8).

Since D8 an agent no host defines is never started, so a test that needs a managed worker, a spawn request or
a start defines the agent first, as a host's push would. `define` keeps every definition a test has pushed
for a machine, because a push is the machine's whole snapshot and one that left an agent out would withdraw it.

`cold_start` wakes a defined agent as a message does. Since phase 2 that queues an automatic lifecycle start,
so nothing in the product queues a new legacy spawn request. The legacy claim, PATCH and settlement routes stay
until phase 3 for requests queued before the upgrade; `legacy_spawn_request` seeds one of those, and
`spawn_defined` defines an agent and seeds one. Neither is evidence about what a send queues now.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from service.api_core.vocabulary import RUNTIME_HARNESS
from service.api_core.definition_snapshot import definition_digest, snapshot_digest
from service.api_core.definition_start import insert_spawn_request, spec_columns, start_binding
from service.api_core.dispatch_start import _coldstart_spawn_request_for_dispatch
from service.api_core.records import _environment_record_to_dict
from service.api_core.start_intent import START
from service.api_core.workspace import _workspace_for_environment
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
                  runtime: str = "claude-code", requested_by: str = "dashboard", start_intent: str = START,
                  **fields: Any) -> dict:
    """Define `agent_id` and seed the legacy spawn request a pre-phase-2 cold start queued for it, as the
    spawn-request list returns it. For tests of the legacy claim, PATCH and settlement routes only."""
    define(test, agent_id, environment_id=environment_id, machine_id=machine_id, bridge_id=bridge_id,
           runtime=runtime, **fields)
    before = {row["id"] for row in _spawn_requests(test, agent_id)}
    legacy_spawn_request(test, agent_id, environment_id=environment_id, requested_by=requested_by,
                         start_intent=start_intent)
    queued = [row for row in _spawn_requests(test, agent_id) if row["id"] not in before]
    test.assertEqual(len(queued), 1, f"the legacy fixture queued nothing for {agent_id}")
    return queued[0]


def legacy_spawn_request(test, agent_id: str, *, environment_id: str, requested_by: str = "dashboard",
                         start_intent: str = START, session_handle: str = "") -> str:
    """Seed the spec and queued spawn request that a cold start wrote before phase 2, for a defined agent on
    `environment_id`. It writes through the product's guarded insert, so the definition fence still applies.
    Returns the request id."""
    async def run():
        db = await get_db()
        try:
            binding = await start_binding(db, agent_id)
            test.assertIsNotNone(binding, f"the legacy fixture needs {agent_id} defined")
            row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))).fetchone()
            test.assertIsNotNone(row, f"the legacy fixture needs environment {environment_id}")
            workspace, workspace_root = _workspace_for_environment(
                _environment_record_to_dict(row), binding.workspace)
            agent = await (await db.execute("SELECT role, name FROM agents WHERE id = ?", (agent_id,))).fetchone()
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            spec_id = f"spec_legacy_{uuid.uuid4().hex[:12]}"
            spawn_id = f"spawn_legacy_{uuid.uuid4().hex[:12]}"
            spec = {"id": spec_id, "agent_id": agent_id, "environment_id": environment_id, "workspace": workspace,
                    "created_at": now, "updated_at": now, **spec_columns(binding)}
            await db.execute(f"INSERT INTO spawn_specs ({', '.join(spec)}) VALUES ({', '.join('?' * len(spec))})",
                             tuple(spec.values()))
            inserted = await insert_spawn_request(db, {
                "id": spawn_id, "spawn_spec_id": spec_id, "created_by": requested_by,
                "environment_id": environment_id, "agent_id": agent_id,
                "role": str((agent["role"] if agent else "") or binding.role or "coder"),
                "name": str((agent["name"] if agent else "") or binding.name or agent_id),
                "runtime": binding.runtime, "workspace": workspace, "workspace_root": workspace_root,
                "initial_message": "", "priority": "normal", "subject": f"Cold-start for {agent_id}",
                "mode": "managed-warm", "resume_policy": "native_first", "status": "queued",
                "session_handle": session_handle, "created_at": now, "updated_at": now,
                "start_intent": start_intent,
            }, binding)
            test.assertTrue(inserted, "the definition fence refused the legacy fixture")
            await db.commit()
            return spawn_id
        finally:
            await db.close()

    return asyncio.run(run())


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
