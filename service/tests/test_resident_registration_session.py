import asyncio
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


class ResidentRegistrationSessionTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "aify-test-resident-session.db"
        asyncio.run(init_db(self._db_path))

        app = FastAPI()
        app.state.ws_manager = _DummyWS()
        app.state.config = SimpleNamespace(data_dir=self._tmpdir.name)
        app.state.testing = True
        app.include_router(router, prefix="/api/v1")
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self._tmpdir.cleanup()

    def test_resident_hermes_register_creates_dashboard_session(self):
        env = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": "windows:test-host:default",
                "label": "Windows Test",
                "machineId": "win32:TEST-HOST",
                "os": "win32",
                "kind": "local",
                "bridgeId": "env-bridge",
                "runtimes": [{"runtime": "hermes"}],
                "status": "offline",
            },
        )
        self.assertEqual(env.status_code, 200, env.text)

        reg = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "resident-hermes-ui",
                "role": "tester",
                "runtime": "hermes",
                "sessionMode": "resident",
                "sessionHandle": "hermes-session-1",
                "bridgeId": "resident-bridge-1",
                "machineId": "win32:test-host",
                "cwd": "C:/repo",
                "runtimeConfig": {"gatewayUrl": "ws://127.0.0.1:7777/api/ws?token=t"},
            },
        )
        self.assertEqual(reg.status_code, 200, reg.text)

        sessions = self.client.get("/api/v1/sessions", params={"agentId": "resident-hermes-ui"}).json()["sessions"]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["mode"], "resident")
        self.assertEqual(sessions[0]["ownerMode"], "resident")
        self.assertEqual(sessions[0]["environmentId"], "windows:test-host:default")
        self.assertEqual(sessions[0]["runtime"], "hermes")
        self.assertEqual(sessions[0]["sessionHandle"], "hermes-session-1")
        self.assertEqual(sessions[0]["status"], "running")


if __name__ == "__main__":
    unittest.main()
