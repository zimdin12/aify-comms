"""Each dashboard slice names every table its endpoint reads.

The dashboard refetches a slice when a commit touched a table the slice names
(service/new_dashboard/slice-tables.mjs). A table the endpoint reads and the map omits is a panel that
stops updating when that table changes -- silently, because nothing polls any more. So this test runs
each slice's real endpoint against a seeded database, records every table SQLite reads while compiling
its statements (the authorizer, an independent reading of the SQL), and fails on any table the slice
does not name.

WHAT IT CANNOT SEE: a query the endpoint only issues for data this seed does not create. The map errs
wide for that reason, and a table added to an endpoint's reads by new code is caught only when the seed
reaches it.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import service.db as db_module
from service.tests._base import FastApiTestCase

DASHBOARD = Path(__file__).resolve().parents[1] / "new_dashboard"

#: Each slice's request, as slice-loaders.mjs and the modules it calls issue it. Checked against their
#: source below so a changed path fails here rather than testing a request the dashboard never makes.
SLICE_REQUESTS = {
    "agents": ("/agents", "slice-loaders.mjs"),
    "contracts": ("/contracts?limit=80", "slice-loaders.mjs"),
    "messages": ("/messages/recent?limit=", "slice-loaders.mjs"),
    "runs": ("/dispatch/runs?", "run-helpers.mjs"),
    "sessions": ("/sessions?limit=80", "slice-loaders.mjs"),
    "environments": ("/environments", "slice-loaders.mjs"),
    "spawnRequests": ("/spawn-requests?limit=200", "slice-loaders.mjs"),
    "stats": ("/stats", "slice-loaders.mjs"),
    "settings": ("/settings", "slice-loaders.mjs"),
    "channels": ("/channels?agentId=", "message-transport.mjs"),
    "conversation": ("/channels/${encodeURIComponent(name)}?limit=80&agentId=", "message-transport.mjs"),
    "files": ("/shared", "shared-files.mjs"),
}

#: The concrete URL each request resolves to against the seed below.
CONCRETE = {
    "agents": "/api/v1/agents",
    "contracts": "/api/v1/contracts?limit=80",
    "messages": "/api/v1/messages/recent?limit=80",
    "runs": "/api/v1/dispatch/runs?limit=80",
    "sessions": "/api/v1/sessions?limit=80",
    "environments": "/api/v1/environments",
    "spawnRequests": "/api/v1/spawn-requests?limit=200",
    "stats": "/api/v1/stats",
    "settings": "/api/v1/settings",
    "channels": "/api/v1/channels?agentId=alice",
    "conversation": "/api/v1/channels/room?limit=80&agentId=alice",
    "files": "/api/v1/shared",
}


def _slice_tables() -> dict:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node is not on PATH, so slice-tables.mjs cannot be read")
    module = (DASHBOARD / "slice-tables.mjs").as_uri()
    out = subprocess.run(
        [node, "--input-type=module", "-e",
         f"const m = await import({json.dumps(module)}); console.log(JSON.stringify(m.SLICE_TABLES));"],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return json.loads(out.stdout)


class TheDashboardSlicesNameEveryTableTheyRead(FastApiTestCase):
    DB_NAME = "slices.db"

    def _seed(self):
        for agent in ("alice", "bob"):
            response = self.client.post("/api/v1/agents", json={"agentId": agent, "role": "coder"})
            self.assertEqual(response.status_code, 200, response.text)
        steps = [
            ("/api/v1/messages/send", {"from_agent": "alice", "to": "bob", "type": "request", "subject": "s", "body": "hi"}),
            ("/api/v1/dispatch", {"from_agent": "alice", "to": "bob", "type": "request", "subject": "t", "body": "do", "requireReply": True}),
            ("/api/v1/channels", {"name": "room", "description": "", "createdBy": "alice"}),
            ("/api/v1/channels/room/join", {"agentId": "bob"}),
            ("/api/v1/channels/room/send", {"from_agent": "alice", "channel": "room", "body": "hello"}),
            ("/api/v1/environments/heartbeat", {
                "id": "linux:test-host:default", "label": "Linux on test-host", "machineId": "linux:test-host",
                "os": "linux", "kind": "linux", "bridgeId": "bridge-current", "cwdRoots": ["/workspace"],
                "runtimes": [{"runtime": "codex", "modes": ["managed-warm"], "capabilities": {}}], "metadata": {},
            }),
        ]
        for path, body in steps:
            response = self.client.post(path, json=body)
            self.assertLess(response.status_code, 300, f"seed step {path} failed: {response.text}")

    def _tables_read(self, url: str) -> set:
        seen: set = set()

        def authorizer(action, arg1, _arg2, _db, _trigger):
            if action == sqlite3.SQLITE_READ and arg1:
                seen.add(arg1.lower())
            return sqlite3.SQLITE_OK

        original = db_module.CONNECTION_POOL._open

        async def opening(path, busy_timeout_ms):
            conn = await original(path, busy_timeout_ms)
            await conn.set_authorizer(authorizer)
            return conn

        with mock.patch.object(db_module.CONNECTION_POOL, "_open", opening):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200, f"{url}: {response.text}")
        return {table for table in seen if not table.startswith("sqlite_")}

    def test_every_request_path_is_the_one_the_dashboard_issues(self):
        missing = [f"{slice_}: {needle!r} not in {source}" for slice_, (needle, source) in SLICE_REQUESTS.items()
                   if needle not in (DASHBOARD / source).read_text(encoding="utf-8")]
        self.assertEqual(missing, [])
        self.assertEqual(set(SLICE_REQUESTS), set(_slice_tables()), "a slice has no request here, or a request no slice")

    def test_no_endpoint_reads_a_table_its_slice_does_not_name(self):
        declared = _slice_tables()
        self._seed()
        undeclared = {}
        for slice_, url in CONCRETE.items():
            read = self._tables_read(url)
            if slice_ in ("agents", "messages"):
                # Positive control on the instrument: these two cannot answer without their own table.
                self.assertIn(slice_, read, f"control: {url} read no {slice_} table, so the recorder is blind")
            extra = sorted(read - set(declared[slice_]))
            if extra:
                undeclared[slice_] = extra
        self.assertEqual(undeclared, {}, "tables read by an endpoint that its slice does not name")

    def test_CONTROL_a_slice_missing_a_table_it_reads_is_reported(self):
        # Drive it by REMOVING what it watches: the same comparison, with the table the files slice needs
        # taken out of its declaration, must name that table.
        declared = {slice_: set(tables) for slice_, tables in _slice_tables().items()}
        declared["files"].discard("shared_artifacts")
        self._seed()
        read = self._tables_read(CONCRETE["files"])
        self.assertIn("shared_artifacts", sorted(read - declared["files"]))


if __name__ == "__main__":
    unittest.main()
