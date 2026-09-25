"""`service/api_core/` and `service/reconcilers/` never import a router.

This is the layer rule in docs/ARCHITECTURE.md: requests enter at a router and descend, and nothing
below the routers reaches back up. The file keeps its old name because other documents cite it; until
v0.7.0 the thing a leaf must not import was the "carrier", the control-plane module, the helper
library left behind when the route domains moved out. That module is deleted, and the rule it stood
for is stated here in its general form: a leaf that imports a router inverts the dependency direction
the whole decomposition rests on.

WHAT IT COST TO FIND OUT. Moving four small helpers out of a router's shared module into three
api_core leaves, an extraction script took the top-level `def` of each name in the source module. For
three of the four, that `def` was not the implementation but a delegating BORROW SHIM whose body
imported upward. So the leaves ended up importing the module they existed to drain. Nothing failed:
the suite stayed green, `create_app()` built every route, and the undefined-name sweep saw nothing,
because a function-scope import is a perfectly valid binding.

SCOPE: module-scope AND function-scope imports both count. Function-scope is the shape that hid, so a
check that only walked module bodies would have missed the entire defect. A function-scope import
cannot close an import-time cycle (`test_no_import_cycles.py` covers those), but it still makes a leaf
depend on the HTTP layer.
"""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent
REPO = SERVICE.parent

ROUTERS = "service.routers"

#: The layers below the routers. `docs/ARCHITECTURE.md` names these two in the rule.
LEAF_DIRS = ("api_core", "reconcilers")


def _router_imports(path: Path):
    """(line, what) for every import of a router module in one file, at any scope."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == ROUTERS or module.startswith(ROUTERS + "."):
                yield node.lineno, f"from {module} import " + ", ".join(a.name for a in node.names)
            elif module == "service" and any(a.name == "routers" for a in node.names):
                yield node.lineno, "from service import routers"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == ROUTERS or alias.name.startswith(ROUTERS + "."):
                    yield node.lineno, f"import {alias.name}"


def _leaf_files():
    for directory in LEAF_DIRS:
        for path in sorted((SERVICE / directory).rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


class LeavesDoNotImportARouterTests(unittest.TestCase):
    def test_no_leaf_imports_a_router(self):
        offenders = [
            f"{path.relative_to(REPO).as_posix()}:{lineno}  {what}"
            for path in _leaf_files()
            for lineno, what in _router_imports(path)
        ]
        self.assertEqual(
            offenders,
            [],
            "a module under service/api_core/ or service/reconcilers/ imports a router, which inverts "
            "the dependency direction the layering depends on:\n  " + "\n  ".join(offenders)
            + "\nIf an extraction produced this, the body that was moved was probably a delegating "
            "shim rather than the implementation; move the real body down instead.",
        )

    def test_the_scan_reads_both_leaf_directories(self):
        """A glob that stopped matching would make the test above pass over nothing."""
        files = list(_leaf_files())
        for directory in LEAF_DIRS:
            self.assertTrue(
                any(path.parent.name == directory for path in files),
                f"no files found under service/{directory}/",
            )
        self.assertGreater(len(files), 20, f"only {len(files)} leaf files found")

    def test_the_DETECTOR_sees_every_import_shape(self):
        """At zero offenders, a broken detector and a clean tree read the same. Hand it each shape."""
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "fake_leaf.py"
            sample.write_text(
                "from service.routers.agents.shared import logger\n"
                "import service.routers.sessions\n"
                "from service import routers\n"
                "from service.api_core.clock import now\n"
                "from service.routers_are_not_this import x\n"
                "def _borrowed():\n"
                "    from service.routers.channels import helper\n"
                "    return helper()\n",
                encoding="utf-8",
            )
            found = [what for _, what in _router_imports(sample)]
        self.assertEqual(
            found,
            [
                "from service.routers.agents.shared import logger",
                "import service.routers.sessions",
                "from service import routers",
                "from service.routers.channels import helper",
            ],
            "the detector must flag module-scope and function-scope router imports, and nothing else",
        )

    def test_the_DETECTOR_finds_real_router_imports(self):
        """Positive control on a real file: the composition module imports every router domain."""
        found = list(_router_imports(SERVICE / "routers" / "api_v2.py"))
        self.assertGreater(len(found), 10, "the detector found no router imports in the composition module")


if __name__ == "__main__":
    unittest.main()
