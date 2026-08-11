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

import re
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1]

GLOBALS = {
    "_LIVE_STATE_CACHE": "service/routers/api_v2.py",
    "_LIVE_SCREENS": "service/terminal_snapshot.py",
}

# An ASSIGNMENT at module level (column 0), which is what creates a second instance. Assignments
# inside a function are locals and harmless; `global X` followed by mutation is fine too.
def _module_level_assignments(text: str, name: str) -> int:
    return len(re.findall(rf"^{re.escape(name)}\s*(?::[^=]+)?=", text, re.MULTILINE))


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
                bad = re.findall(rf"^from\s+\S+\s+import\s+[^\n]*\b{re.escape(name)}\b", text, re.MULTILINE)
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
        """The static checks above cannot see a runtime rebind. This imports both owners and asserts
        the objects are the same ones the modules expose — the property the slices must preserve."""
        import importlib

        api_v2 = importlib.import_module("service.routers.api_v2")
        snapshot = importlib.import_module("service.terminal_snapshot")
        self.assertIsInstance(api_v2._LIVE_STATE_CACHE, dict)
        self.assertIsInstance(snapshot._LIVE_SCREENS, dict)
        # Re-importing must not produce a second object.
        self.assertIs(api_v2._LIVE_STATE_CACHE, importlib.import_module("service.routers.api_v2")._LIVE_STATE_CACHE)
        self.assertIs(snapshot._LIVE_SCREENS, importlib.import_module("service.terminal_snapshot")._LIVE_SCREENS)


if __name__ == "__main__":
    unittest.main()
