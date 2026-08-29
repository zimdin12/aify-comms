r"""The unbounded live-state refresh costs a fixed number of DB round-trips per agent, and it is held.

WHY THIS EXISTS. `service/api_core/status_signal_prefetch.py` records a measurement taken on
2026-08-27: the reconcile sweep's unbounded refresh cost 7.0 round-trips per added agent, which that
change took to 5.0. Re-measured on 2026-08-29 with the same method it was **10.0**, and this change
takes it to 9.0.

Whatever the two figures are measuring differently, the direction is not in doubt: nothing was
watching the number, so it moved. That is the defect this file fixes -- not the queries, the absence
of a gate. A per-agent read added to the status path is invisible in every existing test, costs one
round-trip per agent per sweep pass forever, and shows up only as the fleet growing slower.

WHY ROUND-TRIPS AND NOT MILLISECONDS. This host cannot support a wall-clock assertion: the operator's
live fleet is the load, and the same code has timed 44-47ms and then 22-25ms minutes apart. The count
below is identical across repeated runs -- verified three times before this file was written -- so it
can be an equality rather than a threshold with slack to drift into.

HOW TO CHANGE THE NUMBER. Deliberately. If a new per-agent read is genuinely needed, raise the
ceiling in the same commit and say what it buys. If a read is batched away, LOWER it in the same
commit: a ceiling left slack above the real cost is how this one got from 5 to 10 unnoticed.
"""
from __future__ import annotations

import asyncio

from service.db import get_db
from service.tests._base import FastApiTestCase

#: MEASURED 2026-08-29, three identical runs: 5 agents -> 50 round-trips, 20 -> 185. Exactly 9.0 per
#: added agent. Not rounded up, and not a comfortable margin -- the same convention as the repo's
#: other ceilings, so adding a per-agent read fails here rather than being absorbed.
ROUND_TRIPS_PER_AGENT = 9.0

SMALL_FLEET = 5
LARGE_FLEET = 20


class TheLiveStateRefreshHoldsItsPerAgentCost(FastApiTestCase):
    def _seed(self, count: int) -> None:
        for index in range(count):
            response = self.client.post("/api/v1/agents", json={
                "agentId": f"cost-{index:03d}", "role": "coder", "runtime": "claude-code",
                "sessionMode": "managed", "launchMode": "detached",
            })
            self.assertEqual(response.status_code, 200, response.text)

    def _refresh_round_trips(self) -> tuple[int, int]:
        """(agents refreshed, DB round-trips) for ONE unbounded refresh with a cold cache."""
        import aiosqlite

        from service.api_core.status_refresh import _refresh_expired_agent_live_states
        from service.reconcilers import status_cache

        status_cache._LIVE_STATE_CACHE.clear()
        sink: list[str] = []
        real_execute = aiosqlite.Connection.execute

        async def execute(self, sql, parameters=None, *args, **kwargs):
            sink.append(str(sql))
            if parameters is None:
                return await real_execute(self, sql, *args, **kwargs)
            return await real_execute(self, sql, parameters, *args, **kwargs)

        async def run():
            db = await get_db()
            try:
                return await _refresh_expired_agent_live_states(db, limit=None)
            finally:
                await db.close()

        aiosqlite.Connection.execute = execute
        try:
            refreshed = asyncio.run(run())
        finally:
            aiosqlite.Connection.execute = real_execute
        return refreshed, len(sink)

    def _marginal(self) -> tuple[float, int, int]:
        self._seed(SMALL_FLEET)
        small_refreshed, small_queries = self._refresh_round_trips()
        self.assertEqual(small_refreshed, SMALL_FLEET, "the small fleet did not fully refresh")
        self._seed_more(LARGE_FLEET - SMALL_FLEET)
        large_refreshed, large_queries = self._refresh_round_trips()
        self.assertEqual(large_refreshed, LARGE_FLEET, "the large fleet did not fully refresh")
        rate = (large_queries - small_queries) / (LARGE_FLEET - SMALL_FLEET)
        return rate, small_queries, large_queries

    def _seed_more(self, count: int) -> None:
        for index in range(count):
            response = self.client.post("/api/v1/agents", json={
                "agentId": f"cost-more-{index:03d}", "role": "coder", "runtime": "claude-code",
                "sessionMode": "managed", "launchMode": "detached",
            })
            self.assertEqual(response.status_code, 200, response.text)

    def test_THE_CEILING_the_refresh_costs_no_more_per_agent_than_recorded(self):
        rate, small, large = self._marginal()
        self.assertLessEqual(rate, ROUND_TRIPS_PER_AGENT, (
            f"the unbounded live-state refresh now costs {rate:.1f} DB round-trips per agent, above "
            f"the recorded {ROUND_TRIPS_PER_AGENT} ({SMALL_FLEET} agents -> {small} round-trips, "
            f"{LARGE_FLEET} -> {large}). A per-agent read added to the status path costs one "
            "round-trip per agent on every reconcile pass, forever. Batch it into "
            "PrefetchedStatusSignals, or raise this number in the same commit and say what it buys."
        ))

    def test_a_ceiling_paid_down_is_TIGHTENED_rather_than_left_slack(self):
        rate, small, large = self._marginal()
        self.assertGreaterEqual(rate, ROUND_TRIPS_PER_AGENT, (
            f"the refresh is now {rate:.1f} round-trips per agent, BELOW the recorded "
            f"{ROUND_TRIPS_PER_AGENT}. Lower the number in this commit: slack above the real cost is "
            "exactly how it got from the 5.0 recorded on 2026-08-27 to the 10.0 measured on "
            "2026-08-29 with nobody noticing."
        ))

    def test_THE_COUNTER_ACTUALLY_COUNTS(self):
        """POSITIVE CONTROL. Both assertions above compare two numbers from the same instrument; if it
        silently returned zero for everything, the marginal rate would be 0.0 and the ceiling test
        would pass. Two earlier versions of this counter DID return zero -- patching
        `service.db.get_db` reached no router, and patching `__await__` on an instance is ignored
        because special methods resolve on the type."""
        self._seed(SMALL_FLEET)
        refreshed, queries = self._refresh_round_trips()
        self.assertEqual(refreshed, SMALL_FLEET)
        self.assertGreater(queries, SMALL_FLEET, "the counter saw fewer queries than there are agents")

    def test_the_cost_scales_with_the_fleet_rather_than_being_flat(self):
        """The other way the measurement could be empty: if the large fleet did not actually cost
        more, the marginal rate would be 0.0 from a working counter, and the ceiling would still
        pass. This says the two readings differ in the direction the arithmetic assumes."""
        _rate, small, large = self._marginal()
        self.assertGreater(large, small, (
            "refreshing four times as many agents took no more round-trips, so the marginal rate "
            "above is not measuring what it claims"
        ))
