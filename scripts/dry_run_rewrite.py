"""Show what the accessor rewriter WOULD do, without writing anything.

WHY THIS EXISTS. The shim-retirement slice has been reverted twice, and both times the cause was a
defect in the rewriting tool rather than in the migration plan:

  1. the accessor guard split file text on a marker string and ate a return, then an import;
  2. the replacement domain was raw text, so comments and string literals were rewritten;
  3. reconstruction synthesised newlines and inserted a blank line between every line;
  4. the guard matched the NAME `_borrowed_*`, missing 14 accessors written under an older
     convention, and corrupted their imports.

Every one was found by running the tool on real code, after it had passed its own tests. Four
defects, two reverts. The tool is close, but "close" has cost two rollbacks, so the next run happens
in the open: this prints a unified diff of every file the migration would touch and writes nothing.

A fifth defect then costs a diff read instead of a revert.

    python scripts/dry_run_rewrite.py service/reconcilers/dispatch_queue.py CONST_A CONST_B
    python scripts/dry_run_rewrite.py --all-destinations
"""

from __future__ import annotations

import ast
import difflib
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from service.tests.accessor_rewrite import (  # noqa: E402
    accessor_line_ranges,
    is_constant_accessor,
    rewrite,
)


def report(path: Path, constants: list[str]) -> bool:
    """Print the diff this rewrite would produce. Returns True if it looks safe."""
    source = path.read_text(encoding="utf-8")
    protected = accessor_line_ranges(source)
    result = rewrite(source, constants)

    rel = path.relative_to(REPO).as_posix()
    print(f"\n=== {rel}")
    print(f"    constants: {constants}")
    print(f"    protected accessor ranges: {protected or 'none'}")

    if result == source:
        print("    NO CHANGE")
        return True

    diff = list(difflib.unified_diff(
        source.splitlines(keepends=True), result.splitlines(keepends=True),
        fromfile=rel, tofile=rel + " (rewritten)", n=1,
    ))
    print(f"    changed lines: {sum(1 for l in diff if l.startswith('+') and not l.startswith('+++'))}")
    for line in diff:
        print("    " + line.rstrip("\n"))

    # the three checks that would each have caught a previous defect
    safe = True
    try:
        tree = ast.parse(result)
    except SyntaxError as error:
        print(f"    *** RESULT DOES NOT PARSE: {error}")
        return False
    for node in ast.walk(tree):
        if not is_constant_accessor(node):
            continue
        if any(isinstance(s, ast.Call) and isinstance(s.func, ast.Name)
               and s.func.id == node.name for s in ast.walk(node)):
            print(f"    *** ACCESSOR {node.name} CALLS ITSELF")
            safe = False
    if result.count("\n") != source.count("\n"):
        print(f"    *** LINE COUNT CHANGED: {source.count(chr(10))} -> {result.count(chr(10))}")
        safe = False
    return safe


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    if args[0] == "--all-destinations":
        print("Pass explicit files and constants; there is no baked-in destination list, "
              "deliberately — the migration should state what it touches.")
        return 2
    path, constants = REPO / args[0], args[1:]
    if not constants:
        print("no constants given: nothing would be rewritten")
        return 0
    return 0 if report(path, constants) else 1


if __name__ == "__main__":
    raise SystemExit(main())
