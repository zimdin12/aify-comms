"""Which changed PRODUCT files have evidence, and which have none.

WHY THIS EXISTS. Review's product-scope verdict was "NOT YET CERTIFIED", meaning coverage is
incomplete rather than that a shipping defect was found -- and its recommendation was a "finite
acceptance-to-evidence ledger" rather than more rounds of instrument strengthening. This builds it:
for every product file changed in the release range, the tests that name it.

WHAT A ROW MEANS, and the limit is the whole point. A file is COVERED here when some test names it.
That is a claim about the existence of evidence, NOT about its quality: a test naming a file may
exercise one branch of it, or may only assert that it parses. The value is the other column -- a
changed product file that NO test names anywhere is a gap nobody has to argue about.

WHAT IS EXCLUDED, and why each is not a hole:
  scripts/, docs/, *.md   measurement instruments and prose, which do not ship
  tests, fixtures          the evidence itself
  lockfiles, .gitignore    not behaviour

    python scripts/acceptance-ledger.py [since]

`since` defaults to the range review named. Exit 0 always: this reports, and the decision about what
an uncovered file needs is a person's.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SINCE = "aed8b590"

#: Not product: instruments, prose, evidence, and files that carry no behaviour.
SKIP = re.compile(
    r"^(scripts/|docs/)"
    r"|(^|/)tests?/"
    r"|(^|/)fixtures/"
    r"|test_[^/]*$"
    r"|\.test\.[cm]?js$"
    r"|\.md$"
    r"|(^|/)(package-lock\.json|\.gitignore|oversized-allowlist\.json)$"
)


def changed(since: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", f"{since}..HEAD"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"could not diff {since}..HEAD: {out.stderr.strip()[:200]}")
    return [p for p in out.stdout.split("\n") if p.strip() and not SKIP.search(p)]


def tests_naming(path: str) -> list[str]:
    """Test files that mention this file by basename.

    BASENAME, NOT PATH, because a test names `terminal_write_queue` through an import rather than as
    a path string. That is looser than a call-graph and is stated as such: this answers "is there
    evidence pointed at this file", not "is that evidence good".
    """
    stem = Path(path).name
    module = Path(path).stem
    hits: set[str] = set()
    for needle in {stem, module}:
        out = subprocess.run(["git", "grep", "-l", "-F", needle, "--",
                              "service/tests", "mcp/stdio/tests", "*.test.mjs", "*.test.js"],
                             cwd=ROOT, capture_output=True, text=True)
        if out.returncode == 0:
            hits.update(l for l in out.stdout.split("\n") if l.strip())
    return sorted(hits)


def main() -> int:
    since = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SINCE
    files = changed(since)
    if not files:
        print(f"UNKNOWN: no product files changed in {since}..HEAD, which is implausible for a")
        print("release range -- check the revision rather than reading this as clean.")
        return 0

    # A DELETED FILE IS NOT A COVERAGE GAP, and the first run of this called fifteen of them gaps.
    # Every one was the environment-bridge tier v0.6.2 retired: `git diff --name-only` lists a
    # deletion exactly like a modification, so "changed and no test names it" swept them in. A
    # removed file needs no evidence; what it needs is for nothing to still import it, which is a
    # different gate's question.
    covered, bare, removed = [], [], []
    for path in files:
        if not (ROOT / path).exists():
            removed.append(path)
        elif tests_naming(path):
            covered.append(path)
        else:
            bare.append(path)

    print(f"PRODUCT FILES CHANGED IN {since}..HEAD: {len(files)}")
    print(f"  surviving, named by at least one test : {len(covered)}")
    print(f"  surviving, named by NO test           : {len(bare)}")
    print(f"  DELETED in this range                 : {len(removed)}")
    print()
    if bare:
        print("NAMED BY NO TEST -- each is a surviving shipping file with no evidence pointed at it:")
        for path in bare:
            print(f"  {path}")
    else:
        print("Every SURVIVING changed product file is named by at least one test.")
    if removed:
        print()
        print(f"Deleted ({len(removed)}), which need no evidence -- listed so the count above is")
        print("readable rather than mysterious:")
        for path in removed:
            print(f"  {path}")

    print()
    print("A CONTROL, so the coverage column means something. A file certainly covered must show as")
    print("covered, and a name that is not a file must show as bare:")
    for probe, expect in (("service/terminal_write_queue.py", True),
                          ("service/not_a_real_module_xyz.py", False)):
        got = bool(tests_naming(probe))
        print(f"  {probe:44} {'covered' if got else 'bare':8} "
              f"{'OK' if got == expect else '*** the scan is broken ***'}")

    print()
    print("WHAT THIS IS NOT: a quality judgement. A file counts as covered when a test NAMES it,")
    print("which does not establish that the test exercises the change, or any branch of it. The")
    print("bare column is the one that needs no argument.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
