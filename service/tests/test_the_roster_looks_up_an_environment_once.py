"""One roster request, one environments-by-machine read.

`_managed_owning_environment_row` falls back to `SELECT * FROM environments WHERE machine_id = ?`,
a query that depends on machine_id ALONE. A fleet whose agents share a host therefore gets the same
answer every time, and the handler already carries a request-scoped `environments_by_machine` dict to
serve exactly that.

WHAT THE CACHE MISSED, measured 2026-08-26 with 50 registered agents on one machine: the roster
issued that query SEVENTEEN times. Attributed by walking the stack at each call:

    8x  status_inputs.py:329  in _compute_live_status_cache
    8x  status_inputs.py:526  in _compute_live_status_cache
    1x  registration_gates.py:125 in _enforce_env_reachable_gate   <- the cached path

The cache was created between the two phases. `list_agents` refreshes expired live states FIRST
(bounded by `_borrowed_list_agents_refresh_limit()`, which is why the count is 8 agents and not 50),
and only then builds the dict for the per-agent gate loop. So the phase doing most of the resolving
ran before the cache existed and could not use it.

WHY SHARING ONE DICT ACROSS BOTH PHASES IS SAFE, and not merely convenient: a request-scoped cache
is correct exactly when nothing changes its subject during the request, and
`test_the_roster_never_writes_what_it_caches.py` asserts that no INSERT, UPDATE or DELETE touches
`environments` anywhere in this request. That test is the licence for this one.

The assertion is AT MOST ONE rather than exactly one: an agent whose environment resolves by id
never reaches the machine_id fallback, and a roster with no managed agents should read the table
zero times. Requiring one would fail on a correct handler that had less work to do.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

ENV_ID = "linux:test-host:default"

#: The machine_id fallback in `_managed_owning_environment_row`. Matched on its prefix so the
#: ORDER BY does not have to be repeated here.
BY_MACHINE = "SELECT * FROM environments WHERE machine_id = ?"

#: Enough agents that a per-agent read is unmistakable, and more than the refresh batch cap so both
#: phases of the handler are exercised.
AGENTS = 25


class RosterEnvironmentLookupTests(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    def setUp(self) -> None:
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV_ID, "machineId": "linux:test-host", "os": "linux", "kind": "linux",
            "bridgeId": "bridge-current", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        for n in range(AGENTS):
            response = self.client.post("/api/v1/agents", json={
                "agentId": f"roster-agent-{n:03d}", "role": "coder", "runtime": "claude-code",
                "sessionMode": "managed", "machineId": "linux:test-host",
                "bridgeId": "bridge-current", "capabilities": ["managed-run"],
            })
            self.assertEqual(response.status_code, 200, response.text)

    def _statements(self) -> list[str]:
        seen: list[str] = []

        async def _go():
            import aiosqlite.core as core
            original = core.Connection.execute

            async def spy(conn_self, sql, *args, **kwargs):
                seen.append(re.sub(r"\s+", " ", str(sql)).strip())
                return await original(conn_self, sql, *args, **kwargs)

            core.Connection.execute = spy
            try:
                response = self.client.get("/api/v1/agents")
                self.assertEqual(response.status_code, 200, response.text)
            finally:
                core.Connection.execute = original

        asyncio.run(_go())
        return seen

    def test_the_spy_sees_a_roster_of_managed_agents(self) -> None:
        """Positive control. The assertion below is a CEILING, and a request that did no work at all
        would sit under any ceiling while proving nothing."""
        statements = self._statements()
        self.assertTrue(
            any(s.startswith("SELECT * FROM agents") for s in statements),
            "the roster never read the agents table, so this is not the request under test",
        )
        body = self.client.get("/api/v1/agents").json()
        self.assertEqual(
            len(body["agents"]), AGENTS,
            "the roster did not return every registered agent, so the per-agent work under test "
            "may not have run",
        )

    def test_the_machine_lookup_happens_at_most_once(self) -> None:
        hits = [s for s in self._statements() if s.startswith(BY_MACHINE)]
        self.assertLessEqual(
            len(hits), 1,
            f"the roster read environments-by-machine {len(hits)} times for {AGENTS} agents that "
            "share one host. The answer depends on machine_id alone, so every read after the first "
            "returns what the request already had. The handler's `environments_by_machine` dict is "
            "the intended fix and must reach BOTH phases -- the live-state refresh as well as the "
            "per-agent gate loop.",
        )

    def test_the_cache_returns_what_the_uncached_lookup_returned(self) -> None:
        """The behaviour this must not buy its speed with.

        A ceiling on reads is satisfied perfectly by a handler that resolves nothing, so the count
        alone is not evidence of anything.

        WHAT THIS OBSERVES, and why not the served status: measured on this fixture, forcing the
        resolver to return None for EVERY call leaves the roster's statuses byte-identical
        (`available` 8, `online` 17 either way). A comparison of statuses would therefore pass
        whatever the cache did -- it is a projection that does not depend on the thing under test.
        So this records the resolver's own RETURN VALUE per call and requires the cached run to
        produce the same sequence as the uncached one.

        Call COUNTS are comparable because the cache lives inside the resolver: the same callers
        still call it the same number of times, and only the SQL underneath changes.
        """
        from service.api_core import registration_gates, status_inputs
        from service.reconcilers import status_cache

        def resolutions(*, drop_cache: bool) -> list:
            recorded: list = []
            originals = {
                module: module._managed_owning_environment_row
                for module in (registration_gates, status_inputs)
            }

            def recorder(original):
                async def call(db, agent_row, **kwargs):
                    if drop_cache:
                        kwargs.pop("environments_by_machine", None)
                    row = await original(db, agent_row, **kwargs)
                    recorded.append((str(agent_row["id"]), None if row is None else str(row["id"])))
                    return row
                return call

            for module, original in originals.items():
                module._managed_owning_environment_row = recorder(original)
            try:
                status_cache._LIVE_STATE_CACHE.clear()
                response = self.client.get("/api/v1/agents")
                self.assertEqual(response.status_code, 200, response.text)
            finally:
                for module, original in originals.items():
                    module._managed_owning_environment_row = original
            return recorded

        without_cache = resolutions(drop_cache=True)
        with_cache = resolutions(drop_cache=False)

        self.assertTrue(
            without_cache,
            "the resolver was never called, so this compared two empty lists",
        )
        self.assertTrue(
            any(env_id for _, env_id in with_cache),
            "every resolution came back None, so the comparison cannot tell a cache hit from a miss",
        )
        self.assertEqual(
            with_cache, without_cache,
            "the request-scoped cache changed which environment an agent resolved to, so it is not "
            "the same answer arrived at more cheaply",
        )
