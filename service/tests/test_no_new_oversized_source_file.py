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
import os
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent
REPO = SERVICE.parent
ALLOWLIST_FILE = REPO / "oversized-allowlist.json"

#: Pruned at the DIRECTORY level, so a repo-wide walk never descends into node_modules or .git.
SKIP_DIRS = frozenset(
    {"__pycache__", "tests", "node_modules", ".git", "fixtures", ".pytest_cache", ".venv", "venv"}
)

_POLICY = json.loads(io.open(ALLOWLIST_FILE, encoding="utf-8").read())
LIMIT = _POLICY["limit"]
#: Repo-relative POSIX paths, set by the reviewer. Not inferred from the tree.
ALLOWED = {entry["path"] for entry in _POLICY["allowed"]}


def _source_files(root: Path = REPO, skip=SKIP_DIRS):
    """Every non-test Python file in the repo, pruned at the directory level.

    REPO-WIDE, and it was not until 2026-08-15. This scanned `service/**` only, which left FIFTEEN
    Python files outside the gate entirely: `mcp/sse_server.py` — 730 lines, and the SSE transport
    shipped inside the container — the hermes plugin under `integrations/`, and every script under
    `scripts/`. None of them was oversized, which is precisely why nobody noticed: an unguarded
    population reports green for the same reason a guarded one does, and the difference is invisible
    from the result. The goal this gate serves says "every non-test source file", not "every file
    under one chosen root".

    The root was not wrong so much as unstated — a new module under `service/` was covered and an
    identical one beside the SSE transport was not, and nothing said so. Walking from the repo root
    makes coverage a property of the scan rather than a coincidence of where the code was put.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for name in sorted(filenames):
            if name.endswith(".py") and not name.startswith("test_"):
                yield Path(dirpath) / name


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _line_count(path: Path) -> int:
    """Newline count, matching `wc -l` — the convention every receipt in this series uses."""
    return len(io.open(path, encoding="utf-8", errors="replace").read().split("\n")) - 1


def is_exempt(rel_path: str, allowed=None) -> bool:
    """Is this repo-relative path allowlisted? A PURE predicate, so identity can be tested directly.

    Split out on the reviewer's request. The gate's whole correctness rests on this being path identity
    rather than name identity, and asserting that against the real tree cannot demonstrate it — the tree
    happens to contain only one `control_plane.py`. A pure predicate can be shown two synthetic paths that
    share a basename and prove they are distinguished.
    """
    return rel_path in (ALLOWED if allowed is None else set(allowed))


class NoNewOversizedSourceFileTests(unittest.TestCase):
    def test_no_python_file_outside_the_allowlist_is_oversized(self):
        offenders = [
            f"{_rel(p)}: {_line_count(p)} lines"
            for p in _source_files()
            if not is_exempt(_rel(p)) and _line_count(p) >= LIMIT
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
        """Every exemption carries a reason, because an unexplained one is indistinguishable from a mistake.

        `assertTrue(_POLICY["allowed"])` used to stand here, guarding against the list being silently
        emptied to make a red gate green. On 2026-08-14 the last entry earned its way off — every
        product source file is now under the limit — and that guard turned into a test failing because
        the goal was reached. An EMPTY list is the intended end state, so what is asserted now is that
        it is a list at all and that whatever is in it is explained.
        """
        self.assertIsInstance(_POLICY["allowed"], list)
        for entry in _POLICY["allowed"]:
            self.assertIn("path", entry)
            self.assertTrue(entry.get("reason", "").strip(), f"{entry['path']} has no recorded reason")
            self.assertNotIn("\\", entry["path"], "paths must be repo-relative POSIX")

    def test_two_files_sharing_a_basename_are_distinguished(self):
        """The defect this gate shipped with, pinned as a property of the predicate.

        The first version keyed on `p.name`, so ANY file with an allowlisted basename anywhere was
        exempt. The real tree cannot demonstrate the fix — it contains exactly one of each — so the
        predicate is exercised with synthetic paths that share a basename.

        IT USED TO DRIVE THE PREDICATE FROM THE REAL POLICY, and that failed twice for the same
        reason: the list only ever shrinks. First it named `service/control_plane.py`, which went red
        in v0.5.4 when that file dropped under the limit and was correctly removed. Then it read
        `_POLICY["allowed"][0]`, which went red when `app.js` — the LAST entry — dropped under the
        limit and the list became EMPTY. Both times a test failed because the work succeeded.

        A property of a pure predicate does not need the live policy at all, so it no longer reads it.
        """
        allowed = ["service/new_dashboard/app.js"]
        basename = "app.js"
        self.assertTrue(is_exempt(allowed[0], allowed), "the allowlisted path must be exempt")
        for impostor in (
            f"service/routers/{basename}",
            f"service/api_core/{basename}",
            basename,
            f"{allowed[0]}.bak",
            f"other/{allowed[0]}",
        ):
            self.assertFalse(
                is_exempt(impostor, allowed),
                f"{impostor} shares a basename with an allowlisted file and must NOT inherit its exemption",
            )

    def test_an_EMPTY_allowlist_exempts_nothing(self):
        """The state the repo reached on 2026-08-14, when the last entry earned its way off.

        An empty list is the goal, not a degenerate case — but it is also the input most likely to be
        mishandled by a predicate written as "no rule means allow". It must mean the opposite.
        """
        self.assertFalse(is_exempt("service/new_dashboard/app.js", []))
        self.assertFalse(is_exempt("anything/at/all.py", []))

    def test_the_allowlist_entries_are_paths_not_basenames(self):
        """A bare filename in the JSON would silently reintroduce basename matching."""
        for rel in ALLOWED:
            self.assertIn("/", rel, f"{rel} looks like a basename; the allowlist is path-keyed")

    def test_the_scan_reaches_the_files_it_claims_to_cover(self):
        """A gate over an empty file list passes vacuously."""
        found = {_rel(p) for p in _source_files()}
        self.assertIn("service/control_plane.py", found)
        self.assertIn("service/db.py", found)
        self.assertNotIn("service/tests/test_no_new_oversized_source_file.py", found, "tests are out of scope")

    def test_the_scan_covers_python_OUTSIDE_service(self):
        """The hole this gate shipped with, named file by file.

        Each of these was invisible to the gate until 2026-08-15, and one of them ships inside the
        container. Naming them individually rather than asserting a count keeps the test meaningful
        as files come and go — the mistake the old accessor floor made — and makes a regression to a
        `service/`-only scan say exactly what stopped being measured.
        """
        found = {_rel(p) for p in _source_files()}
        for rel in (
            "mcp/sse_server.py",
            "scripts/undefined_name_sweep.py",
            "integrations/hermes-aify-plugin/aify_hermes_plugin/patches.py",
        ):
            self.assertIn(rel, found, f"{rel} is product Python and must be governed by the size limit")

    def test_the_walk_prunes_rather_than_filters(self):
        """`node_modules` and `.git` must never be DESCENDED, not merely dropped from the results.

        A filter-after-walk produces the same list and takes seconds doing it. The distinction is not
        cosmetic: `mcp/stdio/node_modules` is the largest directory in the repo, and a gate slow
        enough to notice is a gate somebody eventually marks slow and skips.
        """
        seen = {part for p in _source_files() for part in _rel(p).split("/")[:-1]}
        for pruned in ("node_modules", ".git", "__pycache__", "tests", "fixtures"):
            self.assertNotIn(pruned, seen, f"{pruned} must be pruned at the directory level")

    def test_shell_and_css_are_NOT_covered_and_that_is_a_decision(self):
        """What this gate does not measure, said out loud rather than left to be discovered.

        `install.sh` is 3,049 lines and `service/new_dashboard/styles.css` is 1,839 — both
        non-test source, both over the limit, both outside every gate in this repo. That is not an
        oversight being hidden here; it is an open REVIEWER question, because widening the population
        to those languages turns two files red and the remedy for each is a different kind of work
        than the Python and JS decomposition this series did.

        THE FIGURES ABOVE WERE WRONG IN FOUR PLACES until 2026-08-29 — three copies said 4,370 and
        1,844, and CLAUDE.md said 2,978, against a real 3,049 and 1,839. A number in prose rots, so
        the claim the decision RESTS on is asserted below rather than remembered: both files exist
        and are over the limit. The digits are dated; the argument is checked.

        If shell or CSS is later brought in scope, this is the test that must be deleted in the same
        change — which is the point: the exclusion cannot rot into something nobody re-decided.
        """
        found = {_rel(p) for p in _source_files()}
        self.assertNotIn("install.sh", found)
        self.assertFalse([f for f in found if f.endswith((".sh", ".css"))], "this gate is Python-only")
        # THE PREMISE, MEASURED. If either file were under the limit the exclusion would be moot, and
        # a reader would have no way to tell that from the prose.
        for name in ("install.sh", "service/new_dashboard/styles.css"):
            path = REPO / name
            self.assertTrue(path.exists(), f"{name} no longer exists; this exclusion is about nothing")
            lines = path.read_bytes().count(b"\n")
            self.assertGreaterEqual(lines, LIMIT, (
                f"{name} is {lines} lines, under the {LIMIT}-line limit -- the exclusion it "
                "justifies is no longer excluding anything oversized"
            ))

    def test_the_boundary_predicate_is_exact(self):
        """Off-by-one here would silently accept the precise 1000-line file this exists to catch."""
        self.assertTrue(1000 >= LIMIT)
        self.assertFalse(999 >= LIMIT)


if __name__ == "__main__":
    unittest.main()
