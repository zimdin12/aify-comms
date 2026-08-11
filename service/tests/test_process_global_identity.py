"""There must be exactly ONE of each process-global, and every user must share it.

THE v0.5 GATE, established BEFORE the extraction rather than after. The reconciler move is a
10-slice, 3,530-line refactor with an intentionally empty behaviour changelog, and the failure mode
that would survive every other gate is a duplicated module-global:

    from service.routers.api_v2 import _LIVE_STATE_CACHE     # binds the VALUE, not the module
    _LIVE_STATE_CACHE = {}                                   # or a fresh dict in the new module

Either shape leaves two dicts. Reads and writes land in different ones, and NOTHING fails: the
service starts, every route responds, the whole suite passes, and agent status is quietly computed
from a cache that half the code never updates. It is the same class as the `database is locked` era
this cache was introduced to end (`97a497a`), and it breaks the single-worker invariant from the
inside — one process behaving as if it were two.

So the gate runs now, on the pre-move tree, and must keep passing through all ten slices. A test
that is only written after the refactor cannot tell you the refactor introduced the problem.

Two globals are in scope, both named in the plan:
    `_LIVE_STATE_CACHE`  (service/routers/api_v2.py) — derived agent status
    `_LIVE_SCREENS`      (service/terminal_snapshot.py) — live terminal screen buffers
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1]

GLOBALS = {
    # MOVED in v0.5 slice 1a, and this line changing is the point of the gate: the owner really
    # relocated, with no compatibility alias left assigned in api_v2.py. An alias would have created
    # exactly the second-owner class this file exists to catch, while looking like a kindness.
    "_LIVE_STATE_CACHE": "service/reconcilers/status_cache.py",
    "_LIVE_SCREENS": "service/terminal_snapshot.py",
}

# AST, NOT REGEX — and that distinction is the whole gate.
#
# The first version matched `^_LIVE_STATE_CACHE\s*=` at column 0 and single-line `from ... import`.
# Review found two forms that fork the global and sail straight past it:
#
#     if SOME_FLAG:                        # executes at import time, but the assignment is
#         _LIVE_STATE_CACHE = {}           # INDENTED, so a column-0 regex sees nothing
#
#     from service.routers.api_v2 import ( # a by-value import, but the name is not on the
#         _LIVE_STATE_CACHE,               # same line as the word `import`
#     )
#
# A gate against a subtle failure cannot itself be approximate. Parsing means every syntactic form
# of the same thing is caught, including ones nobody has thought of yet.


def _import_time_nodes(tree: ast.AST):
    """Every node that RUNS on import: module body, and inside if/try/with/for/while — but never
    inside a def or class, where an assignment is a local and harmless."""
    stack = list(getattr(tree, "body", []))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for field in ("body", "orelse", "finalbody", "handlers"):
            stack.extend(getattr(node, field, []) or [])


def _module_level_assignments(text: str, name: str) -> int:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 0
    count = 0
    for node in _import_time_nodes(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                count += 1
            elif isinstance(target, ast.Tuple):  # a, _LIVE_STATE_CACHE = ...
                count += sum(1 for e in target.elts if isinstance(e, ast.Name) and e.id == name)
    return count


def _by_value_imports(text: str, name: str) -> list[str]:
    """`from x import NAME` at MODULE level, in any layout including parenthesised multi-line.

    Module level specifically, and that scope is the finding rather than laziness. The hazard is a
    binding that PERSISTS and goes stale: imported once at import time, it keeps pointing at
    whatever object existed then, so a later rebind in the owner leaves this module holding a
    corpse. A `from ... import` INSIDE a function is re-evaluated on every call and therefore cannot
    go stale — several existing tests do exactly that and they are correct.

    My first AST pass used `ast.walk`, flagged those three tests, and was wrong: strictness that
    fails correct code teaches people to weaken the gate.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    found = []
    for node in _import_time_nodes(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == name:
                    found.append(f"from {node.module or '.'} import {name} (line {node.lineno})")
    return found


def _python_files() -> list[Path]:
    return [p for p in SERVICE.rglob("*.py") if "__pycache__" not in p.parts and p.name != Path(__file__).name]


class ProcessGlobalIdentityTests(unittest.TestCase):
    def test_each_global_is_assigned_at_module_level_exactly_once(self):
        for name, owner in GLOBALS.items():
            offenders = []
            for path in _python_files():
                text = path.read_text(encoding="utf-8", errors="replace")
                count = _module_level_assignments(text, name)
                if count:
                    offenders.append((path.relative_to(SERVICE.parent).as_posix(), count))
            with self.subTest(name):
                self.assertEqual(
                    offenders, [(owner, 1)],
                    f"{name} must be created in exactly one module ({owner}). A second module-level "
                    f"assignment forks the state silently — nothing fails, the cache just stops "
                    f"agreeing with itself. Found: {offenders}",
                )

    def test_importers_bind_the_MODULE_not_the_value(self):
        """`from x import _LIVE_STATE_CACHE` binds the dict OBJECT at import time. That is safe for
        mutation and fatal for rebinding: if the owner ever reassigns it (a clear, a swap, a
        test-time reset), the importer keeps the old dict forever. Import the module and reach
        through it — `api_v2._LIVE_STATE_CACHE` — so there is one path to one object."""
        for name in GLOBALS:
            for path in _python_files():
                text = path.read_text(encoding="utf-8", errors="replace")
                bad = _by_value_imports(text, name)
                with self.subTest(f"{name} in {path.name}"):
                    self.assertEqual(
                        bad, [],
                        f"{path.name} imports {name} by value. Import the owning module instead and "
                        f"access it as `module.{name}` — a rebind in the owner would otherwise leave "
                        f"this module holding a stale object.",
                    )

    def test_the_globals_still_exist_where_the_gate_thinks_they_do(self):
        """A gate over a name that has been renamed away passes vacuously and protects nothing —
        the failure mode of every source-scanning test in this repo."""
        for name, owner in GLOBALS.items():
            with self.subTest(name):
                text = (SERVICE.parent / owner).read_text(encoding="utf-8", errors="replace")
                self.assertGreater(
                    _module_level_assignments(text, name), 0,
                    f"{name} is no longer assigned in {owner}. If it MOVED, update GLOBALS here in "
                    f"the same commit; if it was deleted, delete its entry and say so.",
                )

    def test_one_live_object_at_runtime(self):
        """The static checks above cannot see a runtime rebind. This imports each owner and asserts
        the object is the same one on re-import — the property every slice must preserve.

        Derived from GLOBALS rather than naming modules inline: slice 1a moved `_LIVE_STATE_CACHE`
        and this test hardcoded `api_v2`, so it failed for the right reason but in the wrong place.
        Nine more slices will move things; the mapping is the single place that should need editing.
        """
        import importlib

        for name, owner in GLOBALS.items():
            module_path = owner.removesuffix(".py").replace("/", ".")
            with self.subTest(name):
                module = importlib.import_module(module_path)
                obj = getattr(module, name)
                self.assertIsInstance(obj, dict)
                # Re-importing must not produce a second object.
                self.assertIs(obj, getattr(importlib.import_module(module_path), name))

    def test_the_moved_owner_left_no_alias_behind(self):
        """v0.5 slice 1a. A `_LIVE_STATE_CACHE = status_cache._LIVE_STATE_CACHE` left in api_v2 for
        convenience would create the exact second-owner class this file exists to catch, while
        looking like a kindness to callers. The reviewer named it as a condition of the slice."""
        import importlib

        api_v2 = importlib.import_module("service.routers.api_v2")
        self.assertFalse(
            hasattr(api_v2, "_LIVE_STATE_CACHE"),
            "api_v2 must reach the cache through `status_cache._LIVE_STATE_CACHE`, not re-export it",
        )


if __name__ == "__main__":
    unittest.main()


class GateCatchesTheFormsRegexMissedTests(unittest.TestCase):
    """The reviewer found this gate could pass on two forms that DO fork a global.

    The first version matched `^_LIVE_STATE_CACHE\s*=` at column 0 and single-line imports, so an
    indented module-level assignment and a parenthesised multi-line import both sailed through. A
    gate against a subtle failure cannot itself be approximate — hence AST.

    These are the reviewer's exact two shapes, plus the ones a regex would also miss, asserted
    against synthetic source so the gate is proven rather than assumed.
    """

    def _assign_count(self, src):
        return _module_level_assignments(src, "_LIVE_STATE_CACHE")

    def test_an_INDENTED_module_level_assignment_is_caught(self):
        """Executes at import time and creates a second dict; the column-0 regex saw nothing."""
        self.assertEqual(self._assign_count("if FLAG:\n    _LIVE_STATE_CACHE = {}\n"), 1)
        self.assertEqual(self._assign_count("try:\n    _LIVE_STATE_CACHE = {}\nexcept Exception:\n    pass\n"), 1)
        self.assertEqual(self._assign_count("for _ in range(1):\n    _LIVE_STATE_CACHE = {}\n"), 1)
        self.assertEqual(self._assign_count("with open('x'):\n    _LIVE_STATE_CACHE = {}\n"), 1)

    def test_an_assignment_inside_a_function_or_class_is_NOT_counted(self):
        """A local named the same thing is harmless, and flagging it would train people to weaken
        the gate."""
        self.assertEqual(self._assign_count("def f():\n    _LIVE_STATE_CACHE = {}\n"), 0)
        self.assertEqual(self._assign_count("class C:\n    _LIVE_STATE_CACHE = {}\n"), 0)
        self.assertEqual(self._assign_count("async def f():\n    _LIVE_STATE_CACHE = {}\n"), 0)

    def test_annotated_tuple_and_augmented_assignment_forms(self):
        self.assertEqual(self._assign_count("_LIVE_STATE_CACHE: dict = {}\n"), 1)
        self.assertEqual(self._assign_count("a, _LIVE_STATE_CACHE = 1, {}\n"), 1)

    def test_a_MULTILINE_parenthesised_import_is_caught(self):
        """The reviewer's second shape: still a by-value import, but the name is not on the same
        line as the word `import`."""
        src = "from service.routers.api_v2 import (\n    _live_state_get,\n    _LIVE_STATE_CACHE,\n)\n"
        self.assertEqual(len(_by_value_imports(src, "_LIVE_STATE_CACHE")), 1)

    def test_a_single_line_import_is_still_caught(self):
        src = "from service.routers.api_v2 import _LIVE_STATE_CACHE\n"
        self.assertEqual(len(_by_value_imports(src, "_LIVE_STATE_CACHE")), 1)

    def test_a_FUNCTION_LOCAL_import_is_allowed(self):
        """Re-evaluated on every call, so it can never hold a stale object. Three existing tests do
        this correctly and my first AST pass wrongly failed them."""
        src = "def f():\n    from service.routers.api_v2 import _LIVE_STATE_CACHE\n    return _LIVE_STATE_CACHE\n"
        self.assertEqual(_by_value_imports(src, "_LIVE_STATE_CACHE"), [])

    def test_importing_the_MODULE_is_always_fine(self):
        src = "from service.routers import api_v2\nx = api_v2._LIVE_STATE_CACHE\n"
        self.assertEqual(_by_value_imports(src, "_LIVE_STATE_CACHE"), [])
        self.assertEqual(self._assign_count(src), 0)
