"""The roster's per-request caches are only safe if the request never writes what they hold.

`GET /api/v1/agents` builds two caches and reuses them for every agent: `environments_by_machine`
(the owning-environment lookup) and `session_environment_by_agent` (each agent's live-session
binding). Both were introduced to remove repeated reads -- 285 SQL statements per call at 50 agents,
down to 137.

A per-request cache is correct exactly when nothing changes its subject during that request. I stated
in the review dossier that this holds "because nothing writes `environments` or `agent_sessions` on
this path -- I checked; I did not prove it". This is the proof, and it is the kind of claim that
should not rest on a reading: the handler's write phase (`_repair_unusable_active_runs`,
`_refresh_expired_agent_live_states`) runs BEFORE the caches are built, but nothing stops a future
edit from moving a write into the per-agent loop, where it would make one agent's answer depend on
whether it was reached before or after the write.

The assertion is deliberately blunt: across the WHOLE request, no INSERT, UPDATE or DELETE touches
either table. That is stronger than "no writes after the cache was built" and much easier to read --
and if a legitimate reason to write them from this path ever appears, a failure here is the right
place to argue about it.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

ENV_ID = "linux:test-host:default"

#: The tables the two request-scoped caches hold. A write to either mid-request is what would make a
#: cached answer disagree with the database it came from.
CACHED_TABLES = ("environments", "agent_sessions")

_WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.I)


class RosterNeverWritesWhatItCachesTests(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    AGENTS = ("cache-agent-a", "cache-agent-b", "cache-agent-c")

    def setUp(self) -> None:
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV_ID, "machineId": "linux:test-host", "os": "linux", "kind": "linux",
            "bridgeId": "bridge-current", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "hermes", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        for agent_id in self.AGENTS:
            response = self.client.post(
                "/api/v1/agents",
                json={
                    "agentId": agent_id, "role": "coder", "runtime": "hermes",
                    "sessionMode": "managed", "machineId": "linux:test-host",
                    "bridgeId": "bridge-current", "capabilities": ["managed-run"],
                    "runtimeConfig": {"gatewayUrl": "ws://127.0.0.1:9119/api/ws?token=t"},
                },
            )
            self.assertEqual(response.status_code, 200, response.text)

    def _statements_during_roster(self) -> list[str]:
        """Every SQL statement the roster request issues, in order."""
        seen: list[str] = []

        async def _spy_wrapper():
            import aiosqlite.core as core
            original = core.Connection.execute

            async def spy(conn_self, sql, *args, **kwargs):
                seen.append(re.sub(r"\s+", " ", str(sql)).strip())
                return await original(conn_self, sql, *args, **kwargs)

            core.Connection.execute = spy
            try:
                response = self.client.get("/api/v1/agents")
                self.assertEqual(response.status_code, 200, response.text)
            finally:
                core.Connection.execute = original

        asyncio.run(_spy_wrapper())
        return seen

    def test_the_spy_sees_the_request(self) -> None:
        """Positive control. Every assertion below is an ABSENCE of writes; an empty statement list
        would satisfy all of them while proving nothing at all."""
        statements = self._statements_during_roster()
        self.assertGreater(len(statements), 10, "the roster issued almost no SQL; the spy is not attached")
        self.assertTrue(
            any(s.startswith("SELECT * FROM agents") for s in statements),
            "the roster never read the agents table, so this is not the request under test",
        )

    def test_the_caches_are_actually_exercised(self) -> None:
        """The second half of the control. If the per-agent gates never ran -- no managed agent, no
        environment binding to resolve -- then nothing would consult the caches and their safety would
        be untested regardless of what the write assertion says."""
        statements = self._statements_during_roster()
        self.assertTrue(
            any("FROM environments" in s for s in statements),
            "the environments lookup never ran, so its cache was never used",
        )
        self.assertTrue(
            any("FROM agent_sessions" in s for s in statements),
            "the session-environment preload never ran, so its cache was never used",
        )

    def test_the_roster_writes_neither_cached_table(self) -> None:
        offenders = [
            s for s in self._statements_during_roster()
            if _WRITE.match(s) and any(re.search(rf"\b{table}\b", s, re.I) for table in CACHED_TABLES)
        ]
        self.assertEqual(
            offenders, [],
            "the roster wrote a table it caches for the duration of the request, so one agent's answer "
            f"can differ from another's depending on when it was reached: {offenders}",
        )

    def test_the_write_detector_can_say_yes(self) -> None:
        """The regex is the instrument, and an instrument that cannot fire is an instrument that always
        reports clean. Fed a write it must recognise it; fed the reads this path really issues, it must
        not."""
        for sql in (
            "UPDATE environments SET status = 'offline' WHERE id = ?",
            "INSERT INTO agent_sessions (id) VALUES (?)",
            "DELETE FROM environments WHERE id = ?",
        ):
            self.assertTrue(
                _WRITE.match(sql) and any(re.search(rf"\b{t}\b", sql, re.I) for t in CACHED_TABLES),
                f"the detector missed a write: {sql}",
            )
        for sql in (
            "SELECT * FROM environments WHERE machine_id = ? ORDER BY last_seen DESC",
            "SELECT agent_id, environment_id FROM agent_sessions WHERE status IN (?)",
            "UPDATE agents SET last_seen = ? WHERE id = ?",
        ):
            self.assertFalse(
                _WRITE.match(sql) and any(re.search(rf"\b{t}\b", sql, re.I) for t in CACHED_TABLES),
                f"the detector flagged something that is not a write to a cached table: {sql}",
            )
