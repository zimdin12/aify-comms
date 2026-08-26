"""A terminal-capability sequence must not become screen content.

TWO DEFECTS, ONE CLASS, and the second was found by walking the fleet's real streams after the
first was fixed: 304,604 characters across seven live terminals, every private CSI they contain,
and what pyte does with each.

    407x  CSI > 4;2 m   XTMODKEYS (modifyOtherKeys)   -> dispatched as SGR 4: UNDERLINE ON, forever
    406x  CSI < u       Kitty keyboard, pop flags     -> PRINTS A LITERAL `u` into the screen
    406x  CSI > 1 u     Kitty keyboard, push flags    -> inert
      1x  CSI > 0 q     cursor-style query            -> inert

The `u` one is the quiet half: `A` + `CSI < u` + `B` renders `AuB`, so four hundred stray characters
were being injected into rendered consoles at whatever column the cursor was in. It lands on the
HERMES agents -- 120, 109 and 177 occurrences in three live streams -- which is why the operator's
claude screenshot did not show it.

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

    def test_a_capability_sequence_with_any_final_is_removed(self):
        """The widening, 2026-08-26. The first version of this stripped only the SGR-shaped form and
        left `CSI < u` printing a `u` into every hermes console. `<`, `>` and `=` all mark a CSI as
        capability negotiation and pyte implements none of them, so the final character does not
        matter -- enumerating finals would leave the next one to be discovered the same way."""
        for seq in (f"{ESC}[<u", f"{ESC}[>1u", f"{ESC}[>0q", f"{ESC}[>4;2m", f"{ESC}[=3c"):
            self.assertEqual(strip_private_sgr(f"A{seq}B"), "AB", seq)

    def test_a_DEC_MODE_is_untouched_because_pyte_implements_it(self):
        """`?` IS A PRIVATE PREFIX TOO, and is deliberately not in the removed set.

        pyte implements `?...h` and `?...l` -- DEC mode set/reset -- and they carry the alternate
        screen and cursor visibility that the balanced-alt-screen handling depends on. 12,861 of them
        crossed this function in the measured sample and every one must reach the emulator.

        This test listed `CSI < u` and `CSI > 1 u` among the KEPT sequences until 2026-08-26, which is
        how the narrow rule was pinned: the `u` forms were assumed harmless because they are not
        SGR-shaped, and one of them was printing a literal `u` into every hermes console. The
        assertion changed deliberately when the measurement showed otherwise."""
        for keep in (f"{ESC}[?25l", f"{ESC}[?1049h", f"{ESC}[?2026h", f"{ESC}[?1002h"):
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


@unittest.skipUnless(_HAVE_PYTE, "pyte is not installed")
class TheKittyKeyboardSequenceDoesNotPrintItselfTests(unittest.TestCase):
    """`CSI < u` against a real hermes console's bytes.

    Separate from the underline capture because it is a different agent, a different runtime and a
    different failure: the underline is an attribute that should not be set, this is a CHARACTER that
    should not exist. One fixture proving both would prove neither cleanly.
    """

    CAPTURE = Path(__file__).resolve().parent / "data" / "console_kitty_keyboard_capture.txt"

    def setUp(self):
        self.capture = self.CAPTURE.read_text(encoding="utf-8", errors="surrogatepass")

    def test_the_capture_still_contains_the_sequence(self):
        self.assertIn(f"{ESC}[<u", self.capture, "the capture no longer holds the kitty sequence")

    def test_pyte_prints_a_u_for_it_when_it_is_not_stripped(self):
        """The positive control for the whole fix: this is the defect, demonstrated. Without it the
        test below could pass because the sequence never mattered, rather than because it was removed.
        """
        import pyte

        screen = pyte.Screen(20, 3)
        pyte.Stream(screen).feed(f"A{ESC}[<uB")
        rendered = "".join(
            (screen.buffer.get(0, {}).get(x).data if screen.buffer.get(0, {}).get(x) else " ")
            for x in range(20)
        ).rstrip()
        self.assertEqual(rendered, "AuB", "pyte no longer mis-renders it; this fix may be obsolete")

    def test_the_sanitiser_changes_what_a_real_hermes_console_renders(self):
        """A DIFFERENTIAL against the same bytes: render with the sanitiser, and without it.

        WHAT THIS DOES AND DOES NOT SHOW, because the two halves of the class do not both reach the
        screen on this capture. The UNDERLINE does -- the unsanitised render carries underline runs
        through the hermes status bar that the sanitised one does not, and that is asserted below. The
        injected `u` is proven in isolation two tests up but is OVERWRITTEN by later repaints on all
        three live streams that contain the sequence, so nothing here claims to have caught it on a
        real screen. Removing a sequence the emulator cannot parse is right either way; overstating
        which half was observed would not be.
        """
        import service.terminal_snapshot as snapshot_module

        original = snapshot_module.strip_private_sgr
        try:
            snapshot_module.strip_private_sgr = lambda text: text
            unsanitised = render_snapshot(self.capture, 180, 40)
        finally:
            snapshot_module.strip_private_sgr = original
        sanitised = render_snapshot(self.capture, 180, 40)

        self.assertNotEqual(
            unsanitised, sanitised,
            "the sanitiser makes no difference to this capture, so this test proves nothing about it "
            "-- the fixture no longer contains a sequence pyte mis-renders",
        )
        self.assertGreater(len(sanitised), 100, "the snapshot painted nothing, so this proves nothing")
        # And the DIFFERENCE is the defect, not merely a difference: the unsanitised render carries
        # underline attributes the sanitised one does not.
        self.assertGreater(_underline_runs(unsanitised), 0, "the unsanitised render is already clean")
        self.assertEqual(_underline_runs(sanitised), 0)


if __name__ == "__main__":
    unittest.main()
