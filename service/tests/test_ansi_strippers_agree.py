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


if __name__ == "__main__":
    unittest.main()
