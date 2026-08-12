"""Find names a module READS but never binds — the failure a relocation actually causes.

WHY THIS EXISTS. Moving a function between modules breaks its callers by leaving a name unresolvable,
and `python -m py_compile` does NOT catch it: an undefined global is a runtime NameError, not a syntax
error. Skipping this sweep once in the v0.5.4 series cost 302 red tests, all of them the same trivial
cause. It is cheap and it runs BEFORE the suites, because a suite tells you something broke while this
tells you which name.

HOW. `symtable` is the authority on scoping rather than an AST walk of `ast.Name`, because only the
symbol table knows whether a name is a local, a parameter, a comprehension variable, a closure cell or
a genuine global reference. An AST walk cannot tell `x` the local from `x` the module-level import, and
guessing produces exactly the false positives that make a checker get ignored.

A name is REPORTED when it is read as a global in some scope and is not:
  - bound anywhere at module level (def, class, assignment, import, `for`, `with ... as`),
  - a Python builtin,
  - declared `global`/`nonlocal` and bound in another scope of the same module.

FALSE NEGATIVE, stated because a checker with an unstated blind spot is worse than none: a name bound
by `from x import *` is invisible here, so a module using a star-import can hide a real break. No file
in this repo does, and the sweep asserts that rather than assuming it.

Usage:  python scripts/undefined_name_sweep.py [path ...]      (default: every service/**.py)
Exit 1 if anything is reported.
"""

from __future__ import annotations

import ast
import builtins
import io
import os
import symtable
import sys

BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__", "__spec__"}

# COMPILER-GENERATED, not written by anyone. Python 3.14 implements PEP 649 lazy annotations by
# synthesizing `__conditional_annotations__` and `__annotate__` into the symbol table of any module
# using `from __future__ import annotations`. They appear as referenced-but-unbound globals in 15 files
# here and mean nothing about those files. Filtered by name rather than by suppressing dunders, so a
# real missing dunder would still be reported.
COMPILER_SYNTHETIC = {"__conditional_annotations__", "__annotate__"}

# PRISTINE FIXTURES ARE NOT MODULES. `service/tests/data/` holds pre-split snapshots of functions,
# captured verbatim so the extract-method gate can compare a split against the original. They are
# deliberately not importable — a function lifted out of its module reads names that were in scope
# THERE — so sweeping them reports the fixture's whole dependency surface as broken. Excluded by
# directory, and narrowly: `service/tests/` itself is still swept, because a test that reads an
# undefined name is a real break.
FIXTURE_DIRS = (os.path.join("service", "tests", "data"),)


def is_fixture(path: str) -> bool:
    norm = os.path.normpath(path)
    return any(norm.startswith(d) or os.sep + d in norm for d in FIXTURE_DIRS)


def module_level_bindings(tree: ast.Module) -> set[str]:
    """Every name the module binds at its own top level, however it binds it."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                bound.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                bound.add(a.asname or a.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
    return bound


def star_imports(tree: ast.Module) -> list[str]:
    return [
        node.module or "?"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names)
    ]


def walk_scopes(table):
    yield table
    for child in table.get_children():
        yield from walk_scopes(child)


def sweep(path: str) -> list[str]:
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src, filename=path)
    stars = star_imports(tree)
    bound = module_level_bindings(tree)
    findings = []
    if stars:
        findings.append("%s: star-import from %s — this sweep cannot see its names" % (path, ", ".join(stars)))
    top = symtable.symtable(src, path, "exec")
    for scope in walk_scopes(top):
        for sym in scope.get_symbols():
            name = sym.get_name()
            if not sym.is_referenced():
                continue
            if sym.is_local() or sym.is_parameter() or sym.is_assigned():
                continue
            if name in BUILTINS or name in bound or name in COMPILER_SYNTHETIC:
                continue
            if sym.is_free() or sym.is_imported():
                continue
            findings.append("%s: %s reads undefined name %r" % (path, scope.get_name(), name))
    return findings


def main(argv: list[str]) -> int:
    paths = argv[1:]
    if not paths:
        paths = []
        for root, _dirs, files in os.walk("service"):
            for f in files:
                if f.endswith(".py"):
                    paths.append(os.path.join(root, f))
    findings: list[str] = []
    swept = 0
    for p in paths:
        if is_fixture(p):
            continue
        swept += 1
        findings.extend(sweep(p))
    if findings:
        for f in findings:
            print(f)
        print("\n%d finding(s) across %d file(s)" % (len(findings), swept))
        return 1
    print("undefined-name sweep CLEAN across %d file(s)" % swept)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
