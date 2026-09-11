"""A test file nobody runs is not coverage, and it looks exactly like coverage.

WHAT THIS CAUGHT, 2026-09-11. `scripts/tests/` held three files and 29 tests -- the installer
inventory, the legacy-upgrade path and the onboarding guides -- and NOTHING RAN THEM. The documented
invocation in CLAUDE.md is `python -m pytest service/tests`, which does not collect them, and the
only reference to that directory anywhere was one line of prose in `docs/INSTALL_ONBOARDING.md`
pointing at a single file. They had been green-by-never-executing since the day they were written,
and a regression added to them would have been invisible.

THE SAME SHAPE HAS NOW APPEARED TWICE IN ONE DAY. The other was
`service/new_dashboard/fixtures/messenger-browser.mjs`, the only thing that exercises the real
message sanitizer, referenced solely by a comment in the test that points at it. Twice is a
mechanism, not bad luck: a test lands beside the code it covers, the suite invocation names
directories rather than discovering them, and nobody notices the gap because an unrun test and a
passing test both say nothing.

WHY GATE THE DOCUMENTED COMMAND RATHER THAN JUST ADDING A PATH. Adding `scripts/tests` to the
invocation fixes today and nothing else; the next directory lands in the same blind spot. This
compares the population that EXISTS against the population the documented command COLLECTS, so the
next orphan fails here on the day it is written and names itself.

WHAT IT DOES NOT CLAIM. It does not check that the tests are good, that they assert anything, or
that anyone runs the documented command. It answers one question -- would this file be collected by
the invocation this repo tells people to run -- and that is the question that was being answered
wrong.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The gates' own convention, so this judges the same tree they do.
SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".pytest_cache", ".venv", "venv", "fixtures"}


def documented_pytest_paths(claude_md: str) -> list[str]:
    """The path arguments of the pytest invocation CLAUDE.md tells people to run.

    Flags and their values are dropped; what remains is the population the command collects.
    """
    for line in claude_md.splitlines():
        stripped = line.strip()
        if not stripped.startswith("python -m pytest "):
            continue
        tokens = stripped.split("#", 1)[0].split()[3:]
        paths: list[str] = []
        skip_next = False
        for token in tokens:
            if skip_next:
                skip_next = False
                continue
            if token.startswith("-"):
                # `-n 8` carries a separate value; `--dist=loadfile` does not.
                skip_next = token in {"-n", "-k", "-m", "-p", "--dist"}
                continue
            paths.append(token)
        if paths:
            return paths
    return []


def uncollected(test_files: list[str], documented: list[str]) -> list[str]:
    """Which test files no documented path would collect. Pure, so the controls are cheap."""
    prefixes = [p.rstrip("/") + "/" for p in documented]
    return sorted(f for f in test_files if not any(f.startswith(p) for p in prefixes))


def _repo_test_files() -> list[str]:
    found = []
    for path in REPO_ROOT.rglob("test_*.py"):
        if SKIP_DIRS & set(path.relative_to(REPO_ROOT).parts):
            continue
        found.append(path.relative_to(REPO_ROOT).as_posix())
    return sorted(found)


class EveryPythonTestIsActuallyRunTests(unittest.TestCase):
    def test_the_documented_invocation_collects_every_test_file_in_the_repo(self):
        documented = documented_pytest_paths((REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertTrue(documented, "CLAUDE.md no longer states a pytest invocation this can read")
        files = _repo_test_files()
        # POSITIVE CONTROL on the walk itself: a walk that silently stopped finding files would
        # report zero orphans forever, which is the same green as a healthy repo.
        self.assertGreater(len(files), 100, "the test-file walk found almost nothing; it is broken")
        missed = uncollected(files, documented)
        self.assertEqual(
            missed, [],
            "these test files exist and the documented pytest invocation would not collect them, so "
            "they are green by never executing: " + ", ".join(missed),
        )

    def test_the_parser_reads_the_paths_and_drops_the_flags(self):
        line = "python -m pytest service/tests scripts/tests -q -n 8 --dist loadfile # 5671 tests"
        self.assertEqual(documented_pytest_paths(line), ["service/tests", "scripts/tests"])
        # `-n 8` must not leave `8` behind as a path, and `--dist loadfile` must not leave `loadfile`.
        self.assertNotIn("8", documented_pytest_paths(line))
        self.assertNotIn("loadfile", documented_pytest_paths(line))
        self.assertEqual(documented_pytest_paths("nothing here"), [])

    def test_the_detector_can_say_no(self):
        # Drive the control by REMOVING what it watches: the same file list against a documented
        # population that no longer names its directory must be reported, or this gate is decoration.
        files = ["service/tests/test_a.py", "scripts/tests/test_b.py"]
        self.assertEqual(uncollected(files, ["service/tests", "scripts/tests"]), [])
        self.assertEqual(uncollected(files, ["service/tests"]), ["scripts/tests/test_b.py"])
        # A prefix must not match a sibling directory that merely starts the same way.
        self.assertEqual(uncollected(["service/tests-extra/test_c.py"], ["service/tests"]),
                         ["service/tests-extra/test_c.py"])
