"""Reconcile reaper: a bridge_instances row whose process died WITHOUT a clean
supersede (crash / host restart / wrapper kill) lingers `superseded_by=''` with an
old last_seen forever — counted "live" by every status/dispatch scan. Nothing
superseded it (prune only DELETEs already-superseded rows), so orphans re-accumulated
(2026-07-11 perf report). `_reap_stale_orphan_bridges` supersedes them so they drop
out of the hot scans; the existing prune later deletes them.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from service.db import get_db
from service.routers import api_v2

from service.tests._base import FastApiTestCase


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seconds_ago(s: int) -> str:
    return _iso(datetime.now(timezone.utc) - timedelta(seconds=s))


class ReapStaleOrphanBridgesTests(FastApiTestCase):
    DB_NAME = "aify-reap-orphan-bridge-test.db"

    def _register(self, agent_id: str, *, role: str = "coder", **extra):
        payload = {"agentId": agent_id, "role": role}
        payload.update(extra)
        r = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(r.status_code, 200, r.text)

    def _execute(self, q, params=()):
        async def _run():
            db = await get_db()
            try:
                await db.execute(q, params); await db.commit()
            finally:
                await db.close()
        asyncio.run(_run())

    def _fetchone(self, q, params=()):
        async def _run():
            db = await get_db()
            try:
                return await (await db.execute(q, params)).fetchone()
            finally:
                await db.close()
        return asyncio.run(_run())

    def _seed_bridge(self, bridge_id, agent_id, *, seconds_ago, kind="managed-wrapper-child", superseded_by=""):
        self._execute(
            """
            INSERT INTO bridge_instances
                (id, agent_id, machine_id, runtime, session_mode, bridge_kind,
                 registered_at, last_seen, superseded_by)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (bridge_id, agent_id, "host:test", "claude", "managed", kind,
             _seconds_ago(seconds_ago + 5), _seconds_ago(seconds_ago), superseded_by),
        )

    def _run_reaper(self, **kwargs):
        async def _run():
            db = await get_db()
            try:
                out = await api_v2._reap_stale_orphan_bridges(db, **kwargs)
                await db.commit()
                return out
            finally:
                await db.close()
        return asyncio.run(_run())

    def setUp(self):
        super().setUp()
        self._register("orphan-agent")

    def _superseded_by(self, bridge_id):
        r = self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id=?", (bridge_id,))
        return (r["superseded_by"] or "") if r else None

    def test_stale_orphan_is_superseded(self):
        self._seed_bridge("b-stale", "orphan-agent", seconds_ago=600)  # 10 min → definitely dead
        n = self._run_reaper()
        self.assertEqual(n, 1, "one stale orphan should be superseded")
        self.assertEqual(self._superseded_by("b-stale"), "reaper:stale-orphan")
        # Idempotent: a second pass supersedes nothing new.
        self.assertEqual(self._run_reaper(), 0)

    def test_fresh_bridge_is_never_touched(self):
        # A live bridge beats every ~60s; 120s ago is well inside the 300s window.
        self._seed_bridge("b-fresh", "orphan-agent", seconds_ago=120)
        self.assertEqual(self._run_reaper(), 0, "a fresh bridge must never be superseded")
        self.assertEqual(self._superseded_by("b-fresh"), "")

    def test_just_past_the_floor_is_safe(self):
        # Right at the default boundary: 250s < 300s default → still live, untouched.
        self._seed_bridge("b-edge", "orphan-agent", seconds_ago=250)
        self.assertEqual(self._run_reaper(), 0)
        self.assertEqual(self._superseded_by("b-edge"), "")

    def test_already_superseded_is_left_alone(self):
        self._seed_bridge("b-done", "orphan-agent", seconds_ago=900, superseded_by="relaunch")
        self.assertEqual(self._run_reaper(), 0, "an already-superseded row is not re-superseded")
        self.assertEqual(self._superseded_by("b-done"), "relaunch")

    def test_raised_resident_lease_widens_the_window(self):
        # 2026-07-11 review: the reaper must derive its window from settings so it never
        # supersedes a bridge a raised liveness window still treats as fresh. With the lease
        # at 600, a 400s-old bridge (past the 300s floor) is still inside lease+60 → spared.
        self.client.put("/api/v1/settings", json={"resident_lease_seconds": 600})
        self._seed_bridge("b-in-lease", "orphan-agent", seconds_ago=400)
        self._seed_bridge("b-past-lease", "orphan-agent", seconds_ago=700)
        n = self._run_reaper()  # no stale_seconds → derives from settings (max(300, 660, 150)=660)
        self.assertEqual(n, 1, "only the bridge past the widened window is reaped")
        self.assertEqual(self._superseded_by("b-in-lease"), "", "a bridge inside the raised lease must be spared")
        self.assertEqual(self._superseded_by("b-past-lease"), "reaper:stale-orphan")

    def test_mixed_fleet_only_stale_live_orphans_reaped(self):
        self._seed_bridge("m-stale1", "orphan-agent", seconds_ago=700)
        self._seed_bridge("m-stale2", "orphan-agent", seconds_ago=400, kind="channel-sidecar")
        self._seed_bridge("m-fresh", "orphan-agent", seconds_ago=60)
        self._seed_bridge("m-super", "orphan-agent", seconds_ago=800, superseded_by="x")
        n = self._run_reaper()
        self.assertEqual(n, 2, "exactly the two stale live orphans")
        self.assertEqual(self._superseded_by("m-stale1"), "reaper:stale-orphan")
        self.assertEqual(self._superseded_by("m-stale2"), "reaper:stale-orphan")
        self.assertEqual(self._superseded_by("m-fresh"), "")
        self.assertEqual(self._superseded_by("m-super"), "x")
