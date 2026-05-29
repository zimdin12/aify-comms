"""Regression tests for case-insensitive machine_id handling.

Root cause: the host machine_id is "<platform>:<hostname>" (e.g.
"win32:StevenZ-L"). Different launch paths report the hostname with
different casing ("win32:StevenZ-L" vs "win32:STEVENZ-L"). machine_id was
compared CASE-SENSITIVELY in bridge supersession, so a re-registered worker
under a different casing did NOT supersede its prior bridge -> duplicate
live bridge_instances per agent. The fix normalizes machine_id to lowercase
at every storage and comparison site.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.db import get_db, init_db
from service.routers.api_v2 import router, _normalize_machine_id


class _DummyWS:
    def __init__(self):
        self.broadcasts = []
        self.notifications = []

    async def broadcast(self, *_args, **_kwargs):
        self.broadcasts.append((_args, _kwargs))
        return None

    async def notify_agent(self, *_args, **_kwargs):
        self.notifications.append((_args, _kwargs))
        return None


class NormalizeMachineIdUnitTests(unittest.TestCase):
    def test_lowercases_and_strips(self):
        self.assertEqual(_normalize_machine_id("win32:StevenZ-L"), "win32:stevenz-l")
        self.assertEqual(_normalize_machine_id("win32:STEVENZ-L"), "win32:stevenz-l")
        self.assertEqual(_normalize_machine_id("  win32:StevenZ-L  "), "win32:stevenz-l")

    def test_handles_empty_and_none(self):
        self.assertEqual(_normalize_machine_id(None), "")
        self.assertEqual(_normalize_machine_id(""), "")

    def test_idempotent(self):
        once = _normalize_machine_id("win32:StevenZ-L")
        self.assertEqual(_normalize_machine_id(once), once)


class MachineIdCasingSupersessionTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "aify-test.db"
        asyncio.run(init_db(self._db_path))

        app = FastAPI()
        self.ws = _DummyWS()
        app.state.ws_manager = self.ws
        app.state.config = SimpleNamespace(data_dir=self._tmpdir.name)
        app.state.testing = True
        app.include_router(router, prefix="/api/v1")
        self.client = TestClient(app)
        self.client.put(
            "/api/v1/settings",
            json={
                "insert_messages_via_console": True,
                "managed_via_wrapper": False,
                "managed_pty_eager_spawn": False,
            },
        )

    def tearDown(self):
        self.client.close()
        self._tmpdir.cleanup()

    def _register(self, **payload):
        response = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _fetchone(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                cursor = await db.execute(query, params)
                return await cursor.fetchone()
            finally:
                await db.close()

        return asyncio.run(_run())

    def test_reregister_with_different_hostname_casing_supersedes_prior_bridge(self):
        # First bridge for the agent under one casing.
        self._register(
            agentId="cased-worker",
            role="coder",
            runtime="codex",
            sessionMode="managed",
            launchMode="managed",
            sessionHandle="codex-thread-1",
            machineId="win32:StevenZ-L",
            bridgeId="bridge-A",
            capabilities=["managed-run", "native-managed-run", "resume", "interrupt", "steer"],
        )
        bridge_a = self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id=?", ("bridge-A",))
        self.assertEqual(bridge_a["superseded_by"], "")

        # Re-register the SAME agent+runtime under a DIFFERENT hostname casing.
        # Case-sensitive comparison would never match the prior row, leaving a
        # duplicate live bridge. With normalization, bridge-A must be superseded.
        self._register(
            agentId="cased-worker",
            role="coder",
            runtime="codex",
            sessionMode="managed",
            launchMode="managed",
            sessionHandle="codex-thread-1",
            machineId="win32:STEVENZ-L",
            bridgeId="bridge-B",
            capabilities=["managed-run", "native-managed-run", "resume", "interrupt", "steer"],
        )

        prior = self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id=?", ("bridge-A",))
        self.assertEqual(
            prior["superseded_by"],
            "bridge-B",
            "casing difference must NOT prevent supersession of the prior bridge",
        )
        latest = self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id=?", ("bridge-B",))
        self.assertEqual(latest["superseded_by"], "", "newest bridge stays primary")

    def test_agent_machine_id_stored_lowercased(self):
        self._register(
            agentId="store-worker",
            role="coder",
            runtime="codex",
            sessionMode="managed",
            launchMode="managed",
            machineId="win32:StevenZ-L",
            bridgeId="bridge-S",
            capabilities=["managed-run"],
        )
        agent = self._fetchone("SELECT machine_id FROM agents WHERE id=?", ("store-worker",))
        self.assertEqual(agent["machine_id"], "win32:stevenz-l")
        bridge = self._fetchone("SELECT machine_id FROM bridge_instances WHERE id=?", ("bridge-S",))
        self.assertEqual(bridge["machine_id"], "win32:stevenz-l")


if __name__ == "__main__":
    unittest.main()
