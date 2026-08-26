"""A keyboard-protocol sequence must not underline every row of the console.

WHAT THE OPERATOR SAW. A full-width rule under every line of a live agent console, blank lines
included. Reported with a screenshot on 2026-08-26.

WHAT IT ACTUALLY IS. `CSI > 4 ; 2 m` is XTMODKEYS -- "set modifyOtherKeys level 2" -- which Claude
Code emits when it turns on its keyboard protocol, in the same burst as the Kitty `CSI < u` and
`CSI > 1 u` sequences. The leading `>` makes the CSI PRIVATE: it says nothing about how text should
look. pyte drops the prefix and dispatches it as SGR 4;2, so underline goes on and never comes off,
and every character written afterwards carries it. `_cell_sgr` then faithfully emits `0;4;39;49` for
each of them, and xterm draws exactly what it was told.

THE MEASUREMENT, taken off the live console and reproduced offline from the captured bytes before a
line of the fix was written:

    raw stream                          70,871 chars
    real SGR sequences in it                     0
    underline runs in the snapshot              45
    cells with underscore=True           5,722 of 5,722
    screen.default_char.underscore           False

A stream with no attribute sequences at all produced a fully underlined screen. That is not the
agent's output being rendered; it is our emulator being told something the agent never said.

THE FIXTURE IS THE REAL CAPTURE, sliced around the actual sequence, not a hand-written string. A
hand-written one would only prove that the regex matches the regex's author.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.terminal_snapshot import (  # noqa: E402
    _HAVE_PYTE,
    _LiveScreen,
    render_snapshot,
    strip_private_sgr,
)

ESC = chr(27)
CAPTURE = Path(__file__).resolve().parent / "data" / "console_keyboard_protocol_capture.txt"


def _underline_runs(ansi: str) -> int:
    return sum(
        1 for params in re.findall(ESC + r"\[([0-9;]*)m", ansi)
        if "4" in params.split(";")
    )


class PrivateSgrIsNotATextAttributeTests(unittest.TestCase):
    def test_the_private_form_is_removed_and_the_real_one_is_not(self):
        """Both directions in one test, because a sanitiser that ate real attributes would strip
        every colour in the fleet -- a worse bug than the one being fixed."""
        self.assertEqual(strip_private_sgr(f"a{ESC}[>4;2mb"), "ab")
        self.assertEqual(strip_private_sgr(f"a{ESC}[<1mb"), "ab")
        self.assertEqual(strip_private_sgr(f"a{ESC}[=7mb"), "ab")
        self.assertEqual(strip_private_sgr(f"a{ESC}[4mb"), f"a{ESC}[4mb", "a REAL underline was eaten")
        self.assertEqual(strip_private_sgr(f"a{ESC}[0;1;31mb"), f"a{ESC}[0;1;31mb", "colour was eaten")

    def test_a_private_mode_that_is_not_an_SGR_is_untouched(self):
        """`?25l` hides the cursor and `?1049h` switches screens. Both are private CSIs and neither
        ends in `m`, so widening the pattern to all private CSIs would break cursor and alt-screen
        handling that other tests in this suite depend on."""
        for keep in (f"{ESC}[?25l", f"{ESC}[?1049h", f"{ESC}[?2026h", f"{ESC}[<u", f"{ESC}[>1u"):
            self.assertEqual(strip_private_sgr(f"x{keep}y"), f"x{keep}y", keep)


@unittest.skipUnless(_HAVE_PYTE, "pyte is not installed; the snapshot path degrades to the raw log")
class TheCapturedConsoleRendersWithoutUnderlineTests(unittest.TestCase):
    """Against the real bytes. `_HAVE_PYTE` is asserted rather than assumed: without pyte
    `render_snapshot` returns the raw log and every assertion below would pass vacuously."""

    def setUp(self):
        self.capture = CAPTURE.read_text(encoding="utf-8", errors="surrogatepass")

    def test_the_capture_still_contains_the_sequence_this_is_about(self):
        """Anti-vacuity. A fixture that lost the sequence would make the whole file green and empty."""
        self.assertIn(f"{ESC}[>4;2m", self.capture, "the capture no longer holds XTMODKEYS")
        self.assertGreater(len(self.capture), 1000)

    def test_the_snapshot_of_the_captured_console_has_no_underline(self):
        snapshot = render_snapshot(self.capture, 180, 40)
        self.assertEqual(
            _underline_runs(snapshot), 0,
            "the console is still underlining every row: a keyboard-protocol sequence is being "
            "rendered as a text attribute",
        )

    def test_the_snapshot_still_paints_the_console(self):
        """The control for the test above: deleting everything would also produce zero underlines."""
        snapshot = render_snapshot(self.capture, 180, 40)
        self.assertIn("Ran 2 shell commands", snapshot, "the snapshot lost the text it should paint")

    def test_a_REAL_underline_survives_the_snapshot(self):
        """The other control, and the one that matters most. If the fix reached too far, an agent that
        genuinely underlines something would lose it and nobody would notice for months."""
        snapshot = render_snapshot(f"{ESC}[4munderlined text{ESC}[0m", 80, 5)
        self.assertGreater(_underline_runs(snapshot), 0, "a real underline was stripped")


@unittest.skipUnless(_HAVE_PYTE, "pyte is not installed")
class TheLiveScreenHandlesASplitSequenceTests(unittest.TestCase):
    """A live PTY arrives in chunks, and a chunk boundary can fall inside the sequence.

    This is not hypothetical bookkeeping: the live screen is what a watched console renders from, so
    a split that slipped through would leave the underline stuck for the life of that screen -- the
    bug being fixed, arriving by the back door. The bytes are fed one character at a time, which is
    the worst case and covers every boundary at once.
    """

    def test_feeding_the_sequence_one_byte_at_a_time_still_removes_it(self):
        live = _LiveScreen(80, 6)
        for char in f"{ESC}[>4;2mhello":
            live.feed(char)
        self.assertEqual(
            _underline_runs(live.render()), 0,
            "split across chunk boundaries, the keyboard sequence was fed to the emulator broken and "
            "put the underline back on",
        )
        self.assertIn("hello", live.render(), "the text after the sequence was swallowed")

    def test_a_real_underline_split_across_chunks_still_underlines(self):
        live = _LiveScreen(80, 6)
        for char in f"{ESC}[4mhi":
            live.feed(char)
        self.assertGreater(_underline_runs(live.render()), 0, "a real underline was lost")

    def test_a_chunk_ending_in_a_partial_private_csi_does_not_swallow_later_text(self):
        """The held-back bytes must be released, not dropped. Holding forever would be a new way to
        lose output, which is worse than an underline."""
        live = _LiveScreen(80, 6)
        live.feed(f"before{ESC}[>4")
        live.feed(";2mafter")
        rendered = live.render()
        self.assertIn("before", rendered)
        self.assertIn("after", rendered)
        self.assertEqual(_underline_runs(rendered), 0)


if __name__ == "__main__":
    unittest.main()
