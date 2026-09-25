"""An agent's History asks the service for THAT agent's spawn records, not for a page of everyone's.

OPERATOR-REPORTED 2026-09-25: opening History from an agent's details showed "Loading…" and never
finished. Two defects sat under it, and this file is the half that lives in the service:

  * `/spawn-requests` answers 100 rows by default (500 at most), newest first, and the History drawer
    asked for that page and filtered it in the browser. On this deployment there were 1,215 spawn
    requests, so an agent spawned earlier than the newest 100 read as having no history at all.
  * Fetching and filtering the whole page again on every dashboard refresh is what made the drawer
    look permanently busy (the dashboard half is in service/new_dashboard/inspector-forms.mjs).

`agentId` filters in SQL, by the agent a record produced AND by the agent it continued or compacted
from -- the two lineage vocabularies the drawer already reads (`spawnRecordLineage`).
"""

from __future__ import annotations

import asyncio
import json

from service.tests._base import FastApiTestCase

AGENT = "history-agent"


def _seed(rows: list[tuple[str, str, dict | str]]) -> None:
    """(request id, agent the request produced, spec metadata) -- one spec per request."""
    from service.db import get_db

    async def go():
        db = await get_db()
        try:
            # Both spawn tables reference an environment, so one has to exist first.
            await db.execute(
                "INSERT OR IGNORE INTO environments (id, label, machine_id, registered_at, last_seen) "
                "VALUES ('env', 'env', 'host', '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')")
            for index, (request_id, agent_id, metadata) in enumerate(rows):
                spec_id = f"spec-{request_id}"
                meta = metadata if isinstance(metadata, str) else json.dumps(metadata)
                created = f"2026-08-{index + 1:02d}T00:00:00Z"
                await db.execute(
                    "INSERT INTO spawn_specs (id, agent_id, environment_id, runtime, workspace, metadata, "
                    "created_at, updated_at) VALUES (?, ?, 'env', 'claude-code', 'C:/w', ?, ?, ?)",
                    (spec_id, agent_id, meta, created, created))
                await db.execute(
                    "INSERT INTO spawn_requests (id, spawn_spec_id, created_by, environment_id, agent_id, role, "
                    "runtime, workspace, status, created_at, updated_at) "
                    "VALUES (?, ?, 'dashboard', 'env', ?, 'coder', 'claude-code', 'C:/w', 'completed', ?, ?)",
                    (request_id, spec_id, agent_id, created, created))
            await db.commit()
        finally:
            await db.close()

    asyncio.run(go())


class AnAgentsSpawnHistoryIsAskedForByAgent(FastApiTestCase):
    DB_NAME = "aify-spawn-history-by-agent.db"

    def _history(self, agent_id: str, **params) -> dict:
        query = "&".join(f"{k}={v}" for k, v in {"agentId": agent_id, **params}.items())
        response = self.client.get(f"/api/v1/spawn-requests?{query}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_it_returns_the_records_that_produced_the_agent_and_the_ones_it_continued_into(self) -> None:
        _seed([
            ("own", AGENT, {}),
            ("continued", "successor", {"splitIdentity": True, "continuedFromAgentId": AGENT}),
            ("compacted", "compacted-successor", {"compactMode": "handoff", "compactedFromAgentId": AGENT}),
            ("unrelated", "someone-else", {"continuedFromAgentId": "another"}),
        ])
        ids = {r["id"] for r in self._history(AGENT)["spawnRequests"]}
        self.assertEqual(ids, {"own", "continued", "compacted"})

    def test_an_old_record_is_found_however_many_newer_ones_there_are(self) -> None:
        # The browser used to filter the newest page; with 100 newer records for other agents, the
        # agent's own record was past the edge of it and History read "No history".
        _seed([("old", AGENT, {})] + [(f"newer-{i}", f"other-{i}", {}) for i in range(120)])
        ids = {r["id"] for r in self._history(AGENT)["spawnRequests"]}
        self.assertEqual(ids, {"old"})

    def test_a_malformed_metadata_value_does_not_break_the_query(self) -> None:
        # json_extract raises on malformed JSON, which would fail the whole listing for everyone.
        _seed([("broken", "someone", "{not json"), ("own", AGENT, {})])
        ids = {r["id"] for r in self._history(AGENT)["spawnRequests"]}
        self.assertEqual(ids, {"own"})

    def test_CONTROL_without_agentId_the_listing_is_unchanged(self) -> None:
        _seed([("a", AGENT, {}), ("b", "someone-else", {})])
        response = self.client.get("/api/v1/spawn-requests")
        self.assertEqual({r["id"] for r in response.json()["spawnRequests"]}, {"a", "b"})
