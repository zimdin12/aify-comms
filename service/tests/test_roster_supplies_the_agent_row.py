"""The roster hands `_enforce_env_reachable_gate` the agent row it already holds.

`list_agents` selects every agent, then calls this gate once per agent, and the gate's no-binding
branch re-selected the same row by id. Measured on a synthetic 50-agent database: one roster call
issued 285 SQL statements, 58 of them that re-read. Each is an event-loop hop to aiosqlite's worker
thread, which is why an indexed lookup still costs milliseconds. After the change: 235 statements,
and that query fell from 58 to 8 (the rest belong to other code paths).

TESTED DIRECTLY, and that is a correction of my first attempt. This began as an end-to-end check that
`GET /agents` and `GET /agents/{id}` agree field-for-field, which reads well and proved nothing: with
the fixture's environment unresolvable the gate returns its payload untouched, so DELETING the gate
for the roster path left the file green. Three fixture corrections later it still did -- the
environments row needed a matching machine_id, then a non-empty `runtimes`, then `runtimes` as a list
of OBJECTS rather than names, and each wrong shape looked identical from outside. A test that cannot
fail is worse than none, so that version is gone. What remains asserts the two properties the change
actually has:

  1. the gate reaches the same verdict whether the row is supplied or fetched, and
  2. supplying it removes the read.

Both are observable without the environment resolving, which is what made the first version
unsalvageable and this one honest.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase


class RosterSuppliesTheAgentRowTests(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    AGENT_ID = "hermes-managed"
    SECOND_AGENT_ID = "hermes-managed-2"

    def setUp(self) -> None:
        super().setUp()
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": self.AGENT_ID,
                "role": "coder",
                "runtime": "hermes",
                "sessionMode": "managed",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
                "capabilities": ["native-managed-run", "managed-run"],
                "runtimeConfig": {"gatewayUrl": "ws://127.0.0.1:9119/api/ws?token=t"},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _run_gate_for_many(self, agent_ids, share_cache: bool):
        """Run the gate over several agents the way the roster does, with or without the shared cache.

        Returns the verdicts and the statements, so the test can assert BOTH that the repeated read
        collapses and that the answers do not move — a cache that changed a verdict would be a
        correctness bug wearing a performance fix's clothes.
        """

        async def _go():
            import aiosqlite.core as core
            from service.api_core.registration_gates import _enforce_env_reachable_gate
            from service.db import get_db
            from service.routers.agents.identity import _load_settings

            db = await get_db()
            statements: list[str] = []
            original = core.Connection.execute

            async def spy(conn_self, sql, *args, **kwargs):
                statements.append(re.sub(r"\s+", " ", str(sql)).strip())
                return await original(conn_self, sql, *args, **kwargs)

            try:
                settings = await _load_settings(db)
                rows = {}
                for agent_id in agent_ids:
                    rows[agent_id] = await (
                        await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
                    ).fetchone()
                    self.assertIsNotNone(rows[agent_id], f"{agent_id} missing from the fixture")
                cache: dict = {} if share_cache else None
                core.Connection.execute = spy
                verdicts = {}
                for agent_id in agent_ids:
                    verdicts[agent_id] = await _enforce_env_reachable_gate(
                        {"status": "available", "sessionMode": "managed", "runtimeState": {}},
                        db, settings, agent_id, agent_row=rows[agent_id],
                        environments_by_machine=cache,
                    )
                return verdicts, statements
            finally:
                core.Connection.execute = original
                await db.close()

        return asyncio.run(_go())

    def _run_gate(self, supply_row: bool):
        """Call the gate the way the roster does (row in hand) or the way the detail view does (not).

        Returns the payload and every SQL statement the call issued, so the test asserts on the read
        disappearing rather than on a duration, which would be flaky for a sub-millisecond query.
        """

        async def _go():
            import aiosqlite.core as core
            from service.api_core.registration_gates import _enforce_env_reachable_gate
            from service.db import get_db
            from service.routers.agents.identity import _load_settings

            db = await get_db()
            statements: list[str] = []
            original = core.Connection.execute

            async def spy(conn_self, sql, *args, **kwargs):
                statements.append(re.sub(r"\s+", " ", str(sql)).strip())
                return await original(conn_self, sql, *args, **kwargs)

            try:
                settings = await _load_settings(db)
                row = await (
                    await db.execute("SELECT * FROM agents WHERE id = ?", (self.AGENT_ID,))
                ).fetchone()
                self.assertIsNotNone(row, "the fixture agent is missing, so this proves nothing")
                # Shaped like the payload list_agents builds, pinned to the branch under test: a
                # live-looking managed agent with no environment binding anywhere, which is the only
                # path that reads the agent row.
                payload = {"status": "available", "sessionMode": "managed", "runtimeState": {}}
                core.Connection.execute = spy
                result = await _enforce_env_reachable_gate(
                    payload, db, settings, self.AGENT_ID,
                    **({"agent_row": row} if supply_row else {}),
                )
                return result, statements
            finally:
                core.Connection.execute = original
                await db.close()

        return asyncio.run(_go())

    @staticmethod
    def _agent_reads(statements: list[str]) -> int:
        return sum(1 for s in statements if s.startswith("SELECT * FROM agents WHERE id ="))

    def test_supplying_the_row_removes_the_read(self) -> None:
        _, fetched = self._run_gate(supply_row=False)
        _, supplied = self._run_gate(supply_row=True)

        self.assertEqual(
            self._agent_reads(fetched), 1,
            "the gate no longer reads the agent row when none is supplied — this test's premise is gone",
        )
        self.assertEqual(
            self._agent_reads(supplied), 0,
            "the gate re-read a row the caller had already handed it, which is the whole change",
        )

    def test_the_verdict_is_the_same_either_way(self) -> None:
        """The read is an optimisation only if the answer does not move. A roster gating one way and a
        detail view gating the other raises nothing — it puts two screens on the dashboard that
        disagree about whether an agent is reachable, with no reason to trust either."""
        fetched, _ = self._run_gate(supply_row=False)
        supplied, _ = self._run_gate(supply_row=True)
        self.assertEqual(
            fetched, supplied,
            "the gate reached different verdicts from the same row depending on who read it",
        )

    def test_the_gate_still_consults_the_environment(self) -> None:
        """Guards the direction this change could over-reach. The agent read and the environment
        resolution live in the same branch, and an edit that removes one can take the other with it —
        which would silently turn the gate into a no-op rather than a faster gate."""
        _, statements = self._run_gate(supply_row=True)
        self.assertTrue(
            any("FROM environments" in s for s in statements),
            f"the gate stopped consulting environments entirely: {statements}",
        )

    def test_the_shared_cache_collapses_the_repeated_environment_read(self) -> None:
        """The environments lookup depends on machine_id alone, and a roster's agents share a host.

        Measured on a synthetic 50-agent database: the roster's own gate calls issued 50 identical
        reads of a two-row table; with the cache they issue one. This asserts the same property at the
        size a test can hold.
        """
        self._register_second_agent_on_the_same_machine()
        ids = [self.AGENT_ID, self.SECOND_AGENT_ID]

        _, uncached = self._run_gate_for_many(ids, share_cache=False)
        _, cached = self._run_gate_for_many(ids, share_cache=True)

        def env_reads(statements):
            return sum(1 for s in statements if s.startswith("SELECT * FROM environments WHERE machine_id"))

        self.assertEqual(
            env_reads(uncached), len(ids),
            "without the cache each agent should read environments once; the premise has changed",
        )
        self.assertEqual(
            env_reads(cached), 1,
            "two agents on one machine still read the environments table twice",
        )

    def test_the_shared_cache_does_not_change_any_verdict(self) -> None:
        """A cache with a lifetime longer than its caller would be invalidated by every heartbeat that
        writes `environments`. This is why the dict is owned by the request rather than the module —
        and why the verdicts have to match the uncached path exactly."""
        self._register_second_agent_on_the_same_machine()
        ids = [self.AGENT_ID, self.SECOND_AGENT_ID]
        uncached, _ = self._run_gate_for_many(ids, share_cache=False)
        cached, _ = self._run_gate_for_many(ids, share_cache=True)
        self.assertEqual(uncached, cached, "the cached path reached a different verdict")

    def _register_second_agent_on_the_same_machine(self) -> None:
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": self.SECOND_AGENT_ID,
                "role": "coder",
                "runtime": "hermes",
                "sessionMode": "managed",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
                "capabilities": ["native-managed-run", "managed-run"],
                "runtimeConfig": {"gatewayUrl": "ws://127.0.0.1:9120/api/ws?token=t"},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
