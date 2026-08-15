"""Assigning an environment writes a SPEC, and this is that write. Tested by calling it.

`_upsert_spawn_spec_for_assignment` was inline in `assign_agent_environment` until v0.5.4, so
exercising it meant driving `POST /agents/{id}/environment`. It is now a leaf and these tests run it
against a real sqlite database.

WHY IT MATTERS THAT THE WRITE LANDS. The spawn spec is what a future spawn reads to learn where and
how to start this agent. An assignment that updated the agent row but not the spec looks entirely
correct in the dashboard and then produces a worker on the OLD host at the next cold start — which
reads as a spawn bug, days later, in a different part of the system.

THE TWO BRANCHES ARE ASYMMETRIC, and only one of them is exercised by a fresh agent. An agent that
already has a spec takes an UPDATE that touches six columns and MERGES metadata; an agent that never
spawned takes an INSERT that must supply all eighteen. So the tests below give the update path at
least as much attention as the insert path, because it is the one nobody hits by accident.
"""

from __future__ import annotations

import json
import unittest

import aiosqlite

from service.api_core.spawn_spec_assignment import _upsert_spawn_spec_for_assignment

SCHEMA = """
CREATE TABLE spawn_specs (
    id TEXT PRIMARY KEY, agent_id TEXT, environment_id TEXT, runtime TEXT, workspace TEXT,
    model TEXT, profile TEXT, mode TEXT, system_prompt TEXT, standing_instructions TEXT,
    env_vars TEXT, channel_ids TEXT, budget_policy TEXT, context_policy TEXT,
    restart_policy TEXT, metadata TEXT, created_at TEXT, updated_at TEXT
);
"""

OLD = "2026-08-01T10:00:00Z"
NOW = "2026-08-15T12:00:00Z"


class _Req:
    def __init__(self, requested_by=None):
        self.requestedBy = requested_by


class SpawnSpecAssignmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)

    async def asyncTearDown(self):
        await self.db.close()

    async def _spec(self, sid, *, agent="a1", metadata="{}", updated_at=OLD, environment="env-old"):
        await self.db.execute(
            "INSERT INTO spawn_specs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, agent, environment, "codex", "/old", "gpt-old", "", "managed-warm", "",
             "old instructions", "{}", "[]", "{}", "{}", "{}", metadata, OLD, updated_at))

    async def _assign(self, *, agent_id="a1", environment_id="env-new", runtime="hermes",
                      workspace="/new", model="glm", runtime_config=None, requested_by=None,
                      instructions="agent instructions"):
        return await _upsert_spawn_spec_for_assignment(
            self.db, {"instructions": instructions}, agent_id, _Req(requested_by),
            environment_id, runtime, workspace, model, runtime_config or {}, NOW)

    async def _rows(self):
        return await (await self.db.execute("SELECT * FROM spawn_specs ORDER BY id")).fetchall()

    # ---- the insert path ----------------------------------------------------

    async def test_an_agent_with_no_spec_gets_one(self):
        spec_id = await self._assign()
        rows = await self._rows()
        self.assertEqual(1, len(rows))
        self.assertEqual(spec_id, rows[0]["id"])
        self.assertTrue(spec_id.startswith("spec_"))
        self.assertEqual("env-new", rows[0]["environment_id"])
        self.assertEqual("hermes", rows[0]["runtime"])
        self.assertEqual("/new", rows[0]["workspace"])
        self.assertEqual("glm", rows[0]["model"])

    async def test_the_new_spec_carries_the_agents_standing_instructions(self):
        """Not the request's. The spec is created FROM the agent, not from the assignment form."""
        await self._assign(instructions="do the thing carefully")
        self.assertEqual("do the thing carefully", (await self._rows())[0]["standing_instructions"])

    async def test_the_json_columns_are_EMPTY_BUT_VALID_never_null(self):
        """These are read with `json.loads` downstream, so a NULL is a crash, not a missing value."""
        await self._assign()
        row = (await self._rows())[0]
        for column, empty in (("env_vars", {}), ("channel_ids", []), ("budget_policy", {}),
                              ("context_policy", {}), ("restart_policy", {})):
            with self.subTest(column=column):
                self.assertIsNotNone(row[column])
                self.assertEqual(empty, json.loads(row[column]))

    async def test_the_new_spec_records_who_asked_and_that_it_came_from_the_dashboard(self):
        await self._assign(requested_by="steven")
        metadata = json.loads((await self._rows())[0]["metadata"])
        self.assertEqual("steven", metadata["createdBy"])
        self.assertTrue(metadata["assignedFromDashboard"])

    async def test_an_absent_requester_falls_back_to_dashboard(self):
        await self._assign(requested_by=None)
        self.assertEqual("dashboard", json.loads((await self._rows())[0]["metadata"])["createdBy"])

    # ---- the update path ----------------------------------------------------

    async def test_an_existing_spec_is_updated_in_place_and_keeps_its_id(self):
        await self._spec("spec-existing")
        spec_id = await self._assign()
        rows = await self._rows()
        self.assertEqual(1, len(rows), "an assignment must not leave two specs behind")
        self.assertEqual("spec-existing", spec_id)
        self.assertEqual("env-new", rows[0]["environment_id"])
        self.assertEqual(NOW, rows[0]["updated_at"])

    async def test_the_update_MERGES_metadata_rather_than_replacing_it(self):
        """Whatever a previous spawn recorded there survives an environment change."""
        await self._spec("spec-existing", metadata=json.dumps({"createdBy": "someone", "keep": "me"}))
        await self._assign()
        metadata = json.loads((await self._rows())[0]["metadata"])
        self.assertEqual("me", metadata["keep"])
        self.assertEqual("someone", metadata["createdBy"])

    async def test_a_runtime_config_overwrites_the_previous_one(self):
        await self._spec("spec-existing", metadata=json.dumps({"runtimeConfig": {"effort": "low"}}))
        await self._assign(runtime_config={"effort": "high"})
        metadata = json.loads((await self._rows())[0]["metadata"])
        self.assertEqual({"effort": "high"}, metadata["runtimeConfig"])

    async def test_an_EMPTY_runtime_config_leaves_the_stored_one_alone(self):
        """`**({...} if runtime_config else {})` — absence is not an instruction to forget.

        An assignment that does not mention runtime config must not silently drop the effort a
        previous spawn was configured with.
        """
        await self._spec("spec-existing", metadata=json.dumps({"runtimeConfig": {"effort": "high"}}))
        await self._assign(runtime_config={})
        metadata = json.loads((await self._rows())[0]["metadata"])
        self.assertEqual({"effort": "high"}, metadata["runtimeConfig"])

    async def test_the_NEWEST_spec_is_the_one_chosen(self):
        """`ORDER BY updated_at DESC LIMIT 1` — an agent can accumulate specs over its life."""
        await self._spec("spec-older", updated_at="2026-07-01T10:00:00Z")
        await self._spec("spec-newer", updated_at="2026-08-10T10:00:00Z")
        spec_id = await self._assign()
        self.assertEqual("spec-newer", spec_id)

    async def test_another_agents_spec_is_never_chosen_or_touched(self):
        await self._spec("spec-theirs", agent="someone-else")
        spec_id = await self._assign(agent_id="a1")
        rows = await self._rows()
        self.assertNotEqual("spec-theirs", spec_id, "a fresh spec must be created for this agent")
        self.assertEqual(2, len(rows))
        theirs = next(r for r in rows if r["id"] == "spec-theirs")
        self.assertEqual("env-old", theirs["environment_id"], "their spec must be untouched")

    async def test_the_update_writes_every_agent_scoped_row_not_just_the_newest(self):
        """A shape worth knowing rather than assuming: the SELECT picks one spec, but the UPDATE's
        WHERE clause is on `agent_id`, so every spec this agent owns is repointed. That is what keeps
        an older spec from being read later and starting a worker on the environment just left."""
        await self._spec("spec-older", updated_at="2026-07-01T10:00:00Z")
        await self._spec("spec-newer", updated_at="2026-08-10T10:00:00Z")
        await self._assign()
        self.assertEqual(
            {"env-new"}, {r["environment_id"] for r in await self._rows()},
            "every spec for this agent must point at the new environment")


if __name__ == "__main__":
    unittest.main()
