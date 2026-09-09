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

import ast
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SINCE = "aed8b590"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comment_spans import JS_SUFFIXES, PY_SUFFIXES   # noqa: E402  (path set above)

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



#: V8 parsing each test file WITHOUT executing it, and reporting its static import specifiers.
#: Written to a scratch file per run: it is an instrument's instrument, not repo source.
SPECIFIER_HARNESS = """
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
const files = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const out = {};
for (const f of files) {
  try {
    const m = new vm.SourceTextModule(readFileSync(f.abs, 'utf8'), { identifier: f.rel });
    out[f.rel] = m.dependencySpecifiers;
  } catch { out[f.rel] = null; }
}
console.log(JSON.stringify(out));
"""


def python_modules_imported_by_tests() -> set[str]:
    """Dotted module names any Python test file imports, read with `ast`."""
    imported: set[str] = set()
    for test in sorted((ROOT / "service" / "tests").glob("test_*.py")):
        try:
            tree = ast.parse(test.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imported


def js_paths_imported_by_tests() -> tuple[set[str], int, int]:
    """Repo-relative paths any JS test file STATICALLY imports, plus (parsed, unparsed) counts."""
    tests = sorted(list((ROOT / "mcp" / "stdio" / "tests").rglob("*.test.js"))
                   + list((ROOT / "service" / "new_dashboard").glob("*.test.mjs")))
    jobs = [{"rel": p.relative_to(ROOT).as_posix(), "abs": str(p)} for p in tests]
    if not jobs:
        return set(), 0, 0
    scratch = Path(tempfile.mkdtemp())
    harness = scratch / "specifiers.mjs"
    harness.write_text(SPECIFIER_HARNESS, encoding="utf-8")
    payload = scratch / "tests.json"
    payload.write_text(json.dumps(jobs), encoding="utf-8")
    out = subprocess.run(["node", "--experimental-vm-modules", str(harness), str(payload)],
                         capture_output=True, text=True)
    lines = [line for line in out.stdout.splitlines() if line.startswith("{")]
    if out.returncode != 0 or not lines:
        return set(), 0, len(jobs)
    specifiers = json.loads(lines[-1])
    reached: set[str] = set()
    unparsed = 0
    for rel, deps in specifiers.items():
        if deps is None:
            unparsed += 1
            continue
        base = (ROOT / rel).parent
        for dep in deps:
            if dep.startswith("."):
                reached.add((base / dep).as_posix().replace(chr(92), "/"))
    return reached, len(specifiers) - unparsed, unparsed


def imported_by_a_test(path: str, py_modules: set[str], js_paths: set[str]) -> bool:
    """Has some test actually REACHED this file, rather than mentioned its name?"""
    if path.endswith(".py"):
        return path[:-3].replace("/", ".") in py_modules
    if path.endswith(JS_SUFFIXES):
        target = (ROOT / path).as_posix().replace(chr(92), "/")
        return any(spec == target or Path(spec).name == Path(path).name for spec in js_paths)
    return False


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

    # THE SECOND TIER, and it is the one worth having. A NAME match is satisfied by a mention in
    # a comment, a fixture, or a stem collision; an IMPORT means a test reached the file.
    py_modules = python_modules_imported_by_tests()
    js_paths, parsed, unparsed = js_paths_imported_by_tests()
    imported = [p for p in covered if imported_by_a_test(p, py_modules, js_paths)]
    # A `.json`, an `.html`, a `.css` or a shell script cannot be imported by anything, so counting
    # it as "only named" reads as a coverage gap when it is a category error.
    importable = PY_SUFFIXES + JS_SUFFIXES
    named_only = [p for p in covered if p not in imported and p.endswith(importable)]
    not_importable = [p for p in covered if p not in imported and not p.endswith(importable)]

    print(f"PRODUCT FILES CHANGED IN {since}..HEAD: {len(files)}")
    print(f"  surviving, IMPORTED by a test         : {len(imported)}")
    print(f"  surviving code, only NAMED            : {len(named_only)}")
    print(f"  not importable at all (json/html/css/sh) : {len(not_importable)}")
    print(f"  surviving, named by NO test           : {len(bare)}")
    print(f"  DELETED in this range                 : {len(removed)}")
    print()
    print(f"The import tier read {parsed} JS test file(s) with V8 and {unparsed} that would not")
    print("parse, plus every Python test file through `ast`. STATIC imports only: a module")
    print("reached by `require()` or a dynamic `import()` falls to the weaker tier. Two verified")
    print("examples of that, so the limit is not theoretical: `send-tools.mjs` is exercised by")
    print("`send-tools.test.js` through `await import(...)`, and `doctor.js` is deliberately never")
    print("imported at all, because importing it RUNS the doctor.")
    print()
    if named_only:
        print("ONLY NAMED, never imported by a test -- each needs a person to say why:")
        for path in named_only:
            print(f"  {path}")
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
    print("CONTROLS, so each column means something.")
    print("  NAME tier -- a file certainly covered must show covered, a non-file must show bare:")
    for probe, expect in (("service/terminal_write_queue.py", True),
                          ("service/not_a_real_module_xyz.py", False)):
        got = bool(tests_naming(probe))
        print(f"    {probe:42} {'covered' if got else 'bare':8} "
              f"{'OK' if got == expect else '*** the scan is broken ***'}")
    print("  IMPORT tier -- a module a test imports must show imported, and one nothing imports")
    print("  must not. `doctor.js` is the negative control BY DESIGN: importing it runs it.")
    for probe, expect in (("service/terminal_write_queue.py", True),
                          ("mcp/stdio/doctor-predicates.js", True),
                          ("mcp/stdio/doctor.js", False),
                          ("service/not_a_real_module_xyz.py", False)):
        got = imported_by_a_test(probe, py_modules, js_paths)
        print(f"    {probe:42} {'imported' if got else 'not':8} "
              f"{'OK' if got == expect else '*** the import scan is broken ***'}")

    print()
    print("WHAT THIS IS NOT: a quality judgement. Even the IMPORT tier says only that a test")
    print("reached the file -- not that it exercises the change, or any branch of it. The NAME")
    print("tier is weaker still: a mention in a comment or a fixture satisfies it. The bare")
    print("column is the one that needs no argument.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
