"""The console screen must be kept LIVE, not reconstructed from a truncated byte log.

Operator report: consoles render scrambled, missing half their lines, `____` everywhere,
"old state stuck on screen", and Refresh does not help.

Root cause (measured on the live fleet, not assumed):
  * the stored PTY log is a 64KB TAIL;
  * claude's TUI NEVER clears the screen — zero `ESC[2J` across every live console — it
    paints differentially, one line per frame, homing the cursor between frames;
  * so replaying that tail into a BLANK screen cannot rebuild the screen: rows last painted
    before the window stay blank or hold fragments of other frames. A full 64KB replay
    rebuilt 11 of 30 rows (overlapping garbage); the last frame alone rebuilt ONE row.
  * a repaint nudge does not help either: the SIGWINCH keepalive fires every 4s and claude
    still re-renders only its small footer.

These tests encode that: a truncated replay of a differential painter is broken BY
CONSTRUCTION, and feeding a persistent screen every chunk is correct.
"""

import re
import unittest

from service.terminal_snapshot import (
    _HAVE_PYTE,
    drop_live_screen,
    feed_live_screen,
    render_live_screen,
    render_snapshot,
)

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
COLS, ROWS = 60, 10


def visible(ansi_text: str) -> list[str]:
    return [ln.rstrip() for ln in ANSI.sub("", ansi_text).split("\n")]


def paint(row: int, text: str) -> str:
    """One differential frame, the way claude paints: home, move to a row, write ONE line."""
    return f"\x1b[H\x1b[{row};1H{text}"


def session() -> list[str]:
    """A PTY that painted 6 rows over time and never cleared the screen."""
    return [paint(i, f"line-{i} content") for i in range(1, 7)]


@unittest.skipUnless(_HAVE_PYTE, "pyte not installed")
class LiveTerminalScreenTests(unittest.TestCase):
    def setUp(self):
        drop_live_screen("t1")

    def tearDown(self):
        drop_live_screen("t1")

    def test_truncated_replay_loses_rows_this_is_the_bug(self):
        chunks = session()
        # The operator only ever has a TAIL of the log — earlier frames are trimmed away.
        tail = "".join(chunks[-2:])
        rows = visible(render_snapshot(tail, COLS, ROWS))
        drawn = [r for r in rows if r.strip()]
        self.assertEqual(len(drawn), 2, f"replay of a tail can only rebuild what it contains: {drawn}")
        self.assertNotIn("line-1 content", " ".join(drawn))

    def test_live_screen_keeps_every_row_even_though_the_log_is_trimmed(self):
        for c in session():
            feed_live_screen("t1", c, cols=COLS, rows=ROWS)
        got = render_live_screen("t1")
        self.assertIsNotNone(got)
        drawn = [r for r in visible(got[0]) if r.strip()]
        self.assertEqual(len(drawn), 6, drawn)
        for i in range(1, 7):
            self.assertIn(f"line-{i} content", " ".join(drawn))

    def test_escape_sequence_split_across_chunks_is_not_garbled(self):
        # PTY chunks are arbitrary byte boundaries. A replay-from-offset can cut an escape in
        # half and emit its tail as literal text (the "____ everywhere" class). A persistent
        # pyte Stream is stateful and must reassemble it.
        feed_live_screen("t1", "\x1b[H\x1b[2;1HAB", cols=COLS, rows=ROWS)
        feed_live_screen("t1", "\x1b[3", cols=COLS, rows=ROWS)   # <- split mid-CSI
        feed_live_screen("t1", ";1HCD", cols=COLS, rows=ROWS)
        drawn = [r for r in visible(render_live_screen("t1")[0]) if r.strip()]
        self.assertEqual(drawn, ["AB", "CD"], f"split escape leaked into the screen: {drawn}")

    def test_dismissed_alt_screen_dialog_does_not_stay_baked_in(self):
        # Regression guard for the stuck-compaction-prompt fix (b4403da): pyte has no alt
        # buffer, so a dialog drawn in one would otherwise be painted onto the main screen and
        # survive its own dismissal.
        feed_live_screen("t1", paint(1, "real work"), cols=COLS, rows=ROWS)
        feed_live_screen("t1", "\x1b[?1049h" + paint(3, "TRANSIENT DIALOG"), cols=COLS, rows=ROWS)
        while_up = [r for r in visible(render_live_screen("t1")[0]) if r.strip()]
        self.assertIn("TRANSIENT DIALOG", " ".join(while_up), "a LIVE dialog must be visible")

        feed_live_screen("t1", "\x1b[?1049l", cols=COLS, rows=ROWS)  # dismissed
        after = " ".join(r for r in visible(render_live_screen("t1")[0]) if r.strip())
        self.assertNotIn("TRANSIENT DIALOG", after, "dismissed dialog must not stay on screen")
        self.assertIn("real work", after, "the main screen must be restored intact")

    def test_seed_makes_a_pre_existing_terminal_no_worse_than_today(self):
        # A PTY that was already running when this code shipped has no live screen. We seed it
        # from the stored log (today's behaviour, imperfect) and it then self-heals as new
        # output arrives — it must never be WORSE than the replay it replaces.
        chunks = session()
        feed_live_screen("t1", "", cols=COLS, rows=ROWS, seed="".join(chunks))
        drawn = [r for r in visible(render_live_screen("t1")[0]) if r.strip()]
        self.assertEqual(len(drawn), 6, drawn)

        feed_live_screen("t1", paint(7, "line-7 new"), cols=COLS, rows=ROWS)
        after = " ".join(r for r in visible(render_live_screen("t1")[0]) if r.strip())
        self.assertIn("line-7 new", after)
        self.assertIn("line-1 content", after, "seeded history must survive later feeds")

    def test_unknown_terminal_returns_none_so_callers_fall_back(self):
        self.assertIsNone(render_live_screen("never-seen"))
        self.assertIsNone(render_live_screen(""))

    def test_drop_releases_the_screen(self):
        feed_live_screen("t1", paint(1, "x"), cols=COLS, rows=ROWS)
        self.assertIsNotNone(render_live_screen("t1"))
        drop_live_screen("t1")
        self.assertIsNone(render_live_screen("t1"))


if __name__ == "__main__":
    unittest.main()
