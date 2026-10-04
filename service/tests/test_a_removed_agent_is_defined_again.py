"""An agent the operator removed is defined again by its host (found live 2026-10-04).

The operator removed `e2e-check-084` through a change request, then defined it again on its host. The
push refused the id for good ("was intentionally removed before"), nothing on the host said so, and no
0.8 path could clear the tombstone. A newer definition now restores it; a copy no newer than the one the
removal took is still refused, which is what the tombstone is for.
"""
from __future__ import annotations

import json
import sqlite3

from service.api_core.definition_push import FREE_SINCE
from service.tests._base import FastApiTestCase
from service.tests.test_agent_definition_push import A, snapshot_digest, valid


class ARemovedAgentIsDefinedAgain(FastApiTestCase):
    DB_NAME = "aify-test-removed-agent-defined-again.db"

    def setUp(self):
        super().setUp()
        beat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": A["env"], "machineId": A["machine"], "os": "win32", "kind": "win32",
            "bridgeId": A["bridge"], "cwdRoots": ["/work"], "runtimes": [], "metadata": {}})
        self.assertEqual(beat.status_code, 200, beat.text)

    def push(self, revision, entries, store="s1"):
        response = self.client.put(f"/api/v1/environments/{A['env']}/agent-definitions", json={
            "bridgeId": A["bridge"], "machineId": A["machine"], "storeId": store, "revision": revision,
            "snapshotDigest": snapshot_digest(entries), "entries": entries})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def rows(self, sql, params=()):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def execute(self, *statements):
        conn = sqlite3.connect(str(self._db_path))
        try:
            for sql, params in statements:
                conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def tombstones(self):
        return [r["agent_id"] for r in self.rows("SELECT agent_id FROM agent_tombstones")]

    def removed_by_request(self, incarnation):
        """Define `coder` at this incarnation, then remove it the operator's way: a request its host
        claims, applies (the next snapshot lacks it) and reports done."""
        self.push(1, [valid("coder", incarnation=incarnation)])
        asked = self.client.post("/api/v1/agent-definitions/coder/requests",
                                 json={"patch": {"remove": True}, "requestedBy": "dashboard"})
        self.assertEqual(asked.status_code, 200, asked.text)
        request_id = asked.json()["request"]["id"]
        self.client.post(f"/api/v1/environments/{A['env']}/definition-requests/claim",
                         json={"bridgeId": A["bridge"], "machineId": A["machine"]}).raise_for_status()
        self.push(2, [])
        done = self.client.post(f"/api/v1/environments/{A['env']}/definition-requests/{request_id}/result", json={
            "bridgeId": A["bridge"], "machineId": A["machine"], "status": "done",
            "resultIncarnation": incarnation, "resultRevision": 1})
        self.assertEqual(done.json()["request"]["consequence"], "removed", done.text)
        self.assertEqual((self.rows("SELECT id FROM agents WHERE id = 'coder'"), self.tombstones()), ([], ["coder"]),
                         "control: the removal ran and left its tombstone")

    def test_a_definition_newer_than_the_removed_one_defines_it_again(self):
        self.removed_by_request(incarnation=4)
        again = self.push(3, [valid("coder", incarnation=5)])
        self.assertEqual((again["applied"], again["refused"]), (["coder"], []))
        self.assertEqual(self.rows("SELECT definition_state FROM agents WHERE id = 'coder'"), [{"definition_state": "defined"}])
        self.assertEqual(self.tombstones(), [], "the tombstone goes, or the worker's own registration is refused")
        registered = self.client.post("/api/v1/agents", json={"agentId": "coder", "role": "coder", "machineId": A["machine"]})
        self.assertEqual(registered.status_code, 200, registered.text)

    def test_a_copy_no_newer_than_the_removed_one_stays_refused(self):
        self.removed_by_request(incarnation=4)
        stale = self.push(3, [valid("coder", incarnation=4), valid("other")])
        self.assertEqual(stale["refused"], [{"id": "coder", "reason": (
            "incarnation 4 is no newer than incarnation 4, which the operator removed; "
            "define it again (`aify-env agents set`) to restore it")}])
        self.assertEqual(stale["applied"], ["other"], "control: the rest of the snapshot applies")
        self.assertEqual((self.rows("SELECT id FROM agents WHERE id = 'coder'"), self.tombstones()), ([], ["coder"]))
        self.assertEqual(self.push(4, [valid("coder", incarnation=4)], store="s2")["applied"], ["coder"],
                         "the removal was taken from s1; a new store's numbering starts again")

    def test_a_copy_in_another_case_is_judged_as_the_same_id(self):
        """The tombstone is matched without case, so the removal history must be too: `Coder` at the removed
        incarnation cleared `coder`'s tombstone, and the next push restored the removed copy (review of cf4f5710)."""
        self.removed_by_request(incarnation=4)
        stale = self.push(3, [valid("Coder", incarnation=4)])
        self.assertEqual([r["id"] for r in stale["refused"]], ["Coder"])
        self.assertEqual(self.tombstones(), ["coder"], "the tombstone stays")
        again = self.push(4, [valid("coder", incarnation=4)])
        self.assertEqual(again["applied"], [], "the removed copy is not restored afterwards")

    def test_the_removed_copy_stays_refused_after_a_newer_one_cleared_the_tombstone(self):
        """The removal history is the guard, not the tombstone: once incarnation 5 defined the id again and
        cleared the tombstone, incarnation 4 was accepted (review of 78052e25, the B1 non-closure)."""
        self.removed_by_request(incarnation=4)
        self.assertEqual(self.push(3, [valid("coder", incarnation=5)])["applied"], ["coder"])
        self.assertEqual(self.tombstones(), [], "control: the newer definition cleared the tombstone")
        stale = self.push(4, [valid("Coder", incarnation=4)])
        self.assertEqual([r["id"] for r in stale["refused"]], ["Coder"], stale)
        self.assertEqual((self.rows("SELECT incarnation FROM agent_definitions"),
                          self.rows("SELECT definition_state FROM agents WHERE id = 'coder'")),
                         ([], [{"definition_state": "withdrawn"}]),
                         "the host no longer holds 5, and the removed copy does not take its place")
        self.assertEqual(self.push(5, [valid("coder", incarnation=6)])["applied"], ["coder"],
                         "control: a newer one still applies")

    def test_an_id_deleted_while_undefined_is_defined_by_its_host(self):
        """No removal request took a definition, so any definition the host writes is newer."""
        self.client.post("/api/v1/agents", json={"agentId": "retired-one", "role": "coder"}).raise_for_status()
        removed = self.client.request("DELETE", "/api/v1/agents/retired-one", json={"requestedBy": "dashboard"})
        self.assertEqual((removed.status_code, self.tombstones()), (200, ["retired-one"]), removed.text)
        result = self.push(1, [valid("retired-one")])
        self.assertEqual((result["applied"], result["refused"], self.tombstones()), (["retired-one"], [], []))

    def test_a_host_refused_under_the_old_rule_hears_its_id_is_free(self):
        """The live host's own state after a deploy: a revision whose outcome holds the refusal, replayed
        every minute. Its replay must say the id is free, so the host sends the fresh revision that takes it."""
        self.removed_by_request(incarnation=4)
        entries = [valid("coder", incarnation=5)]
        self.push(3, entries)
        self.execute(("DELETE FROM agent_definitions WHERE agent_id = 'coder'", ()),
                     ("DELETE FROM agents WHERE id = 'coder'", ()),
                     ("INSERT INTO agent_tombstones (agent_id, removed_at) VALUES ('coder', '2026-10-04T00:00:00Z')", ()),
                     ("UPDATE definition_stores SET outcome = ?",
                      (json.dumps({"refused": ["coder"], "invalid": [], "kept": []}),)))
        replay = self.push(3, entries)
        self.assertEqual((replay["outcome"], replay["refused"]), ("replay", [{"id": "coder", "reason": FREE_SINCE}]))
        self.assertEqual(self.tombstones(), ["coder"], "a replay changes nothing")
        self.assertEqual(self.push(4, entries)["applied"], ["coder"])
