"""Plan 5 follow-up (2026-05-26) — /api/v1/dispatch must NOT downgrade
wrapper-backed managed codex/hermes dispatches to PTY-input
(console_recipients).

Bug (feature-surrounding review): for the /dispatch endpoint,
api_v2.py:10927-10948 received execution_mode='channel' from
_agent_execution_mode (correctly), then unconditionally fell through into
the _managed_terminal_backing_enabled branch and overwrote
execution_mode=None, routing the message via PTY keystrokes
(console_recipients). The operator banned scrambled-text PTY input:
"I do not want pseudo terminal input because i might write while other
agent sends message in and it gets scrambled. It is bad solution."

The fix narrows the PTY-input branch to execution_mode=='managed' only,
so wrapper-backed managed runtimes (where _agent_execution_mode returns
'channel') flow through to the channel-claim path.

Mirrors test_dispatch_channel_claim.py for the /messages/send path. Pi stays
native managed RPC because OMP is single-client.
"""

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.db import init_db
from service.routers.api_v2 import router


class _DummyWS:
    async def broadcast(self, *_args, **_kwargs):
        return None

    async def notify_agent(self, *_args, **_kwargs):
        return None


class DispatchEndpointWrapperBackedTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "aify-test.db"
        asyncio.run(init_db(self._db_path))

        app = FastAPI()
        app.state.ws_manager = _DummyWS()
        app.state.config = SimpleNamespace(data_dir=self._tmpdir.name)
        app.state.testing = True
        app.include_router(router, prefix="/api/v1")
        self.client = TestClient(app)
        self.client.put(
            "/api/v1/settings",
            json={"managed_via_wrapper": ["codex", "hermes"]},
        )

    def tearDown(self):
        self.client.close()
        self._tmpdir.cleanup()

    def _heartbeat_environment(self, runtime: str) -> None:
        response = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": "linux:test-host:default",
                "label": "Linux on test-host",
                "machineId": "linux:test-host",
                "os": "linux",
                "kind": "linux",
                "bridgeId": "bridge-current",
                "cwdRoots": ["/workspace"],
                "runtimes": [
                    {
                        "runtime": runtime,
                        "modes": ["managed-warm"],
                        "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True},
                    }
                ],
                "metadata": {},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _register_managed_agent(self, *, agent_id: str, runtime: str) -> None:
        runtime_config = {}
        if runtime == "codex":
            runtime_config["appServerUrl"] = "ws://127.0.0.1:1234"
        elif runtime == "hermes":
            runtime_config["gatewayUrl"] = "ws://127.0.0.1:9119/api/ws?token=t"
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": runtime,
                "sessionMode": "managed",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
                "capabilities": ["native-managed-run", "managed-run", "resume", "interrupt"],
                "runtimeConfig": runtime_config,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        if runtime in {"codex", "hermes"}:
            conn = sqlite3.connect(str(self._db_path))
            try:
                session_id = f"session-{agent_id}"
                terminal_id = f"term-{agent_id}"
                conn.execute(
                    """
                    INSERT INTO agent_sessions (
                        id, agent_id, environment_id, runtime, workspace, mode, owner_mode,
                        terminal_id, terminal_status, status, started_at, last_seen
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        session_id,
                        agent_id,
                        "linux:test-host:default",
                        runtime,
                        "/workspace",
                        "managed-warm",
                        "managed",
                        terminal_id,
                        "running",
                        "running",
                        "2099-01-01T00:00:00Z",
                        "2099-01-01T00:00:00Z",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO terminal_sessions (
                        id, session_id, agent_id, environment_id, bridge_id, runtime,
                        workspace, command, status, requested_by, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        terminal_id,
                        session_id,
                        agent_id,
                        "linux:test-host:default",
                        "bridge-current",
                        runtime,
                        "/workspace",
                        f"{runtime}-aify --aify-agent {agent_id}",
                        "running",
                        "dashboard",
                        "2099-01-01T00:00:00Z",
                        "2099-01-01T00:00:00Z",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def _assert_dispatch_endpoint_creates_channel_run(self, *, runtime: str, agent_id: str) -> None:
        response = self.client.post(
            "/api/v1/dispatch",
            json={
                "from_agent": "dashboard",
                "to": agent_id,
                "type": "request",
                "subject": "channel-route-test",
                "body": "hi via /dispatch endpoint",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        runs = body.get("runs") or body.get("dispatchRuns") or []
        self.assertTrue(
            runs,
            f"Plan 5: /dispatch must enqueue a run for wrapper-backed managed {runtime}; "
            f"got body: {body}",
        )
        run_id = runs[0].get("runId") or runs[0].get("id")
        self.assertTrue(
            run_id,
            f"Plan 5: /dispatch run entry must include runId for {runtime}; "
            f"got runs: {runs}",
        )
        # Read execution_mode from the dispatch_runs row directly — the
        # response body intentionally doesn't echo it back (and the test
        # cares about the persisted route, not the response shape).
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT id, execution_mode, status FROM dispatch_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(
            row,
            f"Plan 5: dispatch_runs row for {run_id} not found",
        )
        self.assertEqual(
            row["execution_mode"],
            "channel",
            f"Plan 5: /dispatch run for wrapper-backed managed {runtime} must persist "
            f"execution_mode='channel' (NOT fall through to PTY-input console_recipients). "
            f"Persisted: {dict(row)}",
        )

    def _assert_dispatch_endpoint_creates_native_managed_run(self, *, runtime: str, agent_id: str) -> None:
        response = self.client.post(
            "/api/v1/dispatch",
            json={
                "from_agent": "dashboard",
                "to": agent_id,
                "type": "request",
                "subject": "native-route-test",
                "body": f"hi via native managed {runtime}",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(
            body.get("consoleDeliveries") or [],
            [],
            f"Default /dispatch must not route native managed {runtime} via PTY input; body: {body}",
        )
        runs = body.get("runs") or body.get("dispatchRuns") or []
        self.assertTrue(runs, f"Expected a native managed dispatch run for {runtime}; got body: {body}")
        run_id = runs[0].get("runId") or runs[0].get("id")
        self.assertTrue(run_id, f"Expected runId for native managed {runtime}; got runs: {runs}")
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT id, execution_mode, dispatch_mode, status FROM dispatch_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["execution_mode"], "managed", dict(row))
        self.assertNotEqual(row["dispatch_mode"], "terminal", dict(row))

    def test_dispatch_endpoint_routes_codex_wrapper_backed_to_channel(self):
        self._heartbeat_environment("codex")
        self._register_managed_agent(agent_id="codex-managed", runtime="codex")
        self._assert_dispatch_endpoint_creates_channel_run(
            runtime="codex", agent_id="codex-managed"
        )

    def test_dispatch_endpoint_routes_hermes_wrapper_backed_to_channel(self):
        self._heartbeat_environment("hermes")
        self._register_managed_agent(agent_id="hermes-managed", runtime="hermes")
        self._assert_dispatch_endpoint_creates_channel_run(
            runtime="hermes", agent_id="hermes-managed"
        )

    def test_dispatch_endpoint_keeps_pi_and_opencode_on_native_managed_default(self):
        for runtime in ("pi", "opencode"):
            with self.subTest(runtime=runtime):
                agent_id = f"{runtime}-managed"
                self._heartbeat_environment(runtime)
                self._register_managed_agent(agent_id=agent_id, runtime=runtime)
                self._assert_dispatch_endpoint_creates_native_managed_run(
                    runtime=runtime,
                    agent_id=agent_id,
                )

    def test_dispatch_endpoint_keeps_codex_native_when_wrapper_backing_disabled(self):
        self.client.put("/api/v1/settings", json={"managed_via_wrapper": False})
        self._heartbeat_environment("codex")
        self._register_managed_agent(agent_id="codex-native-managed", runtime="codex")
        self._assert_dispatch_endpoint_creates_native_managed_run(
            runtime="codex",
            agent_id="codex-native-managed",
        )

    def test_dispatch_endpoint_wrapper_backed_without_session_does_not_queue_orphan_run(self):
        self._heartbeat_environment("hermes")
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "hermes-no-backing",
                "role": "coder",
                "runtime": "hermes",
                "sessionMode": "managed",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
                "capabilities": ["native-managed-run", "managed-run", "resume", "interrupt"],
                "runtimeConfig": {"gatewayUrl": "ws://127.0.0.1:9119/api/ws?token=t"},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        sent = self.client.post(
            "/api/v1/dispatch",
            json={
                "from_agent": "dashboard",
                "to": "hermes-no-backing",
                "type": "request",
                "subject": "no backing",
                "body": "this should fail before queueing",
            },
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        body = sent.json()
        self.assertTrue(body.get("ok"), body)
        self.assertTrue(body.get("notStarted"), body)
        self.assertEqual(body.get("runs") or body.get("dispatchRuns") or [], [])
        conn = sqlite3.connect(str(self._db_path))
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM dispatch_runs WHERE target_agent = ? AND status = 'queued'",
                ("hermes-no-backing",),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 0)

if __name__ == "__main__":
    unittest.main()
