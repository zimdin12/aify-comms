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
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not node.name.startswith("_borrowed_"):
                    continue
                imported = None
                for sub in ast.walk(node):
                    if isinstance(sub, ast.ImportFrom) and sub.module == "service.control_plane":
                        imported = sub.names[0].asname or sub.names[0].name
                found.append((path.relative_to(REPO).as_posix(), node.name, imported))
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
        """A check over an empty list passes vacuously."""
        self.assertGreater(len(_accessors()), 20, "accessor discovery looks broken")


if __name__ == "__main__":
    unittest.main()
