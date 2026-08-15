"""Stopping a superseded bridge, without the stop requests piling up. Tested by calling it.

`_queue_stop_for_superseded_bridge` was inline in `environment_heartbeat` until v0.5.4, so exercising
it meant driving `POST /environments`. It is now a leaf and these tests run it against a real sqlite
database.

WHAT IT IS FOR. When a new bridge takes over an environment the old one must be told to stop, and the
telling is a pending row in `environment_controls` that the old bridge claims on its ~3s poll. That
works while the old bridge is alive — and it usually is not, since being dead is why it was
superseded. The row is then never claimed and nothing removes it: one per restart, ninety-nine
observed on a single environment (2026-07-03).

So each heartbeat drains the stops that are past the TTL, then queues one for this bridge only if it
has none outstanding. The tests split evenly between "the right row gets written" and "the drain does
not touch rows that are not its business", because a drain that is too eager silently cancels a stop
that a live bridge was about to claim.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import aiosqlite

from service.api_core.superseded_bridge_stops import (
    SUPERSEDE_STOP_STALE_SECONDS,
    _queue_stop_for_superseded_bridge,
)

SCHEMA = """
CREATE TABLE environment_controls (
    id TEXT PRIMARY KEY, environment_id TEXT, bridge_id TEXT, machine_id TEXT,
    action TEXT, status TEXT, requested_by TEXT, requested_at TEXT,
    handled_at TEXT, error TEXT
);
"""

#: DERIVED FROM THE REAL CLOCK, not hardcoded, and that is the whole point of these three lines.
#: They used to read "2026-08-15T12:00:00Z" / "11:59:00Z" / "00:00:00Z". `_queue_stop_for_superseded_-
#: bridge` takes `now` as a PARAMETER but computes its staleness cutoff from `datetime.now(utc)` —
#: the real clock — so a JUST_NOW pinned to a wall-clock time stops being "just now" as the day
#: advances. These tests passed all morning and began failing at 12:04 UTC, when real time crossed
#: the frozen JUST_NOW plus the TTL. Nothing about the code had changed.
#:
#: Sealing the input is the fix a test owns: derive the fixtures from the same clock the code reads,
#: so "recent" and "stale" mean what they say whenever the suite runs. Not fixed in the production
#: function — making it honour its own `now` argument would be a behaviour change, and in production
#: the caller passes `_now()` so the two agree.
_REAL_NOW = datetime.now(timezone.utc)
NOW = _REAL_NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
#: Comfortably outside the TTL, and written in the same lexical format the drain compares against.
LONG_AGO = (_REAL_NOW - timedelta(seconds=SUPERSEDE_STOP_STALE_SECONDS * 4)).strftime("%Y-%m-%dT%H:%M:%SZ")
#: Comfortably inside it: a fraction of the TTL before now, so a slow suite cannot age it out.
JUST_NOW = (_REAL_NOW - timedelta(seconds=max(1, SUPERSEDE_STOP_STALE_SECONDS // 10))).strftime("%Y-%m-%dT%H:%M:%SZ")

SERVER = "server:superseded-bridge"


class _Req:
    def __init__(self, machine_id="m1"):
        self.machineId = machine_id


class SupersededBridgeStopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)

    async def asyncTearDown(self):
        await self.db.close()

    async def _control(self, cid, *, env="env-1", bridge="b-old", action="stop", status="pending",
                       requested_by=SERVER, requested_at=LONG_AGO):
        await self.db.execute(
            "INSERT INTO environment_controls VALUES (?,?,?,?,?,?,?,?,NULL,'')",
            (cid, env, bridge, "m1", action, status, requested_by, requested_at))

    async def _run(self, *, env="env-1", bridge="b-old"):
        await _queue_stop_for_superseded_bridge(self.db, env, bridge, _Req(), NOW)

    async def _rows(self, **where):
        clause = " AND ".join(f"{k} = ?" for k in where) or "1=1"
        return await (await self.db.execute(
            f"SELECT * FROM environment_controls WHERE {clause} ORDER BY id", tuple(where.values())
        )).fetchall()

    # ---- queueing -----------------------------------------------------------

    async def test_it_queues_a_stop_for_the_superseded_bridge(self):
        await self._run()
        rows = await self._rows()
        self.assertEqual(1, len(rows))
        self.assertEqual("stop", rows[0]["action"])
        self.assertEqual("pending", rows[0]["status"])
        self.assertEqual("b-old", rows[0]["bridge_id"])
        self.assertEqual(SERVER, rows[0]["requested_by"])
        self.assertTrue(rows[0]["id"].startswith("envctl-"))

    async def test_no_bridge_id_means_no_work_at_all(self):
        await self._control("c1", requested_at=LONG_AGO)
        await self._run(bridge="")
        rows = await self._rows()
        self.assertEqual(1, len(rows), "no stop may be queued without a target")
        self.assertEqual("pending", rows[0]["status"], "and the drain must not run either")

    async def test_a_bridge_with_a_PENDING_stop_does_not_get_a_second_one(self):
        await self._control("c1", requested_at=JUST_NOW)
        await self._run()
        self.assertEqual(1, len(await self._rows()))

    async def test_a_bridge_with_a_CLAIMED_stop_does_not_get_a_second_one(self):
        """The old bridge picked it up and is acting on it; a duplicate would stop its successor."""
        await self._control("c1", status="claimed", requested_at=JUST_NOW)
        await self._run()
        self.assertEqual(1, len(await self._rows()))

    async def test_a_bridge_whose_only_stop_was_DRAINED_gets_a_fresh_one(self):
        """The two halves in sequence, which is the actual per-heartbeat behaviour."""
        await self._control("c1", requested_at=LONG_AGO)
        await self._run()
        rows = await self._rows()
        self.assertEqual(2, len(rows))
        self.assertEqual({"failed", "pending"}, {r["status"] for r in rows})

    # ---- draining -----------------------------------------------------------

    async def test_a_stale_server_stop_is_drained_with_a_reason(self):
        await self._control("c1", requested_at=LONG_AGO)
        await self._run()
        drained = (await self._rows(id="c1"))[0]
        self.assertEqual("failed", drained["status"])
        self.assertEqual(NOW, drained["handled_at"])
        self.assertIn("never claimed", drained["error"])

    async def test_a_RECENT_stop_is_left_alone(self):
        """Too eager a drain silently cancels a stop a live bridge was about to claim."""
        await self._control("c1", requested_at=JUST_NOW)
        await self._run()
        self.assertEqual("pending", (await self._rows(id="c1"))[0]["status"])

    async def test_only_SERVER_issued_stops_are_drained(self):
        """An operator's own stop is not this drain's business, however old it is."""
        await self._control("c1", requested_by="dashboard", requested_at=LONG_AGO)
        await self._run()
        self.assertEqual("pending", (await self._rows(id="c1"))[0]["status"])

    async def test_only_PENDING_stops_are_drained(self):
        """A claimed stop is being acted on; failing it would lie about what happened."""
        await self._control("c1", status="claimed", requested_at=LONG_AGO)
        await self._run()
        self.assertEqual("claimed", (await self._rows(id="c1"))[0]["status"])

    async def test_only_the_STOP_action_is_drained(self):
        await self._control("c1", action="restart", requested_at=LONG_AGO)
        await self._run()
        self.assertEqual("pending", (await self._rows(id="c1"))[0]["status"])

    async def test_another_environments_stops_are_never_drained(self):
        await self._control("c1", env="env-other", requested_at=LONG_AGO)
        await self._run(env="env-1")
        self.assertEqual("pending", (await self._rows(id="c1"))[0]["status"])

    async def test_the_drain_is_environment_wide_not_bridge_scoped(self):
        """Deliberate, and the reason the accumulation was unbounded: the rows pile up across
        BRIDGES, one per restart, so a drain that only looked at the current bridge would never
        reach the ninety-eight left by its predecessors."""
        for i in range(3):
            await self._control(f"c{i}", bridge=f"b-dead-{i}", requested_at=LONG_AGO)
        await self._run(bridge="b-new")
        self.assertEqual(
            3, len([r for r in await self._rows() if r["status"] == "failed"]),
            "every stale stop for this environment must drain, not just the current bridge's")

    async def test_the_TTL_is_a_sane_bound(self):
        """A guard on the constant itself: minutes, not seconds and not hours.

        Seconds would drain stops before a live bridge's ~3s poll could claim them; hours would let
        the accumulation this exists to bound grow well past useful.
        """
        self.assertGreaterEqual(SUPERSEDE_STOP_STALE_SECONDS, 60)
        self.assertLessEqual(SUPERSEDE_STOP_STALE_SECONDS, 3600)


if __name__ == "__main__":
    unittest.main()
