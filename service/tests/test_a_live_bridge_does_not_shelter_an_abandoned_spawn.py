"""A spawn claimed by a live bridge is still failed once it is implausibly old.

THE CARVE-OUT WAS UNBOUNDED. `_fail_orphaned_running_spawn_requests` skips any spawn whose claiming
environment bridge is currently online -- "a worker actively (even slowly) booting on the live bridge
is left alone regardless of how long it has been booting". That was written for a worker that is
booting. A bridge that simply stays up made it permanent: the row was never reaped, however stale.

MEASURED on the operator's live service, 2026-08-28. FOUR spawn_requests sat `running`:

    probe-one           created 2026-08-25T02:37:46   updated 2026-08-25T02:37:48
    mc-senior-dev       created 2026-08-26T18:42:23   updated 2026-08-26T18:42:27
    graph-senior-dev    created 2026-08-27T21:19:59   updated 2026-08-27T21:20:02
    comms-senior-dev    created 2026-08-27T21:20:01   updated 2026-08-27T21:20:06

Every one claimed by bridge `5fdddb0f-...`, which belongs to `windows:StevenZ-L:default` and was last
seen the same day -- online. Each was updated once within six seconds of creation and never again.
The oldest had been "booting" for three days, and `/stats` reported
`spawn_requests_by_status.running = 4`, which an operator reads as four spawns in progress.

THE CEILING IS THE ONE THAT ALREADY EXISTS. `active_managed_run_wall_ceiling_minutes` answers the same
question for managed runs -- "this has been in flight implausibly long" -- so it is reused rather than
a second number meaning the same thing being invented beside it.

FAILING THE ROW IS SAFE, and the reaper's own docstring says why: it touches the stale record and
never a process, and the coldstart idempotency gate only inspects queued/claimed spawns, so failing a
`running` orphan cannot block a future autostart.

THE ERROR TEXT NOW NAMES WHICH RULE FIRED. The existing message says the claiming bridge is no longer
live, which is FALSE for a row failed by the ceiling, and an error that misattributes its own cause
sends the next reader to the wrong place.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.clock import ISO_SECONDS
from service.reconcilers.spawn_lifecycle import _fail_orphaned_running_spawn_requests
from service.tests._base import FastApiTestCase

LIVE_BRIDGE = "bridge-live"
DEAD_BRIDGE = "bridge-gone"
CEILING_MINUTES = 30


def _ago(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(ISO_SECONDS)


class ALiveBridgeDoesNotShelterAnAbandonedSpawnTests(FastApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": "linux:test-host:default", "machineId": "linux:test-host", "os": "linux",
            "kind": "linux", "bridgeId": LIVE_BRIDGE, "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)

    #: The environment the heartbeat above created. `spawn_requests.environment_id` is a NOT NULL
    #: FOREIGN KEY, so a row cannot be seeded without one -- as the first version of this file
    #: discovered, four tests at a time.
    ENVIRONMENT = "linux:test-host:default"

    def _seed(self, spawn_id: str, *, bridge: str, claimed_minutes_ago: float,
              claimed_at: str | None = None, created_at: str | None = None) -> None:
        """One `running` spawn request, with the spec its FOREIGN KEY requires."""
        stamp = _ago(claimed_minutes_ago)
        claimed = stamp if claimed_at is None else claimed_at
        created = stamp if created_at is None else created_at

        async def go():
            from service.db import get_db

            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO spawn_specs (id, agent_id, environment_id, runtime, created_at, "
                    "updated_at) VALUES (?,?,?,?,?,?)",
                    (f"spec-{spawn_id}", f"agent-{spawn_id}", self.ENVIRONMENT, "claude-code",
                     stamp, stamp),
                )
                await db.execute(
                    "INSERT INTO spawn_requests (id, spawn_spec_id, agent_id, environment_id, "
                    "runtime, status, claimed_by_bridge_id, claimed_at, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (spawn_id, f"spec-{spawn_id}", f"agent-{spawn_id}", self.ENVIRONMENT,
                     "claude-code", "running", bridge, claimed, created, stamp),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _reap(self) -> int:
        async def go():
            from service.db import get_db

            db = await get_db()
            try:
                return await _fail_orphaned_running_spawn_requests(
                    db, offline_seconds=90, wall_ceiling_minutes=CEILING_MINUTES,
                )
            finally:
                await db.close()

        return asyncio.run(go())

    def _row(self, spawn_id: str):
        async def go():
            from service.db import get_db

            db = await get_db()
            try:
                cursor = await db.execute(
                    "SELECT status, error FROM spawn_requests WHERE id = ?", (spawn_id,)
                )
                return await cursor.fetchone()
            finally:
                await db.close()

        return asyncio.run(go())

    def test_a_recent_spawn_on_a_live_bridge_is_left_alone(self) -> None:
        """The control, and the behaviour that must NOT change. A worker genuinely booting on the
        live bridge is why the carve-out exists; a reaper that failed it would be far worse than one
        that leaves four stale rows."""
        self._seed("fresh-live", bridge=LIVE_BRIDGE, claimed_minutes_ago=2)
        self._reap()
        self.assertEqual(self._row("fresh-live")["status"], "running")

    def test_an_ancient_spawn_on_a_live_bridge_is_failed(self) -> None:
        """The defect. Three days on the operator's service, and the row would have survived
        indefinitely because the bridge stayed up."""
        self._seed("ancient-live", bridge=LIVE_BRIDGE, claimed_minutes_ago=CEILING_MINUTES * 4)
        self.assertEqual(self._reap(), 1)
        row = self._row("ancient-live")
        self.assertEqual(row["status"], "failed")
        self.assertIn("Abandoned", row["error"])
        self.assertNotIn(
            "no longer live", row["error"],
            "the error blames a dead bridge, but this bridge is online -- it names the wrong cause",
        )

    def test_a_spawn_on_a_vanished_bridge_still_fails_the_old_way(self) -> None:
        """The pre-existing rule, unchanged, with its own message. Two rules share this loop and each
        has to say which one fired."""
        self._seed("orphan-dead", bridge=DEAD_BRIDGE, claimed_minutes_ago=10)
        self.assertEqual(self._reap(), 1)
        row = self._row("orphan-dead")
        self.assertEqual(row["status"], "failed")
        self.assertIn("no longer live", row["error"])

    def test_a_spawn_with_no_determinable_age_is_left_alone(self) -> None:
        """Conservative, as before. An unknown age is not evidence of abandonment, and the reaper's
        docstring commits to leaving it."""
        self._seed("ageless", bridge=LIVE_BRIDGE, claimed_minutes_ago=CEILING_MINUTES * 4,
                   claimed_at="", created_at="")
        self._reap()
        self.assertEqual(self._row("ageless")["status"], "running")


if __name__ == "__main__":
    import unittest

    unittest.main()
