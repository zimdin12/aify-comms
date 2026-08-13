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


class PiResidentDrainTests(unittest.TestCase):
    """Task 17 — _drain_and_flip_pi_resident_agents helper.

    When a pi agent has runtime_state.pi_resident_pending_flip == True and
    no active dispatch run blocks it, the helper migrates the agent from
    sessionMode=resident to sessionMode=managed. session_handle is
    preserved, capabilities are recomputed, the flag is cleared, and a
    flipped_at timestamp is recorded.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "aify-test-pi-drain.db"
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

    def _agent_row(self, agent_id):
        async def _run():
            db = await get_db()
            try:
                cur = await db.execute(
                    "SELECT session_mode, runtime_state, session_handle, capabilities FROM agents WHERE id = ?",
                    (agent_id,),
                )
                return await cur.fetchone()
            finally:
                await db.close()

        return asyncio.run(_run())

    def test_drain_and_flip_no_active_runs(self):
        # Register a pi resident agent — gets the pending flip flag (Task 16)
        resp = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "test-drain-1",
                "role": "tester",
                "runtime": "pi",
                "sessionMode": "resident",
                "sessionHandle": "session-handle-x",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        # Run the drain helper synchronously
        from service.pi_resident_flip import _drain_and_flip_pi_resident_agents
        asyncio.run(_drain_and_flip_pi_resident_agents())

        # Verify the flip happened
        row = self._agent_row("test-drain-1")
        self.assertIsNotNone(row, "agent row should exist after flip")
        session_mode = row["session_mode"]
        runtime_state_json = row["runtime_state"]
        session_handle = row["session_handle"]
        rs = json.loads(runtime_state_json or "{}")
        self.assertEqual(
            session_mode, "managed",
            f"pi resident with no active runs should flip to managed. row={dict(row)}",
        )
        # session_handle is preserved
        self.assertEqual(session_handle, "session-handle-x")
        # The pending-flip flag is cleared
        self.assertFalse(
            rs.get("pi_resident_pending_flip"),
            f"pi_resident_pending_flip should be False/None after flip. runtime_state={rs}",
        )
        # flipped_at timestamp is recorded
        self.assertTrue(
            rs.get("flipped_at"),
            f"flipped_at timestamp should be recorded. runtime_state={rs}",
        )


class PiResidentDispatchRejectionTests(unittest.TestCase):
    """Task 18 — reject new resident pi dispatches with 409 during pending flip.

    When a pi agent is registered with sessionMode=resident, the row is
    marked pi_resident_pending_flip=True. Until the drain loop migrates
    the agent to managed, new dispatch attempts must return HTTP 409 with
    a clear "migrating" / "pending" hint so the operator can retry.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "aify-test-pi-flip-reject.db"
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

    def test_resident_pi_dispatch_rejected_during_pending_flip(self):
        # Register pi resident — gets pi_resident_pending_flip = true
        resp = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "test-flip-reject",
                "role": "tester",
                "runtime": "pi",
                "sessionMode": "resident",
                "sessionHandle": "session-handle-q",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        # Attempt a dispatch to this agent
        dispatch_resp = self.client.post(
            "/api/v1/dispatch",
            json={
                "from_agent": "operator",
                "to": "test-flip-reject",
                "subject": "test",
                "body": "hello",
            },
        )
        self.assertEqual(
            dispatch_resp.status_code, 409,
            f"expected 409 during pending pi flip; got {dispatch_resp.status_code}\nbody={dispatch_resp.text}",
        )
        lower_text = dispatch_resp.text.lower()
        self.assertTrue(
            "migrating" in lower_text or "pending" in lower_text,
            f"expected 'migrating' or 'pending' in error body; got {dispatch_resp.text}",
        )

    def test_non_pi_resident_dispatch_not_rejected(self):
        # Sanity: a non-pi resident must NOT be rejected by this gate.
        resp = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "test-claude-resident-ok",
                "role": "tester",
                "runtime": "claude",
                "sessionMode": "resident",
                "sessionHandle": "claude-handle",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        dispatch_resp = self.client.post(
            "/api/v1/dispatch",
            json={
                "from_agent": "operator",
                "to": "test-claude-resident-ok",
                "subject": "test",
                "body": "hello",
            },
        )
        # Must NOT be 409 from the pi-flip gate. (It may legitimately fail
        # for other reasons like no live wake config, but not 409 with the
        # pi migrating/pending message.)
        if dispatch_resp.status_code == 409:
            lower_text = dispatch_resp.text.lower()
            self.assertFalse(
                "migrating" in lower_text and "pi" in lower_text,
                f"non-pi resident must not hit pi-flip 409 gate; got {dispatch_resp.text}",
            )

    def test_messages_send_trigger_rejected_during_pending_pi_flip(self):
        # /messages/send trigger=true is a second live-dispatch entrypoint.
        # It must honor the same pending-flip gate as /dispatch; otherwise
        # chat can enqueue work against a Pi resident mode that is already
        # being migrated away.
        resp = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "test-flip-message-reject",
                "role": "tester",
                "runtime": "pi",
                "sessionMode": "resident",
                "sessionHandle": "session-handle-msg",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        send_resp = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "operator",
                "to": "test-flip-message-reject",
                "type": "request",
                "subject": "test",
                "body": "hello",
                "trigger": True,
            },
        )
        self.assertEqual(send_resp.status_code, 200, send_resp.text)
        body = send_resp.json()
        self.assertFalse(body.get("ok"), body)
        self.assertEqual(body.get("dispatchRuns"), [], body)
        not_started = body.get("notStarted") or []
        self.assertEqual(len(not_started), 1, body)
        reason = json.dumps(not_started[0]).lower()
        self.assertIn("pi flip pending", reason, body)


class PiResidentPreExistingBackfillTests(unittest.TestCase):
    """Plan 2 backfill — pre-existing pi-resident agents (rows that landed
    in the DB BEFORE the Task 16 registration marker shipped) must still
    get flipped by the drain helper. Without the backfill, the operator
    would have to manually re-register every pre-existing pi-resident
    agent — which is exactly what Plan 2 was supposed to avoid.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "aify-test-pi-backfill.db"
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

    def _agent_row(self, agent_id):
        async def _run():
            db = await get_db()
            try:
                cur = await db.execute(
                    "SELECT session_mode, runtime_state, session_handle, capabilities FROM agents WHERE id = ?",
                    (agent_id,),
                )
                return await cur.fetchone()
            finally:
                await db.close()

        return asyncio.run(_run())

    def test_drain_flips_pre_existing_pi_resident_without_marker(self):
        """A pi-resident agent that exists in the DB WITHOUT the
        pi_resident_pending_flip marker (i.e., registered before the Plan 2
        pi-flip rollout) must still get migrated by the drain helper on
        the next launch. Otherwise the operator has to manually re-register
        every pre-existing pi-resident agent.
        """
        # Insert a pi-resident row directly with NO pi_resident_pending_flip
        # marker, simulating a pre-existing agent registered before Task 16.
        async def _insert():
            db = await get_db()
            try:
                now = "2026-05-20T00:00:00Z"
                await db.execute(
                    """
                    INSERT INTO agents (id, role, name, runtime, session_mode,
                                        session_handle, runtime_state, runtime_config,
                                        capabilities, status, registered_at, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "pre-existing-pi", "tester", "pre-existing-pi",
                        "pi", "resident", "handle-existing",
                        "{}",  # runtime_state has NO pi_resident_pending_flip marker
                        "{}", "[]", "online", now, now,
                    ),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_insert())

        from service.pi_resident_flip import _drain_and_flip_pi_resident_agents
        asyncio.run(_drain_and_flip_pi_resident_agents())

        row = self._agent_row("pre-existing-pi")
        self.assertIsNotNone(row, "agent row should exist after backfill drain")
        session_mode = row["session_mode"]
        runtime_state_json = row["runtime_state"]
        session_handle = row["session_handle"]
        self.assertEqual(
            session_mode, "managed",
            f"pre-existing pi-resident agent must be flipped by drain helper; row={dict(row)}",
        )
        self.assertEqual(
            session_handle, "handle-existing",
            "session_handle must be preserved across backfill flip",
        )
        rs = json.loads(runtime_state_json or "{}")
        self.assertTrue(
            rs.get("flipped_at"),
            f"flipped_at timestamp should be recorded. runtime_state={rs}",
        )


if __name__ == "__main__":
    unittest.main()
