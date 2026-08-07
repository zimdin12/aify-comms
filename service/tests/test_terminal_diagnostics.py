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


if __name__ == "__main__":
    unittest.main()
