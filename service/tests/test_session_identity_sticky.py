"""Sticky session identity + new-id guard (governance, 2026-05-30) — Task 3.1.

An agent's pinned session identity is STICKY: registration/operator-set pin it,
but the bridge heartbeat's *discovered* session id must NOT silently overwrite
it. A drift between the discovered id and the pinned handle is the observable
symptom of a split (agent on a fresh id) or a merge (two agents on one id), so
the service parks the proposed id in `pending_session_id`, flags the agent
`session-changed`, and keeps delivery on the OLD handle until the operator
resolves it via confirm (re-pin) or keep (resume the pinned id).

Cases:
  (a) first-id auto-accept    — no persisted handle → accept, no pending.
  (b) same-id no-op           — re-report identical id → no pending.
  (c) different-id guard      — drift → pending set, session-changed, live id
                                unchanged (delivery still targets the OLD id).
  (d) confirm re-pins         — persisted := pending; pending cleared.
  (e) keep clears + resume    — pending cleared; persisted kept; resume command
                                surfaced from the runtime adapter.
"""

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.db import init_db
from service.routers.api_v2 import router


from service.tests._base import FastApiTestCase


class SessionIdentityStickyTests(FastApiTestCase):
    # ── helpers ──────────────────────────────────────────────────────────
    def _register(self, agent_id="claude-1", runtime="claude-code", session_mode="managed"):
        res = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": runtime,
                "sessionMode": session_mode,
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
            },
        )
        self.assertEqual(res.status_code, 200, res.text)

    def _heartbeat_handle(self, agent_id, session_handle):
        """In-session capture path — mirrors session-handle-heartbeat.js."""
        return self.client.patch(
            f"/api/v1/agents/{agent_id}/session-handle",
            json={"sessionHandle": session_handle, "requestedBy": "bridge-heartbeat"},
        )

    def _row(self, agent_id):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        finally:
            conn.close()

    # ── (a) first-id auto-accept ─────────────────────────────────────────
    def test_first_id_auto_accept(self):
        self._register()
        res = self._heartbeat_handle("claude-1", "sess-AAA")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertNotEqual(body.get("state"), "session-changed")
        self.assertEqual(body.get("sessionHandle"), "sess-AAA")
        row = self._row("claude-1")
        self.assertEqual(str(row["session_handle"] or ""), "sess-AAA")
        self.assertEqual(str(row["pending_session_id"] or ""), "")
        self.assertFalse(body["agent"]["sessionChanged"])

    # ── (b) same-id no-op ────────────────────────────────────────────────
    def test_same_id_no_op(self):
        self._register()
        self._heartbeat_handle("claude-1", "sess-AAA")
        res = self._heartbeat_handle("claude-1", "sess-AAA")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertNotEqual(body.get("state"), "session-changed")
        row = self._row("claude-1")
        self.assertEqual(str(row["session_handle"] or ""), "sess-AAA")
        self.assertEqual(str(row["pending_session_id"] or ""), "")

    # ── (c) different-id guard ───────────────────────────────────────────
    def test_different_id_parks_pending_and_keeps_live(self):
        self._register()
        self._heartbeat_handle("claude-1", "sess-AAA")
        res = self._heartbeat_handle("claude-1", "sess-BBB")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body.get("state"), "session-changed")
        # Delivery still targets the OLD id.
        self.assertEqual(body.get("sessionHandle"), "sess-AAA")
        self.assertEqual(body.get("pendingSessionId"), "sess-BBB")
        row = self._row("claude-1")
        self.assertEqual(str(row["session_handle"] or ""), "sess-AAA")
        self.assertEqual(str(row["pending_session_id"] or ""), "sess-BBB")
        self.assertTrue(body["agent"]["sessionChanged"])
        self.assertEqual(body["agent"]["pendingSessionId"], "sess-BBB")

    def test_operator_set_is_not_guarded(self):
        """A deliberate operator re-pin (non-heartbeat requestedBy) overwrites."""
        self._register()
        self._heartbeat_handle("claude-1", "sess-AAA")
        res = self.client.patch(
            "/api/v1/agents/claude-1/session-handle",
            json={"sessionHandle": "sess-ZZZ", "requestedBy": "operator"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        row = self._row("claude-1")
        self.assertEqual(str(row["session_handle"] or ""), "sess-ZZZ")
        self.assertEqual(str(row["pending_session_id"] or ""), "")

    # ── (d) confirm re-pins ──────────────────────────────────────────────
    def test_confirm_repins_to_pending(self):
        self._register()
        self._heartbeat_handle("claude-1", "sess-AAA")
        self._heartbeat_handle("claude-1", "sess-BBB")  # parks pending
        res = self.client.post(
            "/api/v1/agents/claude-1/session/confirm",
            json={"requestedBy": "operator"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body.get("resolution"), "confirm")
        self.assertEqual(body.get("sessionHandle"), "sess-BBB")
        self.assertEqual(body.get("pendingSessionId"), "")
        row = self._row("claude-1")
        self.assertEqual(str(row["session_handle"] or ""), "sess-BBB")
        self.assertEqual(str(row["pending_session_id"] or ""), "")

    def test_confirm_without_pending_409(self):
        self._register()
        self._heartbeat_handle("claude-1", "sess-AAA")
        res = self.client.post(
            "/api/v1/agents/claude-1/session/confirm",
            json={"requestedBy": "operator"},
        )
        self.assertEqual(res.status_code, 409, res.text)

    # ── (e) keep clears pending + surfaces resume command ────────────────
    def test_keep_clears_pending_and_returns_resume_command(self):
        self._register()
        self._heartbeat_handle("claude-1", "sess-AAA")
        self._heartbeat_handle("claude-1", "sess-BBB")  # parks pending
        res = self.client.post(
            "/api/v1/agents/claude-1/session/keep",
            json={"requestedBy": "operator"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body.get("resolution"), "keep")
        # Persisted id is KEPT; pending cleared.
        self.assertEqual(body.get("sessionHandle"), "sess-AAA")
        self.assertEqual(body.get("pendingSessionId"), "")
        # Resume command surfaced from the runtime adapter.
        self.assertEqual(body.get("resumeCommand"), "claude-aify --resume sess-AAA")
        row = self._row("claude-1")
        self.assertEqual(str(row["session_handle"] or ""), "sess-AAA")
        self.assertEqual(str(row["pending_session_id"] or ""), "")

    def test_keep_without_pending_409(self):
        self._register()
        self._heartbeat_handle("claude-1", "sess-AAA")
        res = self.client.post(
            "/api/v1/agents/claude-1/session/keep",
            json={"requestedBy": "operator"},
        )
        self.assertEqual(res.status_code, 409, res.text)

    # ── cross-agent collision guard (root-cause fix, 2026-05-31) ─────────
    def test_cross_agent_collision_parks_and_keeps_own(self):
        # The 651b895f incident: agent B must NOT adopt a session id already
        # owned by a different LIVE agent A (resident<->managed invariant). B's
        # own handle is kept; the colliding id is parked, never bound.
        self._register("claude-A")
        self._heartbeat_handle("claude-A", "sess-SHARED")  # A owns it, fresh/live
        self._register("claude-B")
        res = self._heartbeat_handle("claude-B", "sess-SHARED")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body.get("state"), "session-collision", body)
        self.assertEqual(body.get("collisionWith"), "claude-A")
        # B did NOT adopt the colliding id — its own (empty) handle is kept.
        self.assertEqual(body.get("sessionHandle"), "")
        self.assertEqual(body.get("pendingSessionId"), "sess-SHARED")
        self.assertEqual(str(self._row("claude-B")["session_handle"] or ""), "", "B must not bind the live id")
        self.assertEqual(str(self._row("claude-B")["pending_session_id"] or ""), "sess-SHARED")
        # A is untouched.
        self.assertEqual(str(self._row("claude-A")["session_handle"] or ""), "sess-SHARED")

    def test_stale_owner_is_not_a_collision(self):
        # A dead/stale owner means the id is FREE to reassign — not a collision.
        self._register("claude-A")
        self._heartbeat_handle("claude-A", "sess-FREE")
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("UPDATE agents SET last_seen = ? WHERE id = ?", ("2020-01-01T00:00:00Z", "claude-A"))
            conn.commit()
        finally:
            conn.close()
        self._register("claude-B")
        res = self._heartbeat_handle("claude-B", "sess-FREE")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotEqual(res.json().get("state"), "session-collision", "stale owner is not a live collision")
        self.assertEqual(str(self._row("claude-B")["session_handle"] or ""), "sess-FREE", "B may adopt the freed id")


if __name__ == "__main__":
    unittest.main()
