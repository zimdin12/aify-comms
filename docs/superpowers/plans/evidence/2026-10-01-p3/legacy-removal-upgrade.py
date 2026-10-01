"""Done removals written by 12766276's code, then opened by the successor (review of c8029614, N5).

usage: python legacy-removal-upgrade.py <repo checkout> <database file> produce|upgrade

`produce` runs inside a checkout of 12766276 and, through its real routes, leaves three done removals
of the three kinds that code could record:
- `gone`: removed. The agent row is deleted and tombstoned, and the outcome carries no service note.
- `keeper`: the service refused. Its definition was released first, so the removal fence refused, and
  12766276 appended `[service: <why>]` to the outcome. The same store then defines `keeper` again at the
  same lifetime, so a fence asked now would allow.
- `coder`: never carried out. The removal was made to fail after that code had committed the host's
  report, so the agent row exists and the outcome has no note.

`upgrade` runs inside the successor: it initialises the same file twice, as two service starts would,
then repeats each identical report. Everything goes through the real router in-process (FastAPI
TestClient on a file database), with no network.
"""
import asyncio
import json
import sqlite3
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
HOST = {"bridgeId": A["bridge"], "machineId": A["machine"]}
IDS = ("gone", "keeper", "coder")


class _Ws:
    async def broadcast(self, *args, **kwargs):
        return None


def valid(agent_id):
    agent = {"id": agent_id, "name": agent_id, "role": "coder", "harness": "claude", "mode": "managed",
             "workspace": "/work", "model": "", "effort": "", "instructions": "", "env": {}, "herdrSpace": True}
    return {"id": agent_id, "state": "valid", "incarnation": 1, "revision": 1, "available": True,
            "definition": agent, "definitionDigest": definition_digest(agent)}


def state(label):
    conn = sqlite3.connect(str(database))
    conn.row_factory = sqlite3.Row
    columns = [row[1] for row in conn.execute("PRAGMA table_info(definition_requests)")]
    print(f"{label} (consequence column: {'consequence' in columns})")
    for agent_id in IDS:
        request = dict(conn.execute(
            "SELECT status, outcome" + (", consequence" if "consequence" in columns else "")
            + " FROM definition_requests WHERE agent_id = ?", (agent_id,)).fetchone())
        row = conn.execute("SELECT 1 FROM agents WHERE id = ?", (agent_id,)).fetchone() is not None
        tomb = conn.execute("SELECT 1 FROM agent_tombstones WHERE agent_id = ?", (agent_id,)).fetchone() is not None
        held = conn.execute("SELECT store_id, incarnation FROM agent_definitions WHERE agent_id = ?", (agent_id,)).fetchone()
        print(f"  {agent_id}: request={request} agent row={row} tombstone={tomb} "
              f"definition={tuple(held) if held else None}")
    conn.close()


def main():
    inits = 1 if phase == "produce" else 2
    for _ in range(inits):
        asyncio.run(db_module.init_db(database))
    app = FastAPI()
    app.state.testing = True
    app.include_router(router, prefix="/api/v1")
    app.state.ws_manager = _Ws()
    app.state.config = SimpleNamespace(data_dir=tempfile.mkdtemp())
    client = TestClient(app, raise_server_exceptions=False)
    done = {**HOST, "status": "done", "resultIncarnation": 1, "resultRevision": 1}
    ids_file = Path(str(database) + ".requests.json")

    def report(request_id):
        return client.post(f"/api/v1/environments/{A['env']}/definition-requests/{request_id}/result", json=done)

    def push(revision, entries):
        client.put(f"/api/v1/environments/{A['env']}/agent-definitions", json={
            **HOST, "storeId": "s1", "revision": revision, "snapshotDigest": snapshot_digest(entries),
            "entries": entries}).raise_for_status()

    if phase == "produce":
        client.post("/api/v1/environments/heartbeat", json={
            "id": A["env"], "machineId": A["machine"], "os": "win32", "kind": "win32", "bridgeId": A["bridge"],
            "cwdRoots": ["/work"], "runtimes": [], "metadata": {}}).raise_for_status()
        push(1, [valid(agent_id) for agent_id in IDS])
        requests = {agent_id: client.post(f"/api/v1/agent-definitions/{agent_id}/requests", json={
            "patch": {"remove": True}, "requestedBy": "dashboard"}).json()["request"]["id"] for agent_id in IDS}
        client.post(f"/api/v1/environments/{A['env']}/definition-requests/claim", json=HOST).raise_for_status()
        print(f"produce gone: HTTP {report(requests['gone']).status_code}")
        client.post("/api/v1/agent-definitions/keeper/release",
                    json={"requestedBy": "dashboard", "machineId": A["machine"]}).raise_for_status()
        print(f"produce keeper (released first): HTTP {report(requests['keeper']).status_code}")
        import service.routers.definition_requests as result_route

        async def interrupted(*args, **kwargs):
            raise RuntimeError("the service went away before the removal")

        real, result_route.remove_agent = result_route.remove_agent, interrupted  # 12766276 calls it from the router
        print(f"produce coder (removal fails): HTTP {report(requests['coder']).status_code}")
        result_route.remove_agent = real
        # The host removed gone's and coder's files, as it reported; the same store defines keeper again,
        # at the same lifetime.
        push(2, [valid("keeper")])
        ids_file.write_text(json.dumps(requests))
        state("produce, final")
    else:
        requests = json.loads(ids_file.read_text())
        state(f"upgrade, after {inits} inits")
        for agent_id in IDS:
            response = report(requests[agent_id])
            print(f"upgrade {agent_id}: the identical report -> HTTP {response.status_code} "
                  f"consequence={response.json().get('request', {}).get('consequence')!r}")
        state("upgrade, after the identical reports")
    client.close()


main()
