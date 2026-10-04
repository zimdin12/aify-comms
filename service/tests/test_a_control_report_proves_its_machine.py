"""A terminal control's result is reported by the machine the control was queued for.

THE DEFECT (independent review of the 0.8.5 claim fix). `PATCH /terminals/controls/{id}` recorded a control
`completed` or `failed` from any API-key holder, so a forged report could mark an operator's stop handled, set the
terminal's status and append its output, while the worker ran. The claim route already judged the host proof.

THE RULE. The owner is read from the persisted control (its environment, then that environment's machine) under
`BEGIN IMMEDIATE`, taken before the first read and held through the commit, and the request's proof is judged
against it. A machine with no proof yet, or an environment with no machine recorded, is let through as the claim
route lets it through: trust on first use, not authentication. A control whose environment row is gone is refused.

NO RACE HERE WAITS ON A CLOCK. The route's connection and judge are watched, and the competing transaction acts at a
named statement the route sends (its `BEGIN IMMEDIATE`, its judge, its first write), so each schedule is the one the
test names on every run. What the competing writer met is recorded, never inferred from a timeout.
"""
from __future__ import annotations

import sqlite3
import threading

import service.routers.terminal_controls as terminal_controls_router
from service.api_core.host_proof import HOST_PROOF_HEADER, _digest
from service.tests._base import FastApiTestCase
from service.tests.test_agent_definition_push import A, B

RUNTIMES = [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}]
PROOF_A = "the-proof-of-host-a"
PROOF_B = "the-proof-of-host-b"
# A result that changes everything a forgery could: the control, the terminal's status, its output, its events, and
# the live screen's size (an in-memory effect a rolled-back transaction does not undo).
REPORT = {"status": "completed", "terminalStatus": "stopped", "output": "worker said goodbye\n", "cols": 101, "rows": 31}
# The safety net on every wait below, never the schedule: a run that reaches it has already failed.
STUCK_S = 10


class AControlReportProvesItsMachine(FastApiTestCase):
    DB_NAME = "aify-test-control-report-proof.db"

    def setUp(self):
        super().setUp()
        for host in (A, B):
            beat = self.client.post("/api/v1/environments/heartbeat", json={
                "id": host["env"], "machineId": host["machine"], "os": "win32", "kind": "win32", "bridgeId": host["bridge"],
                "cwdRoots": ["/work"], "runtimes": RUNTIMES, "terminal": True, "pty": True,
                "terminalRuntimes": ["claude-code"], "metadata": {"bridgeKind": "aify-env"}})
            self.assertEqual(beat.status_code, 200, beat.text)
        self.assertEqual(self.rows("SELECT machine_id FROM host_proofs"), [], "control: both machines start unenrolled")
        self.seed("ctl-1", "term-1", A["env"])
        self.resized = []
        real_resize = terminal_controls_router._resize_live_terminal_screen
        terminal_controls_router._resize_live_terminal_screen = lambda *args: self.resized.append(args)
        self.addCleanup(setattr, terminal_controls_router, "_resize_live_terminal_screen", real_resize)

    # --- fixtures ----------------------------------------------------------------------------------------------

    def rows(self, sql, params=()):
        conn = sqlite3.connect(str(self._db_path))
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def write(self, *statements):
        """Raw writes, foreign keys OFF as on any plain connection: how an imported orphan gets in."""
        conn = sqlite3.connect(str(self._db_path))
        try:
            for sql, params in statements:
                conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def seed(self, control_id, terminal_id, control_env, terminal_env=None):
        self.write(
            ("INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, runtime, status, output, created_at, updated_at) "
             "VALUES (?, ?, 'worker', ?, 'claude-code', 'running', 'before\n', 'now', 'now')",
             (terminal_id, f"sess-{terminal_id}", terminal_env or control_env)),
            ("INSERT INTO terminal_controls (id, terminal_id, environment_id, bridge_id, action, status, requested_at) "
             "VALUES (?, ?, ?, ?, 'stop', 'claimed', 'now')", (control_id, terminal_id, control_env, A["bridge"])),
        )

    def enroll(self, host, proof):
        self.write(("INSERT INTO host_proofs (machine_id, proof_digest, recorded_at) VALUES (?, ?, 'now')",
                    (host["machine"].lower(), _digest(proof))))

    def state(self, control_id="ctl-1", terminal_id="term-1"):
        """Everything a report writes to the database: the control, the terminal's status and output, its events."""
        return (self.rows("SELECT status, handled_at FROM terminal_controls WHERE id = ?", (control_id,)),
                self.rows("SELECT status, output FROM terminal_sessions WHERE id = ?", (terminal_id,)),
                self.rows("SELECT COUNT(*) FROM terminal_events WHERE terminal_id = ?", (terminal_id,)))

    def report(self, proof=None, control_id="ctl-1"):
        headers = {HOST_PROOF_HEADER: proof} if proof else {}
        return self.client.patch(f"/api/v1/terminals/controls/{control_id}", json=REPORT, headers=headers)

    def watching_the_route(self, on_statement=lambda sql: None, on_judge=lambda verdict: None):
        """Run `on_statement(sql)` just before each statement the route sends on its connection, and `on_judge` once it
        has judged. Returns the function that undoes it."""
        real_get_db, real_judge = terminal_controls_router.get_db, terminal_controls_router.judge_host_proof

        class Watched:
            def __init__(self, db):
                self._db = db

            def __getattr__(self, name):
                return getattr(self._db, name)

            def execute(self, sql, *args, **kwargs):
                on_statement(sql)
                return self._db.execute(sql, *args, **kwargs)

            def commit(self):
                on_statement("COMMIT")
                return self._db.commit()

        async def get_db(*args, **kwargs):
            return Watched(await real_get_db(*args, **kwargs))

        async def judging(*args, **kwargs):
            verdict = await real_judge(*args, **kwargs)
            on_judge(verdict)
            return verdict

        terminal_controls_router.get_db = get_db
        terminal_controls_router.judge_host_proof = judging

        def restore():
            terminal_controls_router.get_db = real_get_db
            terminal_controls_router.judge_host_proof = real_judge
        return restore

    def try_to_enroll(self):
        """One enrollment attempt from another connection that waits 0.2 s at most, ROLLED BACK if it gets as far as
        writing: what it met, and nothing left behind, so every later attempt meets the same database."""
        writer = sqlite3.connect(str(self._db_path), timeout=0.2, isolation_level=None)
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("INSERT INTO host_proofs (machine_id, proof_digest, recorded_at) VALUES (?, ?, 'now')",
                           (A["machine"].lower(), _digest(PROOF_A)))
            writer.execute("ROLLBACK")
            return ("could enroll", "")
        except sqlite3.OperationalError as error:
            return ("BEGIN IMMEDIATE", str(error))
        finally:
            writer.close()

    def enrolled_first(self, call, also=()):
        """A competing transaction holds the write lock FIRST, enrolls machine A (and runs `also`, as a heartbeat does in
        the same transaction), and commits at the first of: the route sending `BEGIN IMMEDIATE`, or the route judging.
        A route that reserves before reading meets the commit at its lock; one that reads or judges first has done so
        on the state before it. Returns the response and what the route had seen committed when it judged."""
        trigger, committed = threading.Event(), threading.Event()
        seen_at_judge = []

        def on_statement(sql):
            if sql.strip().upper().startswith("BEGIN IMMEDIATE"):
                trigger.set()

        def on_judge(verdict):
            seen_at_judge.append(committed.is_set())
            trigger.set()

        holder = sqlite3.connect(str(self._db_path), isolation_level=None, check_same_thread=False)
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO host_proofs (machine_id, proof_digest, recorded_at) VALUES (?, ?, 'now')",
                       (A["machine"].lower(), _digest(PROOF_A)))
        for sql, params in also:
            holder.execute(sql, params)

        def commit_when_triggered():
            trigger.wait(STUCK_S)
            # Up BEFORE the commit: a route under the lock cannot judge until the commit is done, and one judging
            # without it recorded what it saw before the trigger fired, so neither order races the flag.
            committed.set()
            holder.execute("COMMIT")

        restore = self.watching_the_route(on_statement, on_judge)
        committer = threading.Thread(target=commit_when_triggered)
        try:
            committer.start()
            response = call()
        finally:
            restore()
            trigger.set()
            committer.join()
            # Closed here, not in addCleanup: cleanups run after tearDown deletes the database folder.
            holder.close()
        return response, seen_at_judge

    # --- (a) the owner's proof, and only the owner's ------------------------------------------------------------

    def test_a_report_without_the_owners_proof_changes_nothing(self):
        self.enroll(A, PROOF_A)
        self.enroll(B, PROOF_B)
        before = self.state()
        for label, proof in (("no proof", None), ("another enrolled machine's valid proof", PROOF_B)):
            with self.subTest(label):
                refused = self.report(proof)
                self.assertEqual(refused.status_code, 403, refused.text)
                self.assertIn(f"machine {A['machine'].lower()} proves itself with a host proof", refused.json()["detail"])
                self.assertEqual(self.state(), before, "a refused report wrote something")
                self.assertEqual(self.resized, [], "a refused report resized the live screen before it was refused")

    def test_TWIN_the_owners_own_proof_reports(self):
        self.enroll(A, PROOF_A)
        self.enroll(B, PROOF_B)
        before = self.state()
        done = self.report(PROOF_A)
        self.assertEqual(done.status_code, 200, done.text)
        control, terminal, events = self.state()
        self.assertEqual(control[0][0], "completed")
        self.assertEqual(terminal[0][0], "stopped")
        self.assertIn("worker said goodbye", terminal[0][1])
        self.assertGreater(events[0][0], before[2][0][0], "the report appended no event")
        self.assertEqual(self.resized, [("term-1", 101, 31)], "control: the report's size reached the live screen")

    def test_the_owner_is_the_controls_environment_not_the_terminals(self):
        """The control was queued for machine A; its terminal row names B. A's proof decides, B's says nothing."""
        self.enroll(A, PROOF_A)
        self.seed("ctl-cross", "term-cross", A["env"], terminal_env=B["env"])
        refused = self.report(control_id="ctl-cross")
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertEqual(self.report(PROOF_A, control_id="ctl-cross").status_code, 200, "control: A's own proof reports")

    # --- unknown owner ------------------------------------------------------------------------------------------

    def test_a_control_whose_environment_is_gone_is_refused_not_trusted(self):
        """FK enforcement stops new dangling rows and cascades a deletion, but does not cure an orphan already in the
        file (review of the plan): the report finds no owner, and that is not the empty-machine branch."""
        self.seed("ctl-orphan", "term-orphan", "gone-env")
        before = self.state("ctl-orphan", "term-orphan")
        orphan = self.report(control_id="ctl-orphan")
        self.assertEqual(orphan.status_code, 404, orphan.text)
        self.assertEqual(orphan.json()["detail"], 'Environment "gone-env" of terminal control "ctl-orphan" not found')
        self.assertEqual(self.state("ctl-orphan", "term-orphan"), before)

    def test_CONTROL_unenrolled_and_empty_machine_ids_still_report(self):
        """Trust on first use, unchanged: neither is authenticated, and neither is refused."""
        unenrolled = self.report()
        self.assertEqual(unenrolled.status_code, 200, unenrolled.text)
        self.seed("ctl-2", "term-2", A["env"])
        self.write(("UPDATE environments SET machine_id = '' WHERE id = ?", (A["env"],)))
        empty = self.report(control_id="ctl-2")
        self.assertEqual(empty.status_code, 200, empty.text)

    # --- (d1) the enrollment holds the reservation first -------------------------------------------------------

    def test_an_enrollment_that_commits_first_refuses_the_proofless_report(self):
        before = self.state()
        refused, seen_at_judge = self.enrolled_first(lambda: self.report())
        self.assertEqual(seen_at_judge, [True], "the route judged before the enrollment that held the lock committed")
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertEqual(self.state(), before)

    # --- (d2) the report holds the reservation first, through its writes --------------------------------------

    def test_no_enrollment_lands_between_the_reports_judge_and_its_commit(self):
        """The report judges an unenrolled machine and may report (trust on first use). An enrollment is attempted when
        it has judged, and again just before EVERY statement the route sends after that, up to and including its
        commit: each attempt must meet SQLite's busy lock at its writer acquisition, so the whole report commits on the
        state it judged. A route that judged before reserving, or released the reservation anywhere between the judge
        and the commit (review of bff298f1: a commit after the first write survived two probes before it), lets the
        enrollment commit inside that window."""
        attempts = []
        judged = []

        def on_statement(sql):
            if judged:
                attempts.append((" ".join(sql.split()[:3]).upper(), self.try_to_enroll()))

        def on_judge(verdict):
            judged.append(verdict)
            attempts.append(("judge", self.try_to_enroll()))

        restore = self.watching_the_route(on_statement, on_judge)
        try:
            reported = self.report()
        finally:
            restore()
        self.assertEqual(reported.status_code, 200, reported.text)
        # The window runs from the judge to the route's LAST commit; the reads after it are outside, and the probes
        # there are the positive control that an attempt can get through.
        where = [at for at, _ in attempts]
        self.assertIn("COMMIT", where, "control: the route's commit was never seen")
        last_commit = len(where) - 1 - where[::-1].index("COMMIT")
        window, after = attempts[:last_commit + 1], attempts[last_commit + 1:]
        self.assertEqual(where[0], "judge")
        for write in ("UPDATE TERMINAL_CONTROLS SET", "INSERT INTO TERMINAL_EVENTS"):
            self.assertIn(write, [at for at, _ in window], f"control: no attempt was made before {write}")
        self.assertIn(("could enroll", ""), [met for _, met in after], "control: no attempt got through once the report committed")
        locked = ("BEGIN IMMEDIATE", "database is locked")
        leaked = [(at, met) for at, met in window if met != locked]
        self.assertEqual(leaked, [], "an enrollment was not stopped at its writer acquisition inside the report's window")
        self.assertEqual(self.rows("SELECT machine_id FROM host_proofs"), [], "control: every probe rolled back")

    # --- (e) the owner binding is read under the reservation ----------------------------------------------------

    def test_a_machine_filled_and_enrolled_before_the_reservation_is_the_one_judged(self):
        """The environment starts with no machine; a heartbeat fills it and enrolls it in one transaction, committing
        before the report holds the lock. A machine id read before the reservation would still be empty, and pass."""
        self.write(("UPDATE environments SET machine_id = '' WHERE id = ?", (A["env"],)))
        before = self.state()
        refused, seen_at_judge = self.enrolled_first(
            lambda: self.report(), also=[("UPDATE environments SET machine_id = ? WHERE id = ?", (A["machine"], A["env"]))])
        self.assertEqual(seen_at_judge, [True], "the route judged before the heartbeat that held the lock committed")
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertEqual(self.state(), before)
