"""There must be exactly ONE of each process-global, and every user must share it.

THE v0.5 GATE, established BEFORE the extraction rather than after. The reconciler move is a
10-slice, 3,530-line refactor with an intentionally empty behaviour changelog, and the failure mode
that would survive every other gate is a duplicated module-global:

    from service.control_plane import _LIVE_STATE_CACHE     # binds the VALUE, not the module
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
    # MOVED in v0.5.1g. The settings cache is the one whose forking would be hardest to spot:
    # a second module-level assignment gives each importer its own dict, so writers and readers
    # silently stop sharing and every caller just sees a slightly stale settings view.
    "_SETTINGS_CACHE": "service/api_core/settings.py",
    # MOVED in v0.5.1i, and it is not a constant despite the SHOUTING_CASE: it is an
    # itertools.count() that mints control ids. Two module-level assignments would give two
    # importers two independent counters, and the symptom would not be an error -- it would be
    # DUPLICATE control ids from different call paths, found much later as controls that seem
    # to collide.
    "_CONTROL_ID_COUNTER": "service/api_core/events.py",
    # Moved with the appenders in v0.5.1i. Mutable state again: it drives the prune cadence,
    # so two copies would each count to the threshold separately and prune at the wrong times.
    "_terminal_event_counts": "service/api_core/events.py",
    # Moved with the first route DOMAIN in v0.5.2b. Two copies would give two quota caches and
    # the only symptom would be usage readings that disagree depending which path served them.
    "_OPENAI_POOL_CACHE": "service/routers/usage.py",
    # Moved in v0.5.4, and this entry IS the receipt the move was waiting for: an earlier slice pulled
    # `_terminal_prompt_hint_from_raw` back out precisely because relocating the cache it reads is a
    # process-identity change rather than a relocation. A second copy would not raise -- each importer
    # would get its own dict, every lookup would miss, and the only symptom would be the expensive
    # screen reconstruction running on every poll instead of once per 5s per agent. Slower, never
    # wrong, and therefore invisible.
    "_PROMPT_HINT_CACHE": "service/api_core/terminal_text.py",
    # Moved in v0.5.4 to the module that already owned the OTHER waiter registry. Six agent-surface
    # modules reach it through one borrow accessor and `routers/agents/config.py` inserts into it, so
    # two copies would register a waiter in one dict and fire the wake into the other: `comms_listen`
    # would hang to its timeout, return empty, and log nothing. A hang is the hardest of these to
    # trace back to a duplicated global, which is why it is worth a line here.
    "_listen_events": "service/longpoll.py",
    # THE SIBLING OF THE LINE ABOVE, and it was the one left out. `longpoll.py` holds two waiter
    # registries and its own comment says so — "this module already owns the first one" — but only
    # the second got a line here. `_waiters` is a `defaultdict(set)` of pending futures: `wait()`
    # registers into it and `notify()` resolves out of it, so two copies mean a waiter sits in one
    # dict while the wake fires into the other. The long poll then holds until MAX_WAIT_S and the
    # caller sees a spurious claim timeout — the same silent-hang class as `_listen_events`, which is
    # exactly why that one was judged worth a line.
    #
    # Found by scanning `service/` for module-level mutable state absent from this table: of
    # everything not already here, `_waiters` was the only real omission.
    "_waiters": "service/longpoll.py",
    # THE QUOTA CACHES. `usage_cache.py`'s own docstring names the single-worker constraint, but
    # neither of its two structures had a line here. `_USAGE_CACHE` holds the per-pool snapshots the
    # dashboard and `comms_usage` read; two copies give two caches and the only symptom is usage
    # readings that disagree depending which path served them — which is verbatim the reason already
    # written beside `_OPENAI_POOL_CACHE` above, for the same kind of state one layer along.
    "_USAGE_CACHE": "service/usage_cache.py",
    # Its sibling, and the one nothing named at all. Token rows accumulate here and
    # `summarize_consumption` folds them; two copies split the accumulation, so every total is short
    # by whatever the other copy collected. Under-reporting, never an error.
    "_CONSUMPTION_ROWS": "service/usage_cache.py",
}

# NOT LISTED, and the gate itself is why: `service/ntfy.py::_RELAY`. It is process-global state of
# the same kind — "one relay per process", says that module — but it is `None` at import and only
# becomes an object on first use. This file's runtime half asserts the SAME object survives a
# re-import, and its own guard refuses immutable scalars because identity across re-import proves
# nothing for them. Adding `_RELAY` here made that guard fire, which is the guard working. Weakening
# it to accept a None would have bought one entry at the cost of every entry's meaning, so the relay
# is left for a mechanism that can actually check it (the singleton gate covers this shape for
# `TERMINAL_OUTPUT_WRITES`).

# AST, NOT REGEX — and that distinction is the whole gate.
#
# The first version matched `^_LIVE_STATE_CACHE\s*=` at column 0 and single-line `from ... import`.
# Review found two forms that fork the global and sail straight past it:
#
#     if SOME_FLAG:                        # executes at import time, but the assignment is
#         _LIVE_STATE_CACHE = {}           # INDENTED, so a column-0 regex sees nothing
#
#     from service.control_plane import ( # a by-value import, but the name is not on the
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
                # NOT `isinstance(obj, dict)`. That was incidental to every tracked global being a
                # dict, and it broke the moment v0.5.1i registered `_CONTROL_ID_COUNTER`, an
                # itertools.count(). The property under test is IDENTITY across re-import; what the
                # object must not be is an immutable scalar, because for those "same object" is
                # meaningless (Python interns small ints and short strings) and the gate would pass
                # vacuously on a forked value.
                self.assertNotIsInstance(
                    obj, (int, float, bool, str, bytes, tuple, frozenset, type(None)),
                    f"{name} is an immutable scalar; identity across re-import proves nothing for it",
                )
                # Re-importing must not produce a second object.
                self.assertIs(obj, getattr(importlib.import_module(module_path), name))

    def test_the_moved_owner_left_no_alias_behind(self):
        """v0.5 slice 1a. A `_LIVE_STATE_CACHE = status_cache._LIVE_STATE_CACHE` left in api_v2 for
        convenience would create the exact second-owner class this file exists to catch, while
        looking like a kindness to callers. The reviewer named it as a condition of the slice."""
        import importlib

        api_v2 = importlib.import_module("service.control_plane")
        self.assertFalse(
            hasattr(api_v2, "_LIVE_STATE_CACHE"),
            "api_v2 must reach the cache through `status_cache._LIVE_STATE_CACHE`, not re-export it",
        )




class GateCatchesTheFormsRegexMissedTests(unittest.TestCase):
    r"""The reviewer found this gate could pass on two forms that DO fork a global.

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
        src = "from service.control_plane import (\n    _live_state_get,\n    _LIVE_STATE_CACHE,\n)\n"
        self.assertEqual(len(_by_value_imports(src, "_LIVE_STATE_CACHE")), 1)

    def test_a_single_line_import_is_still_caught(self):
        src = "from service.control_plane import _LIVE_STATE_CACHE\n"
        self.assertEqual(len(_by_value_imports(src, "_LIVE_STATE_CACHE")), 1)

    def test_a_FUNCTION_LOCAL_import_is_allowed(self):
        """Re-evaluated on every call, so it can never hold a stale object. Three existing tests do
        this correctly and my first AST pass wrongly failed them."""
        src = "def f():\n    from service.control_plane import _LIVE_STATE_CACHE\n    return _LIVE_STATE_CACHE\n"
        self.assertEqual(_by_value_imports(src, "_LIVE_STATE_CACHE"), [])

    def test_importing_the_MODULE_is_always_fine(self):
        src = "from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now\nx = api_v2._LIVE_STATE_CACHE\n"
        self.assertEqual(_by_value_imports(src, "_LIVE_STATE_CACHE"), [])
        self.assertEqual(self._assign_count(src), 0)


if __name__ == "__main__":
    unittest.main()
