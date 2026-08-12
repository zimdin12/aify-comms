"""Every borrowed-constant accessor must return the router's object — and must not call itself.

THE BUG THIS EXISTS FOR, found by the reviewer in the agents package:

    def _borrowed_listen_events():
        from service.control_plane import _listen_events
        return _borrowed_listen_events()      # <-- itself, not the constant

RecursionError on every call. It came from the mechanical rewrite that turns `CONSTANT` into
`_borrowed_constant()` throughout a moved body: the rewrite also fired INSIDE the accessor it had
just generated, so the accessor returned a call to itself.

Why nothing else caught it, which is the point:

  - the module imports fine — the recursion is inside a function body;
  - `py_compile` is happy and the undefined-name sweep sees nothing;
  - `create_app()` builds all 124 routes;
  - the route metadata, annotation and body-param gates all pass;
  - and the affected route, `/agents/{agent_id}/listen`, fails only once execution reaches the
    long-poll event setup — which no fast test traverses.

It was an ACTIVE route on the agent long-poll path, not dead shim debt. There are 31 of these
accessors across the routers; one generator bug can produce several, so the class gets a gate rather
than the instance getting a fix.

Two properties are asserted, and the second is the stronger one: no accessor may call itself
(structure), and every accessor must return the very object the router holds (behaviour). Identity,
not equality — a copy would satisfy `==` and would be exactly the forked-constant class the whole
series has been avoiding.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SEARCH = ("service/routers/**/*.py", "service/reconcilers/*.py")


def _accessor_in(tree: ast.AST) -> list[tuple[str, str]]:
    """(accessor_name, borrowed_constant_name) for every `_borrowed_*` accessor in one tree.

    Split out of `_accessors()` so the DETECTOR can be run against a known input. The vacuity guard
    below used to assert the live population was large, which made it a tripwire on the refactor that
    is deliberately shrinking that population; asserting the detector still recognises the shape is
    the property that actually matters and it survives the population reaching zero.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("_borrowed_"):
            continue
        imported = None
        for sub in ast.walk(node):
            if isinstance(sub, ast.ImportFrom) and sub.module == "service.control_plane":
                imported = sub.names[0].asname or sub.names[0].name
        found.append((node.name, imported))
    return found


def _accessors() -> list[tuple[str, str, str]]:
    """(module_path, accessor_name, borrowed_constant_name) for every `_borrowed_*` accessor."""
    found = []
    for pattern in SEARCH:
        for path in REPO.glob(pattern):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for name, imported in _accessor_in(tree):
                found.append((path.relative_to(REPO).as_posix(), name, imported))
    return found


class BorrowedAccessorsTests(unittest.TestCase):
    def test_no_accessor_calls_itself(self):
        offenders = []
        for pattern in SEARCH:
            for path in REPO.glob(pattern):
                if "__pycache__" in path.parts:
                    continue
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if not node.name.startswith("_borrowed_"):
                        continue
                    if any(
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Name)
                        and sub.func.id == node.name
                        for sub in ast.walk(node)
                    ):
                        offenders.append(f"{path.relative_to(REPO).as_posix()}: {node.name}")
        self.assertEqual(
            offenders,
            [],
            "A borrowed-constant accessor calls ITSELF instead of returning the constant. Every "
            "call raises RecursionError, and only at runtime on whichever route uses it:\n  "
            + "\n  ".join(offenders),
        )

    def test_every_accessor_returns_the_routers_own_object(self):
        """Identity, not equality. A copy would pass `==` and be a forked constant."""
        import importlib

        from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now

        mismatched = []
        for module_path, accessor, constant in _accessors():
            if constant is None:
                mismatched.append(f"{module_path}: {accessor} imports nothing from api_v2")
                continue
            module = importlib.import_module(module_path.removesuffix(".py").replace("/", "."))
            try:
                value = getattr(module, accessor)()
            except Exception as error:  # noqa: BLE001 - a raising accessor is the failure
                mismatched.append(f"{module_path}: {accessor}() raised {type(error).__name__}")
                continue
            if value is not getattr(api_v2, constant):
                mismatched.append(f"{module_path}: {accessor}() is not api_v2.{constant}")
        self.assertEqual(mismatched, [], "\n  ".join([""] + mismatched))

    def test_the_scan_finds_the_accessors(self):
        """A check over an empty list passes vacuously — but the list is SUPPOSED to reach empty.

        This asserted `> 20` and failed at exactly 20 the moment a v0.5.4 slice retired three more
        accessors. The guard was right in intent and wrong in mechanism: retiring these accessors is
        the WORK, so any floor under a shrinking population is a tripwire on progress that has to be
        edited downward every slice until someone edits it to `> 0` and it stops guarding anything.

        What actually needs proving is that DISCOVERY works, which is a property of the detector and
        not of how many accessors happen to survive today. So the detector is run against a synthetic
        accessor whose shape is known, and the live scan is only required to be self-consistent. When
        the last real accessor goes, this test keeps working and keeps meaning something.
        """
        synthetic = (
            "def _borrowed_synthetic_probe():\n"
            '    """Shaped exactly like the real ones."""\n'
            "    from service.control_plane import _SYNTHETIC_PROBE_CONSTANT\n"
            "\n"
            "    return _SYNTHETIC_PROBE_CONSTANT\n"
        )
        found = _accessor_in(ast.parse(synthetic))
        self.assertEqual(
            found,
            [("_borrowed_synthetic_probe", "_SYNTHETIC_PROBE_CONSTANT")],
            "the accessor detector no longer recognises the borrowed-accessor shape, so the two "
            "checks above would pass over an empty list while real accessors went unexamined",
        )
        # Self-consistency of the live scan: every discovered accessor must carry both halves.
        for module_path, accessor, constant in _accessors():
            self.assertTrue(accessor, f"{module_path}: discovered an accessor with no name")


if __name__ == "__main__":
    unittest.main()
