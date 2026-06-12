"""Root-cause-G parity for managed CLAUDE (2026-06-12, the graph-tech-lead strand).

A send to a managed claude-code agent whose sessions are ALL dead (exactly the state
after an environment-bridge restart retires every session) must cold-start a
spawn_request — previously the channel-mode claude branch only tried
`_ensure_managed_pty_for_dispatch` (None with nothing to launch into) and the run sat
queued until the 180s backstop FAILED it, while hermes/codex got the coldstart fallback.
"""

import sqlite3

from service.tests._base import FastApiTestCase


class DispatchClaudeColdstartTests(FastApiTestCase):
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
                        "runtime": "claude-code",
                        "modes": ["managed-warm"],
                        "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True},
                    }
                ],
                "metadata": {},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _register_dead_managed_claude(self, agent_id: str) -> None:
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": "claude-code",
                "sessionMode": "managed",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
                "capabilities": ["managed-run", "resume", "interrupt"],
                "runtimeConfig": {"channelEnabled": True},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        conn = sqlite3.connect(str(self._db_path))
        try:
            # The post-env-restart state: a session exists but is STOPPED, no terminal,
            # no live managed-wrapper-child bridge.
            conn.execute(
                """
                INSERT INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode, owner_mode,
                    terminal_id, terminal_status, status, started_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"session-{agent_id}", agent_id, "linux:test-host:default", "claude-code",
                    "/workspace", "managed-warm", "managed", "", "", "stopped",
                    "2026-06-12T00:00:00Z", "2026-06-12T00:00:00Z",
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _spawn_requests_for(self, agent_id: str) -> list[sqlite3.Row]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute(
                "SELECT id, status, runtime, environment_id FROM spawn_requests WHERE agent_id = ?",
                (agent_id,),
            ).fetchall())
        finally:
            conn.close()

    def test_send_to_dead_managed_claude_coldstarts_spawn_request(self):
        self._heartbeat_environment()
        self._register_dead_managed_claude("claude-cold")
        response = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "dashboard",
                "trigger": True,
                "to": "claude-cold",
                "type": "request",
                "subject": "coldstart-test",
                "body": "wake up",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        spawns = self._spawn_requests_for("claude-cold")
        self.assertTrue(
            any(str(s["status"]) in ("queued", "claimed") for s in spawns),
            f"a claimable spawn_request must be cold-started for a dead managed claude; got {[dict(s) for s in spawns]}",
        )
        spawn = next(s for s in spawns if str(s["status"]) in ("queued", "claimed"))
        self.assertEqual(str(spawn["runtime"]), "claude-code")
        self.assertEqual(str(spawn["environment_id"]), "linux:test-host:default")

    def test_send_is_idempotent_on_existing_claimable_spawn(self):
        self._heartbeat_environment()
        self._register_dead_managed_claude("claude-cold2")
        for _ in range(2):
            response = self.client.post(
                "/api/v1/messages/send",
                json={
                    "from_agent": "dashboard",
                    "trigger": True,
                    "to": "claude-cold2",
                    "type": "request",
                    "subject": "coldstart-twice",
                    "body": "wake up again",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
        claimable = [s for s in self._spawn_requests_for("claude-cold2") if str(s["status"]) in ("queued", "claimed")]
        self.assertEqual(len(claimable), 1, "duplicate sends must not pile up coldstart spawn_requests")
