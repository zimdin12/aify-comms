"""A gate that only exists in one person's working directory is not a gate.

THE GENERALISATION OF A REAL DEFECT. `service/tests/data/` was gitignored — a bare `data/` pattern
written for the persisted Docker volume also matched it — so the three snapshots that
`test_route_inventory.py` and `test_route_metadata_inventory.py` compare against were UNTRACKED.

The consequence was not a red test. It was worse:

  - locally, the gates passed, reading files that existed only in one working directory;
  - from a clean clone they raised FileNotFoundError, so nobody else could run them at all;
  - and every "route surface unchanged" claim made from that checkout was evidence about a machine
    rather than about the commit.

`test_route_inventory.py` shipped in v0.5 with this defect, so the API-surface gate that release
leaned on had never been runnable by anyone else. It was found by a reviewer trying to reproduce a
result from an archive of the commit, which is the only way it could have been found.

This is one level beyond the false-green class the repo already knows (`no-evidence-is-not-a-pass`):
not a check that reports ok without evidence, but a check that CANNOT RUN where it matters while
reporting green where it does not.

So: every fixture a test reads must be tracked by git. This test is cheap, and it fails at the
moment the mistake is made rather than the next time somebody clones.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

#: Data-ish files a test might read. Deliberately not source files — importing those would fail
#: loudly anyway, whereas a missing data file is exactly the silent case this exists for.
FIXTURE_RE = re.compile(r"""["']([\w./\-]+\.(?:txt|json|jsonl|csv|yaml|yml|sql))["']""")

SEARCH_GLOBS = (
    "service/tests/**/*.py",
    "mcp/stdio/tests/**/*.js",
    "service/new_dashboard/*.test.mjs",
)


def _tracked_files() -> set[str] | None:
    """Tracked paths, or None when there is no git metadata to ask.

    THIS GATE NEEDS `.git`, which is a real limit and worth stating rather than discovering. It
    passes in a clean CLONE and cannot run in a bare `git archive` extraction — the very shape the
    reviewer used to catch the bug this test exists for. So a missing repository is a documented
    SKIP with a stated reason, not a failure and definitely not a silent pass: an environment that
    cannot gather the evidence must say so out loud, per this repo's standing rule that no evidence
    is not a pass.
    """
    if not (REPO / ".git").exists():
        return None
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def _named_fixtures() -> list[tuple[str, str]]:
    """(fixture_path, test_that_names_it) for every fixture path that actually exists on disk."""
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pattern in SEARCH_GLOBS:
        for test_path in REPO.glob(pattern):
            if "__pycache__" in test_path.parts or "node_modules" in test_path.parts:
                continue
            text = test_path.read_text(encoding="utf-8", errors="replace")
            for match in FIXTURE_RE.finditer(text):
                candidate = match.group(1)
                if candidate.startswith("http") or "/" not in candidate:
                    continue
                for base in (test_path.parent, REPO):
                    resolved = (base / candidate).resolve()
                    if not resolved.exists() or not resolved.is_file():
                        continue
                    try:
                        rel = resolved.relative_to(REPO).as_posix()
                    except ValueError:
                        break
                    key = (rel, test_path.relative_to(REPO).as_posix())
                    if key not in seen:
                        seen.add(key)
                        found.append(key)
                    break
    return found


class FixturesAreTrackedTests(unittest.TestCase):
    def test_every_fixture_a_test_reads_is_tracked_by_git(self):
        tracked = _tracked_files()
        if tracked is None:
            self.skipTest("no .git metadata here (archive extraction) - this gate needs a checkout")
        offenders = [
            f"{fixture}  (read by {test})"
            for fixture, test in _named_fixtures()
            if fixture not in tracked
        ]
        self.assertEqual(
            offenders,
            [],
            "These fixtures exist locally but are NOT in git, so the tests reading them pass here "
            "and fail on a clean clone:\n  "
            + "\n  ".join(offenders)
            + "\nUsually a .gitignore pattern matching more than it meant to. Track the file (or "
            "narrow the pattern) — do not delete the assertion.",
        )

    def test_the_route_surface_snapshots_specifically_are_tracked(self):
        """Named explicitly, because these are the ones it already happened to.

        The generic check above would catch them, but a named assertion says which files the repo
        considers load-bearing, and survives someone rewriting the discovery logic.
        """
        tracked = _tracked_files()
        if tracked is None:
            self.skipTest("no .git metadata here (archive extraction) - this gate needs a checkout")
        for name in (
            "route_inventory.txt",
            "route_metadata_inventory.txt",
            "route_owner_map.txt",
        ):
            with self.subTest(name):
                self.assertIn(f"service/tests/data/{name}", tracked)

    def test_the_sweep_actually_finds_fixtures(self):
        """A tracking check over an empty list passes vacuously — the same class of nothing-burger
        this whole test exists to prevent.

        THE FLOOR CAME DOWN 3 -> 2 on 2026-09-05, and the reason is a shrinking POPULATION rather
        than a broken instrument. The v0.6.2 residue deletion removed 62 files belonging to the
        retired environment-bridge tier, and one of them named a fixture. What the sweep still finds
        was checked by hand at the same time -- `mcp/stdio/package-lock.json` and
        `service/contracts/operator_notify_cases.json`, each named by a live test -- so the
        discovery demonstrably still works, which is the only thing this floor is for.

        If it ever reaches ZERO the instrument IS broken, and that is what this catches.
        """
        self.assertGreaterEqual(
            len(_named_fixtures()), 2, "the fixture discovery found almost nothing; it is broken"
        )


if __name__ == "__main__":
    unittest.main()
