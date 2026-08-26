"""One test's terminal output must not arrive in the next test's database.

THE THIRD PROCESS-GLOBAL. `_base.setUp` already clears two of them -- the derived live-status cache
and the settings cache -- each with a comment saying why: production has one process and one
database, and this suite gives every test a fresh database while keeping the one process. The
terminal output write queue is the third, and until 2026-08-26 nothing reset it.

WHAT IT DOES. `TERMINAL_OUTPUT_WRITES` batches row writes: a POST appends to a per-terminal deque
and schedules the flush through `call_later`, which has not run when the request returns. The
database that request wrote against is deleted at the end of the test. The deque is not, and neither
is `_seq_floor` -- so the next test's first POST for the same terminal id flushes the PREVIOUS
test's bytes into the NEW file, at a sequence eleven ahead of anything it wrote.

MEASURED, not suspected: a terminal seeded with `output=''` in setUp read back nine
`[terminal exited]` markers and `outputSeq: 11`. Both numbers came from other tests in the same file.

THE DANGER IS THE DIRECTION NOBODY CHECKS. Nothing was failing when this was found; the suite was
green, and a test whose own bytes go missing fails loudly anyway. The silent case is the opposite: a
test is HANDED output it never produced and concludes the write path worked. This file is two tests
that share one terminal id on purpose, because a single test cannot observe a leak between tests.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

BRIDGE = "bridge-leak-probe"
ENVIRONMENT = "linux:test-host:default"
#: ONE id shared by both tests below. That is the whole experiment: the queue is keyed by terminal
#: id, so two tests using different ids could never see each other no matter how leaky it was.
TERMINAL = "term_leak_probe"
AGENT = "leak-probe-agent"

FIRST_WRITE = "bytes from the first test\n"


class TerminalOutputDoesNotCrossTestsTests(FastApiTestCase):
    """Two tests, one terminal id, and the second must see none of the first's bytes.

    Ordered by name: unittest runs methods alphabetically, so `test_1_...` writes and
    `test_2_...` checks. Spelled with digits rather than trusting a sentence to sort.
    """

    def setUp(self) -> None:
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENVIRONMENT, "machineId": "linux:test-host", "os": "linux", "kind": "linux",
            "bridgeId": BRIDGE, "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        registered = self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "machineId": "linux:test-host", "bridgeId": BRIDGE,
        })
        self.assertEqual(registered.status_code, 200, registered.text)
        self._seed_terminal()

    def _seed_terminal(self) -> None:
        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                # Both FK columns are named and NULLed: their column DEFAULT is '', which no spawn
                # row has, so omitting them fails the constraint without naming a column.
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, "
                    "started_at, last_seen, spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"sess-{AGENT}", AGENT, ENVIRONMENT, "claude-code", "running",
                     "2026-08-26T02:00:00Z", "2026-08-26T02:00:00Z", None, None),
                )
                await db.execute(
                    "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, "
                    "runtime, bridge_id, command, status, output, error, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (TERMINAL, AGENT, f"sess-{AGENT}", ENVIRONMENT, "claude-code", BRIDGE,
                     "claude-aify --aify-agent x", "attached", "", "",
                     "2026-08-26T02:00:00Z", "2026-08-26T02:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _terminal(self) -> dict:
        response = self.client.get(f"/api/v1/terminals/{TERMINAL}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["terminal"]

    def test_1_a_test_writes_output_and_leaves_the_flush_pending(self) -> None:
        """The producer, and its own positive control.

        The status is deliberately `stopped`: that is the branch that schedules an immediate flush
        through `call_later`, which is precisely the flush that does not run before this test's
        database is deleted. A weaker status would leave the bytes on the idle timer and prove less.
        """
        response = self.client.post(f"/api/v1/terminals/{TERMINAL}/output", json={
            "bridgeId": BRIDGE, "output": FIRST_WRITE, "status": "stopped",
        })
        self.assertEqual(response.status_code, 200, response.text)
        # The write was accepted. Whether it has reached the row yet is exactly what is undefined
        # here, so this test asserts the acceptance and nothing about the row.
        self.assertTrue(response.json().get("ok", True), response.text)

    def test_2_the_next_test_sees_a_terminal_with_nothing_in_it(self) -> None:
        """The consumer. A fresh database, a terminal seeded with an empty output column.

        Without the reset in `_base.setUp` this reads the first test's bytes and a sequence ahead of
        anything written here -- output this test never produced, in a database it created itself.
        """
        terminal = self._terminal()
        self.assertEqual(
            terminal["output"], "",
            "this test's terminal already holds output. It was seeded empty and this test has "
            "written nothing, so these bytes came from another test through the process-global "
            f"write queue: {terminal['output']!r}",
        )
        self.assertNotIn(FIRST_WRITE, terminal["output"])
        self.assertEqual(
            terminal["outputSeq"], 0,
            "the sequence floor carried over from another test's terminal of the same id, so this "
            "terminal's first frame would claim a sequence ahead of anything ever written to it",
        )

    def test_3_the_seeded_row_is_readable_at_all(self) -> None:
        """Anti-vacuity. If the fixture never created the terminal, test 2 would pass by 404 --
        an empty answer that proves nothing, which is the failure mode this whole file is about."""
        terminal = self._terminal()
        self.assertEqual(terminal["id"], TERMINAL)
        self.assertEqual(terminal["agentId"], AGENT)
        # And the instrument can see output when there IS some: write, drain, read it back.
        self.client.post(f"/api/v1/terminals/{TERMINAL}/output", json={
            "bridgeId": BRIDGE, "output": "visible bytes\n", "status": "attached",
        })
        from service import terminal_write_queue
        asyncio.run(terminal_write_queue.flush_terminal_output_writes_for_tests())
        self.assertIn("visible bytes", self._terminal()["output"])


if __name__ == "__main__":
    unittest.main()
