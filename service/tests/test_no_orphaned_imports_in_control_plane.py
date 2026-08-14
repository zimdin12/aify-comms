"""`service/control_plane.py` must not import a name that nothing anywhere reaches.

WHY THIS FILE AND NOT THE WHOLE TREE. This is where the defect actually happened, at a scale nothing
noticed. In v0.5.4 the control plane carried 309 import bindings and 180 of them were reached by
NOTHING — not by the module body, not by another file, not by a patch target. They accumulated one
per extraction across the whole v0.5.x series: a slice moves a helper out to a leaf, adds an import
back for the callers still here, and later the callers move out too. The import stays. Nothing fails,
because an unused import is valid Python.

They cost 148 lines in a file whose line count this series reports as evidence, and they made the
control plane read as coupled to two dozen modules it does not use — including every request model in
`service.models`, for routes that left in v0.5.2.

THE RULE THIS ENFORCES is the second half of the extraction receipt: measure the SOURCE as well as the
destination. A slice that moves code out and leaves the import behind has not finished.

WHAT COUNTS AS REACHED, deliberately over-broad. A false PASS here costs one dead line; a false
FAILURE blocks a legitimate access path and sends the next reader hunting. A binding is reached if:

  * the module body loads the name;
  * the module also defines it (the import is shadowed, not dead);
  * another file does `from service.control_plane import <name>`;
  * another file reaches it as `service.control_plane.<name>`, or through the aliases this repo's
    tests bind the module to (`api_v2.<name>`, `control_plane.<name>`, `cp.<name>`) — the test suite
    genuinely calls helpers that way, e.g. `await api_v2._prune_terminal_history(db, ...)`;
  * the bare name appears in a string literal anywhere under service/, mcp/ or scripts/ — a patch
    target, a `getattr`, an `__all__` entry. Cheap and blunt on purpose.

The last two are why this gate is scoped to one file: applied tree-wide the alias rule would need a
per-module alias table, and getting that wrong produces exactly the confident-looking wrong answer
this series keeps finding in its own measurement tools.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent
REPO = SERVICE.parent
TARGET = SERVICE / "control_plane.py"

#: How the test suite and scripts bind the control-plane module object.
MODULE_ALIASES = ("service.control_plane", "control_plane", "api_v2", "cp")

# TWO patterns, not one. A single `\(?([^)]*)\)?` looks like it covers both shapes and does not: the
# negated class matches newlines, so on a plain one-line `from service.control_plane import X` it runs
# past the end of the statement to the next `)` ANYWHERE in the file and the captured text splits into
# nothing that parses as an identifier. The name is then silently invisible. That bug deleted two live
# imports before the suite caught it, and it is the reason this file has an anti-vacuity test.
_FROM_IMPORT_PAREN = re.compile(r"from\s+service\.control_plane\s+import\s+\(([^)]*)\)")
_FROM_IMPORT_LINE = re.compile(r"from\s+service\.control_plane\s+import\s+([^(\n]+)")
_DOTTED = re.compile(r"(?:%s)\.([A-Za-z_]\w*)" % "|".join(re.escape(a) for a in MODULE_ALIASES))
_QUOTED = re.compile(r"['\"]([A-Za-z_]\w*)['\"]")
_IDENT = re.compile(r"^[A-Za-z_]\w*$")


def _tree():
    return ast.parse(TARGET.read_text(encoding="utf-8", errors="replace"))


def _bindings(tree: ast.Module) -> list[tuple[str, int]]:
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
    """Names loaded anywhere in the module body, imports excluded."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                names.add(sub.id)
    return names


def _python_files():
    for base in ("service", "mcp", "scripts"):
        root = REPO / base
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or "node_modules" in path.parts:
                continue
            if path == TARGET or path == Path(__file__).resolve():
                # Skipping this file matters: it names control-plane symbols in its own docstring and
                # in its anti-vacuity case, and the string-literal rule below would otherwise let the
                # gate mark a name reachable purely because this test mentioned it.
                continue
            yield path


def _reached_from_elsewhere() -> set[str]:
    names: set[str] = set()
    for path in _python_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in (_FROM_IMPORT_PAREN, _FROM_IMPORT_LINE):
            for m in pattern.finditer(text):
                for piece in m.group(1).split(","):
                    nm = piece.strip().split(" as ")[0].strip()
                    if _IDENT.match(nm or ""):
                        names.add(nm)
        names.update(m.group(1) for m in _DOTTED.finditer(text))
        names.update(m.group(1) for m in _QUOTED.finditer(text))
    return names


def orphaned_bindings() -> list[tuple[str, int]]:
    tree = _tree()
    keep = _loaded(tree) | _defined(tree) | _reached_from_elsewhere()
    return [(name, line) for name, line in _bindings(tree) if name not in keep]


class NoOrphanedImportsTests(unittest.TestCase):
    def test_every_import_in_the_control_plane_is_reached_by_something(self):
        orphans = orphaned_bindings()
        detail = "\n".join(
            "  control_plane.py:%d  %s" % (line, name) for name, line in sorted(orphans, key=lambda x: x[1])
        )
        self.assertEqual(
            orphans,
            [],
            "%d import binding(s) in service/control_plane.py are reached by nothing — not by this "
            "module, not by another file, not by a patch target. Each is almost certainly the residue "
            "of an extraction whose callers later moved out too. Delete them; do not add a suppression."
            "\n%s" % (len(orphans), detail),
        )

    def test_the_gate_can_actually_fail(self):
        """Anti-vacuity: a name nothing reaches must be reported as orphaned.

        Without this the whole file would pass just as happily if `_bindings` returned nothing, or if
        the reachability set accidentally swallowed every name — which is the failure mode of a gate
        built from an over-broad "is it mentioned anywhere" rule.
        """
        tree = _tree()
        keep = _loaded(tree) | _defined(tree) | _reached_from_elsewhere()
        invented = "_a_name_no_module_in_this_repo_mentions_zzz"
        self.assertNotIn(invented, keep, "the reachability set must not accept an invented name")

    def test_a_genuinely_used_import_is_not_reported(self):
        """The other direction: something a module plainly uses must never be flagged.

        THIS USED TO DRIVE THE PREDICATE FROM THE LIVE FILE, and asserted the control plane still
        imported at least one name its own body loads. That premise held for the whole series and then
        stopped: v0.5.4 finished emptying the module. `service/control_plane.py` now parses to a
        docstring, twenty-three imports and three assignments — no functions, no classes, nothing that
        loads anything. It is a pure re-export surface, which is the END STATE the decomposition was
        aiming at, so the control failed because the work succeeded.

        A property of `_loaded` does not need the live file, so it is exercised against a synthetic
        module instead. `test_the_control_plane_is_now_a_pure_re_export_surface` records the fact
        separately, where it is an observation rather than a precondition.
        """
        tree = ast.parse(
            "\n".join([
                "import json",
                "from service.db import get_db",
                "VALUE = json.dumps({})",
            ])
        )
        loaded = _loaded(tree)
        bound = {name for name, _ in _bindings(tree)}
        self.assertIn("json", loaded, "_loaded must see a name the module body uses")
        self.assertNotIn("get_db", loaded, "...and must not invent one it does not")
        self.assertEqual(bound & loaded, {"json"})

    def test_the_control_plane_is_now_a_pure_re_export_surface(self):
        """Recorded because it is the end state of the series, and because it is easy to undo.

        The module has no executable body left. Anything added back — a helper, a constant, a branch —
        is a NEW declaration in a file whose entire remaining purpose is to name where things went, and
        should go to a leaf instead. This is not a rule the gate can enforce, so it is written down as
        the thing a reader should notice before adding to it.
        """
        body = [n for n in _tree().body
                if not isinstance(n, (ast.Import, ast.ImportFrom, ast.Expr))]
        offenders = [type(n).__name__ for n in body if not isinstance(n, ast.Assign)]
        self.assertEqual(
            offenders, [],
            "the control plane declares executable code again; it is meant to be imports and "
            "re-export assignments only",
        )


if __name__ == "__main__":
    unittest.main()
