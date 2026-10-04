"""A machine's host routes need the proof its aify-env first presented (external review of 0.8.1, HIGH 2).

THE DEFECT. Anyone holding the API key could push a machine's agent definitions, using the bridge id
`GET /environments` hands out, or take the environment row by beating as the host tier with a later
start time. A forged push rewrote another agent's definition; one with a new store id withdrew every
agent the machine defined and locked its real host out.

Each refusal below is paired with the real host doing the same thing with its proof, which must still
work, and each checks that the refused request changed nothing. A machine that never presented a proof
is let through as before (an older aify-env), and that is pinned too, so the gate cannot pass by
refusing everything.
"""
from __future__ import annotations

import sqlite3

from service.api_core.host_proof import HOST_PROOF_HEADER
from service.api_core.operator_authz import OPERATOR_KEY_HEADER
from service.tests._base import FastApiTestCase
from service.tests.test_agent_definition_push import A, snapshot_digest, valid

PROOF = {HOST_PROOF_HEADER: "the-real-hosts-proof"}
FORGED = {HOST_PROOF_HEADER: "a-forgers-proof"}
RUNTIMES = [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}]


class AMachineSpeaksOnlyThroughItsProvenHost(FastApiTestCase):
    DB_NAME = "aify-test-host-proof.db"

    def beat(self, headers=None, bridge=A["bridge"], started="2026-10-03T00:00:00Z"):
        return self.client.post("/api/v1/environments/heartbeat", headers=headers or {}, json={
            "id": A["env"], "machineId": A["machine"], "os": "win32", "kind": "win32", "bridgeId": bridge,
            "cwdRoots": ["/work"], "runtimes": RUNTIMES, "terminal": True, "pty": True,
            "terminalRuntimes": ["claude-code"], "metadata": {"bridgeKind": "aify-env", "bridgeStartedAt": started}})

    def push(self, headers=None, store="s1", revision=1, agent="lead"):
        entries = [valid(agent)]
        return self.client.put(f"/api/v1/environments/{A['env']}/agent-definitions", headers=headers or {}, json={
            "bridgeId": A["bridge"], "machineId": A["machine"], "storeId": store, "revision": revision,
            "snapshotDigest": snapshot_digest(entries), "entries": entries})

    def rows(self, sql):
        conn = sqlite3.connect(str(self._db_path))
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def setUp(self):
        super().setUp()
        self.assertEqual(self.beat(PROOF).status_code, 200, "the real host's first beat records its proof")
        self.assertEqual(self.push(PROOF).status_code, 200)

    def test_a_forged_push_is_refused_and_changes_nothing(self):
        for headers, why in (({}, "no proof"), (FORGED, "another proof")):
            with self.subTest(why=why):
                rewritten = self.push(headers, revision=2, agent="intruder")
                self.assertEqual(rewritten.status_code, 403, rewritten.text)
                self.assertIn("definition push refused: machine win32:host-a proves itself with a host proof",
                              rewritten.json()["detail"])
                retiring = self.push(headers, store="s2-forged")
                self.assertEqual(retiring.status_code, 403, retiring.text)
        self.assertEqual(self.rows("SELECT agent_id FROM agent_definitions"), [("lead",)])
        self.assertEqual(self.rows("SELECT store_id, revision FROM definition_stores"), [("s1", 1)])
        self.assertEqual(self.rows("SELECT store_id FROM definition_stores_retired"), [], "the real store was not retired")

    def test_CONTROL_the_real_host_pushes_with_its_proof(self):
        self.assertEqual(self.push(PROOF, revision=2, agent="second").status_code, 200)
        self.assertEqual(self.rows("SELECT agent_id FROM agent_definitions"), [("second",)])

    def test_a_takeover_beat_with_a_later_start_is_refused(self):
        taken = self.beat({}, bridge="forger", started="2026-10-03T01:00:00Z")
        self.assertEqual(taken.status_code, 403, taken.text)
        self.assertIn("environment heartbeat refused: machine win32:host-a proves itself with a host proof and "
                      "this request presents none", taken.json()["detail"])
        self.assertEqual(self.rows("SELECT bridge_id FROM environments"), [(A["bridge"],)])
        self.assertEqual(self.beat(PROOF).status_code, 200, "control: the real host still beats")

    def test_the_host_claim_and_report_routes_need_the_proof(self):
        body = {"bridgeId": A["bridge"], "machineId": A["machine"]}
        claim = self.client.post(f"/api/v1/environments/{A['env']}/definition-requests/claim", json=body)
        self.assertEqual(claim.status_code, 403, claim.text)
        report = self.client.post(f"/api/v1/environments/{A['env']}/definition-requests/defreq_x/result",
                                  json={**body, "status": "refused", "outcome": "x"})
        self.assertEqual(report.status_code, 403, report.text)
        proven = self.client.post(f"/api/v1/environments/{A['env']}/definition-requests/claim", headers=PROOF, json=body)
        self.assertEqual(proven.status_code, 200, proven.text)

    def test_the_spawn_and_terminal_claims_and_a_spawn_update_need_the_proof(self):
        """Fenced by the bridge id alone, which `GET /environments` hands any key holder, a forged claim took a
        spawn away from the real host (triage of the external review of 0.8.4)."""
        claim = {"environmentId": A["env"], "bridgeId": A["bridge"]}
        created = self.client.post("/api/v1/spawn-requests", json={
            "environmentId": A["env"], "agentId": "worker-1", "role": "coder", "runtime": "claude-code", "workspace": "/work"})
        self.assertEqual(created.status_code, 200, created.text)
        for headers, why in (({}, "no proof"), (FORGED, "another proof")):
            with self.subTest(why=why):
                forged = self.client.post("/api/v1/spawn-requests/claim", headers=headers, json=claim)
                self.assertEqual(forged.status_code, 403, forged.text)
                controls = self.client.post("/api/v1/terminals/controls/claim", headers=headers, json=claim)
                self.assertEqual(controls.status_code, 403, controls.text)
        self.assertEqual(self.rows("SELECT status FROM spawn_requests"), [("queued",)], "a refused claim took nothing")
        proven = self.client.post("/api/v1/spawn-requests/claim", headers=PROOF, json=claim)
        self.assertEqual(proven.status_code, 200, proven.text)
        spawn_id = proven.json()["spawnRequest"]["id"]
        self.assertEqual(self.client.post("/api/v1/terminals/controls/claim", headers=PROOF, json=claim).status_code, 200)
        update = {"status": "starting", "bridgeId": A["bridge"]}
        self.assertEqual(self.client.patch(f"/api/v1/spawn-requests/{spawn_id}", json=update).status_code, 403)
        self.assertEqual(self.client.patch(f"/api/v1/spawn-requests/{spawn_id}", headers=PROOF, json=update).status_code, 200,
                         "control: the real host updates its spawn")

    def test_the_proof_is_stored_as_a_digest(self):
        [(digest,)] = self.rows("SELECT proof_digest FROM host_proofs")
        self.assertNotIn(PROOF[HOST_PROOF_HEADER], digest)
        self.assertEqual(len(digest), 64)

    def test_only_the_operator_resets_a_proof_and_then_a_new_one_is_recorded(self):
        self.client.app.state.config.operator_key = "op-key"
        refused = self.client.post(f"/api/v1/host-proofs/{A['machine']}/reset", json={"requestedBy": "mallory"})
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertEqual(self.beat(FORGED).status_code, 403, "still the first proof")
        reset = self.client.post(f"/api/v1/host-proofs/{A['machine'].upper()}/reset",
                                 headers={OPERATOR_KEY_HEADER: "op-key"}, json={"requestedBy": "dashboard"})
        self.assertEqual((reset.status_code, reset.json().get("cleared")), (200, 1), reset.text)
        replaced = {HOST_PROOF_HEADER: "a-new-secret"}
        self.assertEqual(self.beat(replaced).status_code, 200)
        self.assertEqual(self.beat(PROOF).status_code, 403, "the old proof no longer speaks for it")


class AProofIsJudgedAndEnrolledUnderTheWriteLock(FastApiTestCase):
    """Review of 08f3e7e5: H2-R1 (a beat that read "no proof yet" wrote after the real host enrolled) and H2-R2
    (a refused definition push enrolled the machine, and the real host was then refused)."""
    DB_NAME = "aify-test-host-proof-lock.db"
    beat, push, rows = (AMachineSpeaksOnlyThroughItsProvenHost.beat, AMachineSpeaksOnlyThroughItsProvenHost.push,
                        AMachineSpeaksOnlyThroughItsProvenHost.rows)

    def test_no_other_writer_can_land_between_a_beats_proof_read_and_its_write(self):
        import service.routers.environments as environments
        real = environments.judge_host_proof
        seen = []

        async def read_then_try_to_write(db, machine_ids, presented):
            answer = await real(db, machine_ids, presented)
            other = sqlite3.connect(str(self._db_path), timeout=0)
            try:
                other.execute("BEGIN IMMEDIATE")
                other.rollback()
                seen.append("another writer got in")
            except sqlite3.OperationalError as error:
                seen.append(str(error))
            finally:
                other.close()
            return answer

        environments.judge_host_proof = read_then_try_to_write
        try:
            self.assertEqual(self.beat(PROOF).status_code, 200)
        finally:
            environments.judge_host_proof = real
        self.assertEqual(seen, ["database is locked"], "the beat holds the write lock from its proof read to its commit")

    def test_a_refused_push_enrolls_nothing_and_the_real_host_still_beats(self):
        refused = self.push(FORGED)
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM host_proofs"), [(0,)])
        self.assertEqual(self.beat(PROOF).status_code, 200, "the real host's first beat enrolls it")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM host_proofs"), [(1,)])

    def test_CONTROL_only_a_beat_that_succeeds_enrolls(self):
        self.assertEqual(self.beat(PROOF).status_code, 200)
        self.assertEqual(self.beat(FORGED).status_code, 403)
        [(digest,)] = self.rows("SELECT proof_digest FROM host_proofs")
        self.assertEqual(self.beat(PROOF).status_code, 200, "the first proof stands")


class AMachineThatNeverPresentedAProof(FastApiTestCase):
    """An older aify-env sends no proof; until its machine presents one, nothing changes for it."""
    DB_NAME = "aify-test-host-proof-legacy.db"

    def test_CONTROL_beats_and_pushes_without_a_proof_still_work(self):
        case = AMachineSpeaksOnlyThroughItsProvenHost
        self.assertEqual(case.beat(self).status_code, 200)
        self.assertEqual(case.push(self).status_code, 200)
        self.assertEqual(case.rows(self, "SELECT COUNT(*) FROM host_proofs"), [(0,)])
