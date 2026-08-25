"""Listing spawn requests attaches each row's OWN spec, and reads them in one query.

The handler used to run `SELECT * FROM spawn_specs WHERE id = ?` inside the row loop. At the
dashboard's limit=200 that is 200 extra round trips on every poll, about every 15 seconds, on a
service that is deliberately single-worker and whose recurring failure is write-lock contention.
Measured 2026-08-25 before the change: 6.1ms at limit=1 against 74.3ms at limit=200.

Batching introduces one failure mode the per-row version could not have: a map keyed or ordered wrong
attaches spec A to request B. Nothing raises, and every row still carries a plausible spec -- so the
tests here pair each request with its OWN distinct spec and check the pairing, rather than checking
that a spec is present. A test asserting "spawnSpec is not None" would pass a fully shuffled result.
"""
from __future__ import annotations

import asyncio

import aiosqlite

from service.tests._base import FastApiTestCase

ENVIRONMENT_ID = "env-list-pairs"
BRIDGE_ID = "bridge-list-pairs"


class SpawnRequestListPairsEachSpec(FastApiTestCase):
    DB_NAME = "spawn-list-pairs.db"

    def setUp(self):
        super().setUp()
        response = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": ENVIRONMENT_ID,
                "label": "Linux on test-host",
                "machineId": "linux:test-host",
                "os": "linux",
                "kind": "linux",
                "bridgeId": BRIDGE_ID,
                "cwdRoots": ["/workspace"],
                "runtimes": [{"runtime": "codex", "available": True}],
                "status": "online",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _create(self, workspace: str) -> str:
        """One spawn request, and with it one spec. The workspace differs per request so each row's
        spec is DISTINGUISHABLE -- which is the whole point."""
        response = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "environmentId": ENVIRONMENT_ID,
                "agentId": "lc-worker",
                "runtime": "codex",
                "workspace": workspace,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["spawnRequest"]["id"]

    def _list(self, limit: int = 100) -> list[dict]:
        response = self.client.get(f"/api/v1/spawn-requests?limit={limit}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["spawnRequests"]

    def test_each_request_carries_its_own_spec(self):
        wanted = {self._create(f"/workspace/proj-{i}"): f"/workspace/proj-{i}" for i in range(6)}
        by_id = {row["id"]: row for row in self._list()}
        for request_id, workspace in wanted.items():
            self.assertIn(request_id, by_id)
            spec = by_id[request_id].get("spawnSpec")
            self.assertIsNotNone(spec, f"{request_id} lost its spec entirely")
            self.assertEqual(
                spec.get("workspace"), workspace,
                f"{request_id} was given another request's spec -- the batch keyed wrong",
            )

    def test_a_request_whose_spec_is_gone_reports_no_spec(self):
        """The per-row version produced None here. A batched `.get()` must too, rather than raising
        or silently borrowing a neighbour's."""
        request_id = self._create("/workspace/orphan")
        rows = {row["id"]: row for row in self._list()}
        spec_id = rows[request_id].get("spawnSpecId")
        self.assertTrue(spec_id, "the fixture did not produce a spec to delete")

        async def delete():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("DELETE FROM spawn_specs WHERE id = ?", (spec_id,))
                await db.commit()

        asyncio.run(delete())
        after = {row["id"]: row for row in self._list()}
        self.assertIn(request_id, after, "the request vanished along with its spec")
        self.assertIsNone(after[request_id].get("spawnSpec"))

    def test_many_rows_still_pair_correctly(self):
        """The dashboard asks for 200. A bug that only appears past a batch boundary would hide in a
        six-row test."""
        wanted = {self._create(f"/workspace/bulk-{i}"): f"/workspace/bulk-{i}" for i in range(40)}
        by_id = {row["id"]: row for row in self._list(limit=200)}
        mismatched = [
            request_id for request_id, workspace in wanted.items()
            if (by_id.get(request_id, {}).get("spawnSpec") or {}).get("workspace") != workspace
        ]
        self.assertEqual(mismatched, [], f"{len(mismatched)} of 40 rows carried the wrong spec")

    def test_an_empty_list_does_not_run_a_spec_query_at_all(self):
        """`WHERE id IN ()` is a syntax error in sqlite, so the empty case has to be guarded. This is
        the branch a happy-path test never reaches."""
        rows = self._list()
        self.assertEqual(rows, [])
