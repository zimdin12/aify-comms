"""Stop, Restart, Recreate and CLI-takeover: the refusals behind those four buttons.

`POST /sessions/{id}/control` is the dashboard's session lifecycle.

    400 Unsupported session control action "<a>"
    404 Session "<s>" not found

SINCE D8 A RESTART OR RECREATE IS NEVER PREPARED HERE. A defined agent's goes to its host's lifecycle queue
before anything else runs (test_legacy_routes_delegate_defined_agents.py), and one no host defines is refused
(`undefined_refusal`, test_api_v2_regressions.py). The refusals about an in-flight spawn, the session's spawn
spec and its environment, and the "only if nothing is live" precondition, went with the code that made them.

TWO LISTS DECIDE WHAT AN ACTION DOES, and drift between them is a 500 rather than a refusal: the
allowlist `{stop, restart, recreate, cli_takeover}` and the `next_status` dict indexed with `[action]`
immediately after. Any action admitted by the first and absent from the second is a KeyError on a
dashboard button. They are cross-checked here by driving the two that still reach the dict (stop and
CLI takeover) and asserting the status each one leaves behind, rather than by reading the two literals.
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

#: The accepted actions that reach the `next_status` dict. Restart and recreate are accepted too, and since D8
#: never reach it: a defined agent's goes to the lifecycle queue and an undefined agent's is refused.
ACCEPTED_ACTIONS = {
    "stop": "stopped",
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

    def test_a_STOP_is_not_blocked_by_a_pending_spawn(self):
        """An operator stopping a session whose spawn is still queued is cancelling exactly that."""
        self._seed_spec()
        self._seed_session(spawn_spec_id=SPEC_ID)
        self._write(
            "INSERT INTO spawn_requests (id, spawn_spec_id, environment_id, agent_id, runtime,"
            " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("spawn-queued", SPEC_ID, ENVIRONMENT_ID, AGENT_ID, "codex", "queued",
             "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z"),
        )
        self.assertEqual(self._control("stop").status_code, 200)
