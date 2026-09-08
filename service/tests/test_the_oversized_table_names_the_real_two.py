"""CLAUDE.md's watch-list must name the files that are actually closest to the 1000-line limit.

WHY THIS EXISTS, and it is a measurement rather than a preference. CLAUDE.md carries a ranked table
of the files nearest the limit, and it has now been wrong FOUR times:

    2026-08-25  named only two files; `pi-session.js` at 993 was on nobody's list
    2026-08-25  the correction claimed to be the whole population and skipped ranks three and four
    2026-08-29  recorded `doctor-predicates.js` at 844 when it was 914 -- 70 lines, in the direction
                that matters, because somebody budgeting a refactor would have budgeted for room
                that was not there
    2026-09-08  `control_plane.py` read 893 and was 901, and `mcp/stdio/doctor.js` at 794 was absent
                while ranking SIXTH -- the middle omitted for the second time

The file already tells the next person to re-run the walk rather than amend a row. Four repeats is a
mechanism, not carelessness, so the instruction gets an instrument.

WHAT IS PINNED IS THE CLAIM THAT CHANGES BEHAVIOUR, not the whole table. "The next edit to `app.js`
goes red" is what makes somebody slice before they type; the exact line count of the eighth row
changes nothing and would turn this gate red on every ordinary commit. So: the two files the table
names as closest must BE the closest two. They move rarely, and when they do it is exactly the
moment the table needs rewriting.

THE POPULATION COMES FROM THE GATE THAT OWNS IT. `_source_files` and `_line_count` are imported from
`test_no_new_oversized_source_file.py`, and the JS half replicates its sibling's skip set and
filename filters. A second walk written from scratch here would agree until one of them was fixed.
"""

from __future__ import annotations

import io
import os
import re
import unittest
from pathlib import Path

from service.tests.test_no_new_oversized_source_file import (
    LIMIT,
    _line_count,
    _source_files,
)

REPO = Path(__file__).resolve().parents[2]
CLAUDE_MD = REPO / "CLAUDE.md"

#: The JS gate's own parameters, from `mcp/stdio/tests/no-new-oversized-source-file.test.js`.
JS_SKIP = frozenset(
    {"node_modules", "tests", "fixtures", "__pycache__", ".git", ".pytest_cache", ".venv", "venv"}
)
JS_NAME = re.compile(r"\.m?js$")
JS_TEST = re.compile(r"\.test\.m?js$")

#: A row of the watch-list table: `| 996 | `path` | 4 |`
ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|\s*(\d+)\s*\|\s*$", re.MULTILINE)


def js_source_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in JS_SKIP)
        for name in sorted(filenames):
            if JS_NAME.search(name) and not JS_TEST.search(name):
                yield Path(dirpath) / name


def measured_ranking() -> list[tuple[int, str]]:
    """Every file both gates judge, longest first."""
    rows = [(_line_count(p), p.relative_to(REPO).as_posix()) for p in _source_files(REPO)]
    rows += [(_line_count(p), p.relative_to(REPO).as_posix()) for p in js_source_files(REPO)]
    rows.sort(key=lambda row: (-row[0], row[1]))
    return rows


def documented_rows() -> list[tuple[int, str, int]]:
    """The watch-list table as CLAUDE.md currently states it."""
    text = io.open(CLAUDE_MD, encoding="utf-8").read()
    return [(int(a), b, int(c)) for a, b, c in ROW.findall(text)]


class TheWatchListNamesTheRealFiles(unittest.TestCase):
    def setUp(self):
        self.measured = measured_ranking()
        self.documented = documented_rows()

    def test_both_readers_found_something(self):
        """POSITIVE CONTROL. Either side returning empty makes every assertion below vacuous, and a
        renamed heading or a reformatted table is exactly how that happens quietly."""
        self.assertGreater(len(self.measured), 400, "the walk found almost no source files")
        self.assertGreaterEqual(
            len(self.documented), 8,
            "CLAUDE.md's watch-list table did not parse; if its shape changed, change this reader",
        )

    def test_the_two_files_named_closest_to_the_limit_are_the_closest_two(self):
        """THE CLAIM THAT CHANGES BEHAVIOUR. Everything else in the table is a snapshot."""
        measured_top = {path for _, path in self.measured[:2]}
        documented_top = {path for _, path, _ in self.documented[:2]}
        self.assertEqual(
            documented_top, measured_top,
            "CLAUDE.md names {} as closest to the {}-line limit; the closest two are {}. "
            "Re-run the walk and rewrite the table -- do not amend one row.".format(
                sorted(documented_top), LIMIT, sorted(measured_top),
            ),
        )

    def test_every_file_the_table_names_still_exists(self):
        """A row naming a deleted file is worse than no row: somebody budgets a refactor against it.
        `terminal-runtime.js` sat in this table at rank three after being deleted."""
        known = {path for _, path in self.measured}
        named = {path for _, path, _ in self.documented}
        missing = sorted(named - known)
        self.assertEqual(
            missing, [],
            f"the watch-list names files the gates no longer see: {missing}",
        )

    def test_the_table_does_not_skip_a_file_larger_than_one_it_lists(self):
        """THE FAILURE IT HAS HAD TWICE. A ranked list that omits its own middle reads as complete.
        `doctor.js` at 794 was absent while `environments.py` at 780 was listed."""
        named = {path for _, path, _ in self.documented}
        smallest_listed = min((n for n, path in self.measured if path in named), default=0)
        skipped = [
            f"{n} {path}"
            for n, path in self.measured
            if path not in named and n > smallest_listed
        ]
        self.assertEqual(
            skipped, [],
            "the watch-list skips files longer than ones it lists, so it reads as complete when it "
            f"is not: {skipped}",
        )

    def test_negative_control_the_comparison_can_fail(self):
        """Without this, a reader that returned the same set for both sides would pass everything."""
        measured_top = {path for _, path in self.measured[:2]}
        self.assertNotEqual(
            measured_top, {"nothing/real.py", "nothing/else.js"},
            "the ranking compared equal to a set that cannot exist",
        )


if __name__ == "__main__":
    unittest.main()
