"""Inferring the width a terminal log was DRAWN at, so replaying it does not mangle the console.

`infer_source_width` was among the service functions the suite never entered. It exists because a
resident wrapper mirrors the operator's REAL terminal, whose width the service never stored —
`terminal_sessions.cols` is 0 or NULL for residents. Replaying that log at a narrower viewer width
wraps every full-screen-TUI line, which is the "gappy / bugged console" the dashboard used to show.

THE HEURISTIC IS THE FURTHEST COLUMN ANY CELL REACHES. A full-screen TUI draws a full-width frame or
rule, so the maximum occupied column IS the source width. It replays once at a generous probe width
with no clamping, so the app's absolute cursor moves land where they were meant to.

RETURNING 0 IS A REAL ANSWER, not a failure to report: it means "unknown", and the caller falls back
to the viewer width and behaves exactly as it did before this existed. Every path that cannot know —
no output, no pyte, a feed that raises — has to reach it, because an invented width is worse than
none: it would wrap a console that was rendering correctly.
"""

from __future__ import annotations

import unittest
from unittest import mock

from service import terminal_snapshot
from service.terminal_snapshot import infer_source_width

HAVE_PYTE = terminal_snapshot._HAVE_PYTE


def line(width: int, char: str = "-") -> str:
    return char * width


@unittest.skipUnless(HAVE_PYTE, "pyte is not installed; the function returns 0 by design")
class InferSourceWidthTests(unittest.TestCase):
    def test_a_full_width_rule_reports_that_width(self):
        """The case the heuristic is built on: a TUI frame spans the terminal, so the furthest
        occupied column is the width it was drawn at."""
        self.assertEqual(infer_source_width(line(120)), 120)

    def test_the_WIDEST_line_wins_even_when_later_lines_are_short(self):
        """Replay order must not decide the answer — a status bar printed after the frame is
        narrower, and taking the last line would report it as the terminal width."""
        raw = line(160) + "\r\n" + line(20)
        self.assertEqual(infer_source_width(raw), 160)

    def test_trailing_spaces_do_not_count_as_content(self):
        """A cell holding a space is not drawn content. Counting it inflates the inferred width by
        however much padding the app emitted, and the console is then replayed too wide."""
        self.assertEqual(infer_source_width(line(40) + " " * 40), 40)

    def test_empty_output_is_UNKNOWN_rather_than_zero_width(self):
        # The `not raw_output` check is a SHORT CIRCUIT, not a guard: feeding "" to pyte occupies no
        # cells and the measurement returns 0 by itself. Removing it is an uncaught mutation —
        # recorded because what it saves is allocating a screen for a log that has nothing in it.
        for raw in ("", None):
            with self.subTest(raw=raw):
                self.assertEqual(infer_source_width(raw), 0)

    def test_a_terminal_that_only_printed_spaces_is_unknown(self):
        self.assertEqual(infer_source_width("   \r\n   "), 0)

    def test_the_probe_width_is_CLAMPED_to_a_sane_range(self):
        """It allocates a pyte screen, so both ends are bounded — but only the FLOOR is visible in
        the return value, and this test says which half it is proving.

        A tiny probe would report a tiny width and wrap a console that was fine: `probe=1` still
        measures a 100-column line as 100, because the floor is 80 and the line is wider than that.
        The ceiling is a memory bound whose effect is the size of the pyte screen, which the return
        value cannot see — removing it is an uncaught mutation, recorded rather than pretended away.
        """
        self.assertGreaterEqual(infer_source_width(line(100), probe=1), 80)
        self.assertLessEqual(infer_source_width(line(400), probe=100000), 500)

    def test_a_line_wider_than_the_probe_is_reported_AT_the_probe(self):
        """The honest limit of the heuristic, pinned so it is not mistaken for the real width: a
        400-column probe cannot see column 600."""
        self.assertEqual(infer_source_width(line(600), probe=400), 400)

    def test_a_BALANCED_alt_screen_region_is_stripped_before_measuring(self):
        """An alt-screen overlay that has already exited is not on screen any more. Measuring it
        would size the console to a full-screen pager the operator closed."""
        overlay = "\x1b[?1049h" + line(200) + "\x1b[?1049l"
        raw = overlay + "\r\n" + line(80)
        self.assertEqual(infer_source_width(raw), 80)

    def test_an_UNCLOSED_alt_screen_is_measured_because_it_is_still_live(self):
        """The other half of the same rule: an overlay with no exit sequence is what the operator is
        looking at right now."""
        raw = "\x1b[?1049h" + line(140)
        self.assertEqual(infer_source_width(raw), 140)

    def test_absolute_cursor_moves_are_honoured_rather_than_wrapped(self):
        """No clamping during the probe replay is the whole reason this works — a TUI positions with
        absolute moves, and a narrow screen would wrap them into the wrong columns."""
        raw = "\x1b[1;150Hx"
        self.assertEqual(infer_source_width(raw), 150)

    def test_output_that_pyte_chokes_on_answers_UNKNOWN(self):
        """Any parse failure has to reach 0. An exception here would take down a console read for
        every viewer of that terminal, and an invented width would wrap one that was fine."""
        with mock.patch.object(terminal_snapshot.pyte, "Stream") as stream:
            stream.return_value.feed.side_effect = RuntimeError("bad escape")
            self.assertEqual(infer_source_width(line(80)), 0)


class WithoutPyteTests(unittest.TestCase):
    def test_no_pyte_means_unknown_not_an_error(self):
        """The dependency is optional. Without it every console still renders — at the viewer's
        width, exactly as it did before this heuristic existed."""
        with mock.patch.object(terminal_snapshot, "_HAVE_PYTE", False):
            self.assertEqual(infer_source_width(line(120)), 0)
