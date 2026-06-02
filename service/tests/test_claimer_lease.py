"""WS5 Task 5.1 + 5.1b — explicit delivery-loop claimer lease + deaf-target fail-fast.

Task 5.1: a managed sidecar-delivery loop (hermes-managed-host.js) POSTs an explicit
`claimer-acquire` when it becomes a live claimer and `claimer-release` on teardown.
The lease is a POSITIVE deliverability signal: a released lease ⇒ immediately
not-deliverable (no waiting for the 180s sidecar staleness window). `_has_live_claimer_lease`
reads it; `_agent_has_live_claimer` PREFERS the lease and only falls back to the
channel-sidecar / bridge-freshness check when NO lease has EVER been recorded (so
pre-existing/older loops and lazy claimers still work — the lazy-claim contract).

Task 5.1b: at send time, a managed sidecar-delivery target whose lease is
RELEASED/stale (genuinely deaf — it HAD a claimer that is now gone) fails fast with
ok:false and writes NO queued run. A cold `available` agent with NO lease ever
(spawnable) is NOT deaf — it still queues + lazy-autostarts (unchanged).
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.db import get_db, init_db
from service.routers import api_v2
from service.routers.api_v2 import router


class _DummyWS:
    async def broadcast(self, *_args, **_kwargs):
        return None

    async def notify_agent(self, *_args, **_kwargs):
        return None


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class ClaimerLeaseStoreTests(unittest.TestCase):
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

    def tearDown(self):
        self.client.close()
        self._tmpdir.cleanup()

    def _register_managed_hermes(self, agent_id: str) -> None:
        resp = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": "hermes",
                "sessionMode": "managed",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
                "capabilities": ["native-managed-run", "managed-run", "resume", "interrupt"],
                "runtimeConfig": {"gatewayUrl": "ws://127.0.0.1:9119/api/ws?token=t"},
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def _agent_row(self, agent_id: str):
        async def _go():
            db = await get_db()
            try:
                return await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
            finally:
                await db.close()

        return _run(_go())

    def _has_lease(self, agent_id: str) -> bool:
        async def _go():
            db = await get_db()
            try:
                return await api_v2._has_live_claimer_lease(db, agent_id)
            finally:
                await db.close()

        return _run(_go())

    def _agent_has_live_claimer(self, agent_id: str) -> bool:
        async def _go():
            db = await get_db()
            try:
                row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
                return await api_v2._agent_has_live_claimer(db, row)
            finally:
                await db.close()

        return _run(_go())

    def _post_lease(self, agent_id: str, action: str, bridge_id: str = "hermes-channel-linux:test-host") -> dict:
        resp = self.client.post(
            f"/api/v1/agents/{agent_id}/claimer-lease",
            json={"action": action, "bridgeId": bridge_id},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    # --- Task 5.1: lease store ---

    def test_acquire_makes_lease_live(self):
        self._register_managed_hermes("hermes-lease")
        self.assertFalse(self._has_lease("hermes-lease"), "no lease before acquire")
        self._post_lease("hermes-lease", "acquire")
        self.assertTrue(self._has_lease("hermes-lease"), "acquire makes lease live")

    def test_release_clears_lease_immediately(self):
        self._register_managed_hermes("hermes-lease")
        self._post_lease("hermes-lease", "acquire")
        self.assertTrue(self._has_lease("hermes-lease"))
        self._post_lease("hermes-lease", "release")
        # Released IMMEDIATELY — no waiting for any staleness window.
        self.assertFalse(self._has_lease("hermes-lease"), "release clears lease immediately")

    def test_agent_has_live_claimer_prefers_acquired_lease(self):
        # An ACQUIRED lease makes the agent deliverable even with NO fresh
        # channel-sidecar bridge row (lease is the positive signal).
        self._register_managed_hermes("hermes-lease")
        self._post_lease("hermes-lease", "acquire")
        self.assertTrue(self._agent_has_live_claimer("hermes-lease"))

    def test_agent_has_live_claimer_released_lease_is_not_deliverable(self):
        # A RELEASED lease makes the agent NOT deliverable immediately, even if a
        # stale channel-sidecar bridge row still exists within the 180s window.
        self._register_managed_hermes("hermes-lease")
        now = api_v2._now()
        # Seed a FRESH channel-sidecar bridge row (the old fallback would call this live).
        async def _seed():
            db = await get_db()
            try:
                await db.execute(
                    """
                    INSERT INTO bridge_instances (
                        id, agent_id, machine_id, runtime, session_mode, session_handle,
                        terminal_id, bridge_kind, registered_at, last_seen, superseded_by
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "hermes-channel-linux:test-host",
                        "hermes-lease",
                        "linux:test-host",
                        "hermes",
                        "managed",
                        "h1",
                        "",
                        "channel-sidecar",
                        now,
                        now,
                        "",
                    ),
                )
                await db.commit()
            finally:
                await db.close()

        _run(_seed())
        # Sanity: with no lease the fresh sidecar would make it deliverable.
        self.assertTrue(self._agent_has_live_claimer("hermes-lease"))
        # Acquire then release the lease — release must win over the fresh sidecar row.
        self._post_lease("hermes-lease", "acquire")
        self._post_lease("hermes-lease", "release")
        self.assertFalse(
            self._has_lease("hermes-lease"),
            "released lease is not live",
        )
        self.assertFalse(
            self._agent_has_live_claimer("hermes-lease"),
            "a released lease must override a still-fresh channel-sidecar row",
        )

    def test_no_lease_ever_falls_back_to_sidecar_check(self):
        # Lazy-claim contract: an agent that NEVER recorded a lease falls back to
        # the channel-sidecar / bridge-freshness check (a not-yet-polled claimer
        # is NOT treated as deaf). With a fresh sidecar row and NO lease ever,
        # the agent is deliverable via the fallback.
        self._register_managed_hermes("hermes-nolease")
        now = api_v2._now()

        async def _seed():
            db = await get_db()
            try:
                await db.execute(
                    """
                    INSERT INTO bridge_instances (
                        id, agent_id, machine_id, runtime, session_mode, session_handle,
                        terminal_id, bridge_kind, registered_at, last_seen, superseded_by
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "hermes-channel-linux:test-host",
                        "hermes-nolease",
                        "linux:test-host",
                        "hermes",
                        "managed",
                        "h1",
                        "",
                        "channel-sidecar",
                        now,
                        now,
                        "",
                    ),
                )
                await db.commit()
            finally:
                await db.close()

        _run(_seed())
        self.assertFalse(self._has_lease("hermes-nolease"), "no lease ever recorded")
        self.assertTrue(
            self._agent_has_live_claimer("hermes-nolease"),
            "no-lease-ever must fall back to the fresh-sidecar check (lazy-claim contract)",
        )


class DeafTargetFailFastTests(unittest.TestCase):
    """WS5 Task 5.1b — send to a deaf managed sidecar-delivery target fails fast."""

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
        # Production wrapper-backed defaults (managed hermes routes to channel).
        self.client.put(
            "/api/v1/settings",
            json={
                "insert_messages_via_console": False,
                "managed_via_wrapper": ["codex", "hermes"],
                "managed_terminal_backing_enabled": True,
            },
        )

    def tearDown(self):
        self.client.close()
        self._tmpdir.cleanup()

    def _heartbeat_hermes_env(self):
        resp = self.client.post(
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
                        "runtime": "hermes",
                        "modes": ["managed-warm"],
                        "capabilities": {"nativeResume": True, "interrupt": True},
                    }
                ],
                "metadata": {},
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def _register_managed_hermes(self, agent_id: str):
        resp = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": "hermes",
                "sessionMode": "managed",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
                "capabilities": ["native-managed-run", "managed-run", "resume", "interrupt"],
                "runtimeConfig": {"gatewayUrl": "ws://127.0.0.1:9119/api/ws?token=t"},
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def _post_lease(self, agent_id: str, action: str):
        resp = self.client.post(
            f"/api/v1/agents/{agent_id}/claimer-lease",
            json={"action": action, "bridgeId": "hermes-channel-linux:test-host"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def _count_dispatch_runs(self, agent_id: str) -> int:
        async def _go():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT COUNT(*) AS n FROM dispatch_runs WHERE target_agent = ?",
                    (agent_id,),
                )).fetchone()
                return int(row["n"])
            finally:
                await db.close()

        return _run(_go())

    def test_send_to_deaf_managed_hermes_fails_fast_no_run(self):
        # A managed hermes whose loop ACQUIRED then RELEASED its lease is deaf.
        self._heartbeat_hermes_env()
        self._register_managed_hermes("deaf-hermes")
        self._post_lease("deaf-hermes", "acquire")
        self._post_lease("deaf-hermes", "release")

        sent = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "dashboard",
                "to": "deaf-hermes",
                "type": "request",
                "subject": "are you there",
                "body": "hello deaf agent",
                "trigger": True,
            },
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        body = sent.json()
        self.assertFalse(body.get("ok"), f"send to a deaf target must fail fast; got {body}")
        # No dispatch_runs row was written (no silent pileup).
        self.assertEqual(
            self._count_dispatch_runs("deaf-hermes"),
            0,
            f"deaf fail-fast must write NO queued run; got {body}",
        )
        # The deaf reason is surfaced.
        not_started = body.get("notStarted") or []
        self.assertTrue(
            any("deaf" in (item.get("reason") or "").lower() for item in not_started),
            f"expected a deaf reason in notStarted; got {not_started}",
        )

    def test_send_to_cold_available_hermes_no_lease_still_queues_and_coldstarts(self):
        # A managed hermes that NEVER recorded a lease is cold-startable, NOT deaf:
        # the send must NOT fail fast — it cold-starts a spawn_request (unchanged
        # lazy-autostart-on-send behavior).
        self._heartbeat_hermes_env()
        self._register_managed_hermes("cold-hermes")

        avail = self.client.get("/api/v1/agents/cold-hermes").json()["agent"]
        self.assertEqual(avail["status"], "available", avail)

        sent = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "dashboard",
                "to": "cold-hermes",
                "type": "request",
                "subject": "wake up",
                "body": "please get to work",
                "trigger": True,
            },
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        body = sent.json()
        self.assertNotEqual(
            body.get("error"),
            "Message was not sent because one or more recipients cannot start live work now.",
            f"cold available hermes (no lease ever) must NOT be hard-rejected; got {body}",
        )

        async def _go():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT COUNT(*) AS n FROM spawn_requests WHERE agent_id = ? AND status IN ('queued','claimed')",
                    ("cold-hermes",),
                )).fetchone()
                return int(row["n"])
            finally:
                await db.close()

        self.assertEqual(_run(_go()), 1, f"cold-start must back the agent with a spawn_request; got {body}")


if __name__ == "__main__":
    unittest.main()
