"""A moved name gets exactly one owner. A copy is a fork waiting to drift.

FOUND BY ACCIDENT, WHICH IS THE PROBLEM. While measuring the analytics domain,
`_ENVIRONMENT_HEARTBEAT_STATUSES` turned out to be declared TWICE: in `service/env_status.py`, where
v0.5 slice 2 moved it, and still in `service/routers/api_v2.py`, which was supposed to have given it
up. Equal values, two distinct objects, and nothing would have failed if either had been edited — the
two would simply have disagreed, and the symptom would have been an environment reading `online` on
one code path and not the other.

The reviewer's ruling on that very slice was that a moved constant gets one owner and never a second
copy, precisely because "no copies, no drift". The ruling was right and the execution missed it, and
nothing in the suite could tell.

`test_process_global_identity.py` does not cover this: it guards a hand-maintained registry of
mutable process state. This is the complementary check and needs no registry — it compares what
api_v2 declares against what every leaf declares, and every disagreement is a defect.

WHAT IS NOT A FORK, and why the distinction is the whole test:
  - `logger` and `router`: every module legitimately has its own.
  - A BORROW SHIM: a function that imports the real implementation from api_v2 and delegates. There
    is still exactly one owner; the shim is the documented way a leaf reaches back without a
    module-level cycle. There are 31 of them and they are all fine.
A fork is a SECOND INDEPENDENT DECLARATION — one that does not delegate.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
ROUTER = REPO / "service" / "routers" / "api_v2.py"

#: Names every module is expected to declare for itself.
PER_MODULE = {"logger", "router"}

LEAF_GLOBS = (
    "service/api_core/*.py",
    "service/reconcilers/*.py",
    "service/routers/*.py",
    "service/env_status.py",
    "service/clock.py",
    "service/status_engine.py",
)


def _module_level(path: Path) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = node
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
    return out


def _is_delegating_shim(node: ast.AST) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    return "from service.routers.api_v2 import" in ast.unparse(node)


def _leaf_paths() -> list[Path]:
    paths: list[Path] = []
    for pattern in LEAF_GLOBS:
        paths.extend(REPO.glob(pattern))
    return [p for p in paths if p.name != "api_v2.py" and p.name != "__init__.py"]


class NoForkedDeclarationsTests(unittest.TestCase):
    def test_no_name_is_declared_independently_in_both_the_router_and_a_leaf(self):
        router_names = _module_level(ROUTER)
        forks = []
        for leaf in _leaf_paths():
            for name, node in _module_level(leaf).items():
                if name.startswith("__") or name in PER_MODULE or name not in router_names:
                    continue
                if _is_delegating_shim(node):
                    continue
                forks.append(f"{name} — declared in api_v2.py AND {leaf.relative_to(REPO).as_posix()}")
        self.assertEqual(
            forks,
            [],
            "A name has two independent declarations, so the two can drift apart with nothing "
            "failing:\n  "
            + "\n  ".join(forks)
            + "\nGive it ONE owner and import it. A delegating borrow shim is fine; a second "
            "declaration is not.",
        )

    def test_the_sweep_can_actually_see_the_shims_it_excludes(self):
        """If the shim detection broke, this test would pass vacuously by excluding everything."""
        shims = 0
        router_names = _module_level(ROUTER)
        for leaf in _leaf_paths():
            for name, node in _module_level(leaf).items():
                if name in router_names and _is_delegating_shim(node):
                    shims += 1
        self.assertGreater(shims, 10, "borrow-shim detection looks broken; it found almost none")

    def test_the_constant_that_was_actually_forked_has_one_owner(self):
        """Named explicitly, because this is the one that really happened."""
        from service import env_status
        from service.routers import api_v2

        self.assertIs(
            api_v2._ENVIRONMENT_HEARTBEAT_STATUSES,
            env_status._ENVIRONMENT_HEARTBEAT_STATUSES,
            "api_v2 must import this from env_status, not declare its own copy",
        )


if __name__ == "__main__":
    unittest.main()
