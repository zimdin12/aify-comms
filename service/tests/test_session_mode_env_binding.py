"""Where a resident agent lands when it is switched to managed, tested by CALLING the derivation.

`_infer_environment_binding_for_managed_switch` was inline in a 320-line handler until v0.5.4, which
meant the only way to exercise it was to drive the whole `PATCH /agents/{id}/session-mode` route. It
is now a leaf, so these tests run it directly against a real sqlite database — the same standard the
bridge predicates are held to: a module that is extracted but never CALLED by a test has been moved,
not covered.

THE DEFECT IT EXISTS FOR (2026-06-12, operator-reported): a resident→managed switch left
`runtime_state` with no `environmentId`, so the agent rendered in the Sessions page "unassigned"
group and looked unreachable until someone hand-edited the identity. Nothing raised, which is what
made it expensive — the switch reported success.

The refusal-shaped cases get more attention than the happy path, deliberately. Binding an agent to
the WRONG environment sends its dispatches to a bridge on another machine, and that failure looks
like an idle agent rather than like a mis-binding.
"""

from __future__ import annotations

import unittest

import aiosqlite

from service.api_core.session_mode_env_binding import _infer_environment_binding_for_managed_switch

#: THE REAL SCHEMA, not a hand-written stand-in.
#:
#: This file used to declare its own two tables, and its `agent_sessions` carried a `created_at`
#: column the service's schema has never had. The function under test ordered by
#: `COALESCE(last_seen, created_at)`, so the query ran here and raised `no such column: created_at`
#: in production -- for 78 days, behind an `except Exception`, with this test green the whole time.
#: A fixture that invents the table the query wants proves the query is self-consistent and nothing
#: else.
#:
#: Importing the real schema costs one executescript of an in-memory database and removes the entire
#: class: a column that does not exist cannot be inserted into, selected, or ordered by here either.
from service.schema import SCHEMA  # noqa: E402


class InferEnvironmentBindingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)
        self.runtime_state: dict = {}
        self.warnings: list[str] = []

    async def asyncTearDown(self):
        await self.db.close()

    async def _session(self, sid, *, agent="a1", environment="", last_seen="2026-08-01T10:00:00Z"):
        await self.db.execute(
            "INSERT INTO agent_sessions (id, agent_id, environment_id, started_at, last_seen,"
            " runtime) VALUES (?,?,?,?,?,'claude-code')",
            (sid, agent, environment, last_seen, last_seen),
        )

    async def _environment(self, eid, *, machine="m1", status="online", last_seen="2026-08-01T10:00:00Z"):
        # Named columns, because the real `environments` table has fifteen. A positional insert is
        # what ties a fixture to a hand-written stand-in in the first place.
        await self.db.execute(
            "INSERT INTO environments (id, machine_id, status, last_seen, label, os, kind,"
            " registered_at) VALUES (?,?,?,?,?,'linux','linux',?)",
            (eid, machine, status, last_seen, eid, last_seen),
        )

    async def _run(self, *, new_mode="managed", machine_id="m1", agent_id="a1"):
        await _infer_environment_binding_for_managed_switch(
            self.db, agent_id, {"machine_id": machine_id}, new_mode, self.runtime_state, self.warnings)
        return self.runtime_state.get("environmentId"), self.warnings

    async def test_the_agents_own_latest_session_wins(self):
        await self._session("s1", environment="env-old", last_seen="2026-08-01T10:00:00Z")
        await self._session("s2", environment="env-new", last_seen="2026-08-02T10:00:00Z")
        bound, warnings = await self._run()
        self.assertEqual("env-new", bound)
        self.assertEqual([], warnings)

    async def test_a_session_with_no_environment_is_not_treated_as_an_answer(self):
        """`COALESCE(environment_id, '') != ''` — an empty binding is absence, not a binding.

        Without that filter the newest session would win with an empty string, the agent would be
        "bound" to nothing, and the machine fallback below would never be consulted.
        """
        await self._session("s1", environment="env-real", last_seen="2026-08-01T10:00:00Z")
        await self._session("s2", environment="", last_seen="2026-08-09T10:00:00Z")
        bound, warnings = await self._run()
        self.assertEqual("env-real", bound)
        self.assertEqual([], warnings)

    async def test_it_falls_back_to_the_machines_environment(self):
        await self._environment("env-machine")
        bound, warnings = await self._run()
        self.assertEqual("env-machine", bound)
        self.assertEqual([], warnings)

    async def test_an_ONLINE_environment_beats_a_newer_offline_one(self):
        """Order matters more than recency: a binding to a dead bridge is a binding to nothing."""
        await self._environment("env-offline", status="offline", last_seen="2026-08-09T10:00:00Z")
        await self._environment("env-online", status="online", last_seen="2026-08-01T10:00:00Z")
        bound, _ = await self._run()
        self.assertEqual("env-online", bound)

    async def test_the_newest_wins_among_environments_of_equal_standing(self):
        await self._environment("env-older", status="online", last_seen="2026-08-01T10:00:00Z")
        await self._environment("env-newer", status="online", last_seen="2026-08-09T10:00:00Z")
        bound, _ = await self._run()
        self.assertEqual("env-newer", bound)

    async def test_forgotten_and_disabled_environments_are_never_inferred(self):
        """Both are operator decisions to stop using a host; inferring one would undo that."""
        for status in ("forgotten", "disabled"):
            with self.subTest(status=status):
                self.runtime_state, self.warnings = {}, []
                await self.db.execute("DELETE FROM environments")
                await self._environment("env-retired", status=status)
                bound, warnings = await self._run()
                self.assertIsNone(bound)
                self.assertEqual(1, len(warnings))

    async def test_another_machines_environment_is_never_inferred(self):
        await self._environment("env-elsewhere", machine="m2")
        bound, warnings = await self._run(machine_id="m1")
        self.assertIsNone(bound)
        self.assertEqual(1, len(warnings))

    async def test_another_agents_session_is_never_inferred(self):
        await self._session("s1", agent="someone-else", environment="env-theirs")
        bound, warnings = await self._run(agent_id="a1")
        self.assertIsNone(bound)
        self.assertEqual(1, len(warnings))

    async def test_when_nothing_can_be_inferred_the_switch_still_proceeds_and_says_so(self):
        """The whole point. A switch the operator asked for must not fail on a missing binding —
        but it must not silently leave them to discover 'unassigned' in the UI either."""
        bound, warnings = await self._run()
        self.assertIsNone(bound)
        self.assertEqual(1, len(warnings))
        self.assertIn("unassigned", warnings[0])

    async def test_a_switch_to_RESIDENT_infers_nothing(self):
        await self._environment("env-machine")
        bound, warnings = await self._run(new_mode="resident")
        self.assertIsNone(bound)
        self.assertEqual([], warnings, "a resident switch must not warn about a binding it does not need")

    async def test_an_existing_binding_is_never_overwritten(self):
        """The operator's own choice outranks anything derivable."""
        self.runtime_state["environmentId"] = "env-chosen"
        await self._environment("env-machine")
        bound, warnings = await self._run()
        self.assertEqual("env-chosen", bound)
        self.assertEqual([], warnings)

    async def test_a_broken_query_warns_instead_of_failing_the_switch(self):
        """The `except Exception` is deliberate and is asserted rather than assumed.

        Inference is advisory. If the tables it reads are missing or malformed, the operator's switch
        must still complete — with the warning — rather than raising out of the route.
        """
        await self.db.executescript("DROP TABLE agent_sessions; DROP TABLE environments;")
        bound, warnings = await self._run()
        self.assertIsNone(bound)
        self.assertEqual(1, len(warnings))


if __name__ == "__main__":
    unittest.main()
