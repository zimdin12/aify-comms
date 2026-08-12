"""Running spawn requests describe live persistent backings and must not be reaped.

There is no ``completed`` spawn state: a managed worker PATCHes its request to ``running`` and the
request remains in that state for the lifetime of the backing. A live terminal therefore confirms
the running request rather than proving that it was superseded.
"""
import asyncio
import time

from service.db import get_db
from service import main as service_main
from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now

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

    def _seed_env(self, env_id="env-1", bridge_id="bridge-live"):
        self._exec(
            "INSERT INTO environments (id, status, bridge_id, registered_at, last_seen) VALUES (?,?,?,?,?)",
            (env_id, "online", bridge_id, api_v2._now(), api_v2._now()),
        )

    def _seed_spawn(self, spawn_id, agent_id, *, created_ago, status="running", bridge_id="bridge-live"):
        self._exec(
            """INSERT INTO spawn_requests (id, spawn_spec_id, environment_id, agent_id, runtime,
                 status, claimed_by_bridge_id, claimed_at, created_at, updated_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                spawn_id, "spec-1", "env-1", agent_id, "hermes", status, bridge_id,
                _iso_ago(created_ago), _iso_ago(created_ago), _iso_ago(created_ago),
            ),
        )

    def _seed_terminal(self, tid, agent_id, *, updated_ago, status="attached", session_id=None):
        self._exec(
            """INSERT INTO terminal_sessions (id, agent_id, environment_id, runtime, status,
                 session_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)""",
            (tid, agent_id, "env-1", "hermes", status, session_id or "sess-"+tid, _iso_ago(3600), _iso_ago(updated_ago)),
        )

    def _seed_agent_session(self, sid, agent_id, spawn_id, terminal_id, *, started_ago):
        self._exec(
            """INSERT INTO agent_sessions (
                 id, agent_id, environment_id, runtime, workspace, mode, spawn_request_id,
                 status, started_at, last_seen, owner_bridge_id, terminal_id
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sid, agent_id, "env-1", "hermes", "/tmp", "managed-warm", spawn_id,
                "running", _iso_ago(started_ago), _iso_ago(5), "bridge-live", terminal_id,
            ),
        )

    def _run_reconcile(self):
        return asyncio.run(service_main._run_dispatch_reconcile_once())

    def _run_superseded_reaper_with_terminal_rebind(self, terminal_id):
        async def _run():
            db = await get_db()

            class RebindBeforeUpdate:
                def __init__(self, connection):
                    self.connection = connection
                    self.rebound = False

                async def execute(self, query, params=()):
                    if not self.rebound and query.lstrip().startswith("UPDATE spawn_requests"):
                        self.rebound = True
                        await self.connection.execute(
                            "UPDATE terminal_sessions SET session_id = ? WHERE id = ?",
                            ("session-rebound", terminal_id),
                        )
                        await self.connection.commit()
                    return await self.connection.execute(query, params)

            try:
                failed = await api_v2._fail_running_spawns_superseded_by_current_session(
                    RebindBeforeUpdate(db)
                )
                await db.commit()
                return failed
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

    def test_reconcile_preserves_running_spawn_with_live_worker(self):
        self._register("mc-senior-dev")
        self._seed_terminal("term-live", "mc-senior-dev", updated_ago=5)   # beat 5s ago → live
        self._seed_spawn("spawn-old", "mc-senior-dev", created_ago=600)     # 10 min old, still running
        self._run_reconcile()
        self.assertEqual(self._spawn_status("spawn-old"), "running")

    def test_reconcile_fails_only_older_spawn_superseded_by_current_live_session(self):
        self._register("mc-senior-dev")
        self._seed_terminal(
            "term-current", "mc-senior-dev", updated_ago=5, session_id="session-current",
        )
        self._seed_spawn("spawn-older", "mc-senior-dev", created_ago=1200)
        self._seed_spawn("spawn-current", "mc-senior-dev", created_ago=600)
        self._seed_agent_session(
            "session-current", "mc-senior-dev", "spawn-current", "term-current", started_ago=600,
        )

        self._run_reconcile()

        self.assertEqual(self._spawn_status("spawn-older"), "failed")
        self.assertEqual(self._spawn_status("spawn-current"), "running")

    def test_reverse_terminal_reference_must_point_back_to_the_same_session(self):
        self._register("mc-senior-dev")
        self._seed_terminal(
            "term-current", "mc-senior-dev", updated_ago=5, session_id="session-real",
        )
        self._seed_spawn("spawn-older", "mc-senior-dev", created_ago=1200)
        self._seed_spawn("spawn-stale", "mc-senior-dev", created_ago=600)
        self._seed_agent_session(
            "session-stale", "mc-senior-dev", "spawn-stale", "term-current", started_ago=600,
        )

        self._run_reconcile()

        self.assertEqual(self._spawn_status("spawn-older"), "running")
        self.assertEqual(self._spawn_status("spawn-stale"), "running")

    def test_reaper_revalidates_live_binding_at_update_time(self):
        self._register("mc-senior-dev")
        self._seed_terminal(
            "term-current", "mc-senior-dev", updated_ago=5, session_id="session-current",
        )
        self._seed_spawn("spawn-older", "mc-senior-dev", created_ago=1200)
        self._seed_spawn("spawn-current", "mc-senior-dev", created_ago=600)
        self._seed_agent_session(
            "session-current", "mc-senior-dev", "spawn-current", "term-current", started_ago=600,
        )
        self._seed_agent_session(
            "session-rebound", "mc-senior-dev", "spawn-current", None, started_ago=1200,
        )

        failed = self._run_superseded_reaper_with_terminal_rebind("term-current")

        self.assertEqual(failed, 0)
        self.assertEqual(self._spawn_status("spawn-older"), "running")

    def test_fresh_spawn_left_alone(self):
        # A spawn still inside the grace window is a legit in-progress boot — never failed.
        self._register("mc-senior-dev")
        self._seed_terminal("term-live", "mc-senior-dev", updated_ago=5)
        self._seed_spawn("spawn-fresh", "mc-senior-dev", created_ago=30)
        self._run_reconcile()
        self.assertEqual(self._spawn_status("spawn-fresh"), "running")

    def test_dead_bridge_reaper_still_owns_stale_spawn_without_live_worker(self):
        # This request has neither a live worker nor a live claiming bridge. The existing
        # dead-bridge reaper still owns that distinct case.
        self._register("mc-hermes2")
        self._seed_terminal("term-stale", "mc-hermes2", updated_ago=9000)  # beat 2.5h ago → not live
        self._seed_spawn("spawn-boot", "mc-hermes2", created_ago=600, bridge_id="bridge-dead")
        self._run_reconcile()
        self.assertEqual(self._spawn_status("spawn-boot"), "failed")

    def test_raw_online_environment_with_stale_heartbeat_does_not_protect_orphan(self):
        self._register("mc-hermes2")
        self._exec(
            "UPDATE environments SET last_seen = ? WHERE id = ?",
            (_iso_ago(600), "env-1"),
        )
        self._seed_spawn(
            "spawn-stale-env", "mc-hermes2", created_ago=600, bridge_id="bridge-live",
        )

        self._run_reconcile()

        self.assertEqual(self._spawn_status("spawn-stale-env"), "failed")
