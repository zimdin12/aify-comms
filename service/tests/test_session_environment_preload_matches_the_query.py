"""The batched session-environment map must answer exactly what the per-agent query answered.

`_managed_owning_environment_row` resolved an agent's live-session environment with
`... WHERE agent_id = ? AND status IN (...) ORDER BY last_seen DESC LIMIT 1`, once per agent. The
roster calls it for every agent, so at 50 agents that was 50 round-trips for one question asked 50
ways. `load_session_environment_by_agent` asks it once and hands down a map.

WHY THIS IS THE RISKY KIND OF OPTIMISATION. A preload does not replace a query with a faster query;
it replaces it with a DIFFERENT query and a Python loop, and the two can disagree in ways nothing
raises: a status the preload forgot to filter on, an ordering that picks the wrong row when an agent
has several sessions, an absent key read as something other than "no binding". Each would show up as
a managed agent gated against the wrong environment — which is a status flip on the dashboard, not an
error anyone sees.

So this file compares the two against the same data rather than asserting the map looks reasonable.
Every case below is one of those disagreements made concrete.

Round-trips per roster call at 50 agents, for the record: 285 originally, 235 after the agent row
stopped being re-read, 186 after the environments cache, 137 after this preload.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase


class SessionEnvironmentPreloadTests(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    # agent -> (session id, environment, status, last_seen)
    SESSIONS = [
        # Two LIVE sessions, different environments: the newer one must win.
        ("multi", "s-multi-old", "env-old", "running", "2026-08-01T00:00:00Z"),
        ("multi", "s-multi-new", "env-new", "running", "2026-08-02T00:00:00Z"),
        # A DEAD session that is the newest of all: it must be ignored entirely. This is the case a
        # preload that forgets the status filter gets wrong, and it gets it wrong silently.
        ("multi", "s-multi-dead", "env-dead", "stopped", "2026-08-09T00:00:00Z"),
        # One live session, nothing else.
        ("single", "s-single", "env-single", "starting", "2026-08-03T00:00:00Z"),
        # Only a dead session: this agent has NO live binding and must be absent from the map.
        ("deadonly", "s-dead", "env-nope", "ended", "2026-08-04T00:00:00Z"),
        # A live session in one of the rarer accepted states.
        ("takeover", "s-takeover", "env-takeover", "cli-takeover", "2026-08-05T00:00:00Z"),
    ]

    def _seed_sessions(self) -> None:
        """Agents and environments first: agent_sessions has ON DELETE CASCADE foreign keys to both,
        so a session row for an unregistered agent is refused outright."""
        for agent_id in sorted({a for a, *_ in self.SESSIONS}):
            response = self.client.post(
                "/api/v1/agents",
                json={
                    "agentId": agent_id, "role": "coder", "runtime": "hermes",
                    "sessionMode": "managed", "machineId": "linux:test-host",
                    "bridgeId": "bridge-current", "capabilities": ["managed-run"],
                },
            )
            self.assertEqual(response.status_code, 200, response.text)

        async def _seed():
            from service.db import get_db
            db = await get_db()
            try:
                for env in sorted({e for _, _, e, _, _ in self.SESSIONS}):
                    await db.execute(
                        """INSERT OR REPLACE INTO environments
                           (id, machine_id, status, registered_at, last_seen)
                           VALUES (?, 'linux:test-host', 'online',
                                   '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')""",
                        (env,),
                    )
                for agent_id, sid, env, status, last_seen in self.SESSIONS:
                    await db.execute(
                        # spawn_spec_id / spawn_request_id are bound to NULL explicitly. Their
                        # column default is '' and both carry a foreign key, and '' is not NULL, so
                        # SQLite enforces it and finds no matching row -- omitting them fails the
                        # insert with a bare "FOREIGN KEY constraint failed". The service's own three
                        # insert sites all bind None, so production never meets it; a default that
                        # cannot be used is a trap for the next writer, not a live bug.
                        """INSERT OR REPLACE INTO agent_sessions
                           (id, agent_id, environment_id, runtime, status, started_at, last_seen,
                            spawn_spec_id, spawn_request_id)
                           VALUES (?, ?, ?, 'hermes', ?, ?, ?, NULL, NULL)""",
                        (sid, agent_id, env, status, last_seen, last_seen),
                    )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_seed())

    @staticmethod
    def _per_agent_answer(db, agent_id: str):
        """The exact query the preload replaced, kept here verbatim as the reference implementation."""
        return db.execute(
            "SELECT environment_id FROM agent_sessions WHERE agent_id = ? "
            "AND status IN ('starting','running','recovering','restarting','cli-takeover') "
            "ORDER BY last_seen DESC LIMIT 1",
            (agent_id,),
        )

    def _compare(self) -> dict:
        async def _go():
            from service.api_core.managed_env import load_session_environment_by_agent
            from service.db import get_db
            db = await get_db()
            try:
                preloaded = await load_session_environment_by_agent(db)
                out = {}
                for agent_id in {a for a, *_ in self.SESSIONS}:
                    row = await (await self._per_agent_answer(db, agent_id)).fetchone()
                    expected = str((row["environment_id"] if row else "") or "").strip()
                    out[agent_id] = (expected, str(preloaded.get(agent_id) or "").strip())
                return out
            finally:
                await db.close()

        return asyncio.run(_go())

    def test_the_preload_matches_the_query_it_replaced(self) -> None:
        self._seed_sessions()
        for agent_id, (expected, actual) in sorted(self._compare().items()):
            self.assertEqual(
                expected, actual,
                f"{agent_id}: the per-agent query says {expected!r}, the preloaded map says {actual!r}",
            )

    def test_the_fixture_actually_discriminates(self) -> None:
        """A positive control on the comparison above. If every agent resolved to the same value, or
        to nothing, the equality would hold for a preload that returned an empty dict."""
        self._seed_sessions()
        answers = {agent: expected for agent, (expected, _) in self._compare().items()}
        self.assertEqual(answers["multi"], "env-new", "the newest LIVE session should win")
        self.assertEqual(answers["single"], "env-single")
        self.assertEqual(answers["takeover"], "env-takeover", "cli-takeover is an accepted live state")
        self.assertEqual(answers["deadonly"], "", "an agent with only a dead session has no binding")
        self.assertNotIn("env-dead", answers.values(), "a stopped session must never win on recency")

    def test_an_agent_with_no_live_session_is_absent_rather_than_empty(self) -> None:
        """The resolver reads a missing key as "no binding". An entry mapping to "" would resolve the
        same way today, but only by accident — this pins the shape the resolver actually relies on."""
        self._seed_sessions()

        async def _go():
            from service.api_core.managed_env import load_session_environment_by_agent
            from service.db import get_db
            db = await get_db()
            try:
                return await load_session_environment_by_agent(db)
            finally:
                await db.close()

        preloaded = asyncio.run(_go())
        self.assertNotIn("deadonly", preloaded)
        self.assertIn("multi", preloaded, "the map came back empty, so the assertion above is vacuous")
