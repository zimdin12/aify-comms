"""Regression: stale Hermes bridges must not claim current channel runs.

Hermes wrapper-backed managed delivery uses execution_mode='channel'. The stale
bridge guard must include Hermes so an old bridge cannot skip the current
bridge/runtime_state check and steal a queued run.
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


class HermesStaleBridgeClaimGuardTests(unittest.TestCase):
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
        self.client.put("/api/v1/settings", json={"managed_via_wrapper": ["codex", "hermes"]})

    def tearDown(self):
        self.client.close()
        self._tmpdir.cleanup()

    def _heartbeat_environment(self) -> None:
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
                        "runtime": "hermes",
                        "modes": ["managed-warm"],
                        "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True},
                    }
                ],
                "metadata": {},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _register_hermes_agent(self) -> None:
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "hermes-stale-bridge",
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
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                """
                INSERT INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode, owner_mode,
                    terminal_id, terminal_status, status, started_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "session-hermes-stale-bridge",
                    "hermes-stale-bridge",
                    "linux:test-host:default",
                    "hermes",
                    "/workspace",
                    "managed-warm",
                    "managed",
                    "term-hermes-stale-bridge",
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
                    "term-hermes-stale-bridge",
                    "session-hermes-stale-bridge",
                    "hermes-stale-bridge",
                    "linux:test-host:default",
                    "bridge-current",
                    "hermes",
                    "/workspace",
                    "hermes-aify --aify-agent hermes-stale-bridge",
                    "running",
                    "dashboard",
                    "2099-01-01T00:00:00Z",
                    "2099-01-01T00:00:00Z",
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _dispatch_to_hermes(self) -> str:
        response = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "dashboard",
                "to": "hermes-stale-bridge",
                "type": "request",
                "subject": "channel-claim",
                "body": "hello via channel",
                "trigger": True,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        runs = response.json().get("dispatchRuns") or []
        self.assertTrue(runs, response.json())
        return runs[0]["runId"]

    def test_stale_and_current_environment_bridge_claims_are_blocked(self):
        self._heartbeat_environment()
        self._register_hermes_agent()
        run_id = self._dispatch_to_hermes()

        stale_claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "hermes-stale-bridge",
                "bridgeId": "bridge-stale",
                "machineId": "linux:test-host",
                "executionModes": ["channel"],
            },
        )
        self.assertEqual(stale_claim.status_code, 200, stale_claim.text)
        stale_body = stale_claim.json()
        self.assertIsNone(stale_body.get("run"), stale_body)
        self.assertIn(
            (stale_body.get("blockedBy") or {}).get("reason"),
            {"bridge_not_current", "environment_bridge_not_current", "managed_wrapper_child_required"},
            stale_body,
        )

        current_claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "hermes-stale-bridge",
                "bridgeId": "bridge-current",
                "machineId": "linux:test-host",
                "executionModes": ["channel"],
            },
        )
        self.assertEqual(current_claim.status_code, 200, current_claim.text)
        current_body = current_claim.json()
        self.assertIsNone(current_body.get("run"), current_body)
        self.assertEqual((current_body.get("blockedBy") or {}).get("reason"), "managed_wrapper_child_required")


if __name__ == "__main__":
    unittest.main()
