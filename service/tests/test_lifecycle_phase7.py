import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.db import get_db, init_db
from service.routers import api_v2
from service.routers.api_v2 import router


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


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


class LifecyclePhase7Tests(unittest.TestCase):
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

    def tearDown(self):
        self.client.close()
        self._tmpdir.cleanup()

    def _register(self, agent_id: str, *, role: str = "coder", **extra):
        payload = {"agentId": agent_id, "role": role}
        payload.update(extra)
        response = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _heartbeat_environment(self, **extra):
        payload = {
            "id": "linux:test-host:default",
            "label": "Linux on test-host",
            "machineId": "linux:test-host",
            "os": "linux",
            "kind": "linux",
            "bridgeId": "bridge-current",
            "cwdRoots": ["/workspace"],
            "runtimes": [
                {
                    "runtime": "codex",
                    "modes": ["managed-warm"],
                    "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True},
                }
            ],
            "metadata": {},
        }
        payload.update(extra)
        response = self.client.post("/api/v1/environments/heartbeat", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["environment"]

    def _fetchone(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                cursor = await db.execute(query, params)
                return await cursor.fetchone()
            finally:
                await db.close()
        return asyncio.run(_run())

    def _fetchall(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                cursor = await db.execute(query, params)
                return await cursor.fetchall()
            finally:
                await db.close()
        return asyncio.run(_run())

    def _execute(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                await db.execute(query, params)
                await db.commit()
            finally:
                await db.close()
        asyncio.run(_run())

    def _seed_ended_session(self, agent_id, *, runtime="codex", environment_id="linux:test-host:default",
                            workspace="/workspace/project", spawn_spec_id="spec-prior"):
        # A previously-managed agent leaves an ended agent_sessions row + spawn_spec
        # behind; cold-start clones environment/runtime/workspace from it.
        now = _iso(datetime.now(timezone.utc))
        self._execute(
            """
            INSERT INTO spawn_specs (id, agent_id, environment_id, runtime, workspace, model, profile, mode,
                system_prompt, standing_instructions, env_vars, channel_ids, budget_policy, context_policy,
                restart_policy, metadata, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (spawn_spec_id, agent_id, environment_id, runtime, workspace, "", "", "managed-warm",
             "", "", "{}", "[]", "{}", "{}", "{}", "{}", now, now),
        )
        self._execute(
            """
            INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, mode, spawn_spec_id,
                spawn_request_id, status, started_at, last_seen, ended_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (f"sess-{agent_id}", agent_id, environment_id, runtime, workspace, "managed-warm",
             spawn_spec_id, None, "ended", now, now, now),
        )

    def _coldstart(self, agent_id, *, runtime="codex", requested_by="alice"):
        async def _run():
            db = await get_db()
            try:
                settings = await api_v2._load_settings(db)
                result = await api_v2._coldstart_spawn_request_for_dispatch(
                    db, agent_id, runtime=runtime, settings=settings, requested_by=requested_by,
                )
                await db.commit()
                return result
            finally:
                await db.close()
        return asyncio.run(_run())

    def test_coldstart_creates_queued_spawn_request_for_cold_managed_agent(self):
        self._heartbeat_environment()
        self._register("worker", runtime="codex", sessionMode="managed")
        self._seed_ended_session("worker")

        created = self._coldstart("worker")
        self.assertTrue(created, "cold-start should report a created spawn request")

        rows = self._fetchall(
            "SELECT agent_id, environment_id, runtime, workspace, status, mode FROM spawn_requests WHERE agent_id = ?",
            ("worker",),
        )
        self.assertEqual(len(rows), 1, "exactly one spawn_request created")
        row = rows[0]
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["environment_id"], "linux:test-host:default")
        self.assertEqual(row["runtime"], "codex")
        self.assertEqual(row["workspace"], "/workspace/project")
        self.assertEqual(row["mode"], "managed-warm")

    def test_coldstart_is_idempotent_when_a_claimable_spawn_request_exists(self):
        self._heartbeat_environment()
        self._register("worker", runtime="codex", sessionMode="managed")
        self._seed_ended_session("worker")

        first = self._coldstart("worker")
        second = self._coldstart("worker")
        self.assertTrue(first)
        self.assertFalse(second, "second cold-start must NOT create a duplicate spawn request")
        rows = self._fetchall("SELECT id FROM spawn_requests WHERE agent_id = ? AND status IN ('queued','claimed')", ("worker",))
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
