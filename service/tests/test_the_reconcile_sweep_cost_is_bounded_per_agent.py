"""One reconcile sweep costs a bounded number of database round-trips per agent.

WHY THIS PATH AND NOT ANOTHER. The sweep runs every ~61s for the life of the process, on a service
that is deliberately single-worker and whose recurring failure is write-lock contention. A per-agent
query added here is not paid once, it is paid every minute forever and grows with the fleet.

MEASURED 2026-08-28 by counting `aiosqlite` execute() calls through one full sweep on a cold
live-state cache, a fresh database per size:

    agents          4      12
    statements     92     164        = 9.0 per agent + 56 fixed

The per-agent half is the live-status computation the sweep shares with `GET /api/v1/agents`, so the
console-boot read removed there is already saved here too: `SELECT created_at FROM terminal_sessions`
measures 1 per agent in this sweep, not the 2 it cost before that change.

TWO CANDIDATE OPTIMISATIONS WERE MEASURED AND BOTH DECLINED. Recording them matters more than the
ceiling, because each looks free and one of them is not:

  * `_has_live_channel_sidecar` reads `bridge_instances` TWICE per agent per sweep -- once from
    `_reconcile_managed_worker_hygiene` and once from `_compute_live_status_cache`. Memoising it is
    UNSAFE: `_prune_superseded_bridges` DELETEs from that table and `_reap_stale_orphan_bridges`
    UPDATEs it, and both run BETWEEN the two reads. The second read is a deliberate re-read after
    mutation, and a sweep-scoped cache would serve the hygiene phase a pre-prune answer -- exactly
    the staleness those reapers exist to correct.

  * `SELECT key, value FROM settings` runs 8 times per sweep, from at least four phases that each
    call `_load_settings`. It is FIXED, not per-agent: 8 at four agents and 8 at six. Threading one
    snapshot through four reconcilers to save 7 reads a minute, on a path where reads never take the
    write lock, is not worth the change.

THE CEILING IS DELIBERATELY LOOSE. It is here to catch a NEW per-agent query -- an N+1 arriving in a
reconciler, which is the shape this repo has paid for repeatedly -- not to police a statement or two.
A change that legitimately needs more should raise it and say what it bought.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

#: Two sizes, so the SLOPE is measured rather than a single total that says nothing about growth.
SMALL = 4
LARGE = 12

#: Measured 9.0 per agent. The ceiling allows one more before it complains, so an added query is
#: caught while ordinary drift is not.
MAX_PER_AGENT = 10.0

#: Measured 56. Fixed cost is bounded separately: a new whole-table read added to a phase would
#: otherwise hide inside a per-agent allowance.
MAX_FIXED = 70


def _sweep_statements(client) -> list[str]:
    """Every SQL statement one full reconcile sweep issues, on a cold cache."""
    import aiosqlite.core as core

    from service.reconcilers import status_cache
    from service.reconcilers.sweep import _run_dispatch_reconcile_once

    status_cache._LIVE_STATE_CACHE.clear()
    seen: list[str] = []
    original = core.Connection.execute

    async def spy(conn_self, sql, *args, **kwargs):
        seen.append(re.sub(r"\s+", " ", str(sql)).strip())
        return await original(conn_self, sql, *args, **kwargs)

    core.Connection.execute = spy
    try:
        asyncio.run(_run_dispatch_reconcile_once())
    finally:
        core.Connection.execute = original
    return seen


class ReconcileSweepCostIsBoundedPerAgentTests(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}
    AGENTS = SMALL

    def setUp(self) -> None:
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": "linux:test-host:default", "machineId": "linux:test-host", "os": "linux",
            "kind": "linux", "bridgeId": "bridge-a", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        for n in range(self.AGENTS):
            response = self.client.post("/api/v1/agents", json={
                "agentId": f"sweep-agent-{n:03d}", "role": "coder", "runtime": "claude-code",
                "sessionMode": "managed", "machineId": "linux:test-host",
                "bridgeId": "bridge-a", "capabilities": ["managed-run"],
            })
            self.assertEqual(response.status_code, 200, response.text)

    def test_the_sweep_actually_did_the_work(self) -> None:
        """Positive control. Every assertion below is a CEILING, and a sweep that did nothing sits
        comfortably under all of them while proving nothing at all."""
        statements = _sweep_statements(self.client)
        self.assertGreater(len(statements), 40, f"only {len(statements)} statements; not a full sweep")
        self.assertTrue(
            any("FROM agents" in s for s in statements),
            "the sweep never read the agents table, so it is not the pass under test",
        )

    def test_the_console_boot_read_is_still_one_per_agent_here(self) -> None:
        """The sweep shares `_compute_live_status_cache` with the roster, so the shared console-boot
        reader saves round-trips on BOTH. Pinned because a regression there is invisible from the
        roster's own test -- that one measures a request, this measures a sweep."""
        statements = _sweep_statements(self.client)
        reads = [s for s in statements if s.startswith("SELECT created_at FROM terminal_sessions")]
        self.assertLessEqual(
            len(reads), self.AGENTS,
            f"the console-boot read ran {len(reads)} times for {self.AGENTS} agents; it is being "
            "asked twice per agent again",
        )

    def test_the_settings_table_is_not_read_once_per_agent(self) -> None:
        """It is read 8 times per sweep, which is a FIXED cost and stays declined above. What must
        not happen is it becoming per-agent, which is a different and much worse shape."""
        statements = _sweep_statements(self.client)
        reads = [s for s in statements if s.startswith("SELECT key, value FROM settings")]
        self.assertLess(
            len(reads), self.AGENTS + 8,
            f"settings was read {len(reads)} times for {self.AGENTS} agents; it has become per-agent",
        )


    def test_the_per_agent_slope_is_bounded(self) -> None:
        """The assertion that catches an N+1. A single total cannot: 92 statements is fine at four
        agents and alarming at one.

        BOTH POINTS COME FROM THIS ONE PROCESS. A first version built a second TestCase by hand to
        get the small-fleet figure and tripped over the per-class template database the base sets up
        -- and a figure quoted from the docstring instead would be the count-drift this repo has
        recorded happening twice in one day. Registering the extra agents here keeps the two
        measurements honest against each other.
        """
        small = len(_sweep_statements(self.client))
        for n in range(SMALL, LARGE):
            response = self.client.post("/api/v1/agents", json={
                "agentId": f"sweep-agent-{n:03d}", "role": "coder", "runtime": "claude-code",
                "sessionMode": "managed", "machineId": "linux:test-host",
                "bridgeId": "bridge-a", "capabilities": ["managed-run"],
            })
            self.assertEqual(response.status_code, 200, response.text)
        large = len(_sweep_statements(self.client))

        slope = (large - small) / (LARGE - SMALL)
        fixed = small - slope * SMALL
        self.assertGreater(large, small, "adding eight agents did not cost a single extra statement")
        self.assertLessEqual(
            slope, MAX_PER_AGENT,
            f"the sweep costs {slope:.1f} statements per agent ({small} at {SMALL}, {large} at "
            f"{LARGE}); a new per-agent query has been added to a path that runs every 61 seconds",
        )
        self.assertLessEqual(
            fixed, MAX_FIXED,
            f"the sweep's fixed cost is {fixed:.0f} statements; a whole-table read has been added",
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
