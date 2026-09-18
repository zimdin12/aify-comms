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

    def test_send_to_dead_managed_claude_coldstarts_ONE_spawn_request(self):
        """The first send cold-starts a claimable spawn_request for the right runtime and
        environment; a second send reuses it rather than piling up another."""
        self._heartbeat_environment()
        self._register_dead_managed_claude("claude-cold")
        for _ in range(2):
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
        claimable = [s for s in spawns if str(s["status"]) in ("queued", "claimed")]
        self.assertEqual(
            len(claimable), 1,
            "a dead managed claude must get exactly one claimable coldstart spawn_request; "
            f"got {[dict(s) for s in spawns]}",
        )
        self.assertEqual(str(claimable[0]["runtime"]), "claude-code")
        self.assertEqual(str(claimable[0]["environment_id"]), "linux:test-host:default")
