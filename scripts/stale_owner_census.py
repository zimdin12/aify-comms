"""Who still reaches a MOVED helper through `service.control_plane`?

The v0.5.4 decomposition moves helpers out of the control plane into leaf modules. The control plane
keeps importing what it still calls, so a consumer that reaches a moved helper through the OLD
address keeps working — right up until the carrier stops calling it, at which point the consumer
breaks for reasons unconnected to its own change.

TWO CARRIERS, and missing the second is what made this script necessary. My repoint passes handled
import lines, so the reviewer kept finding stale consumers I had reported clean:

    1. IMPORT FORM        from service.control_plane import _moved_name
                          from service.control_plane import _moved_name, _still_there
       — the second shape was invisible to a pass that replaced a single-name line verbatim.

    2. ALIAS-ATTRIBUTE    from service import control_plane as api_v2
                          ...
                          api_v2._moved_name(row)
       — not an import of the name at all. No import line mentions it, so no amount of
         import-line parsing finds it. This is the one the reviewer found fourth.

Both are reported. Neither is a production defect while the carrier still imports the name — it is a
correctness-of-ownership issue, and for private-helper tests it matters most: a test asserting a
helper's behaviour should exercise the module that owns it, or it is testing the carrier's import
list.

Usage:
    python scripts/stale_owner_census.py                 # report
    python scripts/stale_owner_census.py --strict         # exit 1 if any found
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import sys

CARRIER = "service.control_plane"

#: Every name a leaf module OWNS, mapped to that module — DERIVED, not hand-listed.
#:
#: This map was a literal dict for three slices and went stale immediately: it covered five owner
#: modules while `agent_sessions`, `turn_state` and `dispatch_state` had already landed, so the census
#: reported "clean" while never looking at 15 of the moved names. A hand-maintained list of what to
#: check is a check that silently narrows every time the code moves — the same green-on-the-wrong-
#: artifact shape as the security probe that scanned one file by name.
#:
#: Deriving it from the leaf modules themselves means a new leaf is covered the moment it exists, and
#: it also picks up names moved in EARLIER releases (api_core/serialization.py and friends), which is
#: how the stale `_quote_untrusted_subject` consumer surfaced.
def _owned_names() -> dict[str, str]:
    owners: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(os.path.join("service", "api_core")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            if not fn.endswith(".py") or fn == "__init__.py":
                continue
            path = os.path.join(dirpath, fn).replace("\\", "/")
            module = path[: -len(".py")].replace("/", ".")
            try:
                tree = ast.parse(io.open(path, encoding="utf-8").read())
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    owners.setdefault(node.name, module)
                elif isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            owners.setdefault(t.id, module)
    return owners


MOVED: dict[str, str] = _owned_names()


def _python_files():
    for dirpath, dirnames, filenames in os.walk("service"):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", "data", "new_dashboard"}]
        for fn in filenames:
            if fn.endswith(".py"):
                p = os.path.join(dirpath, fn).replace("\\", "/")
                # The carrier legitimately imports what it still calls; the owners obviously do.
                if p == "service/control_plane.py" or "/api_core/" in p:
                    continue
                yield p


def census():
    findings = []
    for path in _python_files():
        src = io.open(path, encoding="utf-8").read()
        if CARRIER not in src and "control_plane" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue

        # aliases bound to the carrier module: `from service import control_plane as api_v2`,
        # `import service.control_plane as cp`
        aliases = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "service":
                for a in node.names:
                    if a.name == "control_plane":
                        aliases.add(a.asname or a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == CARRIER and a.asname:
                        aliases.add(a.asname)

        for node in ast.walk(tree):
            # form 1: import of a moved name from the carrier (any arity)
            if isinstance(node, ast.ImportFrom) and node.module == CARRIER:
                for a in node.names:
                    if a.name in MOVED:
                        findings.append((path, node.lineno, "import", a.name, MOVED[a.name]))
            # form 2: alias-qualified attribute access
            elif isinstance(node, ast.Attribute) and node.attr in MOVED:
                base = node.value
                if isinstance(base, ast.Name) and base.id in aliases:
                    findings.append((path, node.lineno, f"{base.id}.attr", node.attr, MOVED[node.attr]))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="exit 1 if any stale consumer is found")
    args = ap.parse_args()
    findings = census()
    print(f"moved-name population: {len(MOVED)} names across "
          f"{len(set(MOVED.values()))} owner modules\n")
    if not findings:
        print("no stale owner consumers found (both import and alias-attribute forms checked)")
        return 0
    print(f"{len(findings)} stale consumer(s):\n")
    for path, lineno, form, name, owner in sorted(findings):
        print(f"  {path}:{lineno}\n      [{form}] {name}  -> should come from {owner}")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
