"""A resident claude idle at its prompt with background work still running reads `shell`.

The operator, 2026-10-01: "i do not see cyan marker on you currently (but you have shell running)".
`shell` existed only for managed workers, read off the screen aify-env observes. A resident's bridge
now reports its background shells and agents (`POST /agents/{id}/background-work`), and `derive()`
reads the lease. These drive the real routes: registration, the bridge's liveness beat, the report,
and the served status.
"""

from __future__ import annotations

import asyncio
import sqlite3
import unittest

from service.api_core.background_work import BACKGROUND_WORK_LEASE_SECONDS, background_work_lease
from service.clock import iso_to_epoch
from service.status_engine import StatusInputs, derive
from service.tests._base import FastApiTestCase

AGENT = "resident-with-a-shell"
BRIDGE = f"channel-linux:test-host-{AGENT}"


def _resident(**kw) -> StatusInputs:
    base = dict(mode="resident", alive=True, in_turn=False, awaiting_input=False, worker_present=True,
                env_reachable=True, disabled=False, bridge_stale=False, has_live_session=True)
    base.update(kw)
    return StatusInputs(**base)


class TheEngine(unittest.TestCase):
    def test_background_work_is_shell_only_for_a_live_resident_at_its_prompt(self):
        self.assertEqual(derive(_resident(background_work=True)), "shell")
        self.assertEqual(derive(_resident()), "online", "control: the same resident without it")
        self.assertEqual(derive(_resident(background_work=True, in_turn=True)), "working",
                         "in a turn it is working, whatever runs behind it")
        self.assertEqual(derive(_resident(background_work=True, alive=False, worker_present=False,
                                          has_live_session=False, bridge_stale=True)), "offline",
                         "a lease never revives a gone resident")


class TheLease(unittest.TestCase):
    def test_a_lease_is_fresh_for_its_window_and_says_when_it_ends(self):
        stamped = "2026-10-01T12:00:00Z"
        at = 1790856000.0  # 2026-10-01T12:00:00Z
        self.assertEqual(background_work_lease(stamped, at + 5), (True, "2026-10-01T12:00:21Z"))
        self.assertEqual(background_work_lease(stamped, at + BACKGROUND_WORK_LEASE_SECONDS + 1), (False, ""))
        self.assertEqual(background_work_lease("", at), (False, ""), "a cleared lease is not fresh")


class AResidentWithBackgroundWork(FastApiTestCase):
    DB_NAME = "aify-test-resident-background-work.db"

    def setUp(self):
        super().setUp()
        registered = self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "claude-code", "sessionMode": "resident",
            "machineId": "linux:test-host", "bridgeId": BRIDGE, "launchMode": "detached",
            "capabilities": ["resident-run", "resume", "interrupt", "steer"],
            "runtimeConfig": {"channelEnabled": True},
        })
        self.assertEqual(registered.status_code, 200, registered.text)
        beat = self.client.post(f"/api/v1/agents/{AGENT}/heartbeat", json={
            "bridgeId": BRIDGE, "bridgeKind": "channel-sidecar", "liveness": True})
        self.assertEqual(beat.status_code, 200, beat.text)

    def _status(self) -> str:
        response = self.client.get(f"/api/v1/agents/{AGENT}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        return (body.get("agent") or body)["status"]

    def _report(self, count):
        return self.client.post(f"/api/v1/agents/{AGENT}/background-work", json={"count": count})

    def test_the_served_status_follows_the_report(self):
        self.assertEqual(self._status(), "online", "control: a live resident with nothing running")
        self.assertEqual(self._report(2).status_code, 200)
        self.assertEqual(self._status(), "shell")
        self.assertEqual(self._report(0).status_code, 200)
        self.assertEqual(self._status(), "online", "zero clears it at once")

    def _computed(self) -> dict:
        """The served path's cache entry, computed now: its derived status and when it recomputes."""
        from service.api_core.status_inputs import _compute_live_status_cache
        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (AGENT,))).fetchone()
                return await _compute_live_status_cache(db, row)
            finally:
                await db.close()

        cache = asyncio.run(go())
        return {"status": derive(cache["status_inputs"]), "refresh_after": cache["refresh_after"]}

    def test_a_lease_the_bridge_stopped_refreshing_expires(self):
        self.assertEqual(self._report(1).status_code, 200)
        conn = sqlite3.connect(str(self._db_path))
        try:
            [stamped] = conn.execute("SELECT background_at FROM agent_console_signal WHERE agent_id = ?",
                                     (AGENT,)).fetchone()
            fresh = self._computed()
            # NOTHING TELLS THE CACHE when a lease ages out, so the cache must recompute by itself
            # no later than the lease ends.
            self.assertEqual(fresh["status"], "shell")
            _, lease_ends = background_work_lease(stamped, iso_to_epoch(stamped))
            self.assertTrue(fresh["refresh_after"] and fresh["refresh_after"] <= lease_ends,
                            (fresh["refresh_after"], lease_ends))
            conn.execute("UPDATE agent_console_signal SET background_at = '2020-01-01T00:00:00Z' WHERE agent_id = ?",
                         (AGENT,))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self._computed()["status"], "online", "recomputed past the lease: online")

    def test_a_report_must_be_a_count(self):
        for bad in (True, -1, "2", 1.5, None):
            refused = self._report(bad)
            self.assertEqual(refused.status_code, 422, repr(bad))
            self.assertEqual(refused.json()["detail"],
                             "count must be a whole number of background tasks, 0 or more", repr(bad))
        unknown = self.client.post("/api/v1/agents/nobody-here/background-work", json={"count": 1})
        self.assertEqual(unknown.status_code, 404, unknown.text)
        self.assertEqual(self._status(), "online", "a refused report changes nothing")
