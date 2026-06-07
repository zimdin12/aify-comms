"""Session-control Stop/Restart/Reset must enqueue a terminal 'stop' to kill the live PTY.

Lifecycle gap (2026-06-07): the managed Stop button routes through POST /sessions/{id}/control,
which flipped DB status + cancelled spawns but never enqueued a terminal stop — so the
claude/hermes PTY lingered as a headless orphan until a reaper / the next Restart's reap-prior.
Only the agent-control stop killed the PTY. Now session-control halts the running backing too.
"""

import sqlite3

from service.tests._base import FastApiTestCase


class SessionStopKillsTerminalTests(FastApiTestCase):
    def _register(self, agent_id, **extra):
        payload = {"agentId": agent_id, "role": "coder"}
        payload.update(extra)
        resp = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(resp.status_code, 200, resp.text)

    def _seed_managed_terminal(self, agent_id, *, status="attached"):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            session_id = f"session_{agent_id}"
            terminal_id = f"term_{agent_id}"
            now = "2099-01-01T00:00:00Z"
            # A real environment row so the FK-enforced terminal_controls insert succeeds
            # (in production a terminal always has a live environment).
            env_id = f"env_{agent_id}"
            bridge_id = f"bridge_{agent_id}"
            conn.execute(
                "INSERT INTO environments (id, machine_id, bridge_id, registered_at, last_seen) VALUES (?,?,?,?,?)",
                (env_id, "test-host", bridge_id, now, now),
            )
            conn.execute(
                "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, workspace, "
                "started_at, last_seen, terminal_id, terminal_status, owner_mode) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (session_id, agent_id, env_id, "claude-code", "running", "/workspace", now, now, terminal_id, status, "managed"),
            )
            conn.execute(
                "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id, runtime, "
                "workspace, command, status, requested_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (terminal_id, session_id, agent_id, env_id, bridge_id, "claude-code", "/workspace", "claude-aify", status, "dashboard", now, now),
            )
            conn.commit()
        finally:
            conn.close()
        return terminal_id, session_id

    def _stop_controls(self, terminal_id):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(
                "SELECT id FROM terminal_controls WHERE terminal_id = ? AND action = 'stop'",
                (terminal_id,),
            ).fetchall()
        finally:
            conn.close()

    def test_session_stop_enqueues_terminal_stop(self):
        self._register("ssk-agent", runtime="claude-code", sessionMode="managed")
        terminal_id, session_id = self._seed_managed_terminal("ssk-agent")
        resp = self.client.post(f"/api/v1/sessions/{session_id}/control", json={"action": "stop", "from_agent": "dashboard"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(
            self._stop_controls(terminal_id),
            "session Stop must enqueue a terminal 'stop' to kill the live PTY",
        )

    def test_session_stop_skips_already_stopped_terminal(self):
        self._register("ssk-agent2", runtime="claude-code", sessionMode="managed")
        terminal_id, session_id = self._seed_managed_terminal("ssk-agent2", status="stopped")
        resp = self.client.post(f"/api/v1/sessions/{session_id}/control", json={"action": "stop", "from_agent": "dashboard"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertFalse(
            self._stop_controls(terminal_id),
            "an already-stopped terminal must not get a redundant stop control",
        )
