"""A wide character on screen must not make the plain-text reader go silent.

EXTERNAL REVIEW, 2026-09-21, finding 2. `live_screen_text` joined `screen.display`, and pyte's
`display` calls `wcwidth(char[0])` per cell -- which raises IndexError on a wide-char CONTINUATION
stub left with no wide char in front of it. The caller's `except Exception: return None` swallowed
that, and the parked-console answerer is `if not screen: return`, so a worker sitting at the
development-channels dialog was never sent Enter. Up, and deaf. Two readers then fell back to
re-rendering up to 64 KB of stored tail, reinstating the CPU cost the live screen exists to avoid.

THE THREE OPERATIONS BELOW ARE ORDINARY, not contrived: deleting over a wide char (CSI P), erasing
one (CSI X), and overwriting its left half with a narrow character. Each leaves the orphaned stub.

`terminal_ansi.py` never had the bug because it reads cells directly and SKIPS the empty-string
continuation. The fix makes the text path do the same, so the two agree about what is on screen.
"""

from __future__ import annotations

import unittest

from service.terminal_snapshot import drop_live_screen, feed_live_screen, live_screen_text

ESC = chr(27)
#: A CJK character: one glyph, two cells, with an empty-string continuation in the second.
WIDE = "漢"


class AWideCharacterDoesNotBlindTheScreenReader(unittest.TestCase):
    TERMINAL = "wide-char-terminal"

    def tearDown(self) -> None:
        drop_live_screen(self.TERMINAL)

    def _read(self, payload: str) -> str | None:
        drop_live_screen(self.TERMINAL)
        self.assertTrue(
            feed_live_screen(self.TERMINAL, payload, cols=40, rows=6),
            "the fixture must produce a live screen, or this test asserts nothing",
        )
        return live_screen_text(self.TERMINAL)

    def test_a_plain_screen_still_reads_back(self) -> None:
        """THE POSITIVE CONTROL. A reader that cannot return text at all proves nothing below."""
        text = self._read(f"{ESC}[2Jready for input")
        self.assertIsNotNone(text)
        self.assertIn("ready for input", text)

    # The cursor is put ON the wide char (row 1, column 10) with CUP. These two used `ESC[10D` from
    # the end of the line until 2026-09-23, which lands in "confirmation", four cells past the wide
    # char: they never orphaned a stub and passed with the fix reverted. On the wide char, both raise
    # in pyte 0.8.2's `display`.

    def test_deleting_over_a_wide_character_leaves_the_screen_readable(self) -> None:
        # CSI P deletes the wide char and shifts the row left, orphaning its continuation cell.
        text = self._read(f"{ESC}[2Jawaiting {WIDE} confirmation{ESC}[1;10H{ESC}[P")
        self.assertIsNotNone(text, "an orphaned wide-char stub must not silence the whole screen")
        self.assertIn("awaiting", text)
        self.assertIn("confirmation", text)

    def test_erasing_a_wide_character_leaves_the_screen_readable(self) -> None:
        # CSI X blanks the wide char's cell and leaves its continuation behind.
        text = self._read(f"{ESC}[2Jawaiting {WIDE} confirmation{ESC}[1;10H{ESC}[X")
        self.assertIsNotNone(text)
        self.assertIn("awaiting", text)
        self.assertIn("confirmation", text)

    def test_overwriting_the_left_half_with_a_narrow_character_leaves_it_readable(self) -> None:
        text = self._read(f"{ESC}[2J{WIDE}{ESC}[1;1Hx")
        self.assertIsNotNone(text)

    def test_a_wide_character_does_not_shift_the_columns_after_it(self) -> None:
        """The continuation is SKIPPED, never rendered as a space.

        A space there moves every following column one right per wide char -- the same defect the
        ANSI path fixed in 2026-07, and the reason this reads cells instead of padding them.
        """
        text = self._read(f"{ESC}[2J{WIDE}|marker")
        self.assertIsNotNone(text)
        self.assertIn(f"{WIDE}|marker", text)


if __name__ == "__main__":
    unittest.main()
