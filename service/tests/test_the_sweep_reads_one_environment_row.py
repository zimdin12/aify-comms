"""One reconcile sweep, one environments-by-machine read.

The same query, the same fix, the other hot path. `_managed_owning_environment_row` falls back to
`SELECT * FROM environments WHERE machine_id = ?`, which depends on machine_id ALONE, so a fleet
sharing a host gets the same answer every time. `fab4204c` gave `GET /api/v1/agents` a request-scoped
dict for exactly this and threaded it through both of the handler's phases. The reconcile sweep calls
the same helpers and passes nothing, so it still asks once per agent, twice.

MEASURED 2026-08-26, before the fix, counting `aiosqlite` execute() calls through one
`_run_dispatch_reconcile_once()`:

    agents=  5   sweep round-trips= 129   environments-by-machine reads=  10
    agents= 25   sweep round-trips= 469   environments-by-machine reads=  50
    agents= 50   sweep round-trips= 894   environments-by-machine reads= 100

Two per agent, linear, and unlike the roster there is no cap: the roster refreshes at most
`LIST_AGENTS_REFRESH_LIMIT` (8) live states per call, while the sweep passes `limit=None` and
recomputes every agent. So this cost grows with the fleet on a single-worker SQLite service whose
lock contention has its own entry in DECISIONS.md.

WHY A SWEEP-SCOPED CACHE IS SAFE, on the same terms as the request-scoped one.
`_managed_owning_environment_row`'s docstring warns that a cache outliving its caller's loop would
need invalidating by whatever writes `environments` -- heartbeats do, constantly. A dict created at
the top of one sweep and dropped at its end has exactly the lifetime the request-scoped dict has.
Measured rather than argued: one sweep issues ZERO writes to `environments` and 50 reads of that one
query at 25 agents.

The ceiling is AT MOST ONE rather than exactly one: a sweep with no managed agent to resolve should
read the table zero times, and requiring one would fail a correct sweep that had less work to do.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

ENV_ID = "linux:test-host:default"
BY_MACHINE = "SELECT * FROM environments WHERE machine_id = ?"
_WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.I)

#: More than the roster's refresh cap, so the sweep's uncapped pass is what is being measured.
AGENTS = 12


class SweepEnvironmentLookupTests(FastApiTestCase):
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
                "agentId": f"sweep-agent-{n:03d}", "role": "coder", "runtime": "claude-code",
                "sessionMode": "managed", "machineId": "linux:test-host",
                "bridgeId": "bridge-current", "capabilities": ["managed-run"],
            })
            self.assertEqual(response.status_code, 200, response.text)

    def _sweep_statements(self) -> list[str]:
        seen: list[str] = []

        async def _go():
            import aiosqlite.core as core
            from service.reconcilers.sweep import _run_dispatch_reconcile_once
            original = core.Connection.execute

            async def spy(conn_self, sql, *args, **kwargs):
                seen.append(re.sub(r"\s+", " ", str(sql)).strip())
                return await original(conn_self, sql, *args, **kwargs)

            core.Connection.execute = spy
            try:
                await _run_dispatch_reconcile_once()
            finally:
                core.Connection.execute = original

        asyncio.run(_go())
        return seen

    def test_the_sweep_actually_ran(self) -> None:
        """Positive control. The assertion below is a CEILING, and a sweep that did nothing sits
        under any ceiling while proving nothing at all."""
        statements = self._sweep_statements()
        self.assertGreater(
            len(statements), 50,
            f"the sweep issued only {len(statements)} statements; it did not do a full pass",
        )
        self.assertTrue(
            any("FROM agents" in s for s in statements),
            "the sweep never read the agents table, so this is not the pass under test",
        )

    def test_the_machine_lookup_happens_at_most_once(self) -> None:
        hits = [s for s in self._sweep_statements() if s.startswith(BY_MACHINE)]
        self.assertLessEqual(
            len(hits), 1,
            f"one reconcile sweep read environments-by-machine {len(hits)} times for {AGENTS} agents "
            "sharing a host. The answer depends on machine_id alone, so every read after the first "
            "returns what the sweep already had. The roster solved this with a request-scoped dict; "
            "the sweep needs a sweep-scoped one, threaded the same way.",
        )

    def test_the_session_binding_is_preloaded_not_asked_per_agent(self) -> None:
        """The second map, gated the same way as the first.

        `_managed_owning_environment_row` resolves an agent's live-session environment with
        `SELECT environment_id FROM agent_sessions WHERE agent_id = ? ... ORDER BY last_seen DESC
        LIMIT 1` whenever no preload is supplied, and this pass asks it twice per agent.
        `load_session_environment_by_agent` fetches the same rows under the same ordering and keeps
        the first per agent -- the row LIMIT 1 would have returned -- which
        `test_session_environment_preload_matches_the_query.py` already holds it to.

        A PRELOAD, not a cache: built before the work and never written during it, so it does not
        need the lifetime argument the environments dict does.
        """
        per_agent = [
            s for s in self._sweep_statements()
            if s.startswith("SELECT environment_id FROM agent_sessions WHERE agent_id = ?")
        ]
        self.assertEqual(
            per_agent, [],
            f"the sweep resolved the session binding per agent {len(per_agent)} times for "
            f"{AGENTS} agents. The roster preloads every agent's binding in one query and the "
            "resolver already accepts the map; the sweep needs to build it once and pass it.",
        )

    def test_no_agent_row_is_re_read_that_the_batch_already_holds(self) -> None:
        """The third of the same family, and the cheapest: no extra query at all.

        `_refresh_expired_agent_live_states` reads every agent to decide who is stale, then
        `_refresh_agent_live_state` re-selected each one by id -- 1.0N round-trips for rows the caller
        was holding. Widening that one `SELECT id FROM agents` to `SELECT *` carries the seven columns
        `_compute_live_status_cache` reads, so the per-agent re-select disappears without adding
        anything. It is `5c45ab44`'s move ("stop re-reading agent rows the handler is already
        holding") on the path that does it once per agent rather than eight times per poll.

        The parameter is OPTIONAL and falls back, because `_refresh_agent_live_state` is also called
        with an id and no row -- from `_compute_agent_status`, whose own row cannot safely be passed
        through without auditing every caller for partial selects.
        """
        re_reads = [s for s in self._sweep_statements() if s.startswith("SELECT * FROM agents WHERE id = ?")]
        self.assertEqual(
            re_reads, [],
            f"the sweep re-read {len(re_reads)} agent rows it had already selected, for {AGENTS} "
            "agents. The batch reads them to sort by staleness; it should hand each one over.",
        )

    def test_the_sweep_writes_no_environment_row(self) -> None:
        """The licence for caching across the pass, asserted rather than assumed. A sweep-scoped cache
        is correct exactly when nothing changes its subject during the sweep."""
        offenders = [
            s for s in self._sweep_statements()
            if _WRITE.match(s) and re.search(r"\benvironments\b", s, re.I)
        ]
        self.assertEqual(
            offenders, [],
            f"the sweep wrote `environments` while caching reads of it: {offenders}",
        )

    def test_the_cache_returns_what_the_uncached_lookup_returned(self) -> None:
        """The behaviour this must not buy its speed with.

        A ceiling on reads is satisfied by a sweep that resolves nothing, so the count alone is not
        evidence. This records the resolver's own RETURN VALUE per call, once with the cache reaching
        it and once with every call forced down the uncached branch, and requires the two sequences to
        match. Call counts are comparable because the cache lives INSIDE the resolver.
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
                self._sweep_statements()
            finally:
                for module, original in originals.items():
                    module._managed_owning_environment_row = original
            return recorded

        without_cache = resolutions(drop_cache=True)
        with_cache = resolutions(drop_cache=False)

        self.assertTrue(without_cache, "the resolver was never called, so this compared two empty lists")
        self.assertTrue(
            any(env_id for _, env_id in with_cache),
            "every resolution came back None, so the comparison cannot tell a hit from a miss",
        )
        self.assertEqual(
            with_cache, without_cache,
            "the sweep-scoped cache changed which environment an agent resolved to, so it is not the "
            "same answer arrived at more cheaply",
        )
