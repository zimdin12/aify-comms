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

    def test_a_12766276_database_gains_the_removal_consequence(self):
        """The same, for `definition_requests.consequence`, which 12766276 made the table without."""
        self.assertEqual(self.push(A, "s1", 1, [valid("coder")]).status_code, 200)
        asked = self.client.post("/api/v1/agent-definitions/coder/requests",
                                 json={"patch": {"remove": True}, "requestedBy": "dashboard"})
        request_id = asked.json()["request"]["id"]
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("ALTER TABLE definition_requests DROP COLUMN consequence")
        conn.commit()
        conn.close()
        init = getattr(db_module, "_real_init_db", None) or db_module.init_db
        for _ in range(2):
            asyncio.run(init(self._db_path))
        self.client.post(f"/api/v1/environments/{A['env']}/definition-requests/claim",
                         json={"bridgeId": A["bridge"], "machineId": A["machine"]}).raise_for_status()
        done = self.client.post(f"/api/v1/environments/{A['env']}/definition-requests/{request_id}/result", json={
            "bridgeId": A["bridge"], "machineId": A["machine"], "status": "done", "resultIncarnation": 1, "resultRevision": 1})
        self.assertEqual((done.status_code, done.json()["request"]["consequence"]), (200, "removed"), done.text)

    def test_a_12766276_database_keeps_what_each_receipt_recorded(self):
        """Review of c8029614, N5: 12766276 committed a host's `done` before removing, and recorded only a
        refusal, as a note on the outcome. Its receipts migrate to what they recorded: the note's refusal
        is kept (even once a fence asked now would allow), a tombstone with no row is `removed`, and a
        receipt that recorded nothing is owed, so the next report removes. Anything not a done removal
        is ''. (evidence/2026-10-01-p3/legacy-removal-upgrade.txt makes the same receipts with
        12766276's own code.)"""
        import service.api_core.definition_requests as requests_module

        ids_ = ("gone", "keeper", "coder", "other")
        self.assertEqual(self.push(A, "s1", 1, [valid(agent_id) for agent_id in ids_]).status_code, 200)
        ids = {}
        for agent_id in ids_:
            patch = {"model": "m2"} if agent_id == "other" else {"remove": True}
            ids[agent_id] = self.client.post(f"/api/v1/agent-definitions/{agent_id}/requests", json={
                "patch": patch, "requestedBy": "dashboard"}).json()["request"]["id"]
        self.client.post(f"/api/v1/environments/{A['env']}/definition-requests/claim",
                         json={"bridgeId": A["bridge"], "machineId": A["machine"]}).raise_for_status()
        result = lambda agent_id: self.client.post(  # noqa: E731
            f"/api/v1/environments/{A['env']}/definition-requests/{ids[agent_id]}/result", json={
                "bridgeId": A["bridge"], "machineId": A["machine"], "status": "done", "resultIncarnation": 1,
                "resultRevision": 2 if agent_id == "other" else 1})
        self.assertEqual(result("gone").status_code, 200)
        self.client.post("/api/v1/agent-definitions/keeper/release",
                         json={"requestedBy": "dashboard", "machineId": A["machine"]}).raise_for_status()
        self.assertEqual(result("keeper").status_code, 200)
        real = requests_module.remove_agent

        async def interrupted(*args, **kwargs):
            raise RuntimeError("the service went away before the removal")

        requests_module.remove_agent = interrupted
        try:
            self.assertEqual(result("coder").status_code, 500, "control: the done committed, the removal did not run")
        finally:
            requests_module.remove_agent = real
        self.assertEqual(result("other").status_code, 200)
        # Before the column goes: today's push reads it (the removal history counts only removals that took
        # the definition), and a service always migrates before it serves. The push touches no request row.
        self.assertEqual(self.push(A, "s1", 2, [valid("keeper")]).status_code, 200,
                         "control: the same store defines keeper again at the same lifetime")
        # The table as 12766276 left it: no column, and its refusal written as a note on the outcome.
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("ALTER TABLE definition_requests DROP COLUMN consequence")
        conn.execute("UPDATE definition_requests SET outcome = '[service: the definition this removal was for "
                     "ended another way; nothing removed]' WHERE id = ?", (ids["keeper"],))
        conn.commit()
        conn.close()
        init = getattr(db_module, "_real_init_db", None) or db_module.init_db
        for _ in range(2):
            asyncio.run(init(self._db_path))
        conn = sqlite3.connect(str(self._db_path))
        owed = dict(conn.execute("SELECT agent_id, consequence FROM definition_requests").fetchall())
        conn.close()
        kept = "nothing removed: the definition this removal was for ended another way"
        self.assertEqual(owed, {"gone": "removed", "keeper": kept, "coder": "pending", "other": ""})
        for agent_id in ("gone", "keeper", "coder"):
            self.assertEqual(result(agent_id).status_code, 200)
        conn = sqlite3.connect(str(self._db_path))
        owed = dict(conn.execute("SELECT agent_id, consequence FROM definition_requests").fetchall())
        present = sorted(row[0] for row in conn.execute("SELECT id FROM agents WHERE id IN ('gone', 'keeper', 'coder')"))
        conn.close()
        self.assertEqual((owed, present), ({"gone": "removed", "keeper": kept, "coder": "removed", "other": ""}, ["keeper"]))
