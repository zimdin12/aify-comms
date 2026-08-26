"""The analytics page resolves the fleet's environment once, not once per agent.

`GET /api/v1/analytics` computes a status for EVERY agent to fill three cards -- live, online and
working. Each of those statuses asked the database the same three questions again:

    SELECT * FROM agents WHERE id = ?                              -- the row the loop already held
    SELECT * FROM environments WHERE machine_id = ?                -- depends on machine_id alone
    SELECT environment_id FROM agent_sessions WHERE agent_id = ?   -- one table read for the fleet

MEASURED 2026-08-26 by counting `aiosqlite` execute() calls through one request on a COLD live-state
cache, four fleet sizes, a fresh database per size:

    agents   6    12    24    40
    before  175   271   463   719      = 16N + 79, exact at all four
    after   147   213   345   521      = 11N + 81, exact at all four

Five fewer round-trips per agent for two more fixed ones, so it wins from a single agent. At 47 agents
that is 831 -> 598.

WHY COLD. The live-status cache serves a warm read in 79 round-trips, and a warm analytics request was
never the problem. The cost lands on whichever request arrives first after the cache expires, which is
a latency spike on an arbitrary caller of a single-worker SQLite service -- the same service whose
lock contention has its own entry in DECISIONS.md.

THIS IS THE THIRD CALLER OF ONE DERIVATION. `GET /api/v1/agents` got request-scoped dicts in
`fab4204c` and the reconcile sweep got a sweep-scoped pair earlier in this round. Analytics was the
last one still asking per agent, and none of the three found the other two -- which is why this test
names the shape rather than the endpoint.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

BY_MACHINE = "SELECT * FROM environments WHERE machine_id = ?"
SESSION_ENV = "SELECT environment_id FROM agent_sessions"
AGENT_BY_ID = "SELECT * FROM agents WHERE id = ?"

#: Comfortably more than one, so a per-agent read is unmistakable against a per-request one.
AGENTS = 8


class AnalyticsResolvesTheFleetOnceTests(FastApiTestCase):
    """`GET /api/v1/analytics`. The subclass below runs every one of these against the pulse board,
    which is the same loop in a different module and was measured separately."""

    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    #: The endpoint under test, and the derived numbers whose value must not change. Both are class
    #: attributes so the pulse board inherits the assertions instead of copying them -- two endpoints
    #: running one derivation is the whole finding, and a copied test would drift the way the code did.
    ENDPOINT = "/api/v1/analytics"
    CARDS = ("liveAgents", "onlineAgents", "workingAgents")

    def setUp(self) -> None:
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": "linux:test-host:default", "machineId": "linux:test-host", "os": "linux",
            "kind": "linux", "bridgeId": "bridge-a", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        for n in range(AGENTS):
            response = self.client.post("/api/v1/agents", json={
                "agentId": f"analytics-agent-{n:03d}", "role": "coder", "runtime": "claude-code",
                "sessionMode": "managed", "machineId": "linux:test-host",
                "bridgeId": "bridge-a", "capabilities": ["managed-run"],
            })
            self.assertEqual(response.status_code, 200, response.text)

    def _statements(self) -> list[str]:
        """Every SQL statement one COLD analytics request issues.

        The cache is cleared first because a warm request never reaches the derivation, and a
        measurement taken through the cache would report a saving the cache had already made.
        """
        from service.reconcilers import status_cache
        status_cache._LIVE_STATE_CACHE.clear()

        import aiosqlite.core as core
        seen: list[str] = []
        original = core.Connection.execute

        async def spy(conn_self, sql, *args, **kwargs):
            seen.append(re.sub(r"\s+", " ", str(sql)).strip())
            return await original(conn_self, sql, *args, **kwargs)

        core.Connection.execute = spy
        try:
            response = self.client.get(self.ENDPOINT)
        finally:
            core.Connection.execute = original
        self.assertEqual(response.status_code, 200, response.text)
        return seen

    def test_the_request_actually_did_the_work(self) -> None:
        """Positive control. Every assertion below is a CEILING, and a request that computed nothing
        sits under all of them while proving nothing at all."""
        statements = self._statements()
        self.assertGreater(
            len(statements), 40,
            f"only {len(statements)} statements; this is not a full analytics request",
        )
        self.assertTrue(
            any(s.startswith("SELECT * FROM agents") for s in statements),
            "the request never read the agents table, so it is not the loop under test",
        )

    def test_the_owning_environment_is_looked_up_once_for_the_whole_fleet(self) -> None:
        hits = [s for s in self._statements() if s.startswith(BY_MACHINE)]
        self.assertLessEqual(
            len(hits), 1,
            f"one analytics request read environments-by-machine {len(hits)} times for {AGENTS} "
            "agents on one host. The answer depends on machine_id alone, so every read after the "
            "first returns what the request already had.",
        )

    def test_the_session_environment_is_loaded_once(self) -> None:
        hits = [s for s in self._statements() if s.startswith(SESSION_ENV)]
        self.assertLessEqual(
            len(hits), 1,
            f"the session environment was read {len(hits)} times; it is one table read for the fleet.",
        )

    def test_the_agent_row_the_loop_already_holds_is_not_re_read(self) -> None:
        """The loop iterates `SELECT * FROM agents`. Re-selecting each row by id inside that loop
        asks for a row already in hand, in the same request, on the same connection."""
        hits = [s for s in self._statements() if s.startswith(AGENT_BY_ID)]
        self.assertLessEqual(
            len(hits), 1,
            f"the analytics loop re-read an agent row by id {len(hits)} times for {AGENTS} agents it "
            "had already selected.",
        )

    def test_the_counts_are_the_same_as_without_the_shared_context(self) -> None:
        """The behaviour this must not buy its speed with.

        A ceiling on reads is satisfied by a request that resolves nothing, so counts alone are not
        evidence. This drives the endpoint twice -- once normally, once with every shared value
        stripped at the boundary so each agent resolves alone -- and requires the three derived cards
        to agree.
        """
        from service.api_core import analytics_series
        from service.reconcilers import status_cache
        from service.routers import analytics

        # Patched on the modules that CALL it, not on the one that defines it. Both callers do
        # `from ... import _compute_agent_status`, which binds their own name at import time, so
        # patching `status_refresh._compute_agent_status` reaches neither -- and this test passed
        # anyway, comparing two identical unpatched runs. Measured before fixing it: the patch was
        # reached 0 times for both endpoints. `reached` below is the control that keeps it honest.
        targets = (analytics, analytics_series)
        reached: list[int] = []

        def cards(*, strip_shared: bool) -> dict:
            originals = {module: module._compute_agent_status for module in targets}

            def wrapper(original):
                async def call(row, db=None, **kwargs):
                    reached.append(1)
                    if strip_shared:
                        for name in ("environments_by_machine", "session_environment_by_agent", "agent_row"):
                            kwargs.pop(name, None)
                    return await original(row, db, **kwargs)
                return call

            for module, original in originals.items():
                module._compute_agent_status = wrapper(original)
            try:
                status_cache._LIVE_STATE_CACHE.clear()
                body = self.client.get(self.ENDPOINT).json()
            finally:
                for module, original in originals.items():
                    module._compute_agent_status = original
            return {key: body.get(key) for key in self.CARDS}

        shared = cards(strip_shared=False)
        alone = cards(strip_shared=True)
        self.assertTrue(
            reached,
            f"the patch never reached a status computation for {self.ENDPOINT}, so this compared two "
            "identical unpatched runs and proved nothing",
        )
        self.assertTrue(
            any(value is not None for value in shared.values()),
            "every card came back None, so this compared two empty answers",
        )
        self.assertEqual(
            shared, alone,
            "sharing the lookups changed what the analytics cards report, so it is not the same "
            "answer arrived at more cheaply",
        )


class PulseBoardResolvesTheFleetOnceTests(AnalyticsResolvesTheFleetOnceTests):
    """`GET /api/v1/analytics/pulse` -- `_build_online_agent_board` in `analytics_series.py`.

    The FOURTH caller of one derivation, found only because the third was fixed and the shape was
    then obvious. Same loop: `SELECT * FROM agents`, then a status per row with no shared context.

    MEASURED 2026-08-26, cold live-state cache, fresh database per size:

        agents   6    12    24    40
        before  102   198   390   646      = 16N + 6, exact at all four
        after    74   140   272   448      = 11N + 8, exact at all four

    At 47 agents, 758 -> 525.
    """

    ENDPOINT = "/api/v1/analytics/pulse"
    CARDS = ("onlineAgents", "workingNow", "fleetUtilizationPct")


class TheSharedLookupIsKeyedPerMachineTests(FastApiTestCase):
    """The case every other fixture in this file misses: MORE THAN ONE HOST.

    `environments_by_machine` is a dict keyed by `machine_id`, and a cache keyed wrongly returns
    another host's environment rather than no environment -- a wrong answer, not a missing one, which
    is the harder kind to notice. Every fixture above registers one environment on one machine, so
    they would ALL pass against a cache that ignored the key entirely and returned whatever it cached
    first.

    Added 2026-08-26 while auditing my own work from that day. The optimisation was already correct --
    this was verified before the test was written, not after -- but it was correct by accident as far
    as the suite was concerned, and an accident nobody pinned is one the next edit can undo.
    """

    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    HOSTS = (("linux:host-a", "linux:host-a:default", "bridge-a"),
             ("linux:host-b", "linux:host-b:default", "bridge-b"))
    AGENTS = 6

    def setUp(self) -> None:
        super().setUp()
        for machine, environment, bridge in self.HOSTS:
            response = self.client.post("/api/v1/environments/heartbeat", json={
                "id": environment, "machineId": machine, "os": "linux", "kind": "linux",
                "bridgeId": bridge, "cwdRoots": ["/workspace"],
                "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
                "metadata": {},
            })
            self.assertEqual(response.status_code, 200, response.text)
        for index in range(self.AGENTS):
            machine, _, bridge = self.HOSTS[index % len(self.HOSTS)]
            response = self.client.post("/api/v1/agents", json={
                "agentId": f"multi-host-{index}", "role": "coder", "runtime": "claude-code",
                "sessionMode": "managed", "machineId": machine, "bridgeId": bridge,
                "capabilities": ["managed-run"],
            })
            self.assertEqual(response.status_code, 200, response.text)

    def _resolved_per_agent(self, *, shared: bool) -> list:
        """Every environment the RESOLVER hands back, per agent, in call order.

        MEASURED AT THE RESOLVER, not at the endpoint's output, and the difference is the whole test.
        The first two versions of this compared the live-state cache's `environment_id` and both PASSED
        against a cache mutated to ignore its key -- once because they read `environmentId` (camelCase,
        a key that does not exist) and once because that field does not come from this lookup at all.
        A test that cannot see the mutation it names is worse than no test: it reports the property as
        held.
        """
        from service.api_core import registration_gates, status_inputs
        from service.reconcilers import status_cache

        seen: list = []
        originals = {module: module._managed_owning_environment_row
                     for module in (registration_gates, status_inputs)}

        def recorder(original):
            async def call(db, agent_row, **kwargs):
                if not shared:
                    kwargs.pop("environments_by_machine", None)
                    kwargs.pop("session_environment_by_agent", None)
                row = await original(db, agent_row, **kwargs)
                seen.append((str(agent_row["id"]), None if row is None else str(row["id"])))
                return row
            return call

        for module, original in originals.items():
            module._managed_owning_environment_row = recorder(original)
        try:
            status_cache._LIVE_STATE_CACHE.clear()
            response = self.client.get("/api/v1/analytics")
            self.assertEqual(response.status_code, 200, response.text)
        finally:
            for module, original in originals.items():
                module._managed_owning_environment_row = original
        return seen

    def test_both_hosts_are_actually_in_play(self) -> None:
        """The control. With one environment registered, or one host's agents missing, the comparison
        below would hold for a cache that ignored its key -- which is the failure it exists to catch."""
        environments = self.client.get("/api/v1/environments").json().get("environments") or []
        registered = {str(e.get("machineId") or "") for e in environments}
        for machine, _, _ in self.HOSTS:
            self.assertIn(machine, registered, "a host is missing, so this fixture is single-host again")

    def test_every_agent_resolves_the_same_with_and_without_the_shared_dict(self) -> None:
        shared = self._resolved_per_agent(shared=True)
        alone = self._resolved_per_agent(shared=False)
        self.assertTrue(shared, "the resolver was never reached, so this compared two empty lists")
        self.assertGreater(
            len({environment for _, environment in shared}), 1,
            "every agent resolved to the SAME environment, so this fixture cannot tell a per-machine "
            "cache from a key-blind one -- which is the only thing it exists to tell",
        )
        self.assertEqual(
            shared, alone,
            "an agent resolved differently once the per-machine dict was shared across the loop. A "
            "cache keyed wrongly hands one host's environment to another host's agent -- a WRONG "
            "answer rather than a missing one.",
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
