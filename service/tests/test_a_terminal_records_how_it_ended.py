"""A terminal records how its process ended, which nothing did until 2026-08-26.

THE JOIN, across three components. node-pty hands the bridge `{exitCode, signal}`;
`terminal-runtime.js` spreads both into the exit detail on all four exit paths -- the PTY, the
DELEGATED aify-env process, the piped child and a forced stop. `terminal-manager.mjs` then posted
only an output marker and a status, so both numbers died one hop short of this table, and
`TerminalOutputRequest` had nowhere to put them anyway.

WHAT IT COST, in the open. sc-claude and sc-architect died mid-turn on 2026-08-26. The operator asked
why. Every record said `status='stopped'`, an empty `error`, and nothing else: the console tail could
show what the agent had been DOING and nothing whatsoever about the stopping. A terminal that dies
took its reason with it.

NULL IS NOT ZERO, and that distinction is the whole design of these two columns. Zero is a clean exit
and the most common value there is. A column that could not tell "exited cleanly" from "nobody told
me" would answer the question wrongly rather than not at all -- so the migration defaults to NULL,
the model types `exitCode` as an int rather than coercing, the route tests `is not None` rather than
truthiness, and the bridge OMITS the field instead of sending null. Every one of those four is a
place a truthiness test would have destroyed the most common case.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

BRIDGE = "bridge-exit-probe"
ENVIRONMENT = "linux:test-host:default"


class TerminalRecordsHowItEndedTests(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    AGENT = "exit-probe-agent"
    TERMINAL = "term_exit_probe"

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
            "agentId": self.AGENT, "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "machineId": "linux:test-host", "bridgeId": BRIDGE,
        })
        self.assertEqual(registered.status_code, 200, registered.text)
        self._seed_terminal()

    def _seed_terminal(self) -> None:
        import asyncio

        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                # spawn_spec_id / spawn_request_id are named and NULLed deliberately: both are
                # nullable FOREIGN KEYs whose column DEFAULT is '', which no spawn row has, so
                # omitting them fails the constraint with no column named in the error.
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, "
                    "started_at, last_seen, spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"sess-{self.AGENT}", self.AGENT, ENVIRONMENT, "claude-code", "running",
                     "2026-08-26T02:00:00Z", "2026-08-26T02:00:00Z", None, None),
                )
                await db.execute(
                    "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, "
                    "runtime, bridge_id, command, status, output, error, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (self.TERMINAL, self.AGENT, f"sess-{self.AGENT}", ENVIRONMENT, "claude-code",
                     BRIDGE, "claude-aify --aify-agent x", "attached", "", "",
                     "2026-08-26T02:00:00Z", "2026-08-26T02:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _post_exit(self, **body):
        return self.client.post(
            f"/api/v1/terminals/{self.TERMINAL}/output",
            json={"bridgeId": BRIDGE, "output": "\n[terminal exited]\n", "status": "stopped", **body},
        )

    def _row(self) -> dict:
        import asyncio

        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT exit_code, exit_signal, status FROM terminal_sessions WHERE id = ?",
                    (self.TERMINAL,),
                )).fetchone()
                return {k: row[k] for k in ("exit_code", "exit_signal", "status")}
            finally:
                await db.close()

        return asyncio.run(go())

    def test_the_columns_start_as_NULL_rather_than_zero(self) -> None:
        """The control, and the design. A fresh terminal has not exited, and 'has not exited' must not
        read as 'exited cleanly' -- which is exactly what a DEFAULT 0 would have produced for every
        row in the table."""
        row = self._row()
        self.assertIsNone(row["exit_code"], "a terminal that has not exited claims an exit code")
        self.assertIn(row["exit_signal"], (None, ""))

    def test_a_clean_exit_records_zero(self) -> None:
        """The case a truthiness test destroys, end to end. If any hop used `if code:` this records
        NULL and the most common death in the fleet stays unexplained."""
        response = self._post_exit(exitCode=0)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._row()["exit_code"], 0)

    def test_a_failing_exit_records_its_code(self) -> None:
        self.assertEqual(self._post_exit(exitCode=137).status_code, 200)
        self.assertEqual(self._row()["exit_code"], 137)

    def test_a_signal_kill_records_the_signal(self) -> None:
        self.assertEqual(self._post_exit(exitSignal="SIGKILL").status_code, 200)
        row = self._row()
        self.assertEqual(row["exit_signal"], "SIGKILL")
        self.assertIsNone(row["exit_code"], "a signalled death invented an exit code")

    def test_an_older_bridge_that_sends_neither_still_works(self) -> None:
        """The deployment skew this will actually meet: the service updates before every wrapper
        relaunches. A bridge that knows nothing about these fields must keep posting output."""
        response = self._post_exit()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self._row()["exit_code"])

    def test_a_later_output_chunk_cannot_blank_a_recorded_exit(self) -> None:
        """Bytes can still arrive after the exit POST on a busy terminal. The exit is written with
        COALESCE for that reason, and the output queue's UPDATE names only output, seq and status."""
        self.assertEqual(self._post_exit(exitCode=42).status_code, 200)
        later = self.client.post(
            f"/api/v1/terminals/{self.TERMINAL}/output",
            json={"bridgeId": BRIDGE, "output": "trailing bytes\n"},
        )
        self.assertEqual(later.status_code, 200, later.text)
        self.assertEqual(self._row()["exit_code"], 42, "a trailing output chunk erased the exit code")

    def test_a_duplicate_exit_report_does_not_unset_it(self) -> None:
        """A retry or a duplicate flush must not turn a known code back into unknown."""
        self.assertEqual(self._post_exit(exitCode=3).status_code, 200)
        self.assertEqual(self._post_exit().status_code, 200)
        self.assertEqual(self._row()["exit_code"], 3)

    def test_the_console_tail_reports_how_it_ended(self) -> None:
        """The reader. A column nothing serves would be the same defect one layer down -- which is
        the shape this round has already found twice."""
        self.assertEqual(self._post_exit(exitCode=137).status_code, 200)
        body = self.client.get(f"/api/v1/agents/{self.AGENT}/console").json()
        self.assertTrue(body.get("historical"), body.get("message"))
        self.assertEqual(body.get("exitCode"), 137)

    def test_the_console_tail_says_so_when_nothing_was_reported(self) -> None:
        """A terminal that ended without reporting is a real answer and must read as one, not as a
        clean exit and not as silence."""
        self.assertEqual(self._post_exit().status_code, 200)
        body = self.client.get(f"/api/v1/agents/{self.AGENT}/console").json()
        self.assertIsNone(body.get("exitCode"))
        self.assertIn("no exit code was recorded", body.get("message") or body.get("output") or "")


if __name__ == "__main__":
    import unittest

    unittest.main()
