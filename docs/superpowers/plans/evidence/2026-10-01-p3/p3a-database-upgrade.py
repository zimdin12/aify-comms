"""A database written by one commit's code, then opened by another's: the N1 upgrade, end to end.

usage: python p3a-database-upgrade.py <repo checkout> <database file> produce|upgrade

`produce` runs inside a checkout of 4af344c5: it initialises the database and makes P3a's state, with
A owning coder and B's revision 1 applied with coder refused. `upgrade` runs inside the successor: it
initialises the same file twice, as two service starts would, then replays B's revision 1, publishes
revision 2 and replays that. Everything goes through the real router in-process (FastAPI TestClient on
a file database), with no network.
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

repo, database, phase = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, repo)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import service.db as db_module  # noqa: E402
from service.api_core.definition_snapshot import definition_digest, snapshot_digest  # noqa: E402
from service.routers.api_v2 import router  # noqa: E402

A = {"env": "win32:host-a:default", "machine": "win32:host-a", "bridge": "bridge-a"}
B = {"env": "win32:host-b:default", "machine": "win32:host-b", "bridge": "bridge-b"}


class _Ws:
    async def broadcast(self, *args, **kwargs):
        return None


def valid(agent_id):
    agent = {"id": agent_id, "name": agent_id, "role": "coder", "harness": "claude", "mode": "managed",
             "workspace": "/work", "model": "", "effort": "", "instructions": "", "env": {}, "herdrSpace": True}
    return {"id": agent_id, "state": "valid", "incarnation": 1, "revision": 1, "available": True,
            "definition": agent, "definitionDigest": definition_digest(agent)}


def main():
    inits = 1 if phase == "produce" else 2
    for _ in range(inits):
        asyncio.run(db_module.init_db(database))
    app = FastAPI()
    app.state.testing = True
    app.include_router(router, prefix="/api/v1")
    app.state.ws_manager = _Ws()
    app.state.config = SimpleNamespace(data_dir=tempfile.mkdtemp())
    client = TestClient(app)
    for host in (A, B):
        client.post("/api/v1/environments/heartbeat", json={
            "id": host["env"], "machineId": host["machine"], "os": "win32", "kind": "win32",
            "bridgeId": host["bridge"], "cwdRoots": ["/work"], "runtimes": [], "metadata": {}}).raise_for_status()

    def push(host, store, revision, entries, label):
        response = client.put(f"/api/v1/environments/{host['env']}/agent-definitions", json={
            "bridgeId": host["bridge"], "machineId": host["machine"], "storeId": store, "revision": revision,
            "snapshotDigest": snapshot_digest(entries), "entries": entries})
        print(f"{label}: HTTP {response.status_code} {response.text}")

    import sqlite3
    columns = [row[1] for row in sqlite3.connect(str(database)).execute("PRAGMA table_info(definition_stores)")]
    print(f"{phase}: inits={inits} definition_stores columns={columns}")
    if phase == "produce":
        push(A, "s1", 1, [valid("coder")], "A s1 rev1 [coder]")
        push(B, "t1", 1, [valid("coder"), valid("helper")], "B t1 rev1 [coder, helper]")
    else:
        push(B, "t1", 1, [valid("coder"), valid("helper")], "B t1 rev1 replay")
        push(B, "t1", 2, [valid("coder"), valid("helper")], "B t1 rev2 fresh")
        push(B, "t1", 2, [valid("coder"), valid("helper")], "B t1 rev2 replay")
    client.close()


main()
