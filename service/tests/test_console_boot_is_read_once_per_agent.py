"""One status computation asks whether an agent's console is booting ONCE, not twice.

`_compute_live_status_cache` asked the same question about the same agent twice in the same request:
once inside `_decide_effective_status` (the authoritative branch) and once directly, for the WS-12
display-parity line further down. Same agent, same connection, same request, two reads of

    SELECT created_at FROM terminal_sessions WHERE agent_id = ? AND status IN (...)

MEASURED 2026-08-28, counting `aiosqlite` execute() calls through one COLD `GET /api/v1/agents`,
a fresh database per size:

    agents in fleet             4     12
    agents refreshed            4      8      (the refresh cap is 8)
    console-boot reads   before 8     16      = 2 per refreshed agent
                          after 4      8      = 1
    all statements       before 48    84
                          after 44    76

Eight of the nine per-agent queries already ran once. This was the ninth, and it ran twice.

WHY A LAZY MEMO RATHER THAN HOISTING. `_decide_effective_status` documents the trade: hoisting the
read out of its late branch "would also add a database query to EVERY status computation on a hot
path", because both call sites are guarded and often neither fires. `ConsoleBootingOnce` keeps both
guards exactly as they are and only prevents the SECOND computation, so an agent reaching neither
branch still pays nothing. Its scope is one agent and one computation, so it cannot go stale.

HOW THE FIRST ATTEMPT FAILED, because it is why this test counts objects and not only rows: the
parameter was added to the callee, the object was created in the caller, and it was never PASSED.
Both ends read correctly in isolation and the query count did not move at all. What found it was
counting instances against calls -- 4 objects for 4 calls means every call site built its own.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

BOOT_READ = "SELECT created_at FROM terminal_sessions"

#: More than one, so a per-agent read is unmistakable against a per-request one.
AGENTS = 4


class ConsoleBootIsReadOncePerAgentTests(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    def setUp(self) -> None:
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": "linux:test-host:default", "machineId": "linux:test-host", "os": "linux",
            "kind": "linux", "bridgeId": "bridge-a", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        for n in range(AGENTS):
            response = self.client.post("/api/v1/agents", json={
                "agentId": f"boot-agent-{n:03d}", "role": "coder", "runtime": "claude-code",
                "sessionMode": "managed", "machineId": "linux:test-host",
                "bridgeId": "bridge-a", "capabilities": ["managed-run"],
            })
            self.assertEqual(response.status_code, 200, response.text)

    def _statements(self) -> list[str]:
        """Every SQL statement one COLD `GET /api/v1/agents` issues.

        Cold because a warm read is served from `_LIVE_STATE_CACHE` and never reaches the
        derivation; measuring through the cache would report a saving the cache had already made.
        """
        from service.reconcilers import status_cache
        status_cache._LIVE_STATE_CACHE.clear()

        import aiosqlite.core as core
        seen: list[str] = []
        original = core.Connection.execute

        async def spy(conn_self, sql, *args, **kwargs):
            seen.append(re.sub(r"\s+", " ", str(sql)).strip())
            return await original(conn_self, sql, *args, **kwargs)

        core.Connection.execute = spy
        try:
            response = self.client.get("/api/v1/agents")
        finally:
            core.Connection.execute = original
        self.assertEqual(response.status_code, 200, response.text)
        return seen

    def test_the_request_actually_did_the_work(self) -> None:
        """Positive control. The assertion below is a CEILING, and a request that computed nothing
        sits under it while proving nothing at all."""
        statements = self._statements()
        self.assertGreater(len(statements), 20, f"only {len(statements)} statements; not a full request")
        self.assertTrue(
            any(s.startswith(BOOT_READ) for s in statements),
            "the request never read a console's start time, so it is not the path under test",
        )

    def test_the_console_start_is_read_at_most_once_per_agent(self) -> None:
        reads = [s for s in self._statements() if s.startswith(BOOT_READ)]
        self.assertLessEqual(
            len(reads), AGENTS,
            f"one cold /agents request read a console's start time {len(reads)} times for "
            f"{AGENTS} agents; the derivation is asking the same question twice",
        )

    def test_each_shared_reader_is_created_once_and_answered_twice(self) -> None:
        """The disconnected-call-site check.

        Counting rows alone cannot tell "the memo works" from "one branch stopped firing". Counting
        INSTANCES against CALLS can: two answers off one object is sharing, two answers off two
        objects is the bug this shipped with for one iteration.
        """
        from service.api_core import managed_env
        from service.reconcilers import status_cache
        status_cache._LIVE_STATE_CACHE.clear()

        created: list[str] = []
        answered: list[object] = []
        original_init = managed_env.ConsoleBootingOnce.__init__
        original_value = managed_env.ConsoleBootingOnce.value

        def counting_init(zelf, db, agent_id):
            created.append(agent_id)
            original_init(zelf, db, agent_id)

        async def counting_value(zelf):
            answered.append(zelf)
            return await original_value(zelf)

        managed_env.ConsoleBootingOnce.__init__ = counting_init
        managed_env.ConsoleBootingOnce.value = counting_value
        try:
            response = self.client.get("/api/v1/agents")
        finally:
            managed_env.ConsoleBootingOnce.__init__ = original_init
            managed_env.ConsoleBootingOnce.value = original_value
        self.assertEqual(response.status_code, 200, response.text)

        self.assertGreater(len(answered), 0, "no agent reached either console-boot branch")
        self.assertEqual(
            len(created), len({id(a) for a in answered}),
            "a call site built its own reader instead of sharing the caller's -- the object was "
            "created and the parameter declared, but never passed",
        )
        self.assertLessEqual(
            len(created), AGENTS,
            f"{len(created)} readers for {AGENTS} agents; one per agent per computation is the budget",
        )

    def test_the_shared_reader_answers_what_a_direct_read_would(self) -> None:
        """Equivalence. An optimisation that changes an answer is a defect, not a saving."""
        import asyncio

        from service.api_core.managed_env import ConsoleBootingOnce, _managed_console_is_booting
        from service.db import get_db

        async def compare():
            db = await get_db()
            try:
                agent_id = "boot-agent-000"
                direct = await _managed_console_is_booting(db, agent_id)
                shared = ConsoleBootingOnce(db, agent_id)
                first = await shared.value()
                second = await shared.value()
                return direct, first, second
            finally:
                await db.close()

        direct, first, second = asyncio.run(compare())
        self.assertEqual(first, direct, "the shared reader disagreed with a direct read")
        self.assertEqual(second, first, "the second answer differed from the first")
        self.assertIsInstance(first, bool, "a non-bool answer would defeat the memo's own None check")
