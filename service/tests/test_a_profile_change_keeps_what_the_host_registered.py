"""`PATCH /agents/{id}/profile` changes role and name and leaves everything the host registered.

Why it exists (0.9.11 slice 3 review, reproduced 2026-10-27): the service's `comms_register` used to POST
/agents with only identity fields, and a registration is a full refresh, so a native Claude resident
became `generic` with no session handle, config or capabilities. The profile route is what that tool
calls now; this holds the row's host fields byte-equal across it.
"""

import sqlite3

from service.tests._base import FastApiTestCase

H = "11111111-2222-4333-8444-555555555555"
HOST_FIELDS = ("runtime", "session_mode", "session_handle", "runtime_config", "runtime_state", "capabilities", "cwd", "model")


class AProfileChangeKeepsWhatTheHostRegistered(FastApiTestCase):
    def _row(self, agent_id):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return dict(conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone())
        finally:
            conn.close()

    def setUp(self):
        super().setUp()
        res = self.client.post("/api/v1/agents", json={
            "agentId": "a", "role": "coder", "name": "Alpha", "runtime": "claude-code", "sessionMode": "resident",
            "machineId": "linux:test-host", "bridgeId": "bridge-a", "sessionHandle": H, "cwd": "/work",
            "model": "claude-opus-4-1", "runtimeConfig": {"channelEnabled": True},
        })
        self.assertEqual(res.status_code, 200, res.text)

    def test_role_and_name_change_and_the_host_fields_do_not(self):
        before = self._row("a")
        self.assertEqual((before["runtime"], before["session_handle"]), ("claude-code", H), "the control row is not native")
        res = self.client.patch("/api/v1/agents/a/profile", json={"role": "reviewer", "name": "Rev"})
        self.assertEqual(res.status_code, 200, res.text)
        after = self._row("a")
        self.assertEqual((after["role"], after["name"]), ("reviewer", "Rev"))
        self.assertEqual({k: after[k] for k in HOST_FIELDS}, {k: before[k] for k in HOST_FIELDS})

    def test_a_name_left_out_keeps_the_name(self):
        self.client.patch("/api/v1/agents/a/profile", json={"role": "reviewer"})
        self.assertEqual(self._row("a")["name"], "Alpha")

    def test_an_unknown_agent_is_not_created(self):
        res = self.client.patch("/api/v1/agents/nobody/profile", json={"role": "coder"})
        self.assertEqual((res.status_code, res.json()["detail"]), (404, "Agent 'nobody' not found"))
        conn = sqlite3.connect(str(self._db_path))
        try:
            self.assertIsNone(conn.execute("SELECT id FROM agents WHERE id = 'nobody'").fetchone())
        finally:
            conn.close()

    def test_an_empty_role_is_refused(self):
        res = self.client.patch("/api/v1/agents/a/profile", json={"role": "  "})
        self.assertEqual((res.status_code, res.json()["detail"]), (422, "role must not be empty"))
        self.assertEqual(self._row("a")["role"], "coder")
