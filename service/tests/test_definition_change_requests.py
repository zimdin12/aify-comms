"""An operator's change to a defined agent, queued for its host (P0 C4), on the service's side.

Each witness drives the real routes and reads `definition_requests` (and, for a removal, `agents`)
back. The host's half (apply, compare-and-set, the trash) is aify-env's, in P4.
"""
from __future__ import annotations

import asyncio
import sqlite3

from service.tests._base import FastApiTestCase
from service.tests.test_agent_definition_push import A, B, snapshot_digest, valid


class AChangeIsQueuedForItsHost(FastApiTestCase):
    DB_NAME = "aify-test-definition-requests.db"

    def setUp(self):
        super().setUp()
        for host in (A, B):
            beat = self.client.post("/api/v1/environments/heartbeat", json={
                "id": host["env"], "machineId": host["machine"], "os": "win32", "kind": "win32",
                "bridgeId": host["bridge"], "cwdRoots": ["/work"], "runtimes": [], "metadata": {}})
            self.assertEqual(beat.status_code, 200, beat.text)

    def push(self, store, revision, entries, host=A):
        response = self.client.put(f"/api/v1/environments/{host['env']}/agent-definitions", json={
            "bridgeId": host["bridge"], "machineId": host["machine"], "storeId": store, "revision": revision,
            "snapshotDigest": snapshot_digest(entries), "entries": entries})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def ask(self, agent_id, patch, requested_by="dashboard"):
        return self.client.post(f"/api/v1/agent-definitions/{agent_id}/requests",
                                json={"patch": patch, "requestedBy": requested_by})

    def claim(self, host=A):
        response = self.client.post(f"/api/v1/environments/{host['env']}/definition-requests/claim",
                                    json={"bridgeId": host["bridge"], "machineId": host["machine"]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["requests"]

    def report(self, request_id, host=A, **body):
        return self.client.post(f"/api/v1/environments/{host['env']}/definition-requests/{request_id}/result",
                                json={"bridgeId": host["bridge"], "machineId": host["machine"], **body})

    def rows(self, sql, params=()):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def execute(self, sql, params=()):
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def test_a_request_is_a_compare_and_set_on_what_the_service_holds(self):
        self.push("s1", 1, [valid("coder", incarnation=2, revision=5)])
        asked = self.ask("coder", {"role": "reviewer"})
        self.assertEqual(asked.status_code, 200, asked.text)
        request = asked.json()["request"]
        self.assertEqual({k: request[k] for k in ("machineId", "storeId", "expectedIncarnation", "expectedRevision", "status")},
                         {"machineId": A["machine"], "storeId": "s1", "expectedIncarnation": 2, "expectedRevision": 5,
                          "status": "pending"})
        second = self.ask("coder", {"model": "opus"})
        self.assertEqual(second.status_code, 409, "two edits from one pair: the second waits for the first")
        self.assertEqual(second.json()["detail"],
                         f'"coder" already has a change waiting for its host ({request["id"]}); ask again once that one is done')

    def test_only_the_operator_asks_and_only_for_a_defined_agent_with_a_patch_it_can_carry(self):
        self.push("s1", 1, [valid("coder")])
        self.assertEqual(self.ask("coder", {"role": "x"}, requested_by="some-agent").status_code, 403)
        missing = self.ask("ghost", {"role": "x"})
        self.assertEqual((missing.status_code, missing.json()["detail"]),
                         (404, '"ghost" is not defined by any host, so there is no definition to change'))
        for patch, problem in (({}, 'patch: an object of agent fields to change, or {"remove": true}'),
                               ({"id": "other"}, "patch: 'id' is not an agent field a request may change"),
                               ({"remove": False}, "patch: 'remove' is not an agent field a request may change"),
                               ({"remove": True, "role": "x"}, "patch: 'remove' is not an agent field a request may change"),
                               ({"remove": 1}, "patch: 'remove' is not an agent field a request may change")):
            with self.subTest(patch=patch):
                refused = self.ask("coder", patch)
                self.assertEqual(refused.status_code, 422, refused.text)
                self.assertIn(problem, refused.json()["detail"])
        self.assertEqual(self.rows("SELECT id FROM definition_requests"), [])

    def test_the_owning_hosts_current_claimer_claims_it_and_gets_it_again_until_it_reports(self):
        self.push("s1", 1, [valid("coder")])
        request_id = self.ask("coder", {"role": "reviewer"}).json()["request"]["id"]
        stranger = self.client.post(f"/api/v1/environments/{A['env']}/definition-requests/claim",
                                    json={"bridgeId": "bridge-old", "machineId": A["machine"]})
        self.assertEqual(stranger.status_code, 409, stranger.text)
        self.assertIn("definition request claim refused: not the current claimer", stranger.text)
        self.assertEqual(self.claim(host=B), [], "another machine is handed nothing")
        [claimed] = self.claim()
        self.assertEqual((claimed["id"], claimed["status"]), (request_id, "claimed"))
        self.assertEqual([r["id"] for r in self.claim()], [request_id], "unreported, so handed out again")

    def test_an_unclaimed_request_expires_and_frees_the_agent_for_another(self):
        self.push("s1", 1, [valid("coder")])
        first = self.ask("coder", {"role": "reviewer"}).json()["request"]["id"]
        self.execute("UPDATE definition_requests SET expires_at = '2000-01-01T00:00:00Z' WHERE id = ?", (first,))
        self.assertEqual(self.ask("coder", {"model": "opus"}).status_code, 200, "the expired one no longer waits")
        [expired] = self.rows("SELECT status, outcome FROM definition_requests WHERE id = ?", (first,))
        self.assertEqual(expired, {"status": "expired", "outcome": "no host claimed it within 10 minutes"})
        self.assertEqual([r["id"] for r in self.claim()], [r["id"] for r in self.rows(
            "SELECT id FROM definition_requests WHERE status = 'claimed'")], "control: the live one is claimable")

    def test_a_request_its_host_can_no_longer_apply_is_refused_at_the_claim(self):
        self.push("s1", 1, [valid("coder"), valid("helper"), valid("mover")])
        replaced = self.ask("coder", {"role": "a"}).json()["request"]["id"]
        self.push("s2", 1, [valid("helper"), valid("mover"), valid("coder")])
        self.assertEqual(self.claim(), [], "made for s1; the machine is on s2 now")
        moved = self.ask("mover", {"role": "b"}).json()["request"]["id"]
        withdrawn = self.ask("helper", {"role": "c"}).json()["request"]["id"]
        self.push("s2", 2, [valid("coder"), valid("mover")])
        self.client.post("/api/v1/agent-definitions/mover/release",
                         json={"requestedBy": "dashboard", "machineId": A["machine"]}).raise_for_status()
        self.push("t1", 1, [valid("mover")], host=B)
        self.assertEqual(self.claim(), [])
        outcomes = {r["id"]: (r["status"], r["outcome"]) for r in self.rows("SELECT id, status, outcome FROM definition_requests")}
        self.assertEqual(outcomes, {
            replaced: ("refused", "made for a store this machine has since replaced"),
            moved: ("refused", "the definition moved to win32:host-b before its host claimed this"),
            withdrawn: ("refused", "the definition was withdrawn before its host claimed this")})

    def test_a_definition_moved_to_another_machine_refuses_the_old_hosts_claim(self):
        self.push("s1", 1, [valid("coder")])
        request_id = self.ask("coder", {"role": "a"}).json()["request"]["id"]
        self.execute("UPDATE agent_definitions SET machine_id = ? WHERE agent_id = 'coder'", (B["machine"],))
        self.assertEqual(self.claim(), [])
        [row] = self.rows("SELECT status, outcome FROM definition_requests WHERE id = ?", (request_id,))
        self.assertEqual(row, {"status": "refused", "outcome": "the definition moved to win32:host-b before its host claimed this"})

    def test_a_report_is_fenced_idempotent_and_final(self):
        self.push("s1", 1, [valid("coder")])
        request_id = self.ask("coder", {"role": "reviewer"}).json()["request"]["id"]
        self.claim()
        unnamed = self.report(request_id, status="done")
        self.assertEqual(unnamed.status_code, 422, unnamed.text)
        self.assertIn("resultIncarnation, resultRevision: a done request names the lifetime and revision it left", unnamed.text)
        self.assertEqual(self.report(request_id, status="maybe").status_code, 422)
        self.assertIn("definition request report refused: not the current claimer",
                      self.report(request_id, host={**A, "bridge": "bridge-old"}, status="done",
                                  resultIncarnation=1, resultRevision=2).text)
        self.assertIn(f"definition request {request_id} is for win32:host-a",
                      self.report(request_id, host=B, status="done", resultIncarnation=1, resultRevision=2).text)
        unknown = self.client.post(f"/api/v1/environments/{A['env']}/definition-requests/nope/result", json={
            "bridgeId": A["bridge"], "machineId": A["machine"], "status": "refused"})
        self.assertEqual((unknown.status_code, unknown.json()["detail"]), (404, "no definition request nope"))
        done = self.report(request_id, status="done", outcome="applied", resultIncarnation=1, resultRevision=2)
        self.assertEqual((done.status_code, done.json()["request"]["status"]), (200, "done"))
        again = self.report(request_id, status="done", outcome="applied", resultIncarnation=1, resultRevision=2)
        self.assertEqual(again.status_code, 200, "a lost acknowledgement, reported again, costs nothing")
        changed = self.report(request_id, status="refused", outcome="changed my mind")
        self.assertEqual((changed.status_code, changed.json()["detail"]), (409, f"definition request {request_id} is already done"))
        self.assertEqual(self.claim(), [], "a finished request is not handed out")

    def test_a_removal_done_while_the_definition_is_held_removes_the_agent(self):
        self.push("s1", 1, [valid("coder")])
        request_id = self.ask("coder", {"remove": True}).json()["request"]["id"]
        self.claim()
        done = self.report(request_id, status="done", resultIncarnation=1, resultRevision=1)
        self.assertEqual((done.status_code, done.json()["removed"]), (200, True), done.text)
        self.assertEqual(self.rows("SELECT id FROM agents WHERE id = 'coder'"), [])
        self.assertEqual(len(self.rows("SELECT agent_id FROM agent_tombstones WHERE agent_id = 'coder'")), 1)

    def test_a_removal_done_after_its_own_withdrawal_landed_still_removes_the_agent(self):
        self.push("s1", 1, [valid("coder"), valid("other")])
        request_id = self.ask("coder", {"remove": True}).json()["request"]["id"]
        self.claim()
        self.push("s1", 2, [valid("other")])
        done = self.report(request_id, status="done", resultIncarnation=1, resultRevision=1)
        self.assertEqual(done.json()["removed"], True, done.text)

    def _remove(self, agent_id, answers):
        """`remove_agent` with a fence that gives these answers in turn, on the service's own connection."""
        from service.api_core.agent_remove import remove_agent
        from service.db import get_db

        asked = []

        async def fence(_db):
            asked.append(len(asked))
            return answers[min(len(asked) - 1, len(answers) - 1)]

        async def go():
            db = await get_db()
            try:
                return await remove_agent(db, agent_id, actor="test", reason="test", refusal=fence)
            finally:
                await db.close()

        return asyncio.run(go()), len(asked)

    def test_the_removal_fence_is_asked_again_inside_the_deleting_transaction(self):
        self.client.post("/api/v1/agents", json={"agentId": "kept", "role": "coder"}).raise_for_status()
        (deleted, why), asked = self._remove("kept", ["", "custody moved"])
        self.assertEqual((deleted, why, asked), (0, "custody moved", 2))
        self.assertEqual(len(self.rows("SELECT id FROM agents WHERE id = 'kept'")), 1, "a late refusal removes nothing")
        (deleted, why), _ = self._remove("kept", [""])
        self.assertEqual((deleted, why), (1, ""), "control: a fence that allows it removes the agent")

    def test_a_removal_refused_at_the_first_asking_stops_no_worker(self):
        self.client.post("/api/v1/agents", json={"agentId": "worker", "role": "coder", "runtime": "codex",
                                                  "sessionMode": "managed"}).raise_for_status()
        before = self.rows("SELECT status FROM agents WHERE id = 'worker'")[0]["status"]
        (deleted, why), asked = self._remove("worker", ["not this one"])
        self.assertEqual((deleted, why, asked), (0, "not this one", 1))
        self.assertEqual(self.rows("SELECT status FROM agents WHERE id = 'worker'")[0]["status"], before,
                         "the managed worker was not told to stop")
        self.assertNotEqual(before, "stopped", "control: the worker was not stopped to begin with")

    def test_a_removal_whose_definition_was_released_removes_nothing(self):
        """A release is the operator's, not the store's: a host's later `done` finds no withdrawal by
        that store at that lifetime, and removes nothing."""
        self.push("s1", 1, [valid("coder")])
        request_id = self.ask("coder", {"remove": True}).json()["request"]["id"]
        self.claim()
        self.client.post("/api/v1/agent-definitions/coder/release",
                         json={"requestedBy": "dashboard", "machineId": A["machine"]}).raise_for_status()
        done = self.report(request_id, status="done", resultIncarnation=1, resultRevision=1)
        self.assertEqual(done.json()["removed"], False, done.text)
        self.assertEqual(done.json()["request"]["outcome"],
                         "[service: the definition this removal was for ended another way; nothing removed]")
        self.assertEqual(len(self.rows("SELECT id FROM agents WHERE id = 'coder'")), 1)

    def test_a_delayed_removal_for_an_earlier_lifetime_removes_nothing_and_says_so(self):
        self.push("s1", 1, [valid("coder")])
        request_id = self.ask("coder", {"remove": True}).json()["request"]["id"]
        self.claim()
        # The id is removed and made again on the host: lifetime 2, before the old removal's report lands.
        self.push("s1", 2, [])
        self.push("s1", 3, [valid("coder", incarnation=2)])
        done = self.report(request_id, status="done", outcome="removed", resultIncarnation=1, resultRevision=1)
        self.assertEqual((done.status_code, done.json()["removed"]), (200, False), done.text)
        self.assertEqual(done.json()["request"]["outcome"],
                         "removed [service: the definition is now win32:host-a store s1 lifetime 2, not the one this "
                         "removal was for; nothing removed]")
        self.assertEqual(len(self.rows("SELECT id FROM agents WHERE id = 'coder'")), 1, "the new lifetime's agent stays")
