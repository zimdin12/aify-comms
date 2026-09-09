"""Does any surviving source still REACH a module this release range deleted?

WHY THIS EXISTS. `docs/V063_ACCEPTANCE_LEDGER.md` rows R-1..R-3 claim nothing still reaches a module
the range deleted. The ledger cited `no-missing-sibling-imports.test.js` and `moved-names-resolve.js`
for it, and neither proves it: the first asks whether a resolved sibling exists, the second whether a
moved NAME resolves, and both are green in a tree that still imports a deleted module from somewhere
they do not walk.

IT DOES NOT MODEL AN IMPORT, AND THAT IS THE REPAIR. The first version matched
`(import|require|from).*"...<name>..."` on ONE LINE, and review broke it three ways in one sitting: a
`require(` with its specifier on the next line was missed, a dynamic `import(` split the same way was
missed, and the POSITIVE CONTROL matched THIS FILE -- the word `import` inside the identifier
`expect_importers`, on the line naming the control probe. Deleting all fifteen real importers still
left the control reading "covered", so the instrument certified itself.

THREE TIERS. First every mention of the module's file NAME as a fixed string, anywhere in a
surviving source. A name found NOWHERE is not spelled anywhere in the searched population, in any
specifier split across any number of lines, and that tier needs no classifier to be believed.
Then each remaining mention is placed in a COMMENT or in CODE, so the prose this repo
deliberately writes about retired modules is not reported as a live reference.

WHAT THE FIRST TIER IS NOT. It is LITERAL-NAME absence, not unreachability. Review falsified the
stronger reading this file used to publish with one line: a dynamic import whose specifier spells
one letter of a deleted module as a unicode escape (`\u006d` for `m`) is valid, evaluates to
the same path, and carries no raw-name hit. A specifier built by concatenation, or computed at
runtime, does the same. Nothing here resolves a specifier, so the claim stops at the spelling.

CODE IS NOT THE SAME AS A LOAD-TIME REFERENCE, and saying it was is an overclaim this file
carried. An exemption list, an assertion message and a test fixture all name a module from code
and none of them loads anything. What the third tier means is "named outside any comment", which
is where an import WOULD be, and every entry is printed for a person to judge.

COMMENTS ARE FOUND WITH A TOKENIZER WHERE ONE EXISTS. Python uses `tokenize` for comments and `ast`
for docstrings, so neither is guessed at. JavaScript has no such tool in the standard library, so it
gets a scanner that tracks strings, template literals and both comment forms -- and it is DRIVEN in
every run rather than trusted, because a hand-rolled scanner is exactly what this repo has been
burned by.

    python scripts/deleted-import-census.py [since]

CONTROLS IN EVERY RUN. Positive: a live module is named, with THIS FILE excluded from the population
so the instrument cannot answer for itself. Negative: a name that was never a file is not. Then TEN
carriers -- SEVEN that must read as CODE and THREE as PROSE, which is not a symmetric set and was
published as one. CODE: a multiline `require`, a multiline dynamic `import`, comment TEXT inside a
string literal, an import sharing its line with a trailing note, a Python assignment with one, and
that assignment after an ASCII and then a NON-ASCII docstring on the same line -- the pair that
isolates a column UNIT rather than a shape. PROSE: a `//` comment, a Python docstring, and a `//`
comment following a regex literal whose character class holds a quote.

AND A DIFFERENTIAL AGAINST V8, because a carrier only exercises a shape somebody thought to write --
and both of this scanner's defects lived in shapes nobody did. Every comment span it reports is
blanked out and the result handed to `vm.SourceTextModule`: a scanner that ate code produces a file
V8 cannot parse. Its own negative control runs beside it, the same files with every span stretched
forty characters past its end, which must FAIL; a file where even that still parses contributes no
evidence and is dropped rather than counted. Exit 1 if any control fails or anything is named from
code.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comment_spans import comment_spans, js_comment_spans   # noqa: E402  (path set above)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SINCE = "aed8b590"
SELF = f"scripts/{Path(__file__).name}"

#: Sources that could reach a module. Tests are INCLUDED: a test importing a deleted module is the
#: same broken import, and `moved-names-resolve.test.js` exists because that is where it happened.
SOURCE_GLOBS = ("*.js", "*.mjs", "*.cjs", "*.py")

#: This file names every deleted module and both control probes, so leaving it in the population
#: would make the census report itself. That is not a hypothetical -- it is how the first version's
#: positive control passed with every real importer removed.
EXCLUDE_SELF = f":!{SELF}"


def deleted_modules(since: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--diff-filter=D", "--name-only", f"{since}..HEAD"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"could not diff {since}..HEAD: {out.stderr.strip()[:200]}")
    paths = [p for p in out.stdout.split("\n") if p.strip()]
    return [p for p in paths if Path(p).suffix in (".js", ".mjs", ".cjs", ".py", ".json")]


def _grep(name: str, cwd: Path, pathspec: list[str], no_index: bool) -> list[str]:
    """One searcher, used for the tree and for the carriers, so both are judged the same way."""
    command = ["git", "grep", "-n", "-F"]
    if no_index:
        command.append("--no-index")
    command += [name]
    if pathspec:
        command += ["--", *pathspec]
    out = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if out.returncode not in (0, 1):
        raise SystemExit(f"the search itself failed for {name}: {out.stderr.strip()[:200]}")
    return [line for line in out.stdout.split("\n") if line.strip()]


def naming(basename: str) -> list[str]:
    """Surviving source FILES naming this file. THIS SCRIPT is excluded.

    Files, not lines: `git grep` reports the line a match falls on and nothing about where in it,
    and a line is not a unit of code. The occurrences are located and placed by `classify`.
    """
    files = []
    for line in _grep(basename, ROOT, [*SOURCE_GLOBS, EXCLUDE_SELF], no_index=False):
        path, _, _rest = line.partition(":")
        rel = path.replace("\\", "/")
        if rel and rel not in files:
            files.append(rel)
    return files


def occurrences(path: Path, name: str) -> list[tuple[int, int]]:
    """(offset, line) for every literal occurrence of the name in this file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    out = []
    at = text.find(name)
    while at != -1:
        out.append((at, text.count(chr(10), 0, at) + 1))
        at = text.find(name, at + 1)
    return out


def classify(files: list[str], name: str, root: Path) -> tuple[list[str], list[str]]:
    """Split each OCCURRENCE into code or prose. THE OCCURRENCE, not the line it shares.

    A line holding a dynamic import AND a trailing note holds both, and asking whether its LINE
    carries a comment answered PROSE for the import too. Review drove that through the entrypoint.
    """
    code, prose = [], []
    for rel in files:
        path = root / rel
        spans = comment_spans(path)
        for offset, line in occurrences(path, name):
            inside = any(start <= offset < end for start, end in spans)
            (prose if inside else code).append(f"{rel}:{line}")
    return code, prose


#: A regex literal whose character class holds a double quote, a single quote AND a backtick,
#: assembled from character codes because every quoting layer this file passes through would
#: otherwise take a bite out of it. It is the exact shape from `server-url-fallback.test.js` that
#: made the first scanner read the rest of that file's comments as code.
REGEX_CARRIER = (chr(47) + "192[.]168[." + chr(92) + "d+["
                 + chr(34) + chr(39) + chr(96) + "]" + chr(47))


#: V8, asked whether a file still parses once everything the scanner calls a comment is blanked out.
#: Written to a scratch file per run rather than kept in the tree: it is an instrument's instrument.
PARSE_HARNESS = """
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
const jobs = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const out = [];
for (const job of jobs) {
  const verdict = { path: job.path };
  for (const which of ['original', 'blanked']) {
    try { new vm.SourceTextModule(job[which], { identifier: job.path + which }); verdict[which] = 'ok'; }
    catch { verdict[which] = 'error'; }
  }
  out.push(verdict);
}
console.log(JSON.stringify(out));
"""


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    """The file with every comment span replaced by spaces, newlines kept so lines still align."""
    chars = list(text)
    for start, end in spans:
        for i in range(start, min(end, len(chars))):
            if chars[i] != chr(10):
                chars[i] = " "
    return "".join(chars)


def scanner_differential(files: list[str]) -> tuple[str, list[str]]:
    """Does blanking the scanner's comment spans leave these files parsing? V8 answers.

    A scanner that eats code produces a file V8 cannot parse. Returns a verdict word and the
    files that broke, or ("unknown", ...) when the harness could not be run -- because a check
    that gathered no evidence is not a passed one.
    """
    jobs = []
    for rel in files:
        path = ROOT / rel
        if path.suffix not in (".js", ".mjs", ".cjs") or not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        spans = js_comment_spans(text)
        if not spans:
            continue
        jobs.append({"path": rel, "original": text, "blanked": _blank(text, spans)})
        # THE NEGATIVE CONTROL, on the same file and in the same invocation: every span stretched
        # forty characters past its end is a scanner that eats code, and it must NOT parse.
        jobs.append({"path": rel + " [stretched]", "original": text,
                     "blanked": _blank(text, [(a, b + 40) for a, b in spans])})
    if not jobs:
        return "unknown", ["no JavaScript file with a comment reached the differential"]

    scratch = Path(tempfile.mkdtemp())
    harness = scratch / "parses.mjs"
    harness.write_text(PARSE_HARNESS, encoding="utf-8")
    payload = scratch / "jobs.json"
    payload.write_text(json.dumps(jobs), encoding="utf-8")
    out = subprocess.run(["node", "--experimental-vm-modules", str(harness), str(payload)],
                         capture_output=True, text=True)
    lines = [line for line in out.stdout.splitlines() if line.startswith("[")]
    if not lines:
        return "unknown", [f"the parser harness did not run: {out.stderr.strip()[-200:]}"]
    results = json.loads(lines[-1])

    broke = [r["path"] for r in results
             if not r["path"].endswith("[stretched]")
             and r["original"] == "ok" and r["blanked"] == "error"]
    # The stretched arm must break wherever the honest arm parsed, or the differential is blind.
    honest = {r["path"]: r for r in results if not r["path"].endswith("[stretched]")}
    blind = [r["path"] for r in results
             if r["path"].endswith("[stretched]")
             and honest.get(r["path"][: -len(" [stretched]")], {}).get("original") == "ok"
             and r["blanked"] != "error"]
    # A FILE WHOSE STRETCHED ARM STILL PARSES CONTRIBUTES NO EVIDENCE and is dropped rather than
    # voiding the run: stretching forty characters past a comment that is followed by forty more
    # characters of comment changes nothing, which is a property of that file and not a fault.
    # What would be a fault is judging the honest arm of a file whose control cannot fire.
    judged = [r for r in honest.values()
             if r["original"] == "ok" and r["path"] not in {b[: -len(" [stretched]")] for b in blind}]
    if len(judged) < 5:
        return "unknown", [f"only {len(judged)} file(s) had a differential that could fail, which is too few to mean anything"]
    broke = [r["path"] for r in judged if r["blanked"] == "error"]
    return ("broken", broke) if broke else ("ok", [f"{len(judged)} file(s) judged"])


#: A Python triple quote, from character codes: written literally it would end the string it
#: is being written into.
DOC_QUOTE = chr(34) * 3


def carrier_verdicts() -> list[str]:
    """Eight carriers, four per direction, through the same searcher and the same classifier."""
    scratch = Path(tempfile.mkdtemp())
    name = "a-deleted-module.mjs"
    cases = {
        "a require split across lines is CODE": (
            "req.mjs", f'const x = require(\n  "./{name}"\n);\n', "code"),
        "a dynamic import split across lines is CODE": (
            "dyn.mjs", f'const y = await import(\n  `./{name}`\n);\n', "code"),
        "a `//` comment naming it is PROSE": (
            "note.mjs", f'const z = 1;\n// see ./{name} for why\n', "prose"),
        "a Python docstring naming it is PROSE": (
            "note.py", f'"""Retired: ./{name} went with the tier."""\nZ = 1\n', "prose"),
        # THE SHAPE THAT DEFEATED THE FIRST SCANNER. A regex literal whose character class
        # holds a quote: read as a string opening, it swallowed every comment after it, and
        # one real comment in `server-url-fallback.test.js` was reported as a live reference.
        "a comment after a regex holding a quote is PROSE": (
            "re.mjs",
            "const ok = " + REGEX_CARRIER + ".test(s);" + chr(10)
            + "// retired with ./" + name + chr(10),
            "prose"),
        # And the other direction, which is why strings are tracked at all: comment TEXT living
        # inside a string literal is code.
        "a `//` inside a string literal is CODE": (
            "str.mjs", f'const doc = "// see ./{name} for why";\n', "code"),
        # A LINE IS NOT A UNIT OF CODE, and the line classifier this replaced read both of these
        # as prose: one comment anywhere on the line decided the verdict for the import beside
        # it. Review drove the first through the whole entrypoint -- planted import NEEDS
        # JUDGEMENT, the identical import with a trailing note CLEAN.
        "an import with a trailing comment is still CODE": (
            "trail.mjs", f'const x = await import("./{name}"); // unrelated note\n', "code"),
        "a Python assignment with a trailing comment is still CODE": (
            "trail.py", f'X = "./{name}"  # unrelated note\n', "code"),
        # TWO TOOLS, TWO COLUMN UNITS. `tokenize` reports CHARACTER columns and the AST reports
        # UTF-8 BYTE columns; joined without conversion, a docstring of non-ASCII characters
        # reported an end column far past its real one and swallowed the code sharing its line.
        # The ASCII twin is here so the pair isolates the ENCODING and not the shape: identical
        # code, identical structure, and only an encoding fault can tell them apart.
        "an assignment after an ASCII docstring on one line is CODE": (
            "ascii-doc.py",
            DOC_QUOTE + ("a" * 40) + DOC_QUOTE + '; X = "./' + name + '"\n',
            "code"),
        "an assignment after a NON-ASCII docstring on one line is CODE": (
            "utf8-doc.py",
            DOC_QUOTE + (chr(233) * 40) + DOC_QUOTE + '; X = "./' + name + '"\n',
            "code"),
    }
    for _label, (filename, body, _want) in cases.items():
        (scratch / filename).write_text(body, encoding="utf-8")
    failures = []
    for label, (filename, _body, want) in cases.items():
        found = [line.partition(':')[0].replace(chr(92), '/')
                 for line in _grep(name, scratch, [], no_index=True)]
        if not any(rel.endswith(filename) for rel in found):
            failures.append(f"{label} -- the SEARCH missed it entirely")
            continue
        code, prose = classify([filename], name, scratch)
        got = "code" if code else "prose"
        if got != want:
            failures.append(f"{label} -- read as {got}")
    return failures


def main() -> int:
    since = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SINCE
    gone = deleted_modules(since)
    if not gone:
        print(f"UNKNOWN: no module was deleted in {since}..HEAD, so this census judged nothing.")
        print("Check the revision rather than reading this as clean.")
        return 1

    product = [p for p in gone if "/tests/" not in p and not Path(p).name.startswith("test_")]
    print(f"MODULES DELETED IN {since}..HEAD: {len(gone)}")
    print(f"  of which PRODUCT (the ledger's R-1..R-3 population) : {len(product)}")
    print(f"  of which deleted TESTS, searched too                : {len(gone) - len(product)}")

    unnamed, prose_only, reached = [], {}, {}
    for path in gone:
        files = naming(Path(path).name)
        if not files:
            unnamed.append(path)
            continue
        code, prose = classify(files, Path(path).name, ROOT)
        if code:
            reached[path] = code
        else:
            prose_only[path] = prose

    print(f"  named NOWHERE in any surviving source : {len(unnamed)}")
    print(f"  named only in comments or docstrings  : {len(prose_only)}")
    print(f"  named FROM CODE, outside any comment  : {len(reached)}")
    print()
    if reached:
        print("NAMED FROM CODE -- outside any comment, which is where an import would be. A")
        print("string literal lands here too, so each line is for a person to judge:")
        for path, where in sorted(reached.items()):
            print(f"  {path}")
            for site in where:
                print(f"      {site}")
        print()

    print("THE CONTROLS, in this same run.")
    ok = True
    for probe, expect in (("doctor-predicates.js", True),
                          ("not-a-real-module-xyz.mjs", False)):
        found = naming(probe)
        agreed = bool(found) == expect
        ok = ok and agreed
        print(f"  {probe:28} {len(found):3} file(s)  "
              f"{'OK' if agreed else '*** THE SEARCH IS BROKEN ***'}")
    print("  (this file is excluded from the population above, so the census cannot answer for")
    print("   itself -- the failure that made the previous version's positive control meaningless)")
    failures = carrier_verdicts()
    for label in ("a require split across lines is CODE",
                  "a dynamic import split across lines is CODE",
                  "a `//` inside a string literal is CODE",
                  "an import with a trailing comment is still CODE",
                  "a Python assignment with a trailing comment is still CODE",
                  "an assignment after an ASCII docstring on one line is CODE",
                  "an assignment after a NON-ASCII docstring on one line is CODE",
                  "a `//` comment naming it is PROSE",
                  "a Python docstring naming it is PROSE",
                  "a comment after a regex holding a quote is PROSE"):
        bad = [f for f in failures if f.startswith(label)]
        print(f"  carrier: {label:44} {bad[0][len(label):].strip() if bad else 'OK'}")
    ok = ok and not failures

    # THE DIFFERENTIAL, over the repo's REAL files rather than constructed ones. Both scanner
    # defects so far lived in shapes nobody thought to write a carrier for.
    sample = sorted({rel for rels in list(prose_only.values()) + list(reached.values())
                     for rel in [entry.rsplit(':', 1)[0] for entry in rels]})
    floor = [str(p.relative_to(ROOT).as_posix())
             for p in sorted((ROOT / 'mcp' / 'stdio').glob('*.mjs'))[:15]]
    verdict, broke = scanner_differential(sorted(set(sample) | set(floor)))
    print(f"  V8 differential: blanking the scanner's comments leaves every file parsing "
          f"-- {verdict.upper()}")
    for item in broke:
        print(f"      {item}")
    ok = ok and verdict == "ok"

    print()
    # THE SCOPE NOTE PRINTS ON EVERY BRANCH THAT REPORTS A FIGURE. It used to sit only under the
    # CLEAN return, so the branch this repo actually takes -- NEEDS JUDGEMENT -- published the
    # counts with no statement of what they mean. A caveat that is skipped exactly where the
    # numbers are read is not a caveat.
    def scope() -> None:
        print()
        print("SCOPE, so this is not over-read: the first figure is literal-name absence, NOT")
        print("unreachability. A specifier that spells a letter as an escape, or is concatenated")
        print("or computed, evaluates to the same path with no raw-name hit, and nothing here")
        print("resolves a specifier. The second and third rest on the classifier, which the ten")
        print("carriers above drive in both directions and which V8 checks differentially.")

    if not ok:
        print("The census reports nothing, because its own instrument failed a control.")
        return 1
    if reached:
        print(f"NEEDS JUDGEMENT: {len(reached)} deleted module(s) are named from code -- and a")
        print(f"string literal lands there too. {len(unnamed)} are not SPELLED anywhere in the")
        print(f"searched population, {len(prose_only)} appear only inside comments or docstrings.")
        scope()
        return 1
    print(f"CLEAN: of {len(gone)} deleted files, {len(unnamed)} are not SPELLED anywhere in the")
    print(f"searched population and {len(prose_only)} appear only inside comments or docstrings.")
    print("None is named from code.")
    scope()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
