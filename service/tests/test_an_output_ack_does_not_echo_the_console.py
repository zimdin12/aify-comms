"""Posting terminal output is answered with an acknowledgement, not with the console it was added to.

MEASURED 2026-09-17 on the operator's host. Three idle hermes consoles post about 456 frames a minute,
each one redraw of a clock in hermes' own status bar, and each POST cost the service 4.4 ms of CPU --
a third of its idle load. Every answer was 121,591 bytes: the terminal's whole live tail, JSON-escaped
and gzipped per request, for a caller that reads `status` and `outputSeq` and nothing else.

The route's own comment already said the ack "intentionally carries no output buffer". It stopped
being true when the serialiser learned to fill `output` from the in-memory tail even for a SELECT that
leaves the column out, so the narrow SELECT kept its promise and the answer broke it anyway.

The full output is still one GET away, and the last test here holds that door open: an ack that drops
the buffer must not be mistaken for a write that dropped it.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

BRIDGE = "bridge-ack-probe"
ENVIRONMENT = "linux:test-host:default"
TERMINAL = "term_ack_probe"
AGENT = "ack-probe-agent"
#: Big enough that an echoed tail cannot hide in the noise of the other fields.
EARLIER_OUTPUT = "earlier screen contents\n" * 400


class AnOutputAckDoesNotEchoTheConsoleTests(FastApiTestCase):
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
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, "
                    "started_at, last_seen, spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"sess-{AGENT}", AGENT, ENVIRONMENT, "claude-code", "running",
                     "2026-09-17T02:00:00Z", "2026-09-17T02:00:00Z", None, None),
                )
                await db.execute(
                    "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, "
                    "runtime, bridge_id, command, status, output, error, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (TERMINAL, AGENT, f"sess-{AGENT}", ENVIRONMENT, "claude-code", BRIDGE,
                     "claude-aify --aify-agent x", "attached", "", "",
                     "2026-09-17T02:00:00Z", "2026-09-17T02:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _post(self, body: dict) -> dict:
        response = self.client.post(f"/api/v1/terminals/{TERMINAL}/output", json={"bridgeId": BRIDGE, **body})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _write_earlier_output(self) -> None:
        from service import terminal_write_queue

        self._post({"output": EARLIER_OUTPUT, "status": "attached"})
        asyncio.run(terminal_write_queue.flush_terminal_output_writes_for_tests())

    def test_a_byte_frame_is_answered_without_the_console(self) -> None:
        self._write_earlier_output()
        ack = self._post({"output": "x", "status": "attached"})["terminal"]
        self.assertFalse(EARLIER_OUTPUT[:200] in str(ack.get("output", "")),
                         "the ack of a byte frame carried the console it was appended to")
        # What the callers DO read is still there.
        self.assertEqual(ack["status"], "attached")
        self.assertGreater(ack["outputSeq"], 0)

    def test_a_liveness_frame_is_answered_without_the_console(self) -> None:
        # The other return path: no bytes and no status, the host saying "still mine".
        self._write_earlier_output()
        ack = self._post({"output": ""})["terminal"]
        self.assertFalse(EARLIER_OUTPUT[:200] in str(ack.get("output", "")),
                         "the ack of a liveness frame carried the console")
        self.assertEqual(ack["status"], "attached")

    def test_the_console_is_still_readable_where_it_is_read(self) -> None:
        # Positive control: the output the acks no longer echo was written, and GET still has it.
        self._write_earlier_output()
        terminal = self.client.get(f"/api/v1/terminals/{TERMINAL}").json()["terminal"]
        self.assertIn(EARLIER_OUTPUT[:200], terminal["output"])


if __name__ == "__main__":
    unittest.main()
