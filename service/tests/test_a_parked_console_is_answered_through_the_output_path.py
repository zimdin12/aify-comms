"""The real dev-channels dialog, posted as terminal output, gets its Enter queued by the service.

`test_the_service_answers_a_parked_console.py` pins the RULES against the captured bytes. Nothing ran
the path production takes: output POST, write-queue flush, live screen, prompt check, control. That
gap mattered on 2026-09-18, when the prompt check stopped building an ANSI render (a quarter of the
service's CPU) and read the screen's plain text instead -- blanking that input left every rule test
green. This test goes red on it.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.api_core.console_prompts import forget_terminal
from service.terminal_snapshot import _HAVE_PYTE
from service.tests._base import FastApiTestCase

REPO = Path(__file__).resolve().parents[2]
RAW_CAPTURE = REPO / "service" / "tests" / "data" / "claude-dev-channels-prompt.raw.txt"
BRIDGE = "bridge-prompt-probe"
ENVIRONMENT = "linux:test-host:default"
TERMINAL = "term_prompt_path_probe"
AGENT = "prompt-path-agent"


class AParkedConsoleIsAnsweredThroughTheOutputPath(FastApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        if not _HAVE_PYTE:
            self.skipTest("pyte is not installed, so this service keeps no live screen")
        forget_terminal(TERMINAL)
        self.assertEqual(self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENVIRONMENT, "machineId": "linux:test-host", "os": "linux", "kind": "linux",
            "bridgeId": BRIDGE, "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        }).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "machineId": "linux:test-host", "bridgeId": BRIDGE,
        }).status_code, 200)
        from service.db import get_db

        async def seed():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, started_at,"
                    " last_seen, spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"sess-{AGENT}", AGENT, ENVIRONMENT, "claude-code", "running",
                     "2026-09-18T02:00:00Z", "2026-09-18T02:00:00Z", None, None))
                await db.execute(
                    "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, runtime, bridge_id,"
                    " command, status, output, error, cols, rows, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (TERMINAL, AGENT, f"sess-{AGENT}", ENVIRONMENT, "claude-code", BRIDGE,
                     "claude-aify --aify-agent x", "attached", "", "", 120, 30,
                     "2026-09-18T02:00:00Z", "2026-09-18T02:00:00Z"))
                await db.commit()
            finally:
                await db.close()

        asyncio.run(seed())

    def tearDown(self) -> None:
        forget_terminal(TERMINAL)
        super().tearDown()

    def _post_and_flush(self, output: str) -> None:
        from service import terminal_write_queue

        response = self.client.post(f"/api/v1/terminals/{TERMINAL}/output",
                                    json={"bridgeId": BRIDGE, "output": output, "status": "attached"})
        self.assertEqual(response.status_code, 200, response.text)
        asyncio.run(terminal_write_queue.flush_terminal_output_writes_for_tests())

    def _prompt_controls(self) -> list:
        from service.db import get_db

        async def read():
            db = await get_db()
            try:
                return await (await db.execute(
                    "SELECT body FROM terminal_controls WHERE terminal_id = ? AND requested_by = 'console-prompt'",
                    (TERMINAL,))).fetchall()
            finally:
                await db.close()

        return asyncio.run(read())

    def test_the_captured_dialog_gets_its_enter_queued(self) -> None:
        self._post_and_flush(RAW_CAPTURE.read_text(encoding="utf-8"))
        self.assertEqual(len(self._prompt_controls()), 1,
                         "the dialog that parks every fresh worker got no answer through the output path")

    def test_CONTROL_ordinary_output_is_not_answered(self) -> None:
        self._post_and_flush("\x1b[2Jboot output naming --dangerously-load-development-channels\r\n")
        self.assertEqual(self._prompt_controls(), [])


if __name__ == "__main__":
    unittest.main()
