"""A live screen rebuilt from the stored log says so until the program clears the whole screen.

After a service restart the first chunk for an existing terminal creates its screen by replaying the
stored output, which is a 64 KB tail that can start mid-escape. TUIs that redraw with relative cursor
moves and never clear leave that screen overlapping and wrong for as long as they sit idle -- measured
2026-09-16 on three running consoles after a rebuild, with 0, 0 and 1 full clears in their tails. The
screen looked like any other, so every reader trusted it. `reconstructed` is the reader's warning.
"""

import unittest

from service.terminal_snapshot import (
    drop_live_screen,
    feed_live_screen,
    live_screen_reconstructed,
    stored_log_is_partial,
)

TAIL = "184;134;11m2\x1b[39m\x1b[40;1H\x1b[H\r\x1b[34C\x1b[38Bstale fragment"


class ARebuiltConsoleScreenSaysSo(unittest.TestCase):
    def setUp(self):
        self.tid = "rebuilt-screen-test"
        drop_live_screen(self.tid)
        self.addCleanup(drop_live_screen, self.tid)

    def test_a_screen_seeded_from_a_tail_with_no_full_clear_is_reconstructed(self):
        self.assertTrue(feed_live_screen(self.tid, "\x1b[1;1Hnew", cols=80, rows=24, seed=TAIL))
        self.assertIs(live_screen_reconstructed(self.tid), True)

    def test_a_full_clear_on_the_main_screen_makes_it_whole_again(self):
        # A terminal reset (ESC c) counts as a full clear too.
        for clear in ("\x1b[2J\x1b[Hfresh frame", "\x1bcfresh"):
            with self.subTest(clear=repr(clear)):
                drop_live_screen(self.tid)
                feed_live_screen(self.tid, "\x1b[1;1Hnew", cols=80, rows=24, seed=TAIL)
                feed_live_screen(self.tid, clear, cols=80, rows=24)
                self.assertIs(live_screen_reconstructed(self.tid), False)

    def test_a_clear_inside_a_dialog_on_the_alt_screen_does_not(self):
        # The main screen underneath is untouched by it, so it is still the rebuilt one.
        feed_live_screen(self.tid, "\x1b[1;1Hnew", cols=80, rows=24, seed=TAIL)
        feed_live_screen(self.tid, "\x1b[?1049h\x1b[2Jdialog\x1b[?1049l", cols=80, rows=24)
        self.assertIs(live_screen_reconstructed(self.tid), True)

    def test_control_a_screen_fed_from_its_first_byte_is_not_reconstructed(self):
        self.assertTrue(feed_live_screen(self.tid, "\x1b[1;1Hfirst bytes", cols=80, rows=24))
        self.assertIs(live_screen_reconstructed(self.tid), False)

    def test_control_a_seed_that_itself_holds_a_full_clear_is_whole(self):
        # Everything after the last clear is in the seed, so the screen it leaves is exact.
        feed_live_screen(self.tid, "more", cols=80, rows=24, seed=TAIL + "\x1b[2J\x1b[Hwhole frame")
        self.assertIs(live_screen_reconstructed(self.tid), False)

    def test_no_screen_is_unknown_not_whole(self):
        self.assertIsNone(live_screen_reconstructed("never-fed"))

    def test_a_replayed_stored_log_is_partial_unless_it_holds_a_full_clear(self):
        self.assertTrue(stored_log_is_partial(TAIL))
        self.assertFalse(stored_log_is_partial(TAIL + "\x1b[2J\x1b[Hframe"))
        self.assertFalse(stored_log_is_partial(TAIL + "\x1bcframe"))
        # A clear inside a balanced dialog is not the main screen's.
        self.assertTrue(stored_log_is_partial(TAIL + "\x1b[?1049h\x1b[2Jdialog\x1b[?1049l"))


if __name__ == "__main__":
    unittest.main()
