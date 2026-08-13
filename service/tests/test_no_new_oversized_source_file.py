"""No product source file may exceed 1000 lines unless the reviewer allowlisted it. Python half.

WHY THIS EXISTS. In v0.5.4 a relocation moved a 6-line helper into `service/db.py` — the right subject
owner — and took that file from 995 lines to 1006. `control_plane.py` got smaller and a NEW file went over
the threshold, which is the shell game the oversized-file goal exists to prevent: the rule does not care
WHICH file is oversized. Every gate in this series passed. The undefined-name sweep was clean, the
stale-owner census was clean, `create_app()` built 124 routes, and all three suites were green, because
none of them measures the DESTINATION of a move. The reviewer caught it by reading line counts by hand.

POLICY-OWNED ALLOWLIST, NOT A SELF-MEASURED RATCHET. The first version of this gate inferred its exempt set
from whatever was already oversized, deliberately, because I did not want to encode a policy that was the
reviewer's to set. They then set it: five decision/ceiling files, each with an open packet or a standing
ruling. `oversized-allowlist.json` at the repo root now holds that list and is read by BOTH this gate and
its JS counterpart — one source of truth, because two copies of the same list is the forked-constant class
this whole series has been removing.

PATHS, NOT BASENAMES. The first version matched `p.name`, which would have exempted any file called
`app.js` or `server.js` anywhere in the tree. That hole was found while converting to the reviewer's shape,
not by a test.

The gate asserts both directions: nothing outside the allowlist reaches the limit, AND nothing inside it is
missing or already cleared. The second is what stops the list rotting into names nobody re-checked —
clearing a file FAILS this test until its path is removed, which is a one-line edit with an obvious
meaning, rather than a number someone quietly edits downward every slice as the old `> 20` accessor floor
was.

DELIBERATELY ABSENT: any cap on how much an allowlisted file may GROW. That would be a second, weaker rule
pretending to be this one. The series' own line-count receipts track those.

Scope is non-test Python under `service/`. `mcp/stdio/tests/no-new-oversized-source-file.test.js` covers
the JS roots, where four of the five allowlisted files live.
"""

from __future__ import annotations

import io
import json
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent
REPO = SERVICE.parent
ALLOWLIST_FILE = REPO / "oversized-allowlist.json"

_POLICY = json.loads(io.open(ALLOWLIST_FILE, encoding="utf-8").read())
LIMIT = _POLICY["limit"]
#: Repo-relative POSIX paths, set by the reviewer. Not inferred from the tree.
ALLOWED = {entry["path"] for entry in _POLICY["allowed"]}


def _source_files():
    for path in sorted(SERVICE.rglob("*.py")):
        parts = path.parts
        if "__pycache__" in parts or "tests" in parts:
            continue
        yield path


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _line_count(path: Path) -> int:
    """Newline count, matching `wc -l` — the convention every receipt in this series uses."""
    return len(io.open(path, encoding="utf-8", errors="replace").read().split("\n")) - 1


class NoNewOversizedSourceFileTests(unittest.TestCase):
    def test_no_python_file_outside_the_allowlist_is_oversized(self):
        offenders = [
            f"{_rel(p)}: {_line_count(p)} lines"
            for p in _source_files()
            if _rel(p) not in ALLOWED and _line_count(p) >= LIMIT
        ]
        self.assertEqual(
            offenders,
            [],
            "A file crossed the 1000-line limit. If a relocation did this, the destination is wrong even "
            "when its SUBJECT is right — reducing one file by growing another past the limit is not "
            "progress. Adding it to oversized-allowlist.json is a REVIEWER decision, not a fix:\n  "
            + "\n  ".join(offenders),
        )

    def test_the_allowlist_has_no_stale_python_entries(self):
        """A cleared file must be REMOVED, or the allowlist rots into unchecked names."""
        stale = []
        for rel in sorted(ALLOWED):
            if not rel.endswith(".py"):
                continue  # the JS gate owns those entries
            path = REPO / rel
            if not path.exists():
                stale.append(f"{rel}: no longer exists")
            elif _line_count(path) < LIMIT:
                stale.append(f"{rel}: now {_line_count(path)} lines — drop it from oversized-allowlist.json")
        self.assertEqual(stale, [], "\n  ".join([""] + stale))

    def test_the_allowlist_is_well_formed_and_reasoned(self):
        """Every exemption carries a reason, because an unexplained one is indistinguishable from a mistake."""
        self.assertTrue(_POLICY["allowed"], "the allowlist must not be silently empty")
        for entry in _POLICY["allowed"]:
            self.assertIn("path", entry)
            self.assertTrue(entry.get("reason", "").strip(), f"{entry['path']} has no recorded reason")
            self.assertNotIn("\\", entry["path"], "paths must be repo-relative POSIX")

    def test_the_allowlist_matches_on_path_not_basename(self):
        """Guards the hole the first version had: a basename match exempts every same-named file.

        Asserted as a property of the entries rather than by planting a decoy file: every path contains a
        directory separator, so no entry can be read as a bare filename.
        """
        for rel in ALLOWED:
            self.assertIn("/", rel, f"{rel} looks like a basename; the allowlist is path-keyed")

    def test_the_scan_reaches_the_files_it_claims_to_cover(self):
        """A gate over an empty file list passes vacuously."""
        found = {_rel(p) for p in _source_files()}
        self.assertIn("service/control_plane.py", found)
        self.assertIn("service/db.py", found)
        self.assertNotIn("service/tests/test_no_new_oversized_source_file.py", found, "tests are out of scope")

    def test_the_boundary_predicate_is_exact(self):
        """Off-by-one here would silently accept the precise 1000-line file this exists to catch."""
        self.assertTrue(1000 >= LIMIT)
        self.assertFalse(999 >= LIMIT)


if __name__ == "__main__":
    unittest.main()
