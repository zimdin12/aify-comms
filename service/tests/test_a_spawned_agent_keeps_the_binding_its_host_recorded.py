"""A managed agent's environment binding must survive its worker dying.

MEASURED ON THE OPERATOR'S HOST, 2026-09-06. Four managed agents, one host, all four carrying
`runtime_state.environmentId = windows:StevenZ-L:default`, and the host itself online and able to
run every one of them:

    sc-critic   machine_id set, session running  -> resolved  -> available
    sc-lead     no machine_id, session stopped   -> None      -> OFFLINE
    sc-coder    no machine_id, session stopped   -> None      -> OFFLINE
    sc-tester   no machine_id, session stopped   -> None      -> OFFLINE

THE BINDING WAS ON EVERY ROW AND NOTHING READ IT. `_managed_owning_environment_row` recovers the
spawn-time binding from `runtime_config.environmentId` -- and nothing writes that key. aify-env's
claim reports `runtimeState: {environmentId, spawnRequestId, mode, resumePolicy}` (`claim.mjs`
builds it), which the service stores as `runtime_state`. A producer and a reader on two carriers,
with the reader's own step comment describing exactly the recovery it was failing to perform.

WHY THE OTHER STEPS COULD NOT COVER IT. Step 2.5 needs a LIVE session and step 3 needs a
`machine_id` a spawn-registered agent never receives -- so both answer "where is it running now",
which is the wrong question for an agent that is not running. That is the only case this resolution
exists for, so the fallbacks were unavailable in precisely the situation they were the fallback for.

WHAT IT COST. `available` promises a cold start on the next send; `offline` says there is none to be
had. Three agents whose host was up, advertising and ready to run them reported unreachable, and the
operator was told to look at an environment that was never the problem.

These tests drive the real resolver against rows shaped exactly like those four.
"""

from __future__ import annotations

import asyncio
import json

from service.tests._base import FastApiTestCase

ENV_ID = "windows:test-host:default"
MACHINE = "win32:test-host"


class SpawnedAgentKeepsItsBindingTests(FastApiTestCase):
    def _register(self, agent_id: str, *, machine_id: str) -> None:
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id, "role": "coder", "runtime": "claude-code",
                "sessionMode": "managed", "machineId": machine_id,
                "capabilities": ["managed-run"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _seed(self, agents) -> None:
        """`agents` is (agent_id, machine_id, runtime_state, runtime_config)."""
        for agent_id, machine_id, _, _ in agents:
            self._register(agent_id, machine_id=machine_id)

        async def _go():
            from service.db import get_db
            db = await get_db()
            try:
                await db.execute(
                    """INSERT OR REPLACE INTO environments
                       (id, machine_id, status, runtimes, registered_at, last_seen)
                       VALUES (?, ?, 'online', ?, ?, ?)""",
                    (ENV_ID, MACHINE, json.dumps([{"runtime": "claude-code", "available": True}]),
                     "2026-09-06T00:00:00Z", "2099-01-01T00:00:00Z"),
                )
                for agent_id, machine_id, state, config in agents:
                    # machine_id is cleared here the way a spawn-registered agent arrives: it never
                    # receives one, and `_register` above cannot express that (the model defaults it).
                    await db.execute(
                        "UPDATE agents SET runtime_state = ?, runtime_config = ?, machine_id = ? "
                        "WHERE id = ?",
                        (json.dumps(state), json.dumps(config), machine_id, agent_id),
                    )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_go())

    def _resolve(self, agent_id: str):
        async def _go():
            from service.db import get_db
            from service.api_core.managed_env import _managed_owning_environment_row
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT * FROM agents WHERE id = ?", (agent_id,)
                )).fetchone()
                return await _managed_owning_environment_row(db, row, resolved_environment_id="")
            finally:
                await db.close()

        return asyncio.run(_go())

    def test_POSITIVE_CONTROL_an_agent_with_a_machine_id_still_resolves(self):
        """`sc-critic`'s shape, and the one that always worked. Every assertion below is "the
        environment resolved"; a resolver that had stopped resolving anything would fail here first,
        so a green result elsewhere could not be mistaken for the fix."""
        # NO runtime_state BINDING, which is the whole point of this control. Seeded with one, the
        # NEW step answered first and step 3 was never reached -- deleting step 3 entirely left all
        # five tests here green. A positive control that the fix itself satisfies is not a control.
        self._seed([("with-machine", MACHINE, {}, {})])
        row = self._resolve("with-machine")
        self.assertIsNotNone(row, "the machine_id path no longer resolves")
        self.assertEqual(row["id"], ENV_ID)

    def test_THE_DEFECT_a_spawn_registered_agent_resolves_from_runtime_state(self):
        """`sc-lead` / `sc-coder` / `sc-tester`: no machine_id, no live session, and the binding
        sitting in runtime_state where aify-env put it."""
        self._seed([("spawned", "", {"environmentId": ENV_ID, "mode": "managed-warm"}, {})])
        row = self._resolve("spawned")
        self.assertIsNotNone(
            row,
            "the agent recorded its environment at claim time and the resolver still could not "
            "find it, so a host that is up and able to run it reports offline",
        )
        self.assertEqual(row["id"], ENV_ID)

    def test_runtime_config_still_wins_when_both_are_present(self):
        """Precedence, stated rather than left to fall out. `runtime_config` is the documented
        carrier and stays first, so nothing that writes it today changes meaning."""
        self._seed([("both", "", {"environmentId": "env-from-state"},
                     {"environmentId": ENV_ID})])
        row = self._resolve("both")
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], ENV_ID)

    def test_NEGATIVE_CONTROL_a_genuinely_unbound_agent_still_resolves_to_nothing(self):
        """The fall-through this must not swallow. An agent with no binding anywhere has to stay
        unresolvable -- the docstring's `available` fall-through for a freshly-registered unbound
        agent depends on None, and answering with some environment would gate it against a host it
        was never given."""
        self._seed([("unbound", "", {"mode": "managed-warm"}, {})])
        self.assertIsNone(self._resolve("unbound"), "an agent with no binding resolved to one")

    def test_an_unparseable_runtime_state_is_not_a_crash(self):
        """This runs on the status path for every managed agent on every poll. A row whose JSON is
        malformed must fall through, not take the roster down."""
        self._register("broken", machine_id="")

        async def _go():
            from service.db import get_db
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE agents SET runtime_state = ?, machine_id = '' WHERE id = ?",
                    ("{not json", "broken"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_go())
        self.assertIsNone(self._resolve("broken"))
