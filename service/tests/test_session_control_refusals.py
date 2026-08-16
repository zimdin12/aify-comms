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
                        (f"spawn-{status}", SPEC_ID, ENVIRONMENT_ID, AGENT_ID, "codex", status,
                         "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z"),
                    )
                    response = self._control(action)
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertEqual(
                        response.json()["detail"],
                        f'Agent "{AGENT_ID}" already has pending spawn request "spawn-{status}"'
                        f" ({status}).",
                    )
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
