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
              claimed_at: str | None = None, created_at: str | None = None,
              status: str = "running") -> None:
        """One spawn request, with the spec its FOREIGN KEY requires."""
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
                     "claude-code", status, bridge, claimed, created, stamp),
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

    # ── the second shape: a `claimed` row that never starts ─────────────────────────────────────
    #
    # THE ONE WITH TEETH, and it was not covered at all until 2026-09-03 -- the query said
    # `status = 'running'`. The reaper's own safety argument says a stale `running` row is harmless
    # because "the coldstart idempotency gate only inspects queued/claimed spawns". That inverts
    # here: a stuck `claimed` row is exactly what the gate reads, so every send to that agent is
    # refused with "a spawn for this agent is ALREADY IN FLIGHT", for ever.
    #
    # MEASURED on the operator's fleet 2026-09-03. `sc-coder` was unreachable for fifteen minutes and
    # would have stayed so: its row was claimed by the LIVE bridge, so even once `claimed` was
    # covered the carve-out above would have sheltered it for thirty minutes. The carve-out exists
    # for a worker that is slowly BOOTING, which a `claimed` row is not -- across 1,068 real spawns
    # on that same service, claim -> `started_at` is 0.0s median, 2s at p99 and SEVEN SECONDS at
    # worst. The three-minute grace is already twenty-five times the slowest claim ever observed.

    def test_A_CLAIMED_SPAWN_THAT_NEVER_STARTED_IS_FAILED(self) -> None:
        """THE DEFECT. Nothing aged one out, and it refuses every send to that agent while it sits."""
        self._seed("stuck-claim", bridge=DEAD_BRIDGE, claimed_minutes_ago=10, status="claimed")
        self._reap()
        self.assertEqual(self._row("stuck-claim")["status"], "failed",
                         "a claimed spawn that never started still blocks every send to its agent")

    def test_A_LIVE_BRIDGE_DOES_NOT_SHELTER_A_STUCK_CLAIM(self) -> None:
        """THE HALF THAT DECIDES WHETHER THE FIX HELPS. sc-coder's row was claimed by the live
        bridge; with the carve-out applied it would have been sheltered for thirty minutes, which is
        the whole outage plus fifteen."""
        self._seed("stuck-claim-live", bridge=LIVE_BRIDGE, claimed_minutes_ago=10, status="claimed")
        self._reap()
        self.assertEqual(self._row("stuck-claim-live")["status"], "failed",
                         "a stuck claim on a live bridge was sheltered by a carve-out written for "
                         "slow BOOTS, which a claim is not")

    def test_a_FRESH_claim_is_left_alone_on_either_bridge(self) -> None:
        """THE DIRECTION THAT WOULD BE CATASTROPHIC. Every real spawn passes through `claimed`, so a
        reaper that failed a fresh one would break every spawn on the fleet rather than unblock one.
        Two minutes is already seventeen times the slowest claim measured."""
        for name, bridge in (("fresh-claim-live", LIVE_BRIDGE), ("fresh-claim-dead", DEAD_BRIDGE)):
            self._seed(name, bridge=bridge, claimed_minutes_ago=2, status="claimed")
        self._reap()
        for name in ("fresh-claim-live", "fresh-claim-dead"):
            self.assertEqual(self._row(name)["status"], "claimed", f"{name} was reaped while fresh")

    def test_the_error_names_the_rule_that_fired(self) -> None:
        """Three rules can now fail a row and they have different remedies. An error that
        misattributes its own cause sends the next reader somewhere else entirely -- which is the
        bug the `running` half of this file already fixed once."""
        self._seed("stuck-claim-text", bridge=LIVE_BRIDGE, claimed_minutes_ago=10, status="claimed")
        self._reap()
        error = str(self._row("stuck-claim-text")["error"] or "")
        self.assertIn("never started", error)
        self.assertIn("already in flight", error.lower())
        self.assertNotIn("no longer live", error,
                         "the claiming bridge IS live; this text sends the reader to the wrong place")

    def test_a_running_row_still_gets_its_own_rules(self) -> None:
        """CONTROL FOR THE SPLIT. Widening the query must not give `running` the claim rules: a
        genuinely booting worker on a live bridge is still sheltered until the wall ceiling."""
        self._seed("boot-live", bridge=LIVE_BRIDGE, claimed_minutes_ago=10, status="running")
        self._reap()
        self.assertEqual(self._row("boot-live")["status"], "running",
                         "a booting worker on a live bridge lost its carve-out")


if __name__ == "__main__":
    import unittest

    unittest.main()
