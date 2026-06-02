import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.db import get_db, init_db
from service.routers import api_v2
from service.routers.api_v2 import router


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


from service.tests._base import FastApiTestCase


class LifecyclePhase7Tests(FastApiTestCase):
    def _register(self, agent_id: str, *, role: str = "coder", **extra):
        payload = {"agentId": agent_id, "role": role}
        payload.update(extra)
        response = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _heartbeat_environment(self, **extra):
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
                    "runtime": "codex",
                    "modes": ["managed-warm"],
                    "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True},
                }
            ],
            "metadata": {},
        }
        payload.update(extra)
        response = self.client.post("/api/v1/environments/heartbeat", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["environment"]

    def _fetchone(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                cursor = await db.execute(query, params)
                return await cursor.fetchone()
            finally:
                await db.close()
        return asyncio.run(_run())

    def _fetchall(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                cursor = await db.execute(query, params)
                return await cursor.fetchall()
            finally:
                await db.close()
        return asyncio.run(_run())

    def _execute(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                await db.execute(query, params)
                await db.commit()
            finally:
                await db.close()
        asyncio.run(_run())

    def _seed_ended_session(self, agent_id, *, runtime="codex", environment_id="linux:test-host:default",
                            workspace="/workspace/project", spawn_spec_id="spec-prior"):
        # A previously-managed agent leaves an ended agent_sessions row + spawn_spec
        # behind; cold-start clones environment/runtime/workspace from it.
        now = _iso(datetime.now(timezone.utc))
        self._execute(
            """
            INSERT INTO spawn_specs (id, agent_id, environment_id, runtime, workspace, model, profile, mode,
                system_prompt, standing_instructions, env_vars, channel_ids, budget_policy, context_policy,
                restart_policy, metadata, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (spawn_spec_id, agent_id, environment_id, runtime, workspace, "", "", "managed-warm",
             "", "", "{}", "[]", "{}", "{}", "{}", "{}", now, now),
        )
        self._execute(
            """
            INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, mode, spawn_spec_id,
                spawn_request_id, status, started_at, last_seen, ended_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (f"sess-{agent_id}", agent_id, environment_id, runtime, workspace, "managed-warm",
             spawn_spec_id, None, "ended", now, now, now),
        )

    def _coldstart(self, agent_id, *, runtime="codex", requested_by="alice"):
        async def _run():
            db = await get_db()
            try:
                settings = await api_v2._load_settings(db)
                result = await api_v2._coldstart_spawn_request_for_dispatch(
                    db, agent_id, runtime=runtime, settings=settings, requested_by=requested_by,
                )
                await db.commit()
                return result
            finally:
                await db.close()
        return asyncio.run(_run())

    def test_coldstart_creates_queued_spawn_request_for_cold_managed_agent(self):
        self._heartbeat_environment()
        self._register("worker", runtime="codex", sessionMode="managed")
        self._seed_ended_session("worker")

        created = self._coldstart("worker")
        self.assertTrue(created, "cold-start should report a created spawn request")

        rows = self._fetchall(
            "SELECT agent_id, environment_id, runtime, workspace, status, mode FROM spawn_requests WHERE agent_id = ?",
            ("worker",),
        )
        self.assertEqual(len(rows), 1, "exactly one spawn_request created")
        row = rows[0]
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["environment_id"], "linux:test-host:default")
        self.assertEqual(row["runtime"], "codex")
        self.assertEqual(row["workspace"], "/workspace/project")
        self.assertEqual(row["mode"], "managed-warm")

    def test_coldstart_is_idempotent_when_a_claimable_spawn_request_exists(self):
        self._heartbeat_environment()
        self._register("worker", runtime="codex", sessionMode="managed")
        self._seed_ended_session("worker")

        first = self._coldstart("worker")
        second = self._coldstart("worker")
        self.assertTrue(first)
        self.assertFalse(second, "second cold-start must NOT create a duplicate spawn request")
        rows = self._fetchall("SELECT id FROM spawn_requests WHERE agent_id = ? AND status IN ('queued','claimed')", ("worker",))
        self.assertEqual(len(rows), 1)

    # ── Phase 2: env auto-bind ────────────────────────────────────────────
    # A managed agent that has never been spawned (no prior agent_sessions
    # row, no environment binding) must still be cold-startable: pick the
    # first ONLINE environment that advertises the runtime, bind to it, and
    # queue a spawn_request. Without this, sending to an `available` managed
    # codex/hermes/pi agent that was only registered (never run) is rejected
    # with "cannot start live work now" — the operator-reported sc-coder bug.
    def test_coldstart_autobinds_first_online_env_when_no_prior_session(self):
        self._heartbeat_environment()  # advertises codex, online
        self._register("fresh", runtime="codex", sessionMode="managed")
        # No _seed_ended_session — this agent has never run.

        created = self._coldstart("fresh")
        self.assertTrue(created, "cold-start should auto-bind an online env and create a spawn request")

        rows = self._fetchall(
            "SELECT agent_id, environment_id, runtime, status, mode FROM spawn_requests WHERE agent_id = ?",
            ("fresh",),
        )
        self.assertEqual(len(rows), 1, "exactly one spawn_request created")
        row = rows[0]
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["environment_id"], "linux:test-host:default")
        self.assertEqual(row["runtime"], "codex")
        self.assertEqual(row["mode"], "managed-warm")

    def test_coldstart_returns_false_when_no_online_env_supports_runtime(self):
        # Env advertises codex only; agent is hermes → no online env supports
        # it → cold-start must decline (caller then rejects clearly) and
        # create NOTHING.
        self._heartbeat_environment()  # codex only
        self._register("lonely", runtime="hermes", sessionMode="managed")

        created = self._coldstart("lonely", runtime="hermes")
        self.assertFalse(created, "cold-start must decline when no online env advertises the runtime")
        rows = self._fetchall("SELECT id FROM spawn_requests WHERE agent_id = ?", ("lonely",))
        self.assertEqual(len(rows), 0, "no spawn_request when no env can host the runtime")

    def test_coldstart_skips_offline_env_and_picks_online_one(self):
        # Two envs advertise codex: one offline (stale heartbeat), one online.
        # Auto-bind must skip the offline env and choose the online one.
        stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._heartbeat_environment(id="env-online", bridgeId="bridge-online", machineId="linux:online")
        self._heartbeat_environment(id="env-offline", bridgeId="bridge-offline", machineId="linux:offline")
        # Force env-offline stale so its effective status degrades to offline.
        self._execute("UPDATE environments SET last_seen = ? WHERE id = ?", (stale, "env-offline"))
        self._register("picky", runtime="codex", sessionMode="managed")

        created = self._coldstart("picky")
        self.assertTrue(created)
        rows = self._fetchall("SELECT environment_id FROM spawn_requests WHERE agent_id = ?", ("picky",))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["environment_id"], "env-online")


if __name__ == "__main__":
    unittest.main()
