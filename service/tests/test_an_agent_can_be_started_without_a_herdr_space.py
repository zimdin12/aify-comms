"""A managed agent can be started without a herdr space of its own.

THE OPERATOR, 2026-09-30: "where is that hide or do not run as space option ? (so we could start
agents that do not show up in herdr-aify". One value per agent, on the agent row beside `favorited`,
set by `PATCH /agents/{id}/herdr-space` and handed to the host tier in the launch it reads.

WHAT THESE PIN: the default is a space (every existing agent keeps today's behaviour); the setting
reaches the launch, which is the only thing the host reads; and a rename keeps it.
"""

from __future__ import annotations

from service.tests._base import FastApiTestCase
# The module, not the class: a class imported by name is collected here and runs twice.
from service.tests import test_a_process_host_can_ask_what_to_run as launch_fixture

AGENT = "sc-lead"


class AnAgentCanBeStartedWithoutAHerdrSpaceTests(FastApiTestCase):
    DB_NAME = "aify-test-herdr-space.db"
    ENV = launch_fixture.AProcessHostCanAskWhatToRunTests.ENV
    _register = launch_fixture.AProcessHostCanAskWhatToRunTests._register
    _terminal = launch_fixture.AProcessHostCanAskWhatToRunTests._terminal

    def _set(self, show, agent_id=AGENT):
        return self._client.patch(f"/api/v1/agents/{agent_id}/herdr-space", json={"show": show})

    def _record(self, agent_id=AGENT):
        response = self._client.get(f"/api/v1/agents/{agent_id}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        return body.get("agent", body)

    def _launch(self, terminal_id="term-1"):
        response = self._client.get(f"/api/v1/terminals/{terminal_id}/launch")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["launch"]

    def test_an_agent_has_a_space_until_the_operator_says_otherwise(self):
        self._register()
        self._terminal()
        self.assertIs(self._record()["herdrSpace"], True)
        self.assertIs(self._launch()["herdrSpace"], True)

    def test_the_setting_reaches_the_launch_the_host_reads(self):
        self._register()
        self._terminal()
        response = self._set(False)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(response.json()["herdrSpace"], False)
        self.assertIs(self._record()["herdrSpace"], False)
        self.assertIs(self._launch()["herdrSpace"], False)
        self._set(True)
        self.assertIs(self._launch()["herdrSpace"], True, "turning it back on did not reach the launch")

    def test_an_existing_database_gains_the_column_and_every_agent_keeps_its_space(self):
        """AN UPGRADE, not a fresh schema: a host's agents table predates the column. The migration
        must add it and every agent already there must read as having a space, as today."""
        import asyncio
        import sqlite3
        import tempfile
        from pathlib import Path

        import aiosqlite

        from service.api_core.records import herdr_space_of
        from service.db import _migrate_agents_table

        path = Path(tempfile.mkdtemp()) / "old.db"
        with sqlite3.connect(path) as old:
            old.execute("CREATE TABLE agents (id TEXT PRIMARY KEY, role TEXT, registered_at TEXT, last_seen TEXT)")
            old.execute("INSERT INTO agents VALUES ('sc-old', 'coder', '2026-01-01', '2026-01-01')")

        async def upgrade():
            async with aiosqlite.connect(path) as db:
                db.row_factory = aiosqlite.Row
                before = {r[1] for r in await (await db.execute("PRAGMA table_info(agents)")).fetchall()}
                await _migrate_agents_table(db)
                await db.commit()
                row = await (await db.execute("SELECT * FROM agents WHERE id = 'sc-old'")).fetchone()
                return before, row["herdr_space"], herdr_space_of(row)

        before, stored, read = asyncio.run(upgrade())
        self.assertNotIn("herdr_space", before, "CONTROL: the old table already had the column")
        self.assertEqual(stored, 1)
        self.assertIs(read, True)

    def test_an_unknown_agent_is_404(self):
        self.assertEqual(self._set(False, agent_id="nobody").status_code, 404)

    def test_a_rename_keeps_the_setting(self):
        self._register()
        self._set(False)
        renamed = self._client.post(f"/api/v1/agents/{AGENT}/rename", json={"newAgentId": "sc-lead-2"})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertIs(self._record("sc-lead-2")["herdrSpace"], False)
