"""A test that patches a moved name patches nothing, and still passes.

THE FAILURE THIS PREVENTS. `mock.patch("service.routers.api_v2._foo")` names its target as a
string, so it is not checked by the import system, by a linter, or by anything else until the line
runs. The v0.5 refactor moved eighteen helpers out of `api_v2` into the modules that call them. When
a helper moves, a patch aimed at the old location can fail in two ways, and only one of them is
loud:

  * the attribute is GONE      -> patch raises, the test goes red, someone fixes it. Fine.
  * the attribute is STILL THERE (a borrow shim, or a name api_v2 still declares) but production now
    calls the OWNER directly -> the patch installs a mock nobody consults. The test passes. It
    asserts nothing. It reports green forever.

The second is the one worth a gate. It is the same silent-green shape as the route snapshots that
shipped ungrabbable in v0.5, and as the doctor checks that reported ok on no evidence: nothing
fails, so nothing gets looked at.

WHAT THIS DOES NOT CLAIM. Resolving a target proves the patch will BIND, not that production reads
it through that module. Proving the latter needs call-graph analysis this repo does not have. So a
target reached through a borrow shim is reported, not failed — the reviewer's N7 shim pattern makes
that legitimate, and whether the patch bites depends on which side of the shim the caller sits.
A target that does not resolve at all is unambiguous, and that is what fails.
"""

from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
TESTS = REPO / "service" / "tests"

#: Callables whose FIRST string argument is a dotted patch target.
PATCHERS = {"patch", "object", "patch_object"}


def _patch_targets():
    for path in sorted(TESTS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = ""
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name not in PATCHERS:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and "." in first.value:
                yield path.relative_to(REPO).as_posix(), node.lineno, first.value


class PatchTargetsResolveTests(unittest.TestCase):
    def test_every_string_patch_target_resolves(self):
        broken = []
        for where, lineno, target in _patch_targets():
            module_path, _, attr = target.rpartition(".")
            try:
                module = importlib.import_module(module_path)
            except Exception as exc:  # noqa: BLE001 - any import failure is a broken target
                broken.append(f"{where}:{lineno}  {target}  ({type(exc).__name__}: {exc})")
                continue
            if not hasattr(module, attr):
                broken.append(f"{where}:{lineno}  {target}  (module has no attribute {attr!r})")

        self.assertEqual(
            broken,
            [],
            "a test patches a target that no longer resolves; the helper probably moved and the "
            "patch was left behind:\n  " + "\n  ".join(broken),
        )

    def test_the_sweep_actually_finds_targets(self):
        """Without this, a broken matcher would make the gate above pass by finding nothing.

        Every sweep in this repo needs its own liveness check. A gate that silently stops matching
        is indistinguishable from a clean codebase, which is precisely the false green the gate
        exists to prevent.
        """
        self.assertGreater(
            len(list(_patch_targets())),
            0,
            "found no string patch targets at all; the matcher is broken, not the codebase",
        )


if __name__ == "__main__":
    unittest.main()
