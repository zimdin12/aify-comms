"""The status path rendered a 64 KB log to find a prompt that was already on a rendered screen.

`_terminal_prompt_hint_from_raw` does three things: pre-gate, render raw -> screen, read the hint off
the screen. `terminal_snapshot` is already holding that screen in memory for every terminal this
process is tracking, fed by the same output the stored tail is written from. So the two status
readers were paying for a pyte reconstruction, every status refresh, to arrive at something they
could have been handed.

THE FALLBACK IS NOT OPTIONAL. `_LIVE_SCREENS` is a process global: it is empty for every terminal
this process has not seen since it started, which is exactly the window after a restart. Dropping the
stored tail would make status WRONG there rather than merely slower, so the tail remains the answer
whenever the screen is absent -- and that is the case these tests pin hardest.

WHY THE CALL SITES AND NOT THE HELPER. A helper proven in isolation leaves the call to it unproven,
and this repo has shipped a feature whose six helper tests were green while the call site was
disconnected. Each test below drives `_agent_awaiting_input`, the real reader, and distinguishes the
two sources by making them DISAGREE: a prompt on the screen and none in the tail, then the reverse.
Agreeing fixtures would pass whichever source was read.
"""

from __future__ import annotations

import unittest

import aiosqlite

from service.api_core.liveness import _agent_awaiting_input
from service.api_core.terminal_text import (
    _terminal_prompt_hint_from_raw,
    _terminal_prompt_hint_from_screen,
)
from service.terminal_snapshot import drop_live_screen, feed_live_screen

#: Text the hint recognises, and text it must not. Both are checked directly in the first test, so a
#: change to the marker set fails there rather than silently making every later test vacuous.
PROMPT = "Do you want to proceed? (y/n)"
ORDINARY = "compiling the workspace, 42 files done"

#: THE SAME TEXT, CARRYING AN ESCAPE. `feed_live_screen` refuses to create a screen for output with
#: no ANSI in it at all -- deliberately: "plain logs must remain byte-for-byte logs, not terminal
#: screen state", because a screen wraps long lines and caps history. A screen exists only once a
#: runtime starts painting. Feeding plain text here would silently create nothing, every call would
#: fall through to the tail, and the tests would pass while proving the opposite of their names.
#: The practical consequence is worth stating: the live-screen path applies to TUI runtimes, which
#: is exactly where the large tails come from.
SGR = "\x1b[0m"
PROMPT_PAINTED = SGR + PROMPT
ORDINARY_PAINTED = SGR + ORDINARY


class TheHelpersAgreeTests(unittest.TestCase):
    def test_the_fixtures_are_what_this_file_assumes(self):
        """CONTROL. Every test below distinguishes two sources by which text they hold; if the
        recogniser stopped seeing PROMPT, they would all pass while proving nothing."""
        self.assertTrue(_terminal_prompt_hint_from_screen("c1", PROMPT))
        self.assertFalse(_terminal_prompt_hint_from_screen("c2", ORDINARY))

    def test_the_screen_path_and_the_raw_path_give_the_same_answer(self):
        """They must, or moving the readers changes status rather than speeding it up. Plain text is
        its own rendering, so the two are comparable here without a PTY."""
        for text in (PROMPT, ORDINARY, ""):
            self.assertEqual(
                bool(_terminal_prompt_hint_from_screen(f"s:{text}", text)),
                bool(_terminal_prompt_hint_from_raw(f"r:{text}", text, 100)),
                f"the two paths disagree on {text!r}",
            )

    def test_the_screen_path_has_its_own_cache_namespace(self):
        """Both variants share one cache dict. One key for both would store a digest of the raw log
        where the screen digest is looked up -- never a hit, so the cache would silently stop working
        rather than answer wrongly. Asserted by giving the SAME key different text in each."""
        self.assertTrue(_terminal_prompt_hint_from_screen("shared", PROMPT))
        self.assertFalse(_terminal_prompt_hint_from_raw("shared", ORDINARY, 100))
        self.assertTrue(_terminal_prompt_hint_from_screen("shared", PROMPT))


class TheReaderPrefersTheLiveScreenTests(unittest.IsolatedAsyncioTestCase):
    TERMINAL = "term_status_probe"

    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.execute(
            """
            CREATE TABLE terminal_sessions (
              id TEXT PRIMARY KEY, agent_id TEXT, status TEXT, output TEXT,
              cols INTEGER, runtime TEXT, updated_at TEXT
            )
            """
        )
        await self.db.commit()
        drop_live_screen(self.TERMINAL)

    async def asyncTearDown(self):
        drop_live_screen(self.TERMINAL)
        await self.db.close()

    async def _row(self, tail: str):
        await self.db.execute(
            "INSERT OR REPLACE INTO terminal_sessions"
            " (id, agent_id, status, output, cols, runtime, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (self.TERMINAL, "probe", "running", tail, 100, "claude-code", "2026-09-02T00:00:00Z"),
        )
        await self.db.commit()

    async def test_the_screen_is_read_when_the_tail_disagrees(self):
        """THE POINT. A prompt on the live screen and none in the stored tail: answering True can
        only have come from the screen."""
        await self._row(ORDINARY)
        self.assertTrue(feed_live_screen(self.TERMINAL, PROMPT_PAINTED, cols=100, rows=40),
                        "no live screen was created, so this test would pass via the tail")
        self.assertTrue(await _agent_awaiting_input(self.db, "probe"))

    async def test_the_screen_wins_the_other_way_too(self):
        """The reverse, so the first test cannot pass by the reader simply answering True. An idle
        screen beside a tail full of an OLD prompt must read as not-awaiting -- which is the stale
        answer the stored tail keeps giving after the prompt is gone."""
        await self._row(PROMPT)
        self.assertTrue(feed_live_screen(self.TERMINAL, ORDINARY_PAINTED, cols=100, rows=40),
                        "no live screen was created, so this test would pass via the tail")
        self.assertFalse(await _agent_awaiting_input(self.db, "probe"))

    async def test_with_no_live_screen_it_falls_back_to_the_tail(self):
        """THE RESTART WINDOW, and the reason the tail is still written. `_LIVE_SCREENS` is a process
        global: after a restart it is empty for every terminal, and status must stay correct rather
        than merely fast."""
        await self._row(PROMPT)
        self.assertTrue(await _agent_awaiting_input(self.db, "probe"))

    async def test_the_fallback_still_answers_no_when_it_should(self):
        """NEGATIVE CONTROL on the fallback: a reader that answered True whenever it reached the tail
        would pass the test above while being useless."""
        await self._row(ORDINARY)
        self.assertFalse(await _agent_awaiting_input(self.db, "probe"))

    async def test_a_runtime_that_is_not_claude_code_is_still_refused(self):
        """Unchanged by this work and worth pinning while the reader is being edited: hermes, codex
        and pi emit the model's own prose, where "which option" is ordinary output rather than proof
        the harness is waiting. Their controllers report turn state natively."""
        await self.db.execute(
            "INSERT OR REPLACE INTO terminal_sessions"
            " (id, agent_id, status, output, cols, runtime, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (self.TERMINAL, "probe", "running", PROMPT, 100, "hermes", "2026-09-02T00:00:00Z"),
        )
        await self.db.commit()
        self.assertTrue(feed_live_screen(self.TERMINAL, PROMPT_PAINTED, cols=100, rows=40),
                        "no live screen was created, so this test would pass via the tail")
        self.assertFalse(await _agent_awaiting_input(self.db, "probe"))


if __name__ == "__main__":
    unittest.main()
