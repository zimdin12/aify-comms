r"""A column compared against `datetime('now', …)` must be wrapped in `datetime()` too.

THE BUG, which is invisible in the result. SQLite has no date type: `created_at` is the TEXT
`2026-08-27T21:02:07Z` and `datetime('now','-24 hours')` returns `2026-08-26 21:23:59` — ISO with a
`T` and a `Z` on one side, a space and no zone on the other. Comparing them is a STRING comparison,
and `'T'` (0x54) sorts above `' '` (0x20), so every row whose date matches the cutoff day compares
greater whatever its time. A "last 24 hours" filter written that way quietly widens.

MEASURED 2026-08-27, on the live database, by writing it wrong myself: asking for failed spawn
requests in the last 24 hours returned **159** rows reaching back 44 hours. The same question with
both sides in the same format returns **9**. I was about to report the dashboard's "11" as a defect;
the dashboard was right and my query was the broken one. That is what makes this class dangerous —
the wrong answer is larger and more alarming than the right one, so it reads as a finding rather than
as a bug in the instrument.

KNOWN CLASS, NOT A NEW IDEA. It has been found in this repo six times, most of them in one bughunt
round. The product code is CLEAN today: 34 comparisons against `datetime('now', …)`, all 34 classified
WRAPPED, none BROKEN and none UNKNOWN. This file exists so the seventh occurrence fails a test instead
of being discovered in a dashboard number.

THAT COUNT WAS NOT PROVEN IN THE FIRST VERSION, and review caught it by instrumenting this gate rather
than reading it. The old pattern was `datetime\([^)]*\)`, which stops at the first `)` — so eleven real
comparisons of the form `datetime(COALESCE(a, b)) <= datetime('now', ?)` matched neither the correct
shape nor the broken one, and were counted purely for containing the text. "34 comparisons, zero
unwrapped" was therefore a verdict on 23 lines dressed as a verdict on 34, and a genuinely broken
`COALESCE(updated_at, created_at) > datetime('now', ?)` would have slipped through the same gap. Every
candidate is now classified by balancing parentheses, and UNKNOWN is a FAILURE rather than a silence:
an anti-vacuity denominator must not include lines the verdict cannot judge.

WHAT THIS CANNOT SEE, said plainly so nobody reads it as broader than it is:

  * A column compared to a BIND PARAMETER (`updated_at >= ?`) is also a string comparison, and it is
    correct only while the value bound has the stored format exactly. `stats.py` does this and is
    correct — `_iso_from_ms` emits `NNNN-NN-NNTNN:NN:NNZ` and the column holds the same shape,
    verified against the live row. A static scan cannot check that, because the format is decided at
    runtime by the producer.
  * SQL assembled across several lines, or built by string concatenation, is matched only if the
    comparison lands on one line. Every current site does.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

SERVICE = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"tests", "__pycache__", "node_modules", ".git"}

#: Where a comparison against `datetime('now', …)` begins.
_NOW = re.compile(r"datetime\(\s*['\"]now['\"]")
#: The operator immediately left of it, and the text before that operator on the same line.
_COMPARISON = re.compile(r"(.*?)\s*(>=|<=|>|<)\s*$")


def _left_operand(before: str) -> str:
    r"""The whole left-hand expression, matched by BALANCING parentheses from the right.

    A REGEX CANNOT DO THIS, and the first version of this file proved it. `datetime\([^)]*\)` stops at
    the first `)`, so `datetime(COALESCE(a, b)) <= datetime('now', ?)` matched neither the correct
    pattern nor the broken one. ELEVEN of the 34 comparisons in this repo are that shape -- they were
    counted only because their line contains `datetime('now'`, and silently classified as neither.
    The gate then reported "34 comparisons, zero unwrapped" while eleven of them had no verdict at all,
    and a genuinely broken `COALESCE(updated_at, created_at) > datetime('now', ?)` would have passed
    through the same hole. Review caught it by instrumenting the gate rather than reading it.
    """
    text = before.rstrip()
    if not text.endswith(")"):
        # A bare identifier: walk back over what an SQL column name may contain.
        i = len(text)
        while i and (text[i - 1].isalnum() or text[i - 1] in "_."):
            i -= 1
        return text[i:]
    depth = 0
    for i in range(len(text) - 1, -1, -1):
        if text[i] == ")":
            depth += 1
        elif text[i] == "(":
            depth -= 1
            if depth == 0:
                j = i
                while j and (text[j - 1].isalnum() or text[j - 1] in "_."):
                    j -= 1
                return text[j:]
    return text


def _classify(line: str) -> str:
    """WRAPPED, BROKEN or UNKNOWN for one line. UNKNOWN is a failure, not a shrug."""
    match = _NOW.search(line)
    if not match:
        return ""
    head = _COMPARISON.match(line[: match.start()])
    if not head:
        return "UNKNOWN"
    left = _left_operand(head.group(1))
    if not left:
        return "UNKNOWN"
    return "WRAPPED" if left.lower().startswith("datetime(") else "BROKEN"


def _sources():
    out = []
    for root, dirs, names in os.walk(SERVICE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        out.extend(os.path.join(root, n) for n in names if n.endswith(".py"))
    return out


def _scan():
    """Every candidate line, classified. UNKNOWN is reported and fails, never absorbed into a total."""
    verdicts = {"WRAPPED": [], "BROKEN": [], "UNKNOWN": []}
    for path in _sources():
        try:
            lines = open(path, encoding="utf-8", errors="replace").read().split("\n")
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            if "datetime('now'" not in line and 'datetime("now"' not in line:
                continue
            verdict = _classify(line) or "UNKNOWN"
            verdicts[verdict].append((os.path.relpath(path, SERVICE), i, line.strip()[:100]))
    return verdicts


VERDICTS = _scan()
TOTAL = sum(len(v) for v in VERDICTS.values())


class ATimestampComparisonNormalisesBothSides(unittest.TestCase):
    def test_the_scan_can_see_the_comparisons_at_all(self):
        """POSITIVE CONTROL. A scan that found no comparisons would report zero defects for the same
        reason a scan of an empty directory does, and this gate would pass for ever while meaning
        nothing."""
        self.assertGreater(TOTAL, 15, f"only {TOTAL} comparisons found; the scan is not reaching the code")

    def test_it_recognises_a_BROKEN_comparison(self):
        """NEGATIVE CONTROL. Fed the shape it exists to catch, it must say BROKEN."""
        self.assertEqual(_classify("AND updated_at > datetime('now','-24 hours')"), "BROKEN")

    def test_it_recognises_a_BROKEN_comparison_behind_a_FUNCTION(self):
        """The hole the first version had. `COALESCE(a, b)` is not a bare column and not
        `datetime(...)`, so a regex looking for either matched neither and the line was silently
        counted without a verdict."""
        self.assertEqual(
            _classify("AND COALESCE(updated_at, created_at) > datetime('now', ?)"), "BROKEN")

    def test_it_CLEARS_a_correct_comparison(self):
        """A probe that cannot say ABSENT cannot say PRESENT -- and a gate that flagged the correct
        form too would be switched off within a day."""
        self.assertEqual(_classify("AND datetime(updated_at) > datetime('now','-24 hours')"), "WRAPPED")

    def test_it_CLEARS_a_correct_comparison_with_a_NESTED_call(self):
        """The eleven real lines that had no verdict. Balancing parentheses from the right is what
        makes `datetime(COALESCE(...))` legible as the correct form it is."""
        self.assertEqual(
            _classify("AND datetime(COALESCE(r.started_at, r.requested_at)) <= datetime('now', ?)"),
            "WRAPPED")

    def test_NOTHING_is_left_unclassified(self):
        """The assertion that makes the count mean something.

        A line the gate cannot judge must not be folded into a clean total. That is exactly how
        "34 comparisons, zero unwrapped" was true of a scan that had actually judged only 23 of
        them: the other eleven matched neither pattern and were counted for containing the text.
        """
        self.assertEqual(
            VERDICTS["UNKNOWN"], [],
            "the gate cannot classify these, so its verdict on the rest is not a population "
            "claim:\n  "
            + "\n  ".join(f"{p}:{n}  {text}" for p, n, text in VERDICTS["UNKNOWN"]),
        )

    def test_no_comparison_leaves_its_column_unnormalised(self):
        self.assertEqual(
            VERDICTS["BROKEN"], [],
            "these compare a raw TEXT value against datetime(now, ...), which is a STRING "
            "comparison and silently widens the window:\n  "
            + "\n  ".join(f"{p}:{n}  {text}" for p, n, text in VERDICTS["BROKEN"])
            + "\nWrap the left side: datetime(expr) > datetime(now, ?).",
        )


if __name__ == "__main__":
    unittest.main()
