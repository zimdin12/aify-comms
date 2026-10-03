"""An operator's change to a defined agent, queued for its host (P0 C4), on the service's side.

Each witness drives the real routes and reads `definition_requests` (and, for a removal, `agents`)
back. The host's half (apply, compare-and-set, the trash) is aify-env's, in P4.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading

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
                         f'"coder" already has a change waiting for its host ({request["id"]}); ask again once that one is done. If that host is gone for good, release the definition from it (POST /agent-definitions/coder/release)')

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
        self.assertEqual((done.status_code, done.json()["request"]["consequence"]), (200, "removed"), done.text)
        self.assertEqual(self.rows("SELECT id FROM agents WHERE id = 'coder'"), [])
        self.assertEqual(len(self.rows("SELECT agent_id FROM agent_tombstones WHERE agent_id = 'coder'")), 1)

    def test_a_removal_done_after_its_own_withdrawal_landed_still_removes_the_agent(self):
        self.push("s1", 1, [valid("coder"), valid("other")])
        request_id = self.ask("coder", {"remove": True}).json()["request"]["id"]
        self.claim()
        self.push("s1", 2, [valid("other")])
        done = self.report(request_id, status="done", resultIncarnation=1, resultRevision=1)
        self.assertEqual(done.json()["request"]["consequence"], "removed", done.text)

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

    def test_the_removal_fence_is_asked_again_after_a_managed_workers_stop(self):
        """A managed agent's stop commits and waits for its host, so custody can move before the delete:
        the fence is asked again inside the deleting transaction."""
        self.client.post("/api/v1/agents", json={"agentId": "kept", "role": "coder", "runtime": "codex",
                                                  "sessionMode": "managed"}).raise_for_status()
        (deleted, why), asked = self._remove("kept", ["", "custody moved"])
        self.assertEqual((deleted, why, asked), (0, "custody moved", 2))
        self.assertEqual(len(self.rows("SELECT id FROM agents WHERE id = 'kept'")), 1, "a late refusal removes nothing")
        (deleted, why), _ = self._remove("kept", [""])
        self.assertEqual((deleted, why), (1, ""), "control: a fence that allows it removes the agent")

    def test_with_no_stop_one_asking_covers_the_delete(self):
        """An unmanaged agent has no stop to wait for: the fence and the delete are one transaction."""
        self.client.post("/api/v1/agents", json={"agentId": "plain", "role": "coder"}).raise_for_status()
        (deleted, why), asked = self._remove("plain", [""])
        self.assertEqual((deleted, why, asked), (1, "", 1))

    def test_a_removal_refused_at_the_first_asking_stops_no_worker(self):
        self.client.post("/api/v1/agents", json={"agentId": "worker", "role": "coder", "runtime": "codex",
                                                  "sessionMode": "managed"}).raise_for_status()
        before = self.rows("SELECT status FROM agents WHERE id = 'worker'")[0]["status"]
        (deleted, why), asked = self._remove("worker", ["not this one"])
        self.assertEqual((deleted, why, asked), (0, "not this one", 1))
        self.assertEqual(self.rows("SELECT status FROM agents WHERE id = 'worker'")[0]["status"], before,
                         "the managed worker was not told to stop")
        self.assertNotEqual(before, "stopped", "control: the worker was not stopped to begin with")

    def test_a_report_skips_no_claim(self):
        """Review of 12766276, 1: the claim is where an undeliverable request is refused, so a report of
        a request nobody claimed, or one that expired unclaimed, finishes nothing and removes nothing."""
        self.push("s1", 1, [valid("coder"), valid("other")])
        unclaimed = self.ask("coder", {"remove": True}).json()["request"]["id"]
        skipped = self.report(unclaimed, status="done", resultIncarnation=1, resultRevision=1)
        self.assertEqual((skipped.status_code, skipped.json()["detail"]),
                         (409, f"definition request {unclaimed} was never claimed; claim it first"))
        self.execute("UPDATE definition_requests SET expires_at = '2000-01-01T00:00:00Z' WHERE id = ?", (unclaimed,))
        expired = self.report(unclaimed, status="done", resultIncarnation=1, resultRevision=1)
        self.assertEqual((expired.status_code, expired.json()["detail"]),
                         (409, f"definition request {unclaimed} is already expired"))
        self.assertEqual(len(self.rows("SELECT id FROM agents WHERE id = 'coder'")), 1, "nothing was removed")

    def test_a_repeated_report_runs_its_consequences_once(self):
        """Review of 12766276, 2: the same report again changes no byte of the request."""
        self.push("s1", 1, [valid("coder")])
        request_id = self.ask("coder", {"remove": True}).json()["request"]["id"]
        self.claim()
        self.client.post("/api/v1/agent-definitions/coder/release",
                         json={"requestedBy": "dashboard", "machineId": A["machine"]}).raise_for_status()
        first = self.report(request_id, status="done", resultIncarnation=1, resultRevision=1)
        self.assertTrue(first.json()["request"]["consequence"].startswith("nothing removed: "), first.text)
        recorded = self.rows("SELECT * FROM definition_requests WHERE id = ?", (request_id,))
        again = self.report(request_id, status="done", resultIncarnation=1, resultRevision=1)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(self.rows("SELECT * FROM definition_requests WHERE id = ?", (request_id,)), recorded)

    def test_a_removal_interrupted_after_its_done_was_recorded_is_finished_by_the_next_report(self):
        """Review of 12766276: the consequence is owed until settled. A failure between recording the
        host's `done` and removing the agent leaves it pending, and the repeated report finishes it."""
        import service.api_core.definition_requests as result_route

        self.push("s1", 1, [valid("coder")])
        request_id = self.ask("coder", {"remove": True}).json()["request"]["id"]
        self.claim()
        real = result_route.remove_agent

        async def interrupted(*args, **kwargs):
            raise RuntimeError("the service went away before the removal")

        result_route.remove_agent = interrupted
        try:
            failed = self.report(request_id, status="done", resultIncarnation=1, resultRevision=1)
        finally:
            result_route.remove_agent = real
        self.assertEqual(failed.status_code, 500, "control: the removal step failed after the done was recorded")
        [row] = self.rows("SELECT status, consequence FROM definition_requests WHERE id = ?", (request_id,))
        self.assertEqual(row, {"status": "done", "consequence": "pending"}, "the host's done is recorded, the removal owed")
        self.assertEqual(len(self.rows("SELECT id FROM agents WHERE id = 'coder'")), 1)
        finished = self.report(request_id, status="done", resultIncarnation=1, resultRevision=1)
        self.assertEqual(finished.json()["request"]["consequence"], "removed", finished.text)
        self.assertEqual(self.rows("SELECT id FROM agents WHERE id = 'coder'"), [])

    def test_a_removal_that_deleted_and_crashed_before_settling_settles_as_removed(self):
        """The row it deleted held the withdrawal record, so its tombstone is what says it was removed."""
        self.push("s1", 1, [valid("coder")])
        request_id = self.ask("coder", {"remove": True}).json()["request"]["id"]
        self.claim()
        # What the interrupted report left: the host's done recorded, the agent deleted and tombstoned,
        # the consequence never settled. Then the host's withdrawal lands.
        self.execute("UPDATE definition_requests SET status = 'done', result_incarnation = 1, result_revision = 1, "
                     "consequence = 'pending' WHERE id = ?", (request_id,))
        self.execute("DELETE FROM agents WHERE id = 'coder'")
        self.execute("INSERT INTO agent_tombstones (agent_id, removed_at, removed_by, reason) "
                     "VALUES ('coder', '2026-10-01T00:00:00Z', 'aify-env', 'definition_removed')")
        self.push("s1", 2, [])
        settled = self.report(request_id, status="done", resultIncarnation=1, resultRevision=1)
        self.assertEqual(settled.json()["request"]["consequence"], "removed", settled.text)

    def a_report_that_waits(self):
        """Ask, claim and report a removal `done`, stopping the report after it read the consequence
        `pending` and before it ran it. Returns what that report read, to be resumed later."""
        import service.routers.definition_requests as result_route

        self.push("s1", 1, [valid("coder")])
        request_id = self.ask("coder", {"remove": True}).json()["request"]["id"]
        self.claim()
        waiting, real = [], result_route.finish_removal

        async def parked(db, request):
            waiting.append(request)

        result_route.finish_removal = parked
        try:
            self.report(request_id, status="done", resultIncarnation=1, resultRevision=1).raise_for_status()
        finally:
            result_route.finish_removal = real
        self.assertEqual(waiting[0]["consequence"], "pending", "control: the waiting report read it owed")
        return request_id, waiting[0]

    def resume(self, request):
        from service.api_core.definition_requests import finish_removal
        from service.db import get_db

        async def run():
            db = await get_db()
            try:
                await finish_removal(db, request)
            finally:
                await db.close()
        asyncio.run(run())

    def test_a_report_that_waited_does_not_rewrite_a_settled_consequence(self):
        """Review of c8029614, 2: the report that settles first decides; one that read `pending` before it
        and resumes after the world moved changes no byte of the request."""
        request_id, waiting = self.a_report_that_waits()
        self.client.post("/api/v1/agent-definitions/coder/release",
                         json={"requestedBy": "dashboard", "machineId": A["machine"]}).raise_for_status()
        settled = self.report(request_id, status="done", resultIncarnation=1, resultRevision=1).json()["request"]
        self.assertEqual(settled["consequence"], "nothing removed: the definition this removal was for ended another way")
        self.push("t1", 1, [valid("coder")], host=B)  # another machine defines it now
        recorded = self.rows("SELECT * FROM definition_requests WHERE id = ?", (request_id,))
        self.resume(waiting)
        self.assertEqual(self.rows("SELECT * FROM definition_requests WHERE id = ?", (request_id,)), recorded)

    def test_a_report_that_waited_runs_no_removal_its_request_no_longer_owes(self):
        """Settled `nothing removed`, then the same store defines the same lifetime again: the waiting
        report's custody fence would now allow, and the agent the request says was kept would go."""
        request_id, waiting = self.a_report_that_waits()
        self.client.post("/api/v1/agent-definitions/coder/release",
                         json={"requestedBy": "dashboard", "machineId": A["machine"]}).raise_for_status()
        self.report(request_id, status="done", resultIncarnation=1, resultRevision=1).raise_for_status()
        self.push("s1", 2, [valid("coder")])
        self.assertEqual(self.rows("SELECT store_id, incarnation FROM agent_definitions WHERE agent_id = 'coder'"),
                         [{"store_id": "s1", "incarnation": 1}], "control: the lifetime the removal named is held again")
        self.resume(waiting)
        self.assertEqual(len(self.rows("SELECT id FROM agents WHERE id = 'coder'")), 1, "nothing was removed")
        [row] = self.rows("SELECT consequence FROM definition_requests WHERE id = ?", (request_id,))
        self.assertTrue(row["consequence"].startswith("nothing removed: "), row)

    def test_a_removal_racing_a_change_of_custody_stops_nothing(self):
        """Review of 12766276, 3: the fence and the stop are one transaction, so custody that moves while
        the removal waits for the lock is seen before any worker is told to stop."""
        from service.api_core.agent_remove import remove_agent
        from service.api_core.definition_requests import removal_refusal
        from service.db import get_db

        self.push("s1", 1, [valid("worker")])
        self.client.post("/api/v1/agents", json={"agentId": "worker", "role": "coder", "runtime": "codex",
                                                  "sessionMode": "managed"}).raise_for_status()
        made_for = {"agentId": "worker", "machineId": A["machine"], "storeId": "s1", "expectedIncarnation": 1}
        holder = sqlite3.connect(str(self._db_path), isolation_level=None)
        self.addCleanup(lambda: holder.close())
        holder.execute("BEGIN IMMEDIATE")
        outcome = {}

        async def removal():
            db = await get_db()
            try:
                return await remove_agent(db, "worker", actor="test", reason="test",
                                          refusal=lambda conn: removal_refusal(conn, made_for))
            finally:
                await db.close()

        worker = threading.Thread(target=lambda: outcome.update(result=asyncio.run(removal())))
        worker.start()
        worker.join(0.6)
        self.assertTrue(worker.is_alive(), "control: the removal is waiting on the write lock")
        holder.execute("DELETE FROM agent_definitions WHERE agent_id = 'worker'")
        holder.execute("COMMIT")
        holder.close()
        worker.join(10)
        deleted, why = outcome["result"]
        self.assertEqual((deleted, why), (0, "the definition this removal was for ended another way"))
        [row] = self.rows("SELECT status FROM agents WHERE id = 'worker'")
        self.assertNotEqual(row["status"], "stopped", "no worker was told to stop")

    def test_a_removal_whose_definition_was_released_removes_nothing(self):
        """A release is the operator's, not the store's: a host's later `done` finds no withdrawal by
        that store at that lifetime, and removes nothing."""
        self.push("s1", 1, [valid("coder")])
        request_id = self.ask("coder", {"remove": True}).json()["request"]["id"]
        self.claim()
        self.client.post("/api/v1/agent-definitions/coder/release",
                         json={"requestedBy": "dashboard", "machineId": A["machine"]}).raise_for_status()
        done = self.report(request_id, status="done", resultIncarnation=1, resultRevision=1)
        self.assertEqual(done.json()["request"]["consequence"],
                         "nothing removed: the definition this removal was for ended another way", done.text)
        self.assertEqual(len(self.rows("SELECT id FROM agents WHERE id = 'coder'")), 1)

    def test_a_delayed_removal_for_an_earlier_lifetime_removes_nothing_and_says_so(self):
        self.push("s1", 1, [valid("coder")])
        request_id = self.ask("coder", {"remove": True}).json()["request"]["id"]
        self.claim()
        # The id is removed and made again on the host: lifetime 2, before the old removal's report lands.
        self.push("s1", 2, [])
        self.push("s1", 3, [valid("coder", incarnation=2)])
        done = self.report(request_id, status="done", outcome="removed", resultIncarnation=1, resultRevision=1)
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual((done.json()["request"]["outcome"], done.json()["request"]["consequence"]),
                         ("removed", "nothing removed: the definition is now win32:host-a store s1 lifetime 2, "
                                     "not the one this removal was for"), "the host's outcome is kept as it said it")
        self.assertEqual(len(self.rows("SELECT id FROM agents WHERE id = 'coder'")), 1, "the new lifetime's agent stays")

    # ---- an owed removal is finished without its host (review of P4, N2) -------------------------------

    def interrupted_removal(self, agent_id="coder"):
        """A `done` removal whose service consequence failed after the host's receipt was committed."""
        import service.api_core.definition_requests as result_route

        request_id = self.ask(agent_id, {"remove": True}).json()["request"]["id"]
        self.claim()
        real = result_route.remove_agent

        async def interrupted(*args, **kwargs):
            raise RuntimeError("the service went away before the removal")

        result_route.remove_agent = interrupted
        try:
            failed = self.report(request_id, status="done", resultIncarnation=1, resultRevision=1)
        finally:
            result_route.remove_agent = real
        self.assertEqual(failed.status_code, 500, "control: the removal failed after the done was recorded")
        return request_id

    def sweep(self):
        from service.db import get_db
        from service.reconcilers.owed_removals import finish_owed_removals

        async def go():
            db = await get_db()
            try:
                return await finish_owed_removals(db)
            finally:
                await db.close()
        return asyncio.run(go())

    def consequence(self, request_id):
        return self.rows("SELECT status, consequence FROM definition_requests WHERE id = ?", (request_id,))[0]

    def test_an_owed_removal_is_finished_by_the_sweep_with_no_report_from_its_host(self):
        """The host never reports it again: it claims only pending and claimed requests. Its timed push
        withdraws the definition, and the sweep still removes the agent and tombstones it."""
        self.push("s1", 1, [valid("coder")])
        request_id = self.interrupted_removal()
        self.assertEqual(self.claim(), [], "the host is not handed it again, so it will never report it again")
        self.push("s1", 2, [])
        self.assertEqual(len(self.rows("SELECT id FROM agents WHERE id = 'coder'")), 1, "control: still owed")
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.consequence(request_id), {"status": "done", "consequence": "removed"})
        self.assertEqual(self.rows("SELECT id FROM agents WHERE id = 'coder'"), [])
        self.assertEqual(len(self.rows("SELECT agent_id FROM agent_tombstones WHERE agent_id = 'coder'")), 1)
        self.assertEqual(self.sweep(), 0, "settled once, and never run again")

    def test_the_sweep_keeps_the_custody_fence(self):
        """An owed removal whose definition has since moved to another store removes nothing, and says why."""
        self.push("s1", 1, [valid("coder")])
        request_id = self.interrupted_removal()
        self.push("s2", 1, [valid("coder")])
        self.sweep()
        settled = self.consequence(request_id)["consequence"]
        self.assertTrue(settled.startswith("nothing removed: "), settled)
        self.assertEqual(len(self.rows("SELECT id FROM agents WHERE id = 'coder'")), 1)

    def test_one_owed_removal_that_fails_leaves_the_others_finished(self):
        import service.api_core.definition_requests as result_route

        self.push("s1", 1, [valid("coder"), valid("tester")])
        first = self.interrupted_removal("coder")
        second = self.interrupted_removal("tester")
        real = result_route.remove_agent

        async def coder_fails(db, agent_id, **kwargs):
            # FAILS AS THE REAL ONE CAN: inside its own transaction, after a write. Left open, that
            # transaction would refuse the next removal's BEGIN and keep the partial write.
            if agent_id == "coder":
                await db.execute("BEGIN IMMEDIATE")
                await db.execute("UPDATE agents SET name = 'half-removed' WHERE id = 'coder'")
                raise RuntimeError("still failing")
            return await real(db, agent_id, **kwargs)

        result_route.remove_agent = coder_fails
        try:
            with self.assertLogs("service.reconcilers.owed_removals", level="ERROR"):
                self.assertEqual(self.sweep(), 2)
        finally:
            result_route.remove_agent = real
        self.assertEqual(self.consequence(first)["consequence"], "pending", "the failing one stays owed")
        self.assertNotEqual(self.rows("SELECT name FROM agents WHERE id = 'coder'")[0]["name"], "half-removed",
                            "its partial write was rolled back")
        self.assertEqual(self.consequence(second)["consequence"], "removed")
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.consequence(first)["consequence"], "removed", "and the next sweep finishes it")

    def test_the_reconcile_pass_runs_the_sweep(self):
        from service.reconcilers.sweep import _run_dispatch_reconcile_once

        self.push("s1", 1, [valid("coder")])
        request_id = self.interrupted_removal()
        result = asyncio.run(_run_dispatch_reconcile_once())
        self.assertEqual(result.get("finished_owed_removals"), 1, result)
        self.assertEqual(self.consequence(request_id)["consequence"], "removed")

    def test_a_prefix_that_keeps_failing_does_not_starve_a_newer_owed_removal(self):
        """Review of P4, N4: over the pass's budget, the fifty oldest fail on every pass and one newer
        removal would succeed. Ordered by age alone, the same fifty were read every pass for ever."""
        import service.api_core.definition_requests as result_route

        ids = [f"agent-{n:02d}" for n in range(51)]
        self.push("s1", 1, [valid(agent_id) for agent_id in ids])
        owed = [self.interrupted_removal(agent_id) for agent_id in ids]
        failing = set(ids[:50])
        real = result_route.remove_agent

        async def the_oldest_fail(db, agent_id, **kwargs):
            if agent_id in failing:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute("UPDATE agents SET name = 'half-removed' WHERE id = ?", (agent_id,))
                raise RuntimeError("still failing")
            return await real(db, agent_id, **kwargs)

        result_route.remove_agent = the_oldest_fail
        try:
            with self.assertLogs("service.reconcilers.owed_removals", level="ERROR"):
                for _ in range(3):
                    self.sweep()
        finally:
            result_route.remove_agent = real
        self.assertEqual(self.consequence(owed[50])["consequence"], "removed", "the newer owed removal was reached")
        self.assertEqual(self.rows("SELECT id FROM agents WHERE id = ?", (ids[50],)), [])
        self.assertEqual({self.consequence(r)["consequence"] for r in owed[:50]}, {"pending"}, "the failing ones stay owed")
        self.assertEqual(self.rows("SELECT COUNT(*) AS n FROM agents WHERE name = 'half-removed'"), [{"n": 0}],
                         "every failed attempt was rolled back")
        self.sweep()
        self.assertEqual({self.consequence(r)["consequence"] for r in owed[:50]}, {"removed"}, "and they finish once they can")
