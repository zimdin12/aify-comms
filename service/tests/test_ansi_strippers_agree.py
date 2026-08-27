"""Two ANSI strippers, one answer.

`service/terminal_diagnostics.py` and `service/api_core/terminal_text.py` each carry their own
`_ANSI_RE`, because a service-level leaf may not import api_core. Two copies that must agree is
exactly the shape this repo answers with an AGREEMENT TEST rather than a refactor.

WHY IT NEEDED ONE. `terminal_text.py` stated in prose that diagnostics "keeps its OWN _ANSI_RE with a
broader pattern", and a reviewer ruling not to unify them was recorded on the strength of that
sentence. It was false. Measured 2026-08-18 over real escape sequences, diagnostics left DCS, APC, PM
and SOS payloads COMPLETELY INTACT — all four of which terminal_text strips — while an external
reviewer had reported precisely that and been filed as a "Low".

It mattered because `terminal_diagnostics` produces the one-line explanation of why a terminal died,
which an operator and other agents read. An unstripped APC payload is raw escape bytes in a
diagnostic line.

Nothing in the suite compared them, so the prose was the only record and the prose was wrong. This
file makes the claim checkable: if the two ever diverge again, it says so on the case that diverges.
"""

from __future__ import annotations

import unittest

import service.terminal_diagnostics as diagnostics
from service.api_core import terminal_text

ESC = "\x1b"
BEL = "\x07"
ST = ESC + "\\"

#: One per escape FAMILY a terminal actually emits. DCS/APC/PM/SOS are the four that were missing,
#: and they are the reason this file exists — a family absent from the cases is a family the test
#: does not check, which is how the previous gap survived.
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


class TheTwoAnsiStrippersAgree(unittest.TestCase):
    def test_both_patterns_strip_every_case_identically(self):
        disagreements = []
        for name, raw in CASES.items():
            left = diagnostics._ANSI_RE.sub("", raw)
            right = terminal_text._ANSI_RE.sub("", raw)
            if left != right:
                disagreements.append(f"  {name}: diagnostics={left!r} terminal_text={right!r}")
        self.assertEqual(
            disagreements, [],
            "the two ANSI strippers disagree. They are separate copies because a service leaf may not "
            "import api_core, which makes this test the only thing keeping them equal:\n"
            + "\n".join(disagreements),
        )

    def test_NEITHER_leaves_an_escape_behind(self):
        """The property that actually matters, asserted directly rather than only through equality:
        two patterns can agree by being equally wrong. Every case here must come out clean."""
        for name, raw in CASES.items():
            with self.subTest(case=name):
                for label, pattern in (("diagnostics", diagnostics._ANSI_RE),
                                       ("terminal_text", terminal_text._ANSI_RE)):
                    stripped = pattern.sub("", raw)
                    self.assertNotIn(
                        ESC, stripped,
                        f"{label} left an escape sequence in {name}: {stripped!r}. This text is read "
                        f"by operators and agents; raw escape bytes in it are not cosmetic.",
                    )

    def test_the_visible_text_SURVIVES(self):
        """ANTI-VACUITY: a pattern that deleted everything would satisfy both tests above."""
        self.assertEqual(diagnostics._ANSI_RE.sub("", CASES["several at once"]), "visible")
        self.assertEqual(terminal_text._ANSI_RE.sub("", CASES["several at once"]), "visible")
        self.assertEqual(diagnostics._ANSI_RE.sub("", CASES["plain text"]), "nothing to strip here")

    def test_the_two_patterns_are_literally_the_same(self):
        """Stronger than case agreement and cheap: if the sources are identical, no untested case can
        diverge either. Kept alongside the behavioural test rather than instead of it — this one says
        WHETHER they differ, the other says WHERE."""
        self.assertEqual(
            diagnostics._ANSI_RE.pattern, terminal_text._ANSI_RE.pattern,
            "the two copies have drifted apart; the behavioural test above names the cases affected",
        )


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

#: The three the class deliberately does NOT match: they are a terminal line's layout, and stripping
#: them would join lines a reader needs kept apart.
LAYOUT_BYTES = (chr(9), chr(10), chr(13))


class TheTwoControlCharStrippersAgree(unittest.TestCase):
    """The OTHER half of "make terminal output plain text", which had no agreement test at all.

    `_ANSI_RE` got one because prose claiming the two copies matched turned out to be false, and it
    mattered: `terminal_diagnostics` produces the one-line explanation of why a terminal died, read by
    operators and by other agents. The control-character class sits in the same two files, does the
    same job on the same text, and nothing compared it -- the identical gap, one line further down.

    It was written out inline at FOUR sites. Three now import the named constant from `terminal_text`;
    `terminal_diagnostics` keeps its own because a service leaf may not import api_core
    (test_leaves_do_not_import_the_carrier.py), which is exactly the condition that makes an agreement
    test the answer here rather than a refactor.
    """

    def test_both_patterns_strip_every_case_identically(self):
        disagreements = []
        for name, raw in CTRL_CASES.items():
            left = diagnostics._CTRL_RE.sub("", raw)
            right = terminal_text._CTRL_RE.sub("", raw)
            if left != right:
                disagreements.append(f"  {name}: diagnostics={left!r} terminal_text={right!r}")
        self.assertEqual(
            disagreements, [],
            "the two control-character strippers disagree, and they are separate copies because a "
            "service leaf may not import api_core -- which makes this test the only thing keeping "
            "them equal:",
            *disagreements,
        )

    def test_NEITHER_leaves_a_control_byte_behind(self):
        """Two patterns can agree by being equally wrong, so the property is asserted directly."""
        for name, raw in CTRL_CASES.items():
            with self.subTest(case=name):
                for label, pattern in (("diagnostics", diagnostics._CTRL_RE),
                                       ("terminal_text", terminal_text._CTRL_RE)):
                    stripped = pattern.sub("", raw)
                    leftover = [
                        c for c in stripped
                        if (ord(c) < 32 and c not in LAYOUT_BYTES) or ord(c) == 127
                    ]
                    self.assertEqual(
                        leftover, [],
                        f"{label} left {leftover!r} in {name}. This text reaches an operator's screen.",
                    )

    def test_LAYOUT_bytes_SURVIVE_both(self):
        """ANTI-VACUITY, and the case most likely to be got wrong by widening the class. A stripper
        that removed newlines would pass every agreement check above while running a dying terminal's
        last lines together into one."""
        for name in ("TAB is LAYOUT, not noise", "LF is LAYOUT", "CR is LAYOUT"):
            raw = CTRL_CASES[name]
            self.assertEqual(diagnostics._CTRL_RE.sub("", raw), raw, name)
            self.assertEqual(terminal_text._CTRL_RE.sub("", raw), raw, name)

    def test_the_visible_text_SURVIVES(self):
        """A pattern that deleted everything would satisfy equality and emptiness both."""
        self.assertEqual(diagnostics._CTRL_RE.sub("", CTRL_CASES["NUL"]), "ab")
        self.assertEqual(terminal_text._CTRL_RE.sub("", CTRL_CASES["NUL"]), "ab")
        self.assertEqual(terminal_text._CTRL_RE.sub("", CTRL_CASES["plain text"]), "nothing to strip here")

    def test_the_two_patterns_are_literally_the_same(self):
        self.assertEqual(
            diagnostics._CTRL_RE.pattern, terminal_text._CTRL_RE.pattern,
            "the two copies have drifted; the behavioural test above names the cases affected",
        )

    def test_the_class_is_NOT_written_out_inline_anywhere_else(self):
        """It was, at four sites. Three of them could import the constant and now do; a fourth copy
        appearing is how this stops being two-copies-with-a-test and becomes four-copies-with-a-test
        that only covers two of them.

        MATCHED ON THE EXACT CLASS, not on a prefix. The first version searched for the opening
        `x00-` and so also flagged `serialization.py`, whose `[\\x00-\\x1f\\x7f]+` is a DIFFERENT class
        answering a different question -- it INCLUDES CR, LF and TAB because it collapses them to a
        space when quoting an untrusted subject, so a newline cannot break the value onto its own
        line. Two classes that look alike and must NOT be unified is exactly the case a prefix
        match gets wrong."""
        import pathlib

        governed = terminal_text._CTRL_RE.pattern
        root = pathlib.Path(__file__).resolve().parent.parent
        skip = {"tests", "__pycache__"}
        carriers = []
        for path in root.rglob("*.py"):
            if any(part in skip for part in path.parts):
                continue
            if governed in path.read_text(encoding="utf-8", errors="replace"):
                carriers.append(path.name)
        self.assertEqual(
            sorted(carriers), ["terminal_diagnostics.py", "terminal_text.py"],
            "the control-character class must live only in the two files this test compares; "
            f"found it in: {sorted(carriers)}",
        )


if __name__ == "__main__":
    unittest.main()
