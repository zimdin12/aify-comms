"""Pi flip mechanics — Plan 2.

When a pi agent attempts to register as sessionMode=resident, the server
sets agents.runtime_state.pi_resident_pending_flip = True. The
_drain_and_flip_pi_resident_agents helper (Task 17) flips it to managed
once active runs drain.

This file follows the same unittest.TestCase + TestClient + init_db
pattern used by test_api_v2_regressions.py so it picks up the same
isolated temp DB per test.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.db import get_db, init_db
from service.routers.api_v2 import router


class _DummyWS:
    async def broadcast(self, *_args, **_kwargs):
        return None

    async def notify_agent(self, *_args, **_kwargs):
        return None


class PiResidentFlipRegistrationTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "aify-test-pi-flip.db"
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

    def _fetchone(self, query, params=()):
        async def _run():
            db = await get_db()
            try:
                cur = await db.execute(query, params)
                return await cur.fetchone()
            finally:
                await db.close()

        return asyncio.run(_run())

    def _runtime_state_for(self, agent_id):
        row = self._fetchone(
            "SELECT runtime_state FROM agents WHERE id = ?", (agent_id,)
        )
        if not row:
            return None
        return json.loads(row["runtime_state"] or "{}")

    def test_pi_resident_registration_marks_pending_flip(self):
        resp = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "test-pi-flip",
                "role": "tester",
                "runtime": "pi",
                "sessionMode": "resident",
                "sessionHandle": "session-handle-x",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        rs = self._runtime_state_for("test-pi-flip")
        self.assertIsNotNone(rs, "agent row should exist")
        self.assertTrue(
            rs.get("pi_resident_pending_flip") is True,
            f"pi resident registration must mark pending flip; got runtime_state={rs}",
        )

    def test_pi_managed_registration_does_not_mark_pending_flip(self):
        resp = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "test-pi-managed",
                "role": "tester",
                "runtime": "pi",
                "sessionMode": "managed",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        rs = self._runtime_state_for("test-pi-managed")
        self.assertIsNotNone(rs, "agent row should exist")
        self.assertIsNone(
            rs.get("pi_resident_pending_flip"),
            f"managed pi registration must NOT mark pending flip; got runtime_state={rs}",
        )

    def test_non_pi_resident_registration_does_not_mark_pending_flip(self):
        # Sanity guard: only pi-runtime resident registrations should
        # set the flag; claude/codex residents must remain untouched.
        resp = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "test-claude-resident",
                "role": "tester",
                "runtime": "claude",
                "sessionMode": "resident",
                "sessionHandle": "claude-handle",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        rs = self._runtime_state_for("test-claude-resident")
        self.assertIsNotNone(rs)
        self.assertIsNone(
            rs.get("pi_resident_pending_flip"),
            f"non-pi resident registration must NOT mark pending flip; got runtime_state={rs}",
        )


if __name__ == "__main__":
    unittest.main()
