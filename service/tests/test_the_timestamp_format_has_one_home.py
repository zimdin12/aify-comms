"""The UTC timestamp format is declared once, in `service/clock.py`, and nowhere else.

WHY IT MATTERS HERE SPECIFICALLY, in that module's own words: this is "the format every timestamp
column in this service stores and every comparison assumes. Changing it is a data migration, not a
formatting choice: stored timestamps are compared LEXICALLY in SQL throughout."

A site that retypes the literal and drifts does not raise. It produces a cutoff that compares wrong
against stored values -- silently, in a WHERE clause -- and this repo has already paid for that class
of bug more than once. Two of the sites found here build exactly such a cutoff:
`managed_env.py` bounds the in-flight spawn window, and `spawn_terminal_settlement.py` bounds the
orphan grace period. Both compare their hand-built string against `updated_at` / `created_at`.

MEASURED 2026-08-28: `ISO_SECONDS` was declared in `clock.py` and the literal was typed out at TEN
further product sites. All ten now import it. Nothing was broken -- every copy was byte-identical, so
this is a defect with a delay on it rather than a live one, and the delay is what the gate removes.

HOW IT WAS FOUND, since the route matters more than the result. A sweep of every timestamp-like
column in the live database asked whether any stored value was NAIVE rather than `Z`-suffixed: 47
columns with data, 46 uniformly `Z`. The one exception, `spawn_requests.finished_at`, turned out to
be 152 rows sharing a single identical timestamp -- one bulk migration on 2026-05-25, no live writer,
and `dispatch_runs.finished_at` clean across 21,753 rows. So the DATA was fine; looking for who
produces that format is what surfaced the eleven producers.

A MEASUREMENT OF MINE WAS WRONG ON THE WAY, and it is worth recording. The first sample reported "36
of 40 naive" for that column, which reads like an ongoing writer. `LIMIT 40` with no `ORDER BY`
returns the head of the table, which is exactly where the legacy rows live. Counting the whole column
gave 152 of 941 and a single distinct value.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "service"

#: The one file allowed to spell it.
HOME = SERVICE / "clock.py"

#: The literal itself, built rather than written, so this file does not become an eleventh copy of
#: the thing it is banning.
FORMAT = "%Y-%m-%d" + "T" + "%H:%M:%S" + "Z"


def _product_sources():
    for path in SERVICE.rglob("*.py"):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        yield path


class TheTimestampFormatHasOneHomeTests(unittest.TestCase):
    def test_the_home_declares_it(self) -> None:
        """Positive control. If `clock.py` stopped declaring the format, the sweep below would report
        a clean repo while every caller imported a name that no longer existed."""
        text = HOME.read_text(encoding="utf-8")
        self.assertIn(f'ISO_SECONDS = "{FORMAT}"', text, "clock.py no longer declares ISO_SECONDS")

    def test_the_scan_reaches_the_files_it_claims_to(self) -> None:
        """Second control. An empty walk agrees with a clean repo, and this repo has produced that
        exact wrong zero before -- a Python size gate read `service/**` only and left fifteen files
        ungoverned, invisibly."""
        paths = list(_product_sources())
        self.assertGreater(len(paths), 100, f"only {len(paths)} product modules walked")
        self.assertIn(HOME, paths, "the walk does not even reach clock.py")
        names = {p.name for p in paths}
        for expected in ("managed_env.py", "ntfy.py", "usage_openai.py"):
            self.assertIn(expected, names, f"the walk misses {expected}")

    def test_no_other_product_module_spells_the_format(self) -> None:
        offenders = []
        for path in _product_sources():
            if path == HOME:
                continue
            if FORMAT in path.read_text(encoding="utf-8", errors="replace"):
                offenders.append(str(path.relative_to(ROOT)).replace("\\", "/"))
        self.assertEqual(
            sorted(offenders), [],
            "these retype the UTC timestamp format instead of importing clock.ISO_SECONDS. A copy "
            "that drifts produces a SQL comparison that is wrong with no error, because stored "
            "timestamps are compared lexically: " + ", ".join(sorted(offenders)),
        )

    def test_the_importers_actually_import_it(self) -> None:
        """The other half. Deleting a literal and forgetting the import is a NameError at runtime on
        whichever branch happens to run, which on a reconciler can be hours after the deploy."""
        missing = []
        pattern = re.compile(r"\bISO_SECONDS\b")
        for path in _product_sources():
            if path == HOME:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if not pattern.search(text):
                continue
            if "from service.clock import" not in text:
                missing.append(str(path.relative_to(ROOT)).replace("\\", "/"))
        self.assertEqual(sorted(missing), [], "these use ISO_SECONDS without importing it")

    def test_at_least_one_module_imports_it(self) -> None:
        """Anti-vacuity for the case above: if nothing imported the constant, that test would pass
        by having nothing to check, and so would the ban, and the format would live in no file at
        all except its declaration."""
        importers = [
            p for p in _product_sources()
            if p != HOME and "from service.clock import" in p.read_text(encoding="utf-8", errors="replace")
            and re.search(r"\bISO_SECONDS\b", p.read_text(encoding="utf-8", errors="replace"))
        ]
        self.assertGreaterEqual(
            len(importers), 5,
            f"only {len(importers)} modules import ISO_SECONDS; the consolidation has been undone",
        )


if __name__ == "__main__":
    unittest.main()
