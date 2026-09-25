"""The two patterns that make terminal output plain text: every escape family out, layout kept.

`_ANSI_RE` and `_CTRL_RE` have one owner, `service/terminal_diagnostics.py`, and
`service/api_core/terminal_text.py` imports them. They used to be two copies each, and prose claiming
the diagnostics copy was "broader" was false: measured 2026-08-18 it left DCS, APC, PM and SOS payloads
intact in the one-line explanation of why a terminal died, which operators and other agents read.

What stays worth proving is the property itself, on the one pattern: no escape or control byte
survives, the visible text and the line layout do, and nobody writes the class out again elsewhere.
"""

from __future__ import annotations

import pathlib
import unittest

import service.terminal_diagnostics as diagnostics
from service.api_core import terminal_text

ESC = "\x1b"
BEL = "\x07"
ST = ESC + "\\"

#: One per escape FAMILY a terminal actually emits. DCS/APC/PM/SOS are the four that were once
#: missing — a family absent from the cases is a family the test does not check.
CASES = {
    "CSI colour": f"{ESC}[31mred{ESC}[0m",
    "CSI private": f"{ESC}[?25lhidden",
    "CSI intermediate": f"{ESC}[?1049;2$phidden",
    "OSC terminated by BEL": f"{ESC}]0;title{BEL}after",
    "OSC terminated by ST": f"{ESC}]0;title{ST}after",
    "charset select": f"{ESC}(Bplain",
    "keypad mode": f"{ESC}=num",
    "DCS": f"{ESC}Pdcs-payload{ST}tail",
    "APC": f"{ESC}_apc-payload{ST}tail",
    "PM": f"{ESC}^pm-payload{ST}tail",
    "SOS": f"{ESC}Xsos-payload{ST}tail",
    "several at once": f"{ESC}[1m{ESC}]0;t{BEL}{ESC}Pd{ST}visible{ESC}[0m",
    "plain text": "nothing to strip here",
}

#: One per class of byte the CONTROL-CHARACTER stripper decides about. Built with chr() rather than
#: written as escapes, because an escape typed into this file is one more thing that can be wrong in
#: the same way the pattern can.
CTRL_CASES = {
    "NUL": "a" + chr(0) + "b",
    "backspace": "a" + chr(8) + "b",
    "vertical tab": "a" + chr(11) + "b",
    "form feed": "a" + chr(12) + "b",
    "shift out": "a" + chr(14) + "b",
    "unit separator": "a" + chr(31) + "b",
    "DEL": "a" + chr(127) + "b",
    "TAB is LAYOUT, not noise": "a" + chr(9) + "b",
    "LF is LAYOUT": "a" + chr(10) + "b",
    "CR is LAYOUT": "a" + chr(13) + "b",
    "plain text": "nothing to strip here",
}

#: The three the class deliberately does NOT match: they are a terminal line's layout.
LAYOUT_BYTES = (chr(9), chr(10), chr(13))


class TerminalTextStrippers(unittest.TestCase):
    def test_terminal_text_uses_the_one_owner(self):
        self.assertIs(terminal_text._ANSI_RE, diagnostics._ANSI_RE)
        self.assertIs(terminal_text._CTRL_RE, diagnostics._CTRL_RE)

    def test_no_escape_survives(self):
        for name, raw in CASES.items():
            with self.subTest(case=name):
                stripped = diagnostics._ANSI_RE.sub("", raw)
                self.assertNotIn(ESC, stripped, f"an escape survived in {name}: {stripped!r}")

    def test_the_visible_text_survives(self):
        """ANTI-VACUITY: a pattern that deleted everything would satisfy the test above."""
        self.assertEqual(diagnostics._ANSI_RE.sub("", CASES["several at once"]), "visible")
        self.assertEqual(diagnostics._ANSI_RE.sub("", CASES["plain text"]), "nothing to strip here")

    def test_no_control_byte_survives(self):
        for name, raw in CTRL_CASES.items():
            with self.subTest(case=name):
                stripped = diagnostics._CTRL_RE.sub("", raw)
                leftover = [
                    c for c in stripped if (ord(c) < 32 and c not in LAYOUT_BYTES) or ord(c) == 127
                ]
                self.assertEqual(leftover, [], f"{leftover!r} survived in {name}")

    def test_layout_bytes_and_visible_text_survive(self):
        """A stripper that removed newlines would run a dying terminal's last lines together."""
        for name in ("TAB is LAYOUT, not noise", "LF is LAYOUT", "CR is LAYOUT"):
            self.assertEqual(diagnostics._CTRL_RE.sub("", CTRL_CASES[name]), CTRL_CASES[name], name)
        self.assertEqual(diagnostics._CTRL_RE.sub("", CTRL_CASES["NUL"]), "ab")

    def test_the_control_class_is_written_out_only_by_its_owner(self):
        """It was written out at four sites. MATCHED ON THE EXACT CLASS, not a prefix:
        `serialization.py` has a different class that INCLUDES CR, LF and TAB, because it collapses
        them to a space when quoting an untrusted subject. Those two must not be unified."""
        governed = diagnostics._CTRL_RE.pattern
        root = pathlib.Path(__file__).resolve().parent.parent
        carriers = sorted(
            path.name
            for path in root.rglob("*.py")
            if not {"tests", "__pycache__"} & set(path.parts)
            and governed in path.read_text(encoding="utf-8", errors="replace")
        )
        self.assertEqual(carriers, ["terminal_diagnostics.py"])


if __name__ == "__main__":
    unittest.main()
