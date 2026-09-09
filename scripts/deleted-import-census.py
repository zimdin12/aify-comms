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

EIGHT CONTROLS IN EVERY RUN. Positive: a live module is named, with THIS FILE excluded from the
population so the instrument cannot answer for itself. Negative: a name that was never a file is not.
Then six carriers, three per direction. CODE: a multiline `require`, a multiline dynamic
`import`, and comment TEXT sitting inside a string literal. PROSE: a `//` comment, a Python
docstring, and a `//` comment following a regex literal whose character class holds a quote --
the shape that made the first scanner read a whole file's comments as code. Exit 1 if any
control fails or anything is named from code.
"""
from __future__ import annotations

import ast
import io
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path

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


def _offsets(text: str) -> list[int]:
    """Character offset of the first character of each line, 1-indexed by line."""
    starts = [0, 0]
    for line in text.split(chr(10))[:-1]:
        starts.append(starts[-1] + len(line) + 1)
    return starts


def python_comment_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) character offsets of comments and docstrings, from Python's own tools."""
    spans: list[tuple[int, int]] = []
    starts = _offsets(text)

    def offset(row: int, col: int) -> int:
        return (starts[row] if row < len(starts) else len(text)) + col

    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                spans.append((offset(*token.start), offset(*token.end)))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return spans
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            spans.append((offset(first.lineno, first.col_offset),
                          offset(first.end_lineno or first.lineno,
                                 first.end_col_offset or first.col_offset)))
    return spans


#: A `/` starts a REGEX rather than a division when the last meaningful thing before it cannot end
#: an expression. The standard JS lexing ambiguity, and the first version of this scanner ignored it
#: entirely: a character class holding a quote opened a string that never closed, and every comment
#: after it read as code. The carriers now include that exact shape.
REGEX_MAY_FOLLOW = set("(,=:[!&|?{};+-*%~^<>") | {""}
REGEX_KEYWORDS = {"return", "typeof", "case", "in", "of", "new", "delete", "void", "throw",
                  "do", "else", "yield", "await", "instanceof"}


def js_comment_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) character offsets of `//` and `/* */` comments, tracking strings and regexes.

    HAND-ROLLED, AND DRIVEN RATHER THAN TRUSTED. This repo's record on hand-rolled JS scanners is
    four of them and four wrong answers, and this one added a fifth and a sixth before its carriers
    caught them: a regex literal read as a string, and then a whole LINE classified from one comment
    on it.

    MISCLASSIFICATION IS SAFE IN ONE DIRECTION ONLY. Reading a comment as CODE over-reports, and an
    over-report is printed for a person to judge. Reading code as a COMMENT hides a real reference.
    So every ambiguity here resolves toward CODE.
    """
    spans: list[tuple[int, int]] = []
    i = 0
    state = "code"          # code | line_comment | block_comment | regex | ' | " | `
    previous = ""           # last meaningful character seen in code
    start = 0
    while i < len(text):
        char = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if char == "/" and nxt == "/":
                state, start, i = "line_comment", i, i + 2
                continue
            if char == "/" and nxt == "*":
                state, start, i = "block_comment", i, i + 2
                continue
            if char == "/" and _regex_may_start(text, i, previous):
                state, i = "regex", i + 1
                continue
            if char in "'\"`":
                state, i = char, i + 1
                continue
            if not char.isspace():
                previous = char
            i += 1
            continue
        if state == "line_comment":
            if char == chr(10):
                spans.append((start, i))
                state = "code"
            i += 1
            continue
        if state == "block_comment":
            if char == "*" and nxt == "/":
                spans.append((start, i + 2))
                state, i = "code", i + 2
                continue
            i += 1
            continue
        if state == "regex":
            if char == "\\":
                i += 2
                continue
            if char == "[":
                # A CHARACTER CLASS SWALLOWS `/`, and this is where the quote in a class like
                # ["'`] lives. Skip to its close rather than ending the regex early.
                close = text.find("]", i + 1)
                i = (close + 1) if close != -1 else i + 1
                continue
            if char == "/":
                state, previous = "code", "/"
            i += 1
            continue
        # inside a string or template literal
        if char == "\\":
            i += 2
            continue
        if char == state:
            state = "code"
            previous = char
        i += 1
    if state in ("line_comment", "block_comment"):
        spans.append((start, len(text)))
    return spans


def _regex_may_start(text: str, index: int, previous: str) -> bool:
    """Could a regex literal begin at this `/`? Ambiguity resolves toward NO, which means CODE."""
    if previous in REGEX_MAY_FOLLOW:
        return True
    before = text[:index].rstrip()
    word = ""
    while before and (before[-1].isalpha() or before[-1] == "_"):
        word = before[-1] + word
        before = before[:-1]
    return word in REGEX_KEYWORDS


def comment_spans(path: Path) -> list[tuple[int, int]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return python_comment_spans(text) if path.suffix == ".py" else js_comment_spans(text)


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
                  "a `//` comment naming it is PROSE",
                  "a Python docstring naming it is PROSE",
                  "a comment after a regex holding a quote is PROSE"):
        bad = [f for f in failures if f.startswith(label)]
        print(f"  carrier: {label:44} {bad[0][len(label):].strip() if bad else 'OK'}")
    ok = ok and not failures

    print()
    if not ok:
        print("The census reports nothing, because its own instrument failed a control.")
        return 1
    if reached:
        print(f"NEEDS JUDGEMENT: {len(reached)} deleted module(s) are named from code.")
        return 1
    print(f"CLEAN: of {len(gone)} deleted files, {len(unnamed)} are not SPELLED anywhere in the")
    print(f"searched population and {len(prose_only)} appear only inside comments or docstrings.")
    print("None is named from code.")
    print()
    print("SCOPE, so this is not over-read: the first figure is literal-name absence, NOT")
    print("unreachability. An escaped, concatenated or computed specifier evaluates to the same")
    print("path with no raw-name hit, and nothing here resolves a specifier. The second figure")
    print("rests on the classifier, which the eight carriers above drive in both directions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
