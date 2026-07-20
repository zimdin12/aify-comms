"""Reap 'running' spawn_requests the agent already outgrew (mc-senior-dev proliferation, 2026-07-20).

A managed worker boots + registers a LIVE terminal, but the bridge never PATCHes the spawn to
completed (turn-interrupt churn), so it lingers 'running' forever on a live bridge — the
dead-bridge reaper skips it, and past the booting window it stops suppressing autostarts, so every
later message cold-starts another worker. This reconcile fails such redundant spawns (DB-only) when
the agent already has a genuinely live worker.
"""
import asyncio
import time

from service.db import get_db
from service.routers import api_v2

from service.tests._base import FastApiTestCase


def _iso_ago(seconds: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))


class SupersededSpawnReaperTests(FastApiTestCase):
    DB_NAME = "aify-superseded-spawn-test.db"

    def _register(self, agent_id, runtime="hermes"):
        r = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": runtime, "sessionMode": "managed"})
        self.assertEqual(r.status_code, 200, r.text)

    def _exec(self, q, params=()):
        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(q, params); await db.commit()
            finally:
                await db.close()
        asyncio.run(_run())

    def _seed_env(self, env_id="env-1"):
        self._exec("INSERT INTO environments (id, registered_at, last_seen) VALUES (?,?,?)",
                   (env_id, api_v2._now(), api_v2._now()))

    def _seed_spawn(self, spawn_id, agent_id, *, created_ago, status="running"):
        self._exec(
            """INSERT INTO spawn_requests (id, spawn_spec_id, environment_id, agent_id, runtime,
                 status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)""",
            (spawn_id, "spec-1", "env-1", agent_id, "hermes", status, _iso_ago(created_ago), _iso_ago(created_ago)),
        )

    def _seed_terminal(self, tid, agent_id, *, updated_ago, status="attached"):
        self._exec(
            """INSERT INTO terminal_sessions (id, agent_id, environment_id, runtime, status,
                 session_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)""",
            (tid, agent_id, "env-1", "hermes", status, "sess-"+tid, _iso_ago(3600), _iso_ago(updated_ago)),
        )

    def _run_reaper(self):
        async def _run():
            db = await get_db()
            try:
                out = await api_v2._fail_running_spawns_superseded_by_live_worker(db)
                await db.commit()
                return out
            finally:
                await db.close()
        return asyncio.run(_run())

    def _spawn_status(self, spawn_id):
        async def _run():
            db = await get_db()
            try:
                r = await (await db.execute("SELECT status FROM spawn_requests WHERE id=?", (spawn_id,))).fetchone()
                return r["status"] if r else None
            finally:
                await db.close()
        return asyncio.run(_run())

    def setUp(self):
        super().setUp()
        self._seed_env()

    def test_stale_running_spawn_with_live_worker_is_failed(self):
        self._register("mc-senior-dev")
        self._seed_terminal("term-live", "mc-senior-dev", updated_ago=5)   # beat 5s ago → live
        self._seed_spawn("spawn-old", "mc-senior-dev", created_ago=600)     # 10 min old, still running
        self.assertEqual(self._run_reaper(), 1)
        self.assertEqual(self._spawn_status("spawn-old"), "failed")

    def test_fresh_spawn_left_alone(self):
        # A spawn still inside the grace window is a legit in-progress boot — never failed.
        self._register("mc-senior-dev")
        self._seed_terminal("term-live", "mc-senior-dev", updated_ago=5)
        self._seed_spawn("spawn-fresh", "mc-senior-dev", created_ago=30)
        self.assertEqual(self._run_reaper(), 0)
        self.assertEqual(self._spawn_status("spawn-fresh"), "running")

    def test_no_live_worker_leaves_spawn_alone(self):
        # No live terminal (worker still booting / genuinely dead) → the dead-bridge reaper owns
        # that case; this one must NOT fail the spawn.
        self._register("mc-hermes2")
        self._seed_terminal("term-stale", "mc-hermes2", updated_ago=9000)  # beat 2.5h ago → not live
        self._seed_spawn("spawn-boot", "mc-hermes2", created_ago=600)
        self.assertEqual(self._run_reaper(), 0)
        self.assertEqual(self._spawn_status("spawn-boot"), "running")
