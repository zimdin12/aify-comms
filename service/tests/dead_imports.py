"""Which import bindings in a module are reached by NOTHING, anywhere.

THE DETECTOR LIVES HERE SO THE SWEEP AND THE GATE SHARE ONE COPY. `mcp/stdio/tests/no-dead-imports.test.js`
records why in its own comment: a sweep tool carrying its own regex deleted four LIVE imports because its
copy had drifted from the gate's. Anything that removes dead imports in this repo must call
`dead_bindings()`, never re-derive the rule.

THE ALIAS TABLE IS COMPUTED, NOT LISTED, and that is the whole difference between this and
`test_no_orphaned_imports_in_control_plane.py`. That gate is scoped to one file and says so: applied
tree-wide, its hardcoded `("api_v2", "cp", ...)` alias list would need a per-module table, and getting
one wrong deletes a live patch target. So here the aliases are derived from the source: for a target
module, every `from <pkg> import <mod> as <alias>` and `import <dotted> as <alias>` in the tree
contributes an alias, and `<alias>.name` counts as a reach. The repo really does bind routers this way —
`dispatch_router`, `agents_shared`, `terminals_router`, `channels_router`, `health_router` are all live
test bindings — which is exactly why guessing was not an option.

WHAT COUNTS AS REACHED, deliberately over-broad. A false PASS costs one dead line; a false FAILURE
deletes working code. A binding is reached if:

  * the module body loads the name;
  * the module also defines it (the import is shadowed, not dead);
  * another file does `from <target module> import <name>`;
  * another file reaches it as `<dotted path>.<name>` or `<alias>.<name>` for any alias bound to the
    target anywhere in the tree;
  * the bare name appears in a STRING LITERAL anywhere under service/, mcp/ or scripts/ — a patch
    target, a `getattr`, an `__all__` entry. Cheap and blunt on purpose.

RE-EXPORT SURFACES ARE THE REASON THE THIRD RULE MATTERS. `service/routers/agents/shared.py` imports
`_now`, `apply_event` and `_json_loads_or` and calls none of them; all six agent routers import those
names FROM it. They are not dead, and a detector that only asked "does this file use it" would delete
them and break six modules.
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent
REPO = SERVICE.parent

_IDENT = re.compile(r"^[A-Za-z_]\w*$")
_QUOTED = re.compile(r"['\"]([A-Za-z_]\w*)['\"]")


def module_path_of(path: Path) -> str:
    """`service/routers/agents/console.py` -> `service.routers.agents.console`."""
    return ".".join(path.relative_to(REPO).with_suffix("").parts)


@lru_cache(maxsize=1)
def python_files() -> tuple[Path, ...]:
    found: list[Path] = []
    for base in ("service", "mcp", "scripts"):
        root = REPO / base
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or "node_modules" in path.parts:
                continue
            found.append(path)
    return tuple(found)


@lru_cache(maxsize=None)
def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def bindings(tree: ast.Module) -> list[tuple[str, int]]:
    """(bound name, line) for every module-level import binding."""
    out: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            out.extend((a.asname or a.name, a.lineno) for a in node.names)
        elif isinstance(node, ast.Import):
            out.extend((a.asname or a.name.split(".")[0], node.lineno) for a in node.names)
    return out


def _defined(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _loaded(tree: ast.Module) -> set[str]:
    """Names loaded anywhere in the module body, import statements excluded.

    Walks the WHOLE statement, not just its top level: a name used only inside a function body is
    used. The control-plane gate can be narrower because that module has no function bodies left.
    """
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                names.add(sub.id)
            elif isinstance(sub, ast.Attribute):
                # `json.dumps` keeps `json` alive; the Name node above already covers it, but an
                # attribute on a call result does not, so this is belt-and-braces in the safe
                # direction.
                pass
    return names


@lru_cache(maxsize=None)
def aliases_for(module: str) -> frozenset[str]:
    """Every name the tree binds the target module object to, including the module's own tail.

    DERIVED, never listed. `from service.routers.dispatch_messages import dispatch as dispatch_router`
    is a real line in this repo's tests, and a hardcoded table that missed it would mark every
    `dispatch_router.X` reach invisible and delete a live patch target.
    """
    package, _, tail = module.rpartition(".")
    found = {module, tail}
    plain = re.compile(r"^\s*from\s+" + re.escape(package) + r"\s+import\s+([^\n]+)", re.M)
    dotted = re.compile(r"^\s*import\s+" + re.escape(module) + r"(?:\s+as\s+(\w+))?", re.M)
    for path in python_files():
        text = _source(path)
        for match in plain.finditer(text):
            for piece in match.group(1).split(","):
                piece = piece.strip().rstrip(")").strip()
                if not piece:
                    continue
                name, _, alias = piece.partition(" as ")
                if name.strip() == tail:
                    found.add((alias.strip() or tail))
        for match in dotted.finditer(text):
            if match.group(1):
                found.add(match.group(1))
    return frozenset(n for n in found if _IDENT.match(n))


@lru_cache(maxsize=None)
def reached_from_elsewhere(module: str, skip: Path | None = None) -> frozenset[str]:
    """Names other files reach on the target: by from-import, by alias attribute, or as a string."""
    alias_pattern = re.compile(
        r"(?:%s)\.([A-Za-z_]\w*)" % "|".join(re.escape(a) for a in sorted(aliases_for(module))))
    from_paren = re.compile(r"from\s+" + re.escape(module) + r"\s+import\s+\(([^)]*)\)")
    from_line = re.compile(r"from\s+" + re.escape(module) + r"\s+import\s+([^(\n]+)")
    target_file = REPO / (module.replace(".", "/") + ".py")

    names: set[str] = set()
    for path in python_files():
        if path == target_file or (skip is not None and path == skip):
            continue
        text = _source(path)
        for pattern in (from_paren, from_line):
            for match in pattern.finditer(text):
                for piece in match.group(1).split(","):
                    name = piece.strip().split(" as ")[0].strip()
                    if _IDENT.match(name or ""):
                        names.add(name)
        names.update(m.group(1) for m in alias_pattern.finditer(text))
        names.update(m.group(1) for m in _QUOTED.finditer(text))
    return frozenset(names)


def dead_bindings(path: Path) -> list[tuple[str, int]]:
    """(name, line) for every import binding in `path` that nothing anywhere reaches."""
    tree = ast.parse(_source(path))
    module = module_path_of(path)
    reachable = _loaded(tree) | _defined(tree) | reached_from_elsewhere(module)
    return [(name, line) for name, line in bindings(tree)
            if name != "annotations" and name not in reachable]
