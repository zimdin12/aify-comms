"""A live screen is rendered once per change, and never served stale.

MEASURED 2026-09-18: the service sat at 119% CPU with 67% of its samples inside `_LiveScreen.render`.
The prompt check and the console-working check run on every output chunk, and every status refresh
renders each agent's screen again, so one chatty idle console cost several full renders of its screen
and history per second, and a keystroke typed into a dashboard console waited up to 3 s behind them.
The render is now reused until the screen changes. A cache like that has one way to be wrong -- an
old screen served after new output -- so both sides are pinned here.
"""

from __future__ import annotations

import unittest

from service import terminal_snapshot
from service.terminal_snapshot import feed_live_screen, render_live_screen

TERMINAL = "term_render_cache_probe"
ESC = chr(27)


class TheLiveScreenRendersOncePerChange(unittest.TestCase):
    def setUp(self) -> None:
        terminal_snapshot._LIVE_SCREENS.pop(TERMINAL, None)
        self.assertTrue(feed_live_screen(TERMINAL, f"{ESC}[2Jfirst line", cols=80, rows=10, seq=1))

    def tearDown(self) -> None:
        terminal_snapshot._LIVE_SCREENS.pop(TERMINAL, None)

    def test_an_unchanged_screen_is_not_rendered_again(self) -> None:
        first = render_live_screen(TERMINAL)[0]
        second = render_live_screen(TERMINAL)[0]
        self.assertIs(second, first, "an unchanged screen was rendered a second time")

    def test_new_output_is_never_served_from_the_old_render(self) -> None:
        before = render_live_screen(TERMINAL)[0]
        feed_live_screen(TERMINAL, "\r\nsecond line", cols=80, rows=10, seq=2)
        after = render_live_screen(TERMINAL)[0]
        self.assertIn("second line", after)
        self.assertNotIn("second line", before)

    def test_an_unnumbered_chunk_still_moves_the_render(self) -> None:
        # `seq` can be None (a chunk nobody numbered), so the cache must not key on it.
        render_live_screen(TERMINAL)
        feed_live_screen(TERMINAL, "\r\nunnumbered", cols=80, rows=10, seq=None)
        self.assertIn("unnumbered", render_live_screen(TERMINAL)[0])

    def test_a_resize_is_never_served_from_the_old_render(self) -> None:
        feed_live_screen(TERMINAL, "\r\n" + "w" * 60, cols=80, rows=10, seq=2)
        self.assertIn("w" * 60, render_live_screen(TERMINAL)[0])
        feed_live_screen(TERMINAL, "", cols=40, rows=10, seq=2)
        self.assertNotIn("w" * 60, render_live_screen(TERMINAL)[0],
                         "a narrower screen was answered with the render from before the resize")


if __name__ == "__main__":
    unittest.main()
