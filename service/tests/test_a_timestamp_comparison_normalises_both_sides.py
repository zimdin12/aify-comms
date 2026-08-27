"""A column compared against `datetime('now', …)` must be wrapped in `datetime()` too.

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
round. The product code is CLEAN today: 34 comparisons against `datetime('now', …)` and zero with an
unwrapped left side, all of them written as `datetime(col) > datetime('now', ?)`. This file exists so
the seventh occurrence fails a test instead of being discovered in a dashboard number.

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

#: A comparison whose LEFT side is a bare column rather than `datetime(col)`.
_UNWRAPPED = re.compile(r"(?<!datetime\()\b([A-Za-z_][\w.]*)\s*(>=|<=|>|<)\s*datetime\(\s*['\"]now['\"]")
#: The correct shape, checked first so it is never reported.
_WRAPPED = re.compile(r"datetime\([^)]*\)\s*(>=|<=|>|<)\s*datetime\(\s*['\"]now['\"]")


def _sources():
    out = []
    for root, dirs, names in os.walk(SERVICE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        out.extend(os.path.join(root, n) for n in names if n.endswith(".py"))
    return out


def _scan():
    """(total comparisons seen, [(path, line, column, text)] for the unwrapped ones)."""
    total, bad = 0, []
    for path in _sources():
        try:
            lines = open(path, encoding="utf-8", errors="replace").read().split("\n")
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            if "datetime('now'" not in line and 'datetime("now"' not in line:
                continue
            total += 1
            if _WRAPPED.search(line):
                continue
            found = _UNWRAPPED.search(line)
            if found:
                bad.append((os.path.relpath(path, SERVICE), i, found.group(1), line.strip()[:100]))
    return total, bad


TOTAL, UNWRAPPED = _scan()


class ATimestampComparisonNormalisesBothSides(unittest.TestCase):
    def test_the_scan_can_see_the_comparisons_at_all(self):
        """POSITIVE CONTROL. A scan that found no comparisons would report zero defects for the same
        reason a scan of an empty directory does, and this gate would pass for ever while meaning
        nothing."""
        self.assertGreater(TOTAL, 15, f"only {TOTAL} comparisons found; the scan is not reaching the code")

    def test_the_pattern_recognises_a_BROKEN_comparison(self):
        """NEGATIVE CONTROL, first half: fed the shape it exists to catch, it must fire."""
        self.assertIsNotNone(
            _UNWRAPPED.search("AND updated_at > datetime('now','-24 hours')"),
            "the exact line that returned 159 rows for a 24-hour question is not detected",
        )
        self.assertIsNone(_WRAPPED.search("AND updated_at > datetime('now','-24 hours')"))

    def test_the_pattern_CLEARS_a_correct_comparison(self):
        """NEGATIVE CONTROL, second half. A probe that cannot say ABSENT cannot say PRESENT -- and a
        gate that flags the correct form too would be turned off within a day."""
        good = "AND datetime(updated_at) > datetime('now','-24 hours')"
        self.assertIsNotNone(_WRAPPED.search(good), "the correct form is not recognised as correct")

    def test_no_comparison_leaves_its_column_unnormalised(self):
        self.assertEqual(
            UNWRAPPED, [],
            "these compare a raw TEXT column against datetime('now', …), which is a STRING comparison "
            "and silently widens the window:\n  "
            + "\n  ".join(f"{p}:{n}  ({col})  {text}" for p, n, col, text in UNWRAPPED)
            + "\nWrap the column: datetime(col) > datetime('now', ?).",
        )


if __name__ == "__main__":
    unittest.main()
