"""An idle hermes channel sidecar's /dispatch/claim poll is its liveness heartbeat.

The poll upserts the sidecar's `channel-sidecar` bridge row (`_has_live_channel_sidecar` reads it); a claim
that does not declare that kind must not. The status word these tests once also asserted is aify-env's since
the 0.9.8 switch, and the legacy derivation that computed it here was deleted in 0.9.9 S3b.
"""

import sqlite3
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


from service.tests._base import FastApiTestCase


class StatusDeliverabilityTests(FastApiTestCase):
    # hermes is NOT in managed_via_wrapper here — the standalone channel
    # sidecar path (Task 1.5b) is the one under test, gated purely on the
    # channelEnabled flag, exactly like claude.
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex"]}

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

    def _insert_hermes_console_pty(self, agent_id: str) -> None:
        """A live hermes console PTY terminal_session — the visible-TUI console
        the delivery loop fronts. WS3 (2026-06-02): managed hermes is in
        _CHANNEL_SIDECAR_DELIVERY_RUNTIMES, so `online` requires BOTH this live
        console AND a live channel-sidecar claimer (visible-TUI HARD requirement).
        Mirrors _insert_claude_wrapper_pty for hermes."""
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            session_id = f"sess-{agent_id}"
            terminal_id = f"term-{agent_id}"
            now = _iso(datetime.now(timezone.utc))
            conn.execute(
                """
                INSERT OR REPLACE INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode, owner_mode,
                    terminal_id, terminal_status, status, started_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id, agent_id, "linux:test-host:default", "hermes",
                    "/workspace", "managed-warm", "managed", terminal_id, "attached",
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
                    "bridge-current", "hermes", "/workspace",
                    f"hermes-aify --aify-agent {agent_id}", "attached", "dashboard", now, now,
                ),
            )
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON")
        finally:
            conn.close()

    def _insert_managed_session(self, agent_id: str, runtime: str) -> None:
        """A managed agent_sessions row so the claim gate's managed-environment
        carve-out applies (mirrors a real warm managed hermes session). Without
        it, /dispatch/claim rejects the sidecar bridge as bridge_not_current."""
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            now = _iso(datetime.now(timezone.utc))
            conn.execute(
                """
                INSERT INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode, owner_mode,
                    terminal_id, terminal_status, status, started_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"sess-{agent_id}", agent_id, "linux:test-host:default", runtime,
                    "/workspace", "managed-warm", "managed", "", "",
                    "running", now, now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _channel_sidecar_claim(self, agent_id: str, *, bridge_id: str) -> dict:
        """Idle channel-sidecar poll: a /dispatch/claim with no queued run,
        declaring bridgeKind='channel-sidecar' (Task 1.5b flag)."""
        res = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": agent_id,
                "bridgeId": bridge_id,
                "machineId": "linux:test-host",
                "bridgeKind": "channel-sidecar",
                "executionModes": ["channel", "resident"],
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def _channel_sidecar_bridge_row(self, agent_id: str) -> tuple | None:
        conn = sqlite3.connect(str(self._db_path))
        try:
            return conn.execute(
                """
                SELECT id, bridge_kind, last_seen, COALESCE(superseded_by, '')
                FROM bridge_instances
                WHERE agent_id = ? AND bridge_kind = 'channel-sidecar'
                """,
                (agent_id,),
            ).fetchone()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Task 1.6b — the idle claim poll IS the liveness heartbeat
    # ------------------------------------------------------------------
    def test_idle_channel_sidecar_claim_upserts_bridge_row(self):
        """An idle hermes sidecar polls /dispatch/claim continuously even with
        NO queued run. That poll must upsert the channel-sidecar bridge row, the
        sidecar's liveness (`_has_live_channel_sidecar`)."""
        self._heartbeat_environment("hermes")
        self._register_managed(agent_id="hermes-idle", runtime="hermes", channel_enabled=True)
        self._insert_managed_session("hermes-idle", "hermes")
        self._insert_hermes_console_pty("hermes-idle")

        self.assertIsNone(self._channel_sidecar_bridge_row("hermes-idle"))

        # Idle claim poll (no queued run) declaring channel-sidecar.
        body = self._channel_sidecar_claim("hermes-idle", bridge_id="hermes-channel-linux:test-host")
        self.assertIsNone(body.get("run"), f"no queued run should be claimed; got {body}")

        # The poll itself upserted a fresh channel-sidecar bridge row.
        row = self._channel_sidecar_bridge_row("hermes-idle")
        self.assertIsNotNone(row, "idle channel-sidecar claim must upsert a channel-sidecar bridge row")
        self.assertEqual(row[1], "channel-sidecar")
        self.assertEqual(row[3], "", "the upserted row must not be superseded")

    def test_repeated_idle_channel_sidecar_claims_are_idempotent(self):
        """Each poll just refreshes last_seen — no duplicate rows, no
        supersession churn."""
        self._heartbeat_environment("hermes")
        self._register_managed(agent_id="hermes-poller", runtime="hermes", channel_enabled=True)
        self._insert_managed_session("hermes-poller", "hermes")
        self._insert_hermes_console_pty("hermes-poller")
        for _ in range(3):
            self._channel_sidecar_claim("hermes-poller", bridge_id="hermes-channel-linux:test-host")
        conn = sqlite3.connect(str(self._db_path))
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM bridge_instances WHERE agent_id = ? AND bridge_kind = 'channel-sidecar'",
                ("hermes-poller",),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1, "repeated idle polls must not create duplicate channel-sidecar rows")

    def test_idle_non_sidecar_claim_does_not_upsert_channel_sidecar_row(self):
        """A claim that does NOT declare bridgeKind='channel-sidecar' (e.g. the
        environment bridge or a wrapper-child poll) must not synthesize a
        channel-sidecar liveness row."""
        self._heartbeat_environment("hermes")
        self._register_managed(agent_id="hermes-envpoll", runtime="hermes", channel_enabled=True)
        res = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "hermes-envpoll",
                "bridgeId": "bridge-current",
                "machineId": "linux:test-host",
                "executionModes": ["channel", "resident"],
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIsNone(
            self._channel_sidecar_bridge_row("hermes-envpoll"),
            "a non-sidecar claim must not create a channel-sidecar bridge row",
        )


if __name__ == "__main__":
    unittest.main()
