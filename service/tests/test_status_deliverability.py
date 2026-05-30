"""Task 1.6 (2026-05-30, Runtime Symmetry & Session Governance) — truthful
status for managed hermes.

`_compute_agent_status` / `_compute_live_status_cache` must report `online`
(deliverable, idle) for a managed hermes agent ONLY when the agent is actually
deliverable: channel-enabled (the hermes-aify wrapper exported
AIFY_CHANNELS_ENABLED=1) AND a LIVE channel sidecar (hermes-channel.js) is
heartbeating. With no/stale sidecar heartbeat the agent must report
`available` — never a falsely positive `online`/`ready`.

This MIRRORS claude's existing `has_live_worker` gate. For claude the live
signal is the claude-aify wrapper PTY terminal_session (the sidecar runs INSIDE
that PTY). The standalone hermes sidecar owns no PTY, so its liveness signal is
its own channel-sidecar bridge heartbeat (kept fresh by the /dispatch/claim
poll loop, which updates bridge_instances.last_seen) — the runtime-agnostic
equivalent. See the shared deliverability branch in _compute_live_status_cache.

claude + resident behavior is unchanged (regression guards below); codex/pi
(no channel flag) are unaffected.
"""

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.db import init_db
from service.routers.api_v2 import router


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _DummyWS:
    async def broadcast(self, *_args, **_kwargs):
        return None

    async def notify_agent(self, *_args, **_kwargs):
        return None


class StatusDeliverabilityTests(unittest.TestCase):
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
        # hermes is NOT in managed_via_wrapper here — the standalone channel
        # sidecar path (Task 1.5b) is the one under test, gated purely on the
        # channelEnabled flag, exactly like claude.
        self.client.put(
            "/api/v1/settings",
            json={"managed_via_wrapper": ["codex"]},
        )

    def tearDown(self):
        self.client.close()
        self._tmpdir.cleanup()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
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
        res = self.client.post("/api/v1/environments/heartbeat", json=payload)
        self.assertEqual(res.status_code, 200, res.text)

    def _register_managed(self, *, agent_id: str, runtime: str, channel_enabled: bool) -> None:
        runtime_config = {}
        if channel_enabled:
            runtime_config["channelEnabled"] = True
        if runtime == "codex":
            runtime_config["appServerUrl"] = "ws://127.0.0.1:1234"
        res = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": runtime,
                "sessionMode": "managed",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
                "capabilities": ["resume", "interrupt"],
                "runtimeConfig": runtime_config,
            },
        )
        self.assertEqual(res.status_code, 200, res.text)

    def _stamp_channel_sidecar_bridge(self, agent_id: str, *, fresh: bool) -> None:
        """Insert a channel-sidecar bridge_instances row for the agent (the
        standalone hermes-channel.js / claude-channel.js process). `fresh`
        controls last_seen — a fresh row models a LIVE sidecar; an aged row
        models a dead/stale sidecar."""
        now = datetime.now(timezone.utc)
        last_seen = _iso(now if fresh else now - timedelta(minutes=20))
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO bridge_instances (
                    id, agent_id, machine_id, runtime, session_mode, session_handle,
                    terminal_id, bridge_kind, registered_at, last_seen, superseded_by, superseded_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "hermes-channel-linux:test-host",
                    agent_id,
                    "linux:test-host",
                    "hermes",
                    "managed",
                    "",
                    "",
                    "channel-sidecar",
                    _iso(now - timedelta(minutes=1)),
                    last_seen,
                    "",
                    None,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _insert_claude_wrapper_pty(self, agent_id: str) -> None:
        """A live claude-aify wrapper PTY terminal_session — claude's existing
        has_live_worker signal (the sidecar runs inside this PTY)."""
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            session_id = f"sess-{agent_id}"
            terminal_id = f"term-{agent_id}"
            now = _iso(datetime.now(timezone.utc))
            conn.execute(
                """
                INSERT INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode, owner_mode,
                    terminal_id, terminal_status, status, started_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id, agent_id, "linux:test-host:default", "claude-code",
                    "/workspace", "managed-warm", "managed", terminal_id, "running",
                    "running", now, now,
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
                    terminal_id, session_id, agent_id, "linux:test-host:default",
                    "bridge-current", "claude-code", "/workspace",
                    f"claude-aify --aify-agent {agent_id}", "running", "dashboard", now, now,
                ),
            )
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON")
        finally:
            conn.close()

    def _status(self, agent_id: str) -> str:
        res = self.client.get(f"/api/v1/agents/{agent_id}")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["agent"]["status"]

    # ------------------------------------------------------------------
    # hermes — the new behavior
    # ------------------------------------------------------------------
    def test_managed_hermes_channel_enabled_with_live_sidecar_is_online(self):
        self._heartbeat_environment("hermes")
        self._register_managed(agent_id="hermes-live", runtime="hermes", channel_enabled=True)
        self._stamp_channel_sidecar_bridge("hermes-live", fresh=True)
        self.assertIn(
            self._status("hermes-live"),
            {"online", "ready"},
            "channel-enabled managed hermes WITH a live sidecar heartbeat must be deliverable (online/ready)",
        )

    def test_managed_hermes_channel_enabled_without_sidecar_is_available_not_online(self):
        self._heartbeat_environment("hermes")
        self._register_managed(agent_id="hermes-nosidecar", runtime="hermes", channel_enabled=True)
        # No channel-sidecar bridge row at all → not deliverable.
        status = self._status("hermes-nosidecar")
        self.assertNotIn(
            status, {"online", "ready"},
            f"channel-enabled hermes with NO live sidecar must not be falsely online; got {status!r}",
        )
        self.assertEqual(status, "available", f"expected available; got {status!r}")

    def test_managed_hermes_channel_enabled_with_stale_sidecar_is_available(self):
        self._heartbeat_environment("hermes")
        self._register_managed(agent_id="hermes-stale", runtime="hermes", channel_enabled=True)
        self._stamp_channel_sidecar_bridge("hermes-stale", fresh=False)
        status = self._status("hermes-stale")
        self.assertNotIn(
            status, {"online", "ready"},
            f"a STALE sidecar heartbeat must not keep hermes online; got {status!r}",
        )
        self.assertEqual(status, "available", f"expected available; got {status!r}")

    # ------------------------------------------------------------------
    # claude — regression guard (unchanged)
    # ------------------------------------------------------------------
    def test_managed_claude_with_live_wrapper_pty_is_online(self):
        self._heartbeat_environment("claude-code")
        self._register_managed(agent_id="claude-live", runtime="claude-code", channel_enabled=True)
        self._insert_claude_wrapper_pty("claude-live")
        self.assertEqual(
            self._status("claude-live"), "online",
            "managed claude with a live wrapper PTY must stay online (unchanged)",
        )

    def test_managed_claude_without_live_worker_is_available(self):
        self._heartbeat_environment("claude-code")
        self._register_managed(agent_id="claude-dead", runtime="claude-code", channel_enabled=True)
        # No wrapper PTY, no sidecar → claude already degrades to available.
        self.assertEqual(
            self._status("claude-dead"), "available",
            "managed claude with no live worker must be available (unchanged)",
        )

    # ------------------------------------------------------------------
    # codex / pi — cheap unchanged guard
    # ------------------------------------------------------------------
    def test_managed_codex_unaffected_by_channel_deliverability_gate(self):
        """codex is not a channel-flag runtime; the hermes deliverability gate
        must not change its status. Managed codex with no live worker is
        available (its existing taxonomy), and a channel-sidecar row must not
        make it online (codex is not sidecar-channel)."""
        self._heartbeat_environment("codex")
        self._register_managed(agent_id="codex-x", runtime="codex", channel_enabled=False)
        status = self._status("codex-x")
        self.assertEqual(status, "available", f"managed codex no-worker should be available; got {status!r}")


if __name__ == "__main__":
    unittest.main()
