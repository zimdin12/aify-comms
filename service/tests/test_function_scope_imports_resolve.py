"""Every function-scope `from service... import name` must resolve. A broken one fails at CALL time.

THE SHAPE OF THIS FAILURE. An import inside a function body is not executed until the function is
CALLED. So if the name it imports moves, or its module is deleted, and the import is not repointed:

  - the module still imports cleanly;
  - `create_app()` still builds every route;
  - `py_compile` is happy, the undefined-name sweep sees nothing;
  - every test that does not exercise this particular path passes;
  - and the first real caller gets ImportError, in production, on a path that by definition is not
    the one anybody was testing.

This gate was written for the v0.5.x "borrow shims", function-scope imports from the control-plane
module that let a leaf reach back without a module-level cycle. Those are all gone and so is that
module (v0.7.0), but the hazard never belonged to them: it belongs to every function-scope import.
So every one under service/ is resolved against the live module. Cheap, and it converts a
production-only failure into a red test.
"""

from __future__ import annotations

import ast
import importlib
import tempfile
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent


def function_scope_imports(path: Path) -> list[tuple[int, str, str]]:
    """(line, module, name) for every `from service... import name` inside a function body."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    found = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.ImportFrom) and not node.level:
                module = node.module or ""
                if module == "service" or module.startswith("service."):
                    for alias in node.names:
                        found.add((node.lineno, module, alias.name))
    return sorted(found)


def resolves(module: str, name: str) -> bool:
    """True if `from module import name` would succeed: an attribute, or a submodule."""
    try:
        owner = importlib.import_module(module)
    except ImportError:
        return False
    if hasattr(owner, name):
        return True
    try:
        importlib.import_module(f"{module}.{name}")
    except ImportError:
        return False
    return True


def _product_files():
    for path in sorted(SERVICE.rglob("*.py")):
        if "tests" not in path.parts and "__pycache__" not in path.parts:
            yield path


class FunctionScopeImportsResolveTests(unittest.TestCase):
    def test_every_function_scope_import_resolves(self):
        broken = [
            f"{path.relative_to(SERVICE.parent).as_posix()}:{line}  from {module} import {name}"
            for path in _product_files()
            for line, module, name in function_scope_imports(path)
            if not resolves(module, name)
        ]
        self.assertEqual(
            broken,
            [],
            "A function-scope import names something that does not exist. It does NOT fail at import; "
            "it fails the first time that code path runs, in production:\n  " + "\n  ".join(broken)
            + "\nRepoint it at the module that owns the name now.",
        )

    def test_the_scan_finds_real_function_scope_imports(self):
        """Anti-vacuity on the live tree: the repo has dozens of these, so finding none means the
        scan broke rather than the imports vanishing."""
        total = sum(len(function_scope_imports(path)) for path in _product_files())
        self.assertGreater(total, 10, f"only {total} function-scope service imports found")

    def test_the_detector_and_the_resolver_say_both_yes_and_no(self):
        """Positive and negative control in one sample: a real name, a missing name, a missing module,
        a submodule, and a module-scope import that must not be counted."""
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "sample.py"
            sample.write_text(
                "from service.clock import now as module_scope\n"
                "def f():\n"
                "    from service.clock import now\n"
                "    from service.clock import _a_name_clock_does_not_have_zzz\n"
                "    from service.module_that_does_not_exist_zzz import thing\n"
                "    from service.api_core import tuning\n"
                "async def g():\n"
                "    from service.control_plane import anything\n",
                encoding="utf-8",
            )
            found = function_scope_imports(sample)
        self.assertEqual(
            [(module, name) for _, module, name in found],
            [
                ("service.clock", "now"),
                ("service.clock", "_a_name_clock_does_not_have_zzz"),
                ("service.module_that_does_not_exist_zzz", "thing"),
                ("service.api_core", "tuning"),
                ("service.control_plane", "anything"),
            ],
        )
        verdicts = [resolves(module, name) for _, module, name in found]
        self.assertEqual(verdicts, [True, False, False, True, False])


if __name__ == "__main__":
    unittest.main()
