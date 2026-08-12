"""Who actually READS a module-level constant — by AST, not by grep.

The reviewer's bar for a sole-reader constant move (v0.5.3) is that readership is measured on
Python CODE references, and that four categories are told apart rather than lumped:

    declaration   the assignment that owns the name
    accessor      a `_borrowed_*()` shim whose whole job is to return it
    code read     a real read in some other function's body   <-- the only one that blocks a move
    test read     a read from service/tests/                  <-- reported, but repointable

grep cannot make those distinctions: it matches the name inside comments, docstrings, SQL strings
and prose, all of which are noise, and it cannot tell an accessor's return from a genuine consumer.
Both mistakes point the wrong way — the first invents readers that do not exist, the second hides
that a constant is already borrowed elsewhere.

Usage:
    python scripts/constant_readership.py TURN_BUSY_BACKSTOP_SECONDS VIRTUAL_RPC_COMMAND_SET
    python scripts/constant_readership.py --verdict-for service/routers/terminals.py NAME
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import sys

ROOTS = ("service", "mcp")
SKIP_DIRS = {"__pycache__", "node_modules", ".git", "data"}


def python_files():
    for root in ROOTS:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn).replace("\\", "/")


def _enclosing_function(tree, node):
    """The innermost function containing `node`, or None at module scope."""
    best = None
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.lineno <= node.lineno and node.lineno <= (fn.end_lineno or fn.lineno):
            if best is None or fn.lineno > best.lineno:
                best = fn
    return best


def _is_accessor_returning(fn, name):
    """`fn` exists only to hand back `name` — the `_borrowed_*` shape."""
    if fn is None:
        return False
    body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    if len(body) != 2:
        return False
    imp, ret = body
    if not (isinstance(imp, ast.ImportFrom) and any(a.name == name for a in imp.names)):
        return False
    return isinstance(ret, ast.Return) and isinstance(ret.value, ast.Name) and ret.value.id == name


def scan(name):
    declarations, accessors, code_reads, test_reads = [], [], [], []
    for path in python_files():
        try:
            src = io.open(path, encoding="utf-8").read()
            tree = ast.parse(src)
        except (SyntaxError, UnicodeDecodeError):
            continue
        if name not in src:
            continue

        for node in ast.walk(tree):
            # the declaration itself
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == name:
                        declarations.append((path, node.lineno))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
                declarations.append((path, node.lineno))

        seen_import_lines = {
            n.lineno for n in ast.walk(tree)
            if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in n.names if (a.asname or a.name).split(".")[0] == name or a.name == name
        }
        declared_lines = {ln for p, ln in declarations if p == path}

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load)):
                continue
            if node.lineno in seen_import_lines or node.lineno in declared_lines:
                continue
            fn = _enclosing_function(tree, node)
            where = (path, node.lineno, fn.name if fn else "<module>")
            if _is_accessor_returning(fn, name):
                accessors.append(where)
            elif path.startswith("service/tests/"):
                test_reads.append(where)
            else:
                code_reads.append(where)
    return declarations, accessors, code_reads, test_reads


def report(name, verdict_for=None):
    declarations, accessors, code_reads, test_reads = scan(name)
    print(f"\n=== {name} ===")
    print(f"  declaration : {[f'{p}:{l}' for p, l in declarations] or 'NONE'}")
    print(f"  accessors   : {[f'{p}:{l}' for p, l, _ in accessors] or 'none'}")
    print(f"  CODE reads  : {len(code_reads)}")
    for p, l, f in code_reads:
        print(f"      {p}:{l}  in {f}()")
    print(f"  test reads  : {len(test_reads)}"
          + (f"  ({len({p for p, _, _ in test_reads})} files)" if test_reads else ""))

    if verdict_for:
        outside = [(p, l, f) for p, l, f in code_reads if p != verdict_for]
        if outside:
            print(f"  VERDICT     : ACCESSOR — {len(outside)} code read(s) live outside {verdict_for}")
            for p, l, f in outside:
                print(f"                  {p}:{l} in {f}()")
        else:
            print(f"  VERDICT     : SOLE-READER MOVE — every code read is in {verdict_for}")
            if test_reads:
                print(f"                  repoint {len({p for p, _, _ in test_reads})} test file(s) to the new owner")
    return code_reads


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+")
    ap.add_argument("--verdict-for", help="destination module path; decide move-vs-accessor against it")
    args = ap.parse_args()
    for n in args.names:
        report(n, args.verdict_for)
    return 0


if __name__ == "__main__":
    sys.exit(main())
