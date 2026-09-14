"""A managed claude past the in-turn ceiling still reads `working` while its screen says it is.

The failure: a claude-code turn opened by the `UserPromptSubmit` hook cannot be renewed, so
`_in_turn_survives` clears it after 30 minutes. The console-working lease was what kept a longer turn
at `working`, and its only writer was the environment bridge deleted in v0.6.2. So a claude working
for 50 minutes read `online` in the dashboard while aify-env showed it busy.

These drive the real ingest (`_append_terminal_output`) and the served status path
(`_compute_live_status_cache` -> `derive`), with synthetic screens only.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import sqlite3

from service.api_core import console_working
from service.api_core.console_working import shows_claude_working
from service.api_core.status_inputs import _compute_live_status_cache
from service.api_core.terminal_output import _append_terminal_output
from service.db import get_db
from service.status_engine import derive
from service.terminal_snapshot import drop_live_screen
from service.tests._base import FastApiTestCase
from service.tests.test_status_engine_integration import _seed_live_channel_worker

#: Every screen opens with an escape: `feed_live_screen` keeps a terminal whose first chunk has none
#: as a plain log and renders nothing, which would make every negative here pass vacuously.
_SGR = chr(27) + "[0m"
_MODE_LINE = "  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt\r\n"

RUNNING = _SGR + "⏺ Reading 3 files\r\n\r\n✻ Actualizing… (49m 37s · ↓ 79.0k tokens)\r\n\r\n> \r\n" + _MODE_LINE
RUNNING_SHORT = _SGR + "· Thinking… (12s · ↑ 340 tokens)\r\n"
RUNNING_STAR = _SGR + "  * Thinking… (1h 2m 3s · ↓ 1.5k tokens)\r\n"
OLD_FOOTER = _SGR + "✻ Crunched for 3m 12s (esc to interrupt · ctrl+t to show todos)\r\n"

FINISHED = _SGR + "⏺ Done.\r\n\r\n✻ Worked for 49m 37s\r\n\r\n> \r\n  ⏵⏵ bypass permissions on (shift+tab to cycle)\r\n"
FINISHED_WITH_SHELL = _SGR + "✻ Churned for 59m 12s · done 1:15 PM · 1 shell still running\r\n" + _MODE_LINE
SUBAGENT_DONE = _SGR + "  ⎿  Done (+3 tool uses · ↓ 12.1k tokens)\r\n"
PROSE = _SGR + "The run took (12s · 3 lines) and nothing else.\r\n" + _MODE_LINE
#: Prose QUOTING a running footer, the whole shape included, on an idle screen. The rule matched it
#: until the footer was anchored to a line opening with a spinner frame.
QUOTED_FOOTER = _SGR + "⏺ The dot read online while the screen showed (49m 37s · ↓ 79.0k tokens).\r\n\r\n> \r\n"


class TheFooterRuleTests(FastApiTestCase):
    DB_NAME = "aify-test-claude-footer-rule.db"

    def test_running_footers_match(self):
        for screen in (RUNNING, RUNNING_SHORT, RUNNING_STAR, OLD_FOOTER):
            with self.subTest(screen=screen[-50:]):
                self.assertTrue(shows_claude_working(screen))

    def test_idle_screens_do_not(self):
        # An idle screen keeps a finished `Worked for` line, completed subagent rows and whatever
        # prose was last printed. None of it may hold the lease.
        for screen in (FINISHED, FINISHED_WITH_SHELL, SUBAGENT_DONE, PROSE, QUOTED_FOOTER):
            with self.subTest(screen=screen[-50:]):
                self.assertFalse(shows_claude_working(screen))


class AClaudePastTheCeilingTests(FastApiTestCase):
    DB_NAME = "aify-test-claude-footer-working.db"
    ENV_ID = "linux:test-host:default"
    MACHINE = "linux:test-host"

    def setUp(self):
        super().setUp()
        console_working._last_checked.clear()
        self.addCleanup(console_working._last_checked.clear)

    def _agent_in_a_long_turn(self, aid: str, runtime: str = "claude-code") -> None:
        """The live shape: a live managed worker whose turn began 45 minutes ago."""
        r = self.client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV_ID, "label": "L", "machineId": self.MACHINE, "os": "linux",
            "kind": "linux", "bridgeId": "env-bridge", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": runtime, "modes": ["managed-warm"],
                          "capabilities": {"nativeResume": True}}], "metadata": {}})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post("/api/v1/agents", json={"agentId": aid, "role": "coder",
            "runtime": runtime, "sessionMode": "managed", "machineId": self.MACHINE,
            "bridgeId": "env-bridge"})
        self.assertEqual(r.status_code, 200, r.text)
        _seed_live_channel_worker(self._db_path, self.ENV_ID, aid, runtime=runtime)
        self.client.post(f"/api/v1/agents/{aid}/status-event", json={"kind": "turn_start", "runId": "r"})
        started = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=45)).isoformat().replace("+00:00", "Z")
        c = sqlite3.connect(str(self._db_path))
        try:
            c.execute("UPDATE agent_status_state SET last_event_at=?, turn_started_at=? WHERE agent_id=?",
                      (started, started, aid))
            c.commit()
        finally:
            c.close()
        self.addCleanup(drop_live_screen, f"term_{aid}")

    def _stream(self, aid: str, chunk: str) -> None:
        async def go():
            db = await get_db()
            try:
                terminal = await (await db.execute(
                    "SELECT id, session_id, agent_id, environment_id, bridge_id, runtime, output, "
                    "status, output_seq, created_at, cols, rows FROM terminal_sessions WHERE id = ?",
                    (f"term_{aid}",))).fetchone()
                await _append_terminal_output(db, terminal, chunk)
                await db.commit()
            finally:
                await db.close()
        asyncio.run(go())

    def _status(self, aid: str) -> str:
        async def go():
            db = await get_db()
            try:
                row = await (await db.execute("SELECT * FROM agents WHERE id=?", (aid,))).fetchone()
                return derive((await _compute_live_status_cache(db, row))["status_inputs"])
            finally:
                await db.close()
        return asyncio.run(go())

    def _lease(self, aid: str):
        c = sqlite3.connect(str(self._db_path))
        try:
            row = c.execute("SELECT working_at FROM agent_console_signal WHERE agent_id=?", (aid,)).fetchone()
            return row[0] if row else None
        finally:
            c.close()

    def test_A_RUNNING_FOOTER_KEEPS_IT_WORKING(self):
        self._agent_in_a_long_turn("fw1")
        self._stream("fw1", RUNNING)
        self.assertEqual(self._status("fw1"), "working",
                         "a claude showing its running footer read as not working past the in-turn ceiling")

    def test_an_idle_screen_does_not(self):
        # CONTROL: the ceiling still clears the turn, so the test above is measuring the footer.
        self._agent_in_a_long_turn("fw2")
        self._stream("fw2", FINISHED)
        self.assertIsNone(self._lease("fw2"))
        self.assertNotEqual(self._status("fw2"), "working")

    def test_a_non_claude_terminal_is_not_rendered(self):
        self._agent_in_a_long_turn("fw3", runtime="codex")
        rendered: list[str] = []
        real = console_working.render_live_screen
        console_working.render_live_screen = lambda tid: rendered.append(tid) or real(tid)
        self.addCleanup(setattr, console_working, "render_live_screen", real)
        self._stream("fw3", RUNNING)
        self.assertEqual(rendered, [])
        self.assertIsNone(self._lease("fw3"))

    def test_a_chunk_stream_renders_once_per_window(self):
        self._agent_in_a_long_turn("fw4")
        rendered: list[str] = []
        real = console_working.render_live_screen
        console_working.render_live_screen = lambda tid: rendered.append(tid) or real(tid)
        self.addCleanup(setattr, console_working, "render_live_screen", real)
        for _ in range(20):
            self._stream("fw4", RUNNING)
        self.assertEqual(rendered, ["term_fw4"])
        self.assertIsNotNone(self._lease("fw4"))

    def test_an_idle_stream_renders_once_per_window_too(self):
        # The throttle must be spent by the render, not by a match. An idle claude is the common case,
        # and a throttle that only armed on a running footer would render its screen on every chunk.
        self._agent_in_a_long_turn("fw5")
        rendered: list[str] = []
        real = console_working.render_live_screen
        console_working.render_live_screen = lambda tid: rendered.append(tid) or real(tid)
        self.addCleanup(setattr, console_working, "render_live_screen", real)
        for _ in range(20):
            self._stream("fw5", FINISHED)
        self.assertEqual(rendered, ["term_fw5"])
        self.assertIsNone(self._lease("fw5"))
