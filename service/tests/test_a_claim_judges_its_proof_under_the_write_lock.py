"""A terminal claim and a spawn update judge the host proof under the write lock they write with.

THE DEFECT (review of f97f08da). Both routes read the machine's proof, found the machine unenrolled (trust on first
use, so a request with no proof is let through), and wrote later. A heartbeat that enrolled the machine in between
committed first, and the proofless write landed anyway. `judge_host_proof` says its caller holds `BEGIN IMMEDIATE`
from that read to its write; these two did not.

The race is made deterministic: a second connection holds the write lock and enrolls the machine, committing as
soon as the route has judged (or after 0.5 s, when the route judges only once it holds the lock). The claim waits
for a lock up to 1.2 s (`SQLITE_CLAIM_BUSY_TIMEOUT_MS`) and the update up to 5 s, so the commit always comes first.
"""
from __future__ import annotations

import sqlite3
import threading

import service.api_core.terminal_controls_io as terminal_controls_io
import service.routers.spawn_requests as spawn_requests_router
from service.api_core.host_proof import _digest
from service.tests._base import FastApiTestCase
from service.tests.defined_agents import spawn_defined
from service.tests.test_agent_definition_push import A

RUNTIMES = [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}]


class AClaimJudgesItsProofUnderTheWriteLock(FastApiTestCase):
    DB_NAME = "aify-test-proof-under-lock.db"

    def setUp(self):
        super().setUp()
        beat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": A["env"], "machineId": A["machine"], "os": "win32", "kind": "win32", "bridgeId": A["bridge"],
            "cwdRoots": ["/work"], "runtimes": RUNTIMES, "terminal": True, "pty": True,
            "terminalRuntimes": ["claude-code"], "metadata": {"bridgeKind": "aify-env"}})
        self.assertEqual(beat.status_code, 200, beat.text)
        self.assertEqual(self.rows("SELECT machine_id FROM host_proofs"), [], "control: the machine starts unenrolled")

    def spawned(self, agent_id):
        """A spawn request to update: since D8 the only one made is a defined agent's cold start."""
        return spawn_defined(self, agent_id, environment_id=A["env"], machine_id=A["machine"], bridge_id=A["bridge"],
                             workspace="/work")["id"]

    def rows(self, sql, params=()):
        conn = sqlite3.connect(str(self._db_path))
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def enrolled_while(self, module, call, also=()):
        """Run `call` while another connection enrolls the machine (and runs `also`, as a heartbeat does in the
        same transaction), committing once the route has judged."""
        real = module.judge_host_proof
        judged = threading.Event()

        async def judging(*args, **kwargs):
            verdict = await real(*args, **kwargs)
            judged.set()
            return verdict

        holder = sqlite3.connect(str(self._db_path), isolation_level=None, check_same_thread=False)
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO host_proofs (machine_id, proof_digest, recorded_at) VALUES (?, ?, 'now')",
                       (A["machine"].lower(), _digest("the-real-hosts-proof")))
        for sql, params in also:
            holder.execute(sql, params)

        def commit_once_judged():
            judged.wait(0.5)
            holder.execute("COMMIT")

        committer = threading.Thread(target=commit_once_judged)
        module.judge_host_proof = judging
        try:
            committer.start()
            response = call()
        finally:
            module.judge_host_proof = real
            committer.join()
            # Closed here, not in addCleanup: cleanups run after tearDown deletes the database folder.
            holder.close()
        return response

    def test_a_terminal_claim_raced_by_an_enrollment_claims_nothing(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("INSERT INTO terminal_controls (id, terminal_id, environment_id, bridge_id, action, status, requested_at) "
                     "VALUES ('ctl-1', 'term-1', ?, ?, 'stop', 'pending', 'now')", (A["env"], A["bridge"]))
        conn.commit()
        conn.close()
        claimed = self.enrolled_while(terminal_controls_io, lambda: self.client.post(
            "/api/v1/terminals/controls/claim", json={"environmentId": A["env"], "bridgeId": A["bridge"]}))
        self.assertEqual(claimed.status_code, 403, claimed.text)
        self.assertIn("proves itself with a host proof and this request presents none", claimed.json()["detail"])
        self.assertEqual(self.rows("SELECT status FROM terminal_controls WHERE id = 'ctl-1'"), [("pending",)])

    def test_a_terminal_claim_reads_the_machine_under_the_lock_too(self):
        """Review of the fix: the claim read the environment's machine id before its lock. A heartbeat that fills an
        empty machine id and enrolls it in one transaction let the second check judge the stale empty id, and pass."""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("UPDATE environments SET machine_id = '' WHERE id = ?", (A["env"],))
        conn.execute("INSERT INTO terminal_controls (id, terminal_id, environment_id, bridge_id, action, status, requested_at) "
                     "VALUES ('ctl-3', 'term-3', ?, ?, 'stop', 'pending', 'now')", (A["env"], A["bridge"]))
        conn.commit()
        conn.close()
        claimed = self.enrolled_while(terminal_controls_io, lambda: self.client.post(
            "/api/v1/terminals/controls/claim", json={"environmentId": A["env"], "bridgeId": A["bridge"]}),
            also=[("UPDATE environments SET machine_id = ? WHERE id = ?", (A["machine"], A["env"]))])
        self.assertEqual(claimed.status_code, 403, claimed.text)
        self.assertEqual(self.rows("SELECT status FROM terminal_controls WHERE id = 'ctl-3'"), [("pending",)])

    def test_no_enrollment_lands_between_the_claims_final_judge_and_its_write(self):
        """Review of dc724538: the cases above release the competing enrollment at the FIRST judge, the unlocked
        precheck, so the locked re-read refuses even with the lock removed. This one tries to enroll right AFTER the
        final judge: under the claim's write lock that writer is blocked until the claim commits, so the claim lands
        on the state it judged. Without the lock the enrollment commits first and the proofless claim follows it."""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("INSERT INTO terminal_controls (id, terminal_id, environment_id, bridge_id, action, status, requested_at) "
                     "VALUES ('ctl-4', 'term-4', ?, ?, 'stop', 'pending', 'now')", (A["env"], A["bridge"]))
        conn.commit()
        conn.close()
        real = terminal_controls_io.judge_host_proof
        judged = []
        enrolled = []

        def enroll_now():
            writer = sqlite3.connect(str(self._db_path), timeout=0.2, isolation_level=None)
            try:
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("INSERT INTO host_proofs (machine_id, proof_digest, recorded_at) VALUES (?, ?, 'now')",
                               (A["machine"].lower(), _digest("the-real-hosts-proof")))
                writer.execute("COMMIT")
                enrolled.append("committed")
            except sqlite3.OperationalError as error:
                enrolled.append(f"blocked: {error}")
            finally:
                writer.close()

        async def judging(*args, **kwargs):
            verdict = await real(*args, **kwargs)
            judged.append(verdict)
            if len(judged) == 2:  # the final judge, the one the write must be held to
                attempt = threading.Thread(target=enroll_now)
                attempt.start()
                attempt.join()
            return verdict

        terminal_controls_io.judge_host_proof = judging
        try:
            claimed = self.client.post("/api/v1/terminals/controls/claim", json={"environmentId": A["env"], "bridgeId": A["bridge"]})
        finally:
            terminal_controls_io.judge_host_proof = real
        self.assertEqual(len(judged), 2, "control: the claim judged twice, the second time under its lock")
        self.assertEqual([c["id"] for c in claimed.json()["controls"]], ["ctl-4"], "the claim lands on the state it judged")
        self.assertTrue(enrolled and enrolled[0].startswith("blocked"), f"an enrollment committed inside the claim's window: {enrolled}")
        self.assertEqual(self.rows("SELECT machine_id FROM host_proofs"), [], "nothing enrolled before the claim's commit")

    def test_a_spawn_update_raced_by_an_enrollment_writes_nothing(self):
        request_id = self.spawned("worker")
        took = self.client.post("/api/v1/spawn-requests/claim", json={"environmentId": A["env"], "bridgeId": A["bridge"]})
        self.assertEqual(took.status_code, 200, took.text)
        updated = self.enrolled_while(spawn_requests_router, lambda: self.client.patch(
            f"/api/v1/spawn-requests/{request_id}", json={"status": "failed", "bridgeId": A["bridge"], "error": "x"}))
        self.assertEqual(updated.status_code, 403, updated.text)
        self.assertEqual(self.rows("SELECT status FROM spawn_requests WHERE id = ?", (request_id,)), [("claimed",)])

    def test_CONTROL_with_no_enrollment_racing_both_still_succeed(self):
        """Unenrolled and unraced, the trust-on-first-use path is unchanged."""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("INSERT INTO terminal_controls (id, terminal_id, environment_id, bridge_id, action, status, requested_at) "
                     "VALUES ('ctl-2', 'term-2', ?, ?, 'stop', 'pending', 'now')", (A["env"], A["bridge"]))
        conn.commit()
        conn.close()
        claimed = self.client.post("/api/v1/terminals/controls/claim", json={"environmentId": A["env"], "bridgeId": A["bridge"]})
        self.assertEqual([c["id"] for c in claimed.json()["controls"]], ["ctl-2"])
        request_id = self.spawned("worker2")
        self.client.post("/api/v1/spawn-requests/claim", json={"environmentId": A["env"], "bridgeId": A["bridge"]}).raise_for_status()
        updated = self.client.patch(f"/api/v1/spawn-requests/{request_id}", json={"status": "failed", "bridgeId": A["bridge"], "error": "x"})
        self.assertEqual(updated.status_code, 200, updated.text)
