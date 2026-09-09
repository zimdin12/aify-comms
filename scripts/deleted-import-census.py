"""Does anything still IMPORT a module this release range deleted?

WHY THIS EXISTS. `docs/V063_ACCEPTANCE_LEDGER.md` rows R-1..R-3 claim "zero remaining importers of
any deleted module". Until this script the ledger cited `no-missing-sibling-imports.test.js` and
`moved-names-resolve.test.js` for it, and review was right that neither proves that claim: the first
asks whether a resolved sibling exists, the second whether a moved NAME resolves. Both are green in a
tree that still imports a module deleted two commits ago from somewhere they do not walk. This
answers the ledger's actual question, over the ledger's actual population.

WHAT A HIT MEANS. A surviving source file whose import/require/dynamic-import specifier names a file
this range deleted. That is a broken import -- it fails loudly at load rather than resolving, which
is the property CLAUDE.md records as deliberate -- so a hit is a defect and an empty result is the
claim the ledger makes.

    python scripts/deleted-import-census.py [since]

BOTH CONTROLS RUN IN THE SAME INVOCATION, because a search that returns nothing looks identical
whether the thing is absent or the instrument is broken. The POSITIVE control asks the same question
about a module that is very much alive and must find importers; the NEGATIVE control asks it about a
name that was never a file and must find none. Exit 1 if a control fails or an importer is found.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SINCE = "aed8b590"

#: The specifier forms this project uses. A bare mention in prose or a comment is not an import, and
#: the whole point of the census is that it counts importers rather than mentions.
#:
#: POSIX ERE, because that is what `git grep -E` speaks: a `(?:...)` group is refused outright, and
#: `re.escape` produces `\-`, which it rejects as an invalid preceding expression. The names here
#: are alphanumerics, dots, dashes and underscores, so escaping the dot is the whole requirement --
#: and a name outside that shape is REFUSED rather than searched for with a pattern nobody checked.
SPECIFIER = "(import|require|from)"
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

#: Sources that could carry an import. Tests are INCLUDED: a test importing a deleted module is the
#: same broken import, and `moved-names-resolve.test.js` exists because that is where it happened.
SOURCE_GLOBS = ("*.js", "*.mjs", "*.cjs", "*.py")


def deleted_modules(since: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--diff-filter=D", "--name-only", f"{since}..HEAD"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"could not diff {since}..HEAD: {out.stderr.strip()[:200]}")
    paths = [p for p in out.stdout.split("\n") if p.strip()]
    return [p for p in paths if Path(p).suffix in (".js", ".mjs", ".cjs", ".py", ".json")]


def importers(basename: str) -> list[str]:
    """Lines in surviving sources whose import/require/from specifier names this file."""
    if not SAFE_NAME.match(basename):
        raise SystemExit(f"{basename!r} is not a shape this census can search for safely")
    pattern = (SPECIFIER + r".*['\"][^'\"]*"
               + basename.replace(".", "[.]") + r"['\"]")
    out = subprocess.run(
        ["git", "grep", "-n", "-E", pattern, "--", *SOURCE_GLOBS],
        cwd=ROOT, capture_output=True, text=True)
    if out.returncode not in (0, 1):
        raise SystemExit(f"the search itself failed for {basename}: {out.stderr.strip()[:200]}")
    return [line for line in out.stdout.split("\n") if line.strip()]


def main() -> int:
    since = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SINCE
    gone = deleted_modules(since)
    if not gone:
        print(f"UNKNOWN: no module was deleted in {since}..HEAD, so this census judged nothing.")
        print("Check the revision rather than reading this as clean.")
        return 1

    # THE PRODUCT SUBSET IS WHAT THE LEDGER'S ROWS COUNT, and the whole set is what this searches.
    # Reporting both is what lets the ledger's 35 reconcile with the population actually judged.
    product = [p for p in gone if "/tests/" not in p and not Path(p).name.startswith("test_")]
    print(f"MODULES DELETED IN {since}..HEAD: {len(gone)}")
    print(f"  of which PRODUCT (the ledger's R-1..R-3 population) : {len(product)}")
    print(f"  of which deleted TESTS, searched too                : {len(gone) - len(product)}")
    hits: dict[str, list[str]] = {}
    for path in gone:
        found = importers(Path(path).name)
        if found:
            hits[path] = found
    print(f"  still imported by a surviving source : {len(hits)}")
    print(f"  no importer anywhere                 : {len(gone) - len(hits)}")
    print()
    if hits:
        print("STILL IMPORTED -- each of these is an import that fails at load:")
        for path, lines in sorted(hits.items()):
            print(f"  {path}")
            for line in lines:
                print(f"      {line}")
        print()

    print("THE CONTROLS, in this same run, because a zero from a broken search reads like a clean")
    print("tree. The positive names a module that is alive and heavily imported; the negative names")
    print("a file that never existed:")
    ok = True
    for probe, expect_importers in (("doctor-predicates.js", True),
                                    ("not-a-real-module-xyz.mjs", False)):
        found = importers(probe)
        agreed = bool(found) == expect_importers
        ok = ok and agreed
        print(f"  {probe:28} {len(found):3} importer(s)  "
              f"{'OK' if agreed else '*** THE SEARCH IS BROKEN ***'}")

    print()
    if not ok:
        print("The census reports nothing, because its own instrument failed a control.")
        return 1
    if hits:
        print(f"NOT CLEAN: {len(hits)} deleted module(s) are still imported.")
        return 1
    print(f"CLEAN: none of the {len(gone)} deleted modules is imported by any surviving source, and")
    print("the same search finds importers for a live module.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
