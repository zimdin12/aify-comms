"""A database created by P3a (4af344c5) gains `definition_stores.outcome` at init (review of a171e8a3, N1).

P3a made the table without the column. Declared only in CREATE TABLE, the column never reached such a
database: init succeeded twice and the next replay was a 500, `no such column: outcome`. A fresh test
database cannot show that, so this one is made the shape P3a left it, and init then runs on it as the
service runs it. (evidence/2026-10-01-p3/p3a-database-upgrade.txt runs the same check on a database
written by 4af344c5's own code.)

WHAT P3a NEVER RECORDED STAYS UNKNOWN. The column arrives NULL, so the replay of a revision applied
before it says `outcomeRecorded: false` rather than reporting an empty refusal list as fact; the host's
next fresh revision records one.
"""
from __future__ import annotations

import asyncio
import sqlite3

import service.db as db_module
from service.tests._base import FastApiTestCase
from service.tests.test_agent_definition_push import A, B, snapshot_digest, valid


class AP3aDatabaseGainsTheReplayOutcome(FastApiTestCase):
    DB_NAME = "aify-test-p3a-upgrade.db"

    def setUp(self):
        super().setUp()
        for host in (A, B):
            beat = self.client.post("/api/v1/environments/heartbeat", json={
                "id": host["env"], "machineId": host["machine"], "os": "win32", "kind": "win32",
                "bridgeId": host["bridge"], "cwdRoots": ["/work"], "runtimes": [], "metadata": {}})
            self.assertEqual(beat.status_code, 200, beat.text)

    def push(self, host, store, revision, entries):
        return self.client.put(f"/api/v1/environments/{host['env']}/agent-definitions", json={
            "bridgeId": host["bridge"], "machineId": host["machine"], "storeId": store, "revision": revision,
            "snapshotDigest": snapshot_digest(entries), "entries": entries})

    def columns(self):
        conn = sqlite3.connect(str(self._db_path))
        try:
            return {row[1] for row in conn.execute("PRAGMA table_info(definition_stores)")}
        finally:
            conn.close()

    def test_init_adds_the_column_and_an_unrecorded_outcome_says_so(self):
        # P3a's state: A owns coder; B's revision 1 was applied with coder refused.
        self.assertEqual(self.push(A, "s1", 1, [valid("coder")]).status_code, 200)
        self.assertEqual(self.push(B, "t1", 1, [valid("coder"), valid("helper")]).json()["refused"][0]["id"], "coder")
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("ALTER TABLE definition_stores DROP COLUMN outcome")
        conn.commit()
        conn.close()
        self.assertNotIn("outcome", self.columns(), "control: the table is the shape P3a left it")
        broken = self.push(B, "t1", 1, [valid("coder"), valid("helper")])
        self.assertEqual(broken.status_code, 500, "control: without the column the replay fails, as reviewed")
        init = getattr(db_module, "_real_init_db", None) or db_module.init_db
        for _ in range(2):
            asyncio.run(init(self._db_path))
        self.assertIn("outcome", self.columns())
        replay = self.push(B, "t1", 1, [valid("coder"), valid("helper")])
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual((replay.json()["outcomeRecorded"], replay.json()["refused"]), (False, []),
                         "P3a never recorded this revision's refusal: unknown, not empty")
        fresh = self.push(B, "t1", 2, [valid("coder"), valid("helper")])
        self.assertEqual(fresh.json()["refused"], [{"id": "coder", "reason": "defined on win32:host-a"}])
        again = self.push(B, "t1", 2, [valid("coder"), valid("helper")]).json()
        self.assertEqual((again["outcomeRecorded"], again["refused"]), (True, fresh.json()["refused"]))
