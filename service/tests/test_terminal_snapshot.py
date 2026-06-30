"""Tests for the clean console-replay snapshot renderer (service/terminal_snapshot.py)."""

import re
import unittest

from service.terminal_snapshot import render_snapshot


def _visible(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", s).replace("\r", "")


class TerminalSnapshotTests(unittest.TestCase):
    def test_renders_current_screen_and_drops_stale_frames(self):
        raw = (
            "\x1b[2J\x1b[Hgarbage old frame\x1b[5;1Hmore noise"
            "\x1b[2J\x1b[H\x1b[1;1mHello\x1b[0m\r\nWorld line two\x1b[3;1HLine three here"
        )
        snap = render_snapshot(raw, 40, 6)
        vis = _visible(snap)
        self.assertIn("Hello", vis)
        self.assertIn("World line two", vis)
        self.assertIn("Line three here", vis)
        self.assertNotIn("garbage old frame", vis)  # overwritten frame must not leak

    def test_snapshot_is_self_contained_reset_clear(self):
        snap = render_snapshot("hi there", 20, 4)
        # Begins by resetting attributes + clearing so it paints cleanly into a fresh xterm.
        self.assertTrue(snap.startswith("\x1b[0m\x1b[2J\x1b[H"))

    def test_empty_input_returns_empty(self):
        self.assertEqual(render_snapshot("", 80, 24), "")

    def test_clamps_absurd_dimensions(self):
        # Should not raise or allocate insanely; just renders within clamped bounds.
        snap = render_snapshot("x\r\ny\r\nz", 99999, 0)
        self.assertIn("x", _visible(snap))

    def test_preserves_color_runs(self):
        # Red "ERR" then default text — the snapshot should carry an SGR color code.
        raw = "\x1b[31mERR\x1b[0m ok"
        snap = render_snapshot(raw, 20, 3)
        self.assertIn("31", snap)  # red foreground preserved in the rebuilt SGR
        self.assertIn("ERR", _visible(snap))


if __name__ == "__main__":
    unittest.main()
