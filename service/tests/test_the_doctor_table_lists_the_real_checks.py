"""CLAUDE.md's doctor table names exactly the checks the doctor emits.

WHY THIS ONE EARNS A GATE, when most doc drift does not. CLAUDE.md loads into every Claude Code
session on this repo, so a wrong row there is paid on every session — the same argument that justifies
gating skill tool names. And `aify-comms doctor` is the tool this repo tells everyone to trust instead
of assuming a deploy worked, so its map being wrong is worse than most maps being wrong.

The failure has happened here. CLAUDE.md records `bridge-current` vanishing from the output entirely —
"not a skip, not a failure, absent. An operator counting checks saw ten" — which is a check that the
documentation promised and the run did not produce. This compares the two sets directly.

Measured 2026-08-25: nine emitted, nine documented, identical. The four checks that moved to
aify-wrapper and aify-env (wrappers, wrapper-current, runtimes, bridge-terminal) are correctly absent
from both sides, which is the interesting half — the table was updated when they left.

BOTH DIRECTIONS, because they mislead differently: a documented check that no longer runs sends an
operator hunting for a row that will never appear, and an emitted check nobody documents is a result
with no explanation of what it caught.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCTOR = ROOT / "mcp" / "stdio" / "doctor.js"
CLAUDE_MD = ROOT / "CLAUDE.md"

#: How a check reports itself. Ids arrive as the first argument to these reporters.
REPORTERS = ("add", "skip", "ok", "fail", "warn")


def emitted_check_ids() -> set[str]:
    source = DOCTOR.read_text(encoding="utf-8")
    found: set[str] = set()
    for reporter in REPORTERS:
        found |= set(re.findall(rf"""\b{reporter}\(\s*["']([a-z][a-z0-9-]{{2,30}})["']""", source))
    return found


def documented_check_ids() -> set[str]:
    """The first column of the doc's check table -- a row whose first cell is a backticked id."""
    return set(re.findall(r"^\|\s*`([a-z][a-z0-9-]{2,30})`\s*\|", CLAUDE_MD.read_text(encoding="utf-8"), re.M))


class TheDoctorTableListsTheRealChecks(unittest.TestCase):
    def test_both_scans_find_something(self):
        """The control. Two empty sets agree perfectly, and the tests below would pass while proving
        nothing — which is the exact shape of every wrong zero this round has produced."""
        self.assertGreaterEqual(len(emitted_check_ids()), 5, "the doctor scan found almost nothing")
        self.assertGreaterEqual(len(documented_check_ids()), 5, "the doc scan found almost nothing")
        self.assertIn("env-bridge", emitted_check_ids(), "the doctor scan missed a check that exists")
        self.assertIn("env-bridge", documented_check_ids(), "the doc scan missed a row that exists")

    def test_the_scans_can_say_no(self):
        self.assertNotIn("zzz-not-a-check", emitted_check_ids())
        self.assertNotIn("zzz-not-a-check", documented_check_ids())

    def test_no_documented_check_has_stopped_running(self):
        missing = sorted(documented_check_ids() - emitted_check_ids())
        self.assertEqual(
            missing, [],
            "CLAUDE.md documents these checks and the doctor no longer emits them, so an operator "
            "counting rows will wait for output that never comes: " + ", ".join(missing),
        )

    def test_no_running_check_is_undocumented(self):
        undocumented = sorted(emitted_check_ids() - documented_check_ids())
        self.assertEqual(
            undocumented, [],
            "the doctor emits these and CLAUDE.md's table explains none of them, so a red row arrives "
            "with no account of what it caught: " + ", ".join(undocumented),
        )

    def test_the_checks_that_moved_to_other_tiers_are_gone_from_both(self):
        """The interesting half. wrappers, wrapper-current and runtimes went to aify-wrapper and
        bridge-terminal to aify-env; a second implementation of one question does not agree for free.
        If one reappears here, the boundary in docs/AIFY_ENV_BOUNDARY.md has been crossed back."""
        moved = {"wrappers", "wrapper-current", "runtimes", "bridge-terminal"}
        self.assertEqual(
            sorted(moved & emitted_check_ids()), [],
            "a check that belongs to another tier is being answered here again",
        )


if __name__ == "__main__":
    unittest.main()
