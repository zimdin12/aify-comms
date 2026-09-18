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


from service.tests._base import FastApiTestCase


class AgentStatusReadGateTests(FastApiTestCase):
    """Plan 5 Tasks C1 + C2 — read-path live-worker gate."""

    # Plan 4 defaults are on; confirm via no-op PUT for explicitness.
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

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

    def _stamp_stale_cache(self, agent_id: str, status: str) -> None:
        """Put a cached `online`/`ready` entry with a far-future refresh_after into the IN-MEMORY
        live-status cache -- the post-PTY-exit stale state observed 2026-05-25 for
        graph-senior-dev. The read path serves that entry without recomputing, so the Plan 5
        gate is the only thing between it and the response. (This used to write the retired
        `agent_live_state` TABLE, which nothing reads, so the gate never fired.)"""
        from service.reconcilers.status_cache import _LIVE_STATE_CACHE

        _LIVE_STATE_CACHE[agent_id] = {
            "status": status, "reason": f"stale-{status}-cache-for-test", "environment_id": "",
            "session_id": "sess-fake", "terminal_id": "", "active_run_id": "",
            "refresh_after": "2099-01-01T00:00:00Z", "updated_at": "2026-05-25T19:29:10Z",
        }

    def _stamp_stale_online_cache(self, agent_id: str) -> None:
        self._stamp_stale_cache(agent_id, "online")

    def _stamp_stale_ready_cache(self, agent_id: str) -> None:
        """`ready` is just as wrong as `online` without a live wrapper PTY."""
        self._stamp_stale_cache(agent_id, "ready")

    def _insert_stale_synth_terminal(self, agent_id: str, runtime: str = "hermes") -> str:
        """Insert a stale `vterm_*` (synth/virtual) terminal_sessions row with
        status='running'. Plan 4 deprecated synth terminals for wrapper-backed
        runtimes, but pre-Plan-4 rows persist in operator DBs with no cleanup.
        The Plan 5 gate must NOT treat these as live (observed 2026-05-26 —
        sc-coder, sc-architect: stale vterm_* rows from 2026-05-24 kept them
        showing as `online` after Plan 5 deploy)."""

        async def _stamp():
            from service.db import get_db
            db = await get_db()
            try:
                # Disable FK checks for this insert so we don't have to
                # synthesize the full agent_sessions parent row — production
                # rows that exhibit the bug have valid FKs but stale status.
                await db.execute("PRAGMA foreign_keys = OFF")
                vterm_id = f"vterm_{agent_id}_stale_synth"
                await db.execute(
                    """INSERT INTO terminal_sessions
                    (id, session_id, agent_id, environment_id, bridge_id,
                     runtime, workspace, command, status, requested_by,
                     created_at, updated_at)
                    VALUES (?, ?, ?, '', '',
                            ?, '', '', 'running', '',
                            '2026-05-24T16:05:04Z', '2026-05-24T16:05:04Z')""",
                    (vterm_id, f"sess-{agent_id}", agent_id, runtime),
                )
                await db.commit()
                await db.execute("PRAGMA foreign_keys = ON")
                return vterm_id
            finally:
                await db.close()

        return asyncio.run(_stamp())

    def _read_agent_live_state(self, agent_id: str) -> dict:
        from service.reconcilers.status_cache import _LIVE_STATE_CACHE

        return dict(_LIVE_STATE_CACHE.get(agent_id) or {})

    # ------------------------------------------------------------------
    # Task C1 — read-path downgrades stale `online`
    # ------------------------------------------------------------------

    def test_get_agent_downgrades_stale_online_with_no_live_worker(self):
        """GET /api/v1/agents/{id} for a managed wrapper-backed agent
        with a stale `online` cache and no live terminal_sessions row
        must NOT return `online`. `ready` is just as wrong when the wrapper PTY is gone.

        READ-ONLY (2026-06-18, reverses the Plan-5 C2 writeback): the served response is
        corrected, and the cache row is NOT written on the read path."""
        cases = (
            ("codex", "codex-stale", self._stamp_stale_online_cache, "online"),
            ("hermes", "hermes-stale-ready", self._stamp_stale_ready_cache, "ready"),
        )
        for runtime, agent_id, stamp, cached_status in cases:
            with self.subTest(runtime=runtime, cached=cached_status):
                self._heartbeat_environment(runtime)
                self._register_managed_agent(agent_id=agent_id, runtime=runtime)
                stamp(agent_id)

                res = self.client.get(f"/api/v1/agents/{agent_id}")
                self.assertEqual(res.status_code, 200, res.text)
                body = res.json()
                agent = body["agent"]
                self.assertEqual(
                    agent["status"], "available",
                    f"expected downgrade to 'available' because no live terminal_sessions row "
                    f"exists; got {agent['status']!r} (body={body})",
                )
                self.assertEqual(
                    self._read_agent_live_state(agent_id).get("status"), cached_status,
                    "read path must not persist the downgrade (no write on read)",
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
    # Plan 5 follow-up (2026-05-26) — stale synth (`vterm_*`) rows must
    # NOT count as live workers for wrapper-backed runtimes.
    # ------------------------------------------------------------------

    def test_stale_synth_vterm_row_does_not_keep_agent_online(self):
        """sc-coder / sc-architect kept showing 'online' after Plan 5 deploy
        because a pre-Plan-4 `vterm_*` (synth/virtual) terminal_sessions row
        from 2026-05-24 was still marked status='running'. Plan 4 deprecated
        synth terminals for wrapper-backed runtimes (see
        `_synth_terminal_should_be_created`) but did not clean up old DB
        rows. The Plan 5 gate's `_has_live_terminal_session` must exclude
        these — only real wrapper PTY rows (`term_*`) should count as live
        workers for wrapper-backed runtimes."""
        self._heartbeat_environment("hermes")
        self._register_managed_agent(agent_id="hermes-stale-synth", runtime="hermes")
        self._stamp_stale_online_cache("hermes-stale-synth")
        self._insert_stale_synth_terminal("hermes-stale-synth", runtime="hermes")

        res = self.client.get("/api/v1/agents/hermes-stale-synth")
        self.assertEqual(res.status_code, 200, res.text)
        agent = res.json()["agent"]
        self.assertNotEqual(
            agent["status"], "online",
            f"Plan 5 follow-up: a stale vterm_* row must NOT keep "
            f"a wrapper-backed managed agent showing online. Got {agent['status']!r}",
        )
        self.assertEqual(agent["status"], "available")


if __name__ == "__main__":
    unittest.main()
