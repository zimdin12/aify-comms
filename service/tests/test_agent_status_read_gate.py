"""Plan 5 (2026-05-25) Section C — `has_live_worker` gate in the agent read path.

The `_compute_live_status_cache` path correctly consults `terminal_sessions`
when it runs, but `refresh_after` is keyed on heartbeat freshness via
`_status_refresh_after`, not on worker presence. When a wrapper PTY exits
but a parallel heartbeat keeps the agent alive, the cache row stays
`status='online'` indefinitely and the read path returns the stale value
(observed 2026-05-25 — graph-senior-dev: agent_live_state.status='online'
terminal_id='' updated_at=19:29 Z, no live terminal_sessions row, but
GET /api/v1/agents/{id} returned 'online').

Plan 5 Tasks C1 + C2:
- C1: agent serializer downgrades stale cached 'online' to 'available'
      when no live terminal_sessions row exists for a managed
      wrapper-backed agent.
- C2: that downgrade is written back to agent_live_state so the next
      poll sees the correct value without re-running the check.
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.db import init_db
from service.routers.api_v2 import router


class _DummyWS:
    async def broadcast(self, *_args, **_kwargs):
        return None

    async def notify_agent(self, *_args, **_kwargs):
        return None


class AgentStatusReadGateTests(unittest.TestCase):
    """Plan 5 Tasks C1 + C2 — read-path live-worker gate."""

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

        # Plan 4 defaults are on; confirm via no-op PUT for explicitness.
        self.client.put(
            "/api/v1/settings",
            json={"managed_via_wrapper": ["codex", "hermes", "pi"]},
        )

    def tearDown(self):
        self.client.close()
        self._tmpdir.cleanup()

    def _heartbeat_environment(self, runtime: str) -> None:
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
                    "runtime": runtime,
                    "modes": ["managed-warm"],
                    "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True},
                }
            ],
            "metadata": {},
        }
        response = self.client.post("/api/v1/environments/heartbeat", json=payload)
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

    def _stamp_stale_online_cache(self, agent_id: str) -> None:
        """Write a cached `online` row to agent_live_state with a future
        refresh_after — mirrors the post-PTY-exit stale state observed
        2026-05-25 for graph-senior-dev. Far-future ISO timestamps
        guarantee `_refresh_expired_agent_live_states` won't touch it
        before the read-path gate runs."""

        async def _stamp():
            from service.db import get_db
            db = await get_db()
            try:
                await db.execute(
                    """INSERT OR REPLACE INTO agent_live_state
                    (agent_id, status, reason, environment_id, session_id,
                     terminal_id, active_run_id, refresh_after, updated_at)
                    VALUES (?, 'online', 'stale-cache-for-test', '',
                            'sess-fake', '', '',
                            '2099-01-01T00:00:00Z', '2026-05-25T19:29:10Z')""",
                    (agent_id,),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_stamp())

    def _read_agent_live_state(self, agent_id: str) -> dict:
        async def _read():
            from service.db import get_db
            db = await get_db()
            try:
                cursor = await db.execute(
                    "SELECT status, reason FROM agent_live_state WHERE agent_id = ?",
                    (agent_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    return {}
                return {"status": row["status"], "reason": row["reason"]}
            finally:
                await db.close()

        return asyncio.run(_read())

    # ------------------------------------------------------------------
    # Task C1 — read-path downgrades stale `online`
    # ------------------------------------------------------------------

    def test_get_agent_downgrades_stale_online_with_no_live_worker(self):
        """GET /api/v1/agents/{id} for a managed wrapper-backed agent
        with a stale `online` cache and no live terminal_sessions row
        must NOT return `online`."""
        self._heartbeat_environment("codex")
        self._register_managed_agent(agent_id="codex-stale", runtime="codex")
        self._stamp_stale_online_cache("codex-stale")

        res = self.client.get("/api/v1/agents/codex-stale")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        agent = body["agent"]
        self.assertNotEqual(
            agent["status"], "online",
            f"Plan 5 read-path gate: expected downgrade because no live "
            f"terminal_sessions row exists; got {agent['status']!r} (body={body})",
        )
        self.assertEqual(
            agent["status"], "available",
            f"Expected 'available' fallback; got {agent['status']!r}",
        )

    def test_list_agents_downgrades_stale_online_with_no_live_worker(self):
        """GET /api/v1/agents (list) honors the same gate as the single-agent
        endpoint."""
        self._heartbeat_environment("codex")
        self._register_managed_agent(agent_id="codex-stale-list", runtime="codex")
        self._stamp_stale_online_cache("codex-stale-list")

        res = self.client.get("/api/v1/agents")
        self.assertEqual(res.status_code, 200, res.text)
        agents = res.json()["agents"]
        self.assertIn("codex-stale-list", agents)
        self.assertNotEqual(
            agents["codex-stale-list"]["status"], "online",
            "list_agents must apply the same Plan 5 read-path gate as get_agent",
        )

    # ------------------------------------------------------------------
    # Task C2 — downgrade is written back to the cache
    # ------------------------------------------------------------------

    def test_downgrade_writeback_persists_to_agent_live_state(self):
        """After the read-path gate fires, agent_live_state.status must
        be updated so the dashboard's next poll sees the corrected value
        without re-running the live-worker check."""
        self._heartbeat_environment("codex")
        self._register_managed_agent(agent_id="codex-writeback", runtime="codex")
        self._stamp_stale_online_cache("codex-writeback")

        # First read — gate fires and the response is downgraded.
        res = self.client.get("/api/v1/agents/codex-writeback")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotEqual(res.json()["agent"]["status"], "online")

        # Cache row should reflect the downgrade.
        cached = self._read_agent_live_state("codex-writeback")
        self.assertNotEqual(
            cached.get("status"), "online",
            f"Plan 5 C2: agent_live_state should reflect downgrade after "
            f"read-path gate fires; got cached row {cached!r}",
        )
        self.assertEqual(
            cached.get("status"), "available",
            f"Cache should hold 'available' after gate fires; got {cached!r}",
        )


if __name__ == "__main__":
    unittest.main()
