"""Stop, Restart, Recreate and CLI-takeover — the six refusals behind those four buttons.

`POST /sessions/{id}/control` is the dashboard's session lifecycle. Restart is the operation with
the worst history in this repo: three separate root causes for "restart produced no worker", one of
them a deterministic loss from sweep ordering. Six of its refusals had no test — all six read as
exercised until fe1e22ad, because `service/tests/data/` holds a pre-split copy of the handler and
the coverage scan was reading it.

    400 Unsupported session control action "<a>"
    409 Agent "<a>" already has pending spawn request "<id>" (<status>).
    409 Session "<s>" has no stored spawn spec. <the cold-start reason>
    409 Session "<s>" references missing spawn spec "<id>"
    409 Environment "<e>" is not available
    409 Environment "<e>" is <status>; assign a live environment before <action>.

TWO LISTS DECIDE WHAT AN ACTION DOES, and drift between them is a 500 rather than a refusal: the
allowlist `{stop, restart, recreate, cli_takeover}` and the `next_status` dict indexed with `[action]`
immediately after. Any action admitted by the first and absent from the second is a KeyError on a
dashboard button. They are cross-checked here by driving all four and asserting the status each one
leaves behind, rather than by reading the two literals.

THE "no stored spawn spec" REFUSAL CARRIES A REASON IT USED TO INVENT. It once asserted "no online
environment can host managed X" — true for the environment-resolution causes and false for a runtime
that cannot be cold-started at all. It now appends whatever cold-start actually recorded, which is
why the test asserts the prefix AND that the specific cause survives into the message.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT_ID = "lc-worker"
ENVIRONMENT_ID = "linux:test-host:default"
SESSION_ID = "sess-1"
SPEC_ID = "spec-1"

ACCEPTED_ACTIONS = {
    "stop": "stopped",
    "restart": "restarting",
    "recreate": "ended",
    "cli_takeover": "cli-takeover",
}
REFUSED_ACTIONS = ("recover", "resume", "start", "kill", "", "restart-now")


def _recent_iso(seconds_ago: int = 5) -> str:
    """A timestamp that is unambiguously recent, for spawns that are meant to look in-flight."""
    import datetime
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class SessionControlRefusalTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self._register_agent()
        self._heartbeat()

    # ── seeding ──────────────────────────────────────────────────────────────────────────────

    def _register_agent(self) -> None:
        response = self.client.post(
            "/api/v1/agents",
            json={"agentId": AGENT_ID, "role": "coder", "runtime": "codex"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _heartbeat(self, status: str = "online", runtimes=("codex",)) -> None:
        response = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": ENVIRONMENT_ID,
                "label": "Linux on test-host",
                "machineId": "linux:test-host",
                "os": "linux",
                "kind": "linux",
                "bridgeId": "bridge-one",
                "cwdRoots": ["/workspace"],
                "runtimes": [{"runtime": r, "available": True} for r in runtimes],
                "status": status,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _read(self, sql: str, params: tuple = ()):
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(sql, params)
                row = await cursor.fetchone()
                return dict(row) if row else {}

        return asyncio.run(run())

    def _seed_spec(self, environment_id: str = ENVIRONMENT_ID) -> None:
        self._write(
            "INSERT INTO spawn_specs (id, agent_id, environment_id, runtime, workspace, mode,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (SPEC_ID, AGENT_ID, environment_id, "codex", "/workspace/proj", "managed-warm",
             "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z"),
        )

    def _seed_session(self, spawn_spec_id: str = "", session_id: str = SESSION_ID,
                      session_handle: str = "thread-abc") -> None:
        """A NON-EMPTY session handle by default, because it is the thing restart keeps and recreate
        throws away. Seeded empty, both branches produce "" and a mutation swapping them survives —
        which is exactly what happened before this argument existed."""
        self._write(
            "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, status,"
            " spawn_spec_id, session_handle, started_at, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (session_id, AGENT_ID, ENVIRONMENT_ID, "codex", "/workspace/proj", "running",
             spawn_spec_id, session_handle, "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z"),
        )

    def _control(self, action: str, session_id: str = SESSION_ID):
        return self.client.post(
            f"/api/v1/sessions/{session_id}/control",
            json={"action": action, "from_agent": "dashboard"},
        )

    def _refused_for_liveness(self, response) -> bool:
        """Was THIS the precondition refusal? The route 409s for several unrelated reasons, and
        `assertNotEqual(409)` made three of these tests pass or fail for the wrong one -- the
        fixture's restart refuses on a missing spawn spec whatever the precondition says.
        """
        if response.status_code != 409:
            return False
        return "nothing was running" in response.json().get("detail", "")

    def _control_if_idle(self, action: str, session_id: str = SESSION_ID):
        """A control carrying the caller's precondition: "I acted because nothing was live."""
        return self.client.post(
            f"/api/v1/sessions/{session_id}/control",
            json={"action": action, "from_agent": "aify-env",
                  "only_if_no_live_session": True},
        )

    # ── the caller's precondition, re-evaluated where the state is ───────────────────────────

    def test_a_conditional_restart_is_refused_when_a_session_went_live(self):
        """REVIEW'S DESIGN FINDING, closed at the authority.

        aify-env's "start available agent" reads which agents have no live session and then
        restarts the one it chose. Those are two round trips, and a worker starting in between
        turned a start into a STOP of somebody's live terminal. A third client-side reading only
        shortens the window; the condition has to be evaluated by the thing performing the act.
        """
        self._seed_session()  # seeded `running`, which is live
        response = self._control_if_idle("restart")
        self.assertEqual(response.status_code, 409, response.text)
        # THE MESSAGE TEXT, SPELLED OUT. `test_every_refusal_is_exercised.py` greps tests for the
        # literal a refusal carries, so a substring assertion leaves it counted as UNTESTED -- and
        # this file learned that the hard way when the census went red on this very refusal.
        self.assertEqual(
            response.json()["detail"],
            f'Agent "{AGENT_ID}" has a live session "{SESSION_ID}" (running'
            "), so this conditional restart was refused. It was requested on the belief that "
            "nothing was running; something is.",
        )

    def test_a_status_written_in_mixed_case_is_still_live(self):
        """A status is written by several producers and this comparison is the whole guard, so a
        row saying `Running` must not read as not-live. Mutation found it: dropping the LOWER()
        killed nothing, because every fixture here happens to write lowercase.
        """
        self._seed_session()
        self._write("UPDATE agent_sessions SET status = ? WHERE id = ?", ("Running", SESSION_ID))
        response = self._control_if_idle("restart")
        self.assertTrue(self._refused_for_liveness(response), response.text)

    def test_the_same_request_without_the_precondition_is_unconditional(self):
        """THE CONTROL, and the reason the field is opt-in.

        The dashboard's own Restart button means what it says: an operator pressing it on a live
        session is choosing to restart a live session. A guard that refused every restart of a
        running session would break that, and would also pass the test above for the wrong
        reason -- so the SAME state has to be driven both ways.
        """
        self._seed_session()
        response = self._control("restart")
        self.assertFalse(self._refused_for_liveness(response), response.text)

    def test_a_conditional_restart_proceeds_when_nothing_is_live(self):
        """The other half: the precondition holding must not block the action it guards."""
        self._seed_session()
        self._write("UPDATE agent_sessions SET status = ? WHERE id = ?", ("stopped", SESSION_ID))
        response = self._control_if_idle("restart")
        self.assertFalse(self._refused_for_liveness(response), response.text)

    def test_a_live_session_of_ANOTHER_agent_does_not_refuse_it(self):
        """Scoped to the agent, and this is the mutation that would otherwise survive: a guard
        reading the whole table refuses every conditional start on a busy fleet, which is
        indistinguishable from the feature not working.
        """
        self._seed_session()
        self._write("UPDATE agent_sessions SET status = ? WHERE id = ?", ("stopped", SESSION_ID))
        self._write(
            "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace,"
            " status, started_at, last_seen) VALUES (?,?,?,?,?,?,?,?)",
            ("sess-other", "another-agent", ENVIRONMENT_ID, "codex", "/workspace/proj",
             "running", "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z"),
        )
        response = self._control_if_idle("restart")
        self.assertFalse(self._refused_for_liveness(response), response.text)

    def _seed_running_dispatch(self, run_id: str = "run-live") -> None:
        """A run this route would INTERRUPT, which is the thing the ordering test watches for."""
        self._write(
            "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, message_type,"
            " subject, body, priority, status, require_reply, requested_at, claimed_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, f"msg-{run_id}", "peer", AGENT_ID, "request", "s", "b", "normal",
             "running", 1, "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z"),
        )

    def test_a_refused_conditional_restart_writes_nothing_at_all(self):
        """THE TRANSACTION IS THE GUARANTEE, and mutation is how that got established.

        The first version of this asserted that the precondition is checked BEFORE the dispatch
        interrupt, by counting controls. Moving the guard after the interrupt left it green --
        because this route has ONE commit, at the end, so any refusal raised before it discards
        every write of that request. A refused restart cannot interrupt anybody in either order.

        What is observable, and what an operator actually depends on, is that a refusal leaves no
        trace. The SUCCESSFUL restart below is what makes that mean something: the same route, on
        the same state, does append a control when it proceeds.
        """
        self._seed_spec()
        self._seed_session(spawn_spec_id=SPEC_ID)
        self._seed_running_dispatch()
        before = self._read("SELECT COUNT(*) AS n FROM dispatch_controls")

        refusal = self._control_if_idle("restart")
        self.assertEqual(refusal.status_code, 409, refusal.text)
        after_refusal = self._read("SELECT COUNT(*) AS n FROM dispatch_controls")
        self.assertEqual(after_refusal["n"], before["n"],
                         "a refused conditional restart left a control behind")

        # THE CONTROL THAT MAKES THE COUNT READABLE: the same request without the precondition
        # proceeds, and appends the interrupt the assertion above is watching for.
        accepted = self._control("restart")
        self.assertEqual(accepted.status_code, 200, accepted.text)
        after_accept = self._read("SELECT COUNT(*) AS n FROM dispatch_controls")
        self.assertGreater(after_accept["n"], before["n"],
                           "this route never appends a control, so the assertion above holds "
                           "for a reason unrelated to the refusal")

    def _commit_a_live_session_from_another_connection(self) -> str:
        """A SECOND writer, exactly where review inserted one: after the guard's read.

        Its own connection, a short timeout, and its outcome RETURNED rather than swallowed --
        whether this commit can land is the whole question.
        """
        import sqlite3
        try:
            other = sqlite3.connect(self._db_path, timeout=0.5)
            try:
                other.execute("UPDATE agent_sessions SET status = ? WHERE id = ?",
                              ("running", SESSION_ID))
                other.commit()
            finally:
                other.close()
            return ""
        except sqlite3.OperationalError as error:
            return str(error)

    def test_a_second_writer_CANNOT_slip_a_live_session_past_the_guard(self):
        """REVIEW REPRODUCED THIS RACE SURVIVING THE GUARD, and they were right.

        `get_db` hands out a plain connection with `isolation_level=''`, so a SELECT starts no
        transaction: the first version of the guard read with `in_transaction=False`. Another
        connection could commit `session=running` AFTER that read and BEFORE this route's first
        write, and the restart then queued a stop for the terminal that had just come live.
        Their interleaving: guard finds none, competing commit lands, route returns 200 with a
        stop control and a spawn request.

        My earlier correction -- that one commit at the end means a REFUSED request writes
        nothing -- was true and did not touch this. A refusal writing nothing says nothing about
        whether the read that DECIDES the request is atomic with the writes that follow it.

        So the writer is reserved before the read, and this drives the same interleaving: the
        competing commit is attempted at exactly the point review inserted it, and must be
        refused rather than land.
        """
        self._seed_spec()
        self._seed_session(spawn_spec_id=SPEC_ID)
        self._write("UPDATE agent_sessions SET status = ? WHERE id = ?", ("stopped", SESSION_ID))

        from service.routers import session_control
        real = session_control._live_session_for
        outcomes = []

        async def read_then_race(db, agent_id):
            # THE REAL QUERY, with its real answer -- only the SCHEDULING is arranged.
            answer = await real(db, agent_id)
            outcomes.append(self._commit_a_live_session_from_another_connection())
            return answer

        session_control._live_session_for = read_then_race
        try:
            response = self._control_if_idle("restart")
        finally:
            session_control._live_session_for = real

        self.assertEqual(len(outcomes), 1, "the race was never attempted, so this test judged nothing")
        self.assertNotEqual(
            outcomes[0], "",
            "a second connection committed a live session between the guard and the writes it authorises, which is the race this reservation exists to close",
        )
        self.assertIn("locked", outcomes[0].lower(), outcomes[0])
        # AND THE ROUTE STILL DID ITS JOB. A reservation that closed the window by refusing every
        # request would satisfy the assertion above and break the feature.
        self.assertEqual(response.status_code, 200, response.text)

    def test_the_second_writer_lands_freely_when_no_reservation_is_asked_for(self):
        """THE CONTROL, and without it the assertion above is unreadable: a competing commit that
        could never land under any circumstances -- a locked file, a bad path -- would satisfy it.
        The same second writer, at the same moment, on an UNCONDITIONAL restart that reserves
        nothing, must succeed.
        """
        self._seed_spec()
        self._seed_session(spawn_spec_id=SPEC_ID)
        self._write("UPDATE agent_sessions SET status = ? WHERE id = ?", ("stopped", SESSION_ID))

        from service.routers import session_control
        real = session_control._get_blocking_active_run
        outcomes = []

        async def read_then_race(db, agent_id, *args, **kwargs):
            answer = await real(db, agent_id, *args, **kwargs)
            outcomes.append(self._commit_a_live_session_from_another_connection())
            return answer

        session_control._get_blocking_active_run = read_then_race
        try:
            self._control("restart")
        finally:
            session_control._get_blocking_active_run = real

        self.assertEqual(len(outcomes), 1, "the race was never attempted")
        self.assertEqual(
            outcomes[0], "",
            "the competing write cannot land even with nothing reserved, so the refusal above "
            f"proves nothing about the reservation: {outcomes[0]}",
        )

    # ── the action allowlist, and the second list that must agree with it ────────────────────

    def test_the_action_allowlist_refuses_everything_outside_the_four(self):
        """`recover` and `resume` are in the list deliberately: they were byte-identical aliases of
        restart with no dashboard caller, dropped in 2026-06-03. A test that only tried nonsense
        values would not notice them coming back."""
        self._seed_session()
        for action in REFUSED_ACTIONS:
            with self.subTest(action=action):
                response = self._control(action)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(
                    response.json()["detail"],
                    f'Unsupported session control action "{action}"',
                )

    def test_the_action_is_checked_before_the_session_is_looked_up(self):
        response = self._control("nonsense", session_id="no-such-session")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            response.json()["detail"], 'Unsupported session control action "nonsense"',
        )

    def test_an_unknown_session_is_404(self):
        response = self._control("stop", session_id="no-such-session")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], 'Session "no-such-session" not found')

    def test_every_accepted_action_has_a_status_to_move_the_session_to(self):
        """THE DRIFT TEST. The allowlist and the `next_status` dict are two literals written side by
        side, and the dict is indexed with `[action]` — an action admitted by one and missing from
        the other is a KeyError on a dashboard button, not a refusal. Driven end to end, so the
        agreement is proved by the row each action leaves rather than by reading both lists."""
        for action, expected_status in ACCEPTED_ACTIONS.items():
            with self.subTest(action=action):
                session_id = f"sess-{action}"
                self._seed_spec()
                self._seed_session(spawn_spec_id=SPEC_ID, session_id=session_id)
                response = self._control(action, session_id=session_id)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(
                    self._read(
                        "SELECT status FROM agent_sessions WHERE id = ?", (session_id,),
                    )["status"],
                    expected_status,
                )
                self._write("DELETE FROM spawn_specs WHERE id = ?", (SPEC_ID,))
                self._write("DELETE FROM spawn_requests WHERE agent_id = ?", (AGENT_ID,))

    def test_the_action_is_normalised_before_the_allowlist(self):
        self._seed_session()
        response = self._control("  STOP  ")
        self.assertEqual(response.status_code, 200, response.text)

    # ── restart: a spawn already in flight ───────────────────────────────────────────────────

    def test_an_ABANDONED_spawn_does_not_block_the_restart_that_would_fix_it(self):
        """The sc-manager deadlock, 2026-08-18. A spawn stuck `queued` for ~30 minutes — surviving an
        operator bridge+wrapper restart — refused `comms_restart`, which is the exact action the
        undeliverable backstop's own message prescribes. No worker, so the spawn stayed queued; a
        spawn pending, so the restart 409'd. From inside a session there was no way out.

        The guard stays (two concurrent spawns race for one terminal), but it is no longer fail-safe
        in one direction only: a spawn that has made NO progress for the whole window is superseded
        rather than honoured, and the superseding is recorded on the row it cancels."""
        self._seed_spec()
        self._seed_session(spawn_spec_id=SPEC_ID)
        self._write(
            "INSERT INTO spawn_requests (id, spawn_spec_id, environment_id, agent_id,"
            " runtime, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("spawn-stuck", SPEC_ID, ENVIRONMENT_ID, AGENT_ID, "codex", "queued",
             "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z"),
        )
        response = self._control("restart")
        self.assertEqual(response.status_code, 200, response.text)

        row = self._read("SELECT status, error FROM spawn_requests WHERE id = ?", ("spawn-stuck",))
        self.assertEqual(row["status"], "cancelled", "the stuck spawn was left pending")
        self.assertIn("superseded", str(row["error"] or ""),
                      "the cancellation does not say why, so an operator cannot tell it from a real failure")

    def test_a_restart_is_refused_while_a_spawn_is_already_in_flight(self):
        """Every in-flight status, not one. A second spawn request for the same agent is how two
        workers end up racing for one session — and `starting` is the one a reader drops, because
        the row looks finished from the dashboard's point of view."""
        self._seed_spec()
        self._seed_session(spawn_spec_id=SPEC_ID)
        for status in ("queued", "claimed", "starting"):
            for action in ("restart", "recreate"):
                with self.subTest(status=status, action=action):
                    self._write(
                        "INSERT INTO spawn_requests (id, spawn_spec_id, environment_id, agent_id,"
                        " runtime, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                        # RECENT on purpose: "in flight" means a spawn that is still progressing.
                        # These rows used to be dated two days back, which was incidental to what the
                        # test asserts and became load-bearing when an abandoned spawn stopped
                        # blocking the restart (the sc-manager deadlock, 2026-08-18). A fixture whose
                        # staleness was never the point should not decide the verdict.
                        (f"spawn-{status}", SPEC_ID, ENVIRONMENT_ID, AGENT_ID, "codex", status,
                         _recent_iso(), _recent_iso()),
                    )
                    response = self._control(action)
                    self.assertEqual(response.status_code, 409, response.text)
                    detail = response.json()["detail"]
                    self.assertIn(
                        f'Agent "{AGENT_ID}" already has pending spawn request "spawn-{status}"'
                        f" ({status}).",
                        detail,
                    )
                    # The refusal now also says what to do about it. sc-manager hit this 409 with no
                    # way forward — the spawn it named was never going to produce a worker — so the
                    # message names the escape rather than leaving the caller to discover there
                    # isn't one.
                    self.assertIn("superseded automatically", detail,
                                  "the refusal does not tell the caller how it resolves")
                    self._write("DELETE FROM spawn_requests WHERE id = ?", (f"spawn-{status}",))

    def test_a_FINISHED_spawn_request_does_not_block_a_restart(self):
        """The mirror. A worker that failed is exactly when an operator presses Restart, so a
        terminal row must not read as in flight."""
        self._seed_spec()
        self._seed_session(spawn_spec_id=SPEC_ID)
        for status in ("running", "failed", "cancelled"):
            with self.subTest(status=status):
                self._write(
                    "INSERT INTO spawn_requests (id, spawn_spec_id, environment_id, agent_id,"
                    " runtime, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (f"old-{status}", SPEC_ID, ENVIRONMENT_ID, AGENT_ID, "codex", status,
                     "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z"),
                )
                response = self._control("restart")
                self.assertEqual(response.status_code, 200, response.text)
                self._write("DELETE FROM spawn_requests WHERE agent_id = ?", (AGENT_ID,))

    def test_a_STOP_is_not_blocked_by_a_pending_spawn(self):
        """The in-flight check is scoped to restart and recreate on purpose: an operator stopping a
        session whose spawn is still queued is cancelling exactly that."""
        self._seed_spec()
        self._seed_session(spawn_spec_id=SPEC_ID)
        self._write(
            "INSERT INTO spawn_requests (id, spawn_spec_id, environment_id, agent_id, runtime,"
            " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("spawn-queued", SPEC_ID, ENVIRONMENT_ID, AGENT_ID, "codex", "queued",
             "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z"),
        )
        self.assertEqual(self._control("stop").status_code, 200)

    # ── restart: what the session's spawn spec points at ─────────────────────────────────────

    def test_a_session_with_no_spawn_spec_reports_the_REAL_cold_start_reason(self):
        """A resident-origin session has no spawn spec, so restart tries a cold start first. When
        that cannot be done the refusal must carry the cause cold-start recorded — this raise used
        to discard it and assert "no online environment", which is true for some causes and false
        for others. Here the environment advertises no codex, so the reason is about the runtime."""
        self._heartbeat(runtimes=())
        self._seed_session(spawn_spec_id="")
        response = self._control("restart")
        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertIn(f'Session "{SESSION_ID}" has no stored spawn spec.', detail)
        self.assertIn("Cannot start managed codex for this agent", detail)
        self.assertNotEqual(
            detail.strip(), f'Session "{SESSION_ID}" has no stored spawn spec.',
            "the refusal must carry a reason, not just the fact",
        )

    def test_a_session_pointing_at_a_deleted_spawn_spec_names_the_missing_id(self):
        self._seed_session(spawn_spec_id="spec-gone")
        response = self._control("restart")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"],
            f'Session "{SESSION_ID}" references missing spawn spec "spec-gone"',
        )

    def test_a_spec_pointing_at_a_deleted_environment_is_not_available(self):
        """Different from the one below: the environment ROW is gone, so there is no status to
        report and nothing to assign work to."""
        self._seed_spec(environment_id="linux:gone:default")
        self._seed_session(spawn_spec_id=SPEC_ID)
        response = self._control("restart")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"], 'Environment "linux:gone:default" is not available',
        )

    def test_an_environment_that_is_not_online_names_the_status_and_the_action(self):
        """The message interpolates the ACTION, so a Recreate says recreate. An operator reading
        "before restart" after pressing Recreate would reasonably think they pressed the wrong
        button."""
        self._seed_spec()
        for action in ("restart", "recreate"):
            for status in ("offline", "degraded"):
                with self.subTest(action=action, status=status):
                    session_id = f"sess-{action}-{status}"
                    self._heartbeat(status=status)
                    self._seed_session(spawn_spec_id=SPEC_ID, session_id=session_id)
                    response = self._control(action, session_id=session_id)
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertEqual(
                        response.json()["detail"],
                        f'Environment "{ENVIRONMENT_ID}" is {status}; assign a live environment '
                        f"before {action}.",
                    )

    def test_an_environment_whose_bridge_went_silent_is_refused_too(self):
        """The derived status again, on the restart path: a bridge that stopped heartbeating ages
        to offline, and a restart onto it would queue a spawn nothing can claim."""
        self._seed_spec()
        self._seed_session(spawn_spec_id=SPEC_ID)
        self._write(
            "UPDATE environments SET last_seen = ? WHERE id = ?",
            ("2020-01-01T00:00:00Z", ENVIRONMENT_ID),
        )
        response = self._control("restart")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("is offline; assign a live environment before restart.", response.json()["detail"])

    # ── the accepting side ───────────────────────────────────────────────────────────────────

    def test_a_restart_creates_a_spawn_request_that_reuses_the_saved_backing(self):
        """Restart and recreate differ in ONE thing — the resume policy — and it is the whole point
        of the pair. Asserting the row rather than the response, because the policy is what the
        bridge reads."""
        self._seed_spec()
        self._seed_session(spawn_spec_id=SPEC_ID)
        self.assertEqual(self._control("restart").status_code, 200)
        row = self._read(
            "SELECT resume_policy, session_handle FROM spawn_requests WHERE agent_id = ?"
            " ORDER BY created_at DESC",
            (AGENT_ID,),
        )
        self.assertEqual(row["resume_policy"], "native_first")
        self.assertEqual(
            row["session_handle"], "thread-abc",
            "restart REUSES the backing, so the handle has to survive into the spawn request",
        )

    def test_a_recreate_discards_the_saved_context_instead(self):
        self._seed_spec()
        self._seed_session(spawn_spec_id=SPEC_ID)
        self.assertEqual(self._control("recreate").status_code, 200)
        row = self._read(
            "SELECT resume_policy, session_handle FROM spawn_requests WHERE agent_id = ?"
            " ORDER BY created_at DESC",
            (AGENT_ID,),
        )
        self.assertEqual(row["resume_policy"], "fresh_context")
        self.assertEqual(row["session_handle"], "", "recreate must not carry the old handle")
