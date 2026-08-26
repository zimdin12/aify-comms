"""Tests for service/terminal_diagnostics.py (v0.2 WS-1).

The primary fixture is the REAL recorded output of the terminal that died in the
2026-08-07 13:36 incident — 459 bytes read verbatim out of
`terminal_sessions.output` for `term_1786109794427_0f32fd75`, ANSI and CRLF intact.
It is not a hand-written approximation: the compaction bug shipped because its
fixtures were authored from documentation rather than capture, and this feature's
whole purpose is to reproduce what a real dead worker leaves behind.
"""

import unittest

from service.terminal_diagnostics import (
    clean_terminal_text,
    failure_tail,
    meaningful_failure_line,
    meaningful_lines,
)

# Captured 2026-08-07 from the live DB. Do not "tidy" this string.
REAL_DEAD_HERMES_OUTPUT = (
    "[terminal attached pid=49060]\n"
    "\x1b[?9001h\x1b[?1004h\x1b[?25l\x1b[2J\x1b[m\x1b[H\x1b]0;Windows PowerShell\x07\x1b[?25h"
    "[hermes-managed-host] fatal: hermes dashboard at http://127.0.0.1:9147/ did not"
    " become ready within 60000ms: fetch failed\r\n"
    "[hermes-aify] FATAL: managed gateway host for 'sc-architect' did not come up.\r\n"
    "[hermes-aify]   (node C:\\Users\\Administrator\\.aify-comms\\mcp\\stdio\\hermes-managed-host.js"
    " ensure-host sc-architect failed -- see the error above)\r\n"
    "\n[terminal exited]\n"
)

EXPECTED_ROOT_CAUSE = (
    "[hermes-managed-host] fatal: hermes dashboard at http://127.0.0.1:9147/"
    " did not become ready within 60000ms: fetch failed"
)


class RealIncidentTests(unittest.TestCase):
    def test_yields_the_root_cause_line_from_the_real_incident(self):
        """THE acceptance test named in the v0.2 spec."""
        self.assertEqual(meaningful_failure_line(REAL_DEAD_HERMES_OUTPUT), EXPECTED_ROOT_CAUSE)

    def test_prefers_the_first_fatal_over_later_consequences(self):
        # Three consecutive fatal lines; the third says "see the error above", so the
        # LAST fatal is the symptom and the FIRST is the cause.
        line = meaningful_failure_line(REAL_DEAD_HERMES_OUTPUT)
        self.assertIn("did not become ready", line)
        self.assertNotIn("see the error above", line)
        self.assertNotIn("did not come up", line)

    def test_strips_ansi_and_never_leaks_escape_bytes(self):
        line = meaningful_failure_line(REAL_DEAD_HERMES_OUTPUT)
        self.assertNotIn("\x1b", line)
        self.assertNotIn("\x07", line)
        self.assertNotIn("\r", line)

    def test_drops_terminal_scaffolding_lines(self):
        lines = meaningful_lines(REAL_DEAD_HERMES_OUTPUT)
        self.assertTrue(lines)
        joined = " ".join(lines).lower()
        self.assertNotIn("[terminal attached", joined)
        self.assertNotIn("[terminal exited", joined)

    def test_tail_keeps_all_three_diagnostic_lines_in_order(self):
        tail = failure_tail(REAL_DEAD_HERMES_OUTPUT)
        self.assertEqual(len(tail.splitlines()), 3)
        self.assertTrue(tail.startswith("[hermes-managed-host] fatal:"))
        self.assertIn("see the error above", tail)


class FallbackAndBoundTests(unittest.TestCase):
    def test_no_marker_falls_back_to_the_last_meaningful_line(self):
        raw = "[terminal attached pid=1]\nbooting\nabout to do the thing\n[terminal exited]\n"
        self.assertEqual(meaningful_failure_line(raw), "about to do the thing")

    def test_scaffolding_only_recording_yields_empty_not_a_fake_diagnosis(self):
        # "" MUST mean "nothing was recorded". If this returned "[terminal exited]"
        # every failure message would claim the terminal exiting was the cause.
        raw = "[terminal attached pid=1]\n\n[terminal exited]\n"
        self.assertEqual(meaningful_failure_line(raw), "")

    def test_empty_and_none_are_safe(self):
        for value in ("", None, "   \n\n  "):
            self.assertEqual(meaningful_failure_line(value), "")
            self.assertEqual(failure_tail(value), "")
            self.assertEqual(meaningful_lines(value), [])

    def test_long_fatal_line_is_truncated_with_an_ellipsis(self):
        raw = "fatal: " + ("x" * 900)
        line = meaningful_failure_line(raw)
        self.assertLessEqual(len(line), 240)
        self.assertTrue(line.endswith("…"))
        self.assertTrue(line.startswith("fatal: xxx"))

    def test_max_chars_is_honoured_and_floored(self):
        raw = "error: " + ("y" * 500)
        self.assertLessEqual(len(meaningful_failure_line(raw, max_chars=40)), 40)
        # A nonsensical bound must not produce an empty or negative slice.
        self.assertTrue(meaningful_failure_line(raw, max_chars=0))
        self.assertTrue(meaningful_failure_line(raw, max_chars=-5))

    def test_tail_is_byte_bounded(self):
        raw = "\n".join(f"line {i} of noise" for i in range(400))
        tail = failure_tail(raw)
        self.assertLessEqual(len(tail), 1200)
        self.assertLessEqual(len(tail.splitlines()), 13)  # 12 lines + possible "…" prefix line

    def test_clean_normalizes_crlf_to_lf(self):
        self.assertEqual(clean_terminal_text("a\r\nb\rc"), "a\nb\nc")


class OtherRuntimeShapesTests(unittest.TestCase):
    """Shapes the other three runtimes actually produce on a failed launch."""

    def test_python_traceback(self):
        raw = (
            "[terminal attached pid=2]\nStarting\n"
            "Traceback (most recent call last):\n"
            '  File "x.py", line 1, in <module>\n'
            "ModuleNotFoundError: No module named 'foo'\n"
        )
        self.assertEqual(meaningful_failure_line(raw), "Traceback (most recent call last):")

    def test_windows_missing_command(self):
        raw = "hermes : The term 'hermes' is not recognized as the name of a cmdlet\n"
        self.assertIn("not recognized as", meaningful_failure_line(raw))

    def test_node_enoent(self):
        raw = "node:internal/modules/cjs/loader:1146\n  throw err;\nError: Cannot find module './x'\n"
        self.assertEqual(meaningful_failure_line(raw), "Error: Cannot find module './x'")

    def test_port_conflict(self):
        raw = "listen EADDRINUSE: address already in use 127.0.0.1:9147\n"
        self.assertIn("address already in use", meaningful_failure_line(raw))

class DecoratedMarkerLineIsNotAnEpitaphTests(unittest.TestCase):
    """A fatal MARKER on a line the terminal DREW is still the screen, not a cause.

    MEASURED 2026-08-26 over 83 real dead consoles from the operator's database. One produced 870
    characters of a tool-call tree as its cause, matched on `cannot ` occurring inside the agent's own
    English: "source movement occurred between snapshot but CANNOT BE claimed as the cause of the
    numerical...". Box drawing, a caret and a bullet, reported as why the terminal died.

    `is_terminal_decoration` already records this exact failure -- "the operator was told the worker
    died and handed a progress meter as the reason, three times over" -- but its guard was wired into
    the FALLBACK path only, so a marker match walked straight past it.

    THE FIXTURES ARE TRIMMED CAPTURES, not inventions: the decorative prefix and the marker phrase are
    the shape that was actually recorded. The agent's analysis text itself is not reproduced here --
    it is the operator's work, and the defect is in the glyphs and the marker, not in what was said.
    """

    def test_a_marker_inside_a_drawn_line_is_refused(self):
        raw = "\u2514\u2500 \u25be Tool calls (3) Search Files(...) cannot be claimed as the cause\n"
        self.assertEqual(meaningful_failure_line(raw), "")

    def test_an_undecorated_fatal_line_LATER_in_the_capture_still_wins(self):
        """CONTINUE, not break. A decorated match is no evidence that no real one follows, and a
        runtime often paints a frame before writing its own fatal line."""
        raw = (
            "\u2514\u2500 \u25be Tool calls (3) cannot be claimed as the cause\n"
            "[hermes-managed-host] fatal: dashboard did not become ready within 60000ms\n"
        )
        self.assertEqual(
            meaningful_failure_line(raw),
            "[hermes-managed-host] fatal: dashboard did not become ready within 60000ms",
        )

    def test_a_bullet_counts_as_drawing_too(self):
        raw = "\u25cf Baked for 1m 54s - cannot continue\n"
        self.assertEqual(meaningful_failure_line(raw), "")

    def test_the_founding_incident_is_UNCHANGED(self):
        """The acceptance test for this whole module, re-asserted from this angle.

        The guard is per LINE, not per recording, precisely so this keeps working: a plain-text fatal
        line stands even when a spinner was painted elsewhere in the same capture. Measured: the
        captured frame carries no decoration at all, so a per-recording rule would ALSO have passed
        here -- which is why the choice between them had to be made on the losing case (a TUI that
        dies after painting) rather than on this one.
        """
        self.assertEqual(meaningful_failure_line(REAL_DEAD_HERMES_OUTPUT), EXPECTED_ROOT_CAUSE)

    def test_a_plain_fatal_line_beside_a_drawn_one_is_unaffected(self):
        raw = (
            "[hermes-aify] FATAL: managed gateway host did not come up.\n"
            "\u2514\u2500 \u25be Tool calls (3) cannot be claimed\n"
        )
        self.assertEqual(
            meaningful_failure_line(raw),
            "[hermes-aify] FATAL: managed gateway host did not come up.",
        )

    def test_decoration_anywhere_still_blocks_the_FALLBACK(self):
        """Unchanged behaviour, pinned because the two paths now share a signal and a later edit
        could easily collapse them into one rule."""
        raw = "\u2514\u2500 \u25be drawing something\nordinary conversation with no marker at all\n"
        self.assertEqual(meaningful_failure_line(raw), "")


if __name__ == "__main__":
    unittest.main()
