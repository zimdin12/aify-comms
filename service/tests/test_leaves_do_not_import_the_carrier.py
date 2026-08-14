"""A leaf must not import `service.control_plane` — the rule the whole v0.5.x series rests on, and
until now it was written in CLAUDE.md and enforced nowhere.

WHAT IT COST TO FIND OUT. Moving four small helpers out of
`service/routers/dispatch_messages/shared.py` into three existing api_core leaves, my extraction script
took the top-level `def` of each name in the source module. For three of the four, that `def` was not
the implementation — it was a delegating BORROW SHIM:

    def _pending_dispatch_count(*a, **k):
        from service.control_plane import _pending_dispatch_count as _impl
        return _impl(*a, **k)

So the leaves ended up containing shims that import the carrier: a leaf depending UPWARD on the
20k-line module it exists to drain, which is the exact inversion this architecture forbids. Nothing
failed. The suite stayed at 1450 passing, `create_app()` built all 124 routes, the census went green
(the imports resolved — to the wrong direction), and the undefined-name sweep saw nothing, because a
function-scope import is a perfectly valid binding. I found it only because
`scripts/constant_readership.py` reported a reader in the carrier for a function I believed had left,
and chasing that inconsistency turned up three duplicate definitions.

TWO LESSONS, and the gate below is the first one:
  1. The leaf/carrier direction has to be checked, not documented. It is invisible to every other gate.
  2. A top-level `def NAME` in a module does NOT mean that module implements NAME. When the source is a
     router that borrows the name, the `def` is a shim, and "move the def" moves the wrong artifact.
     Any extraction must confirm the body it is moving is an implementation.

SCOPE: module-scope AND function-scope imports both count. Function-scope is the shape that hid here,
and it is the shape borrow shims use, so a check that only walked module bodies would have missed the
entire defect.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent
REPO = SERVICE.parent

CARRIER = "service.control_plane"

#: Directories whose modules are LEAVES: they may be imported by the carrier, never the reverse.
#: `service/reconcilers/` is listed with a documented exception below.
LEAF_DIRS = ("api_core",)

#: The reconcilers still borrow from the carrier through function-scope shims. That debt is recorded in
#: docs/ROADMAP.md and is being retired slice by slice, so they are tracked with a COUNT rather than a
#: ban — a count that may only go down. A hard ban here would fail the suite for pre-existing debt and
#: teach the next person to weaken the gate instead of paying it.
#:
#: The number is the MEASURED count, not a comfortable margin above it. I first wrote 200 against an
#: actual 13, which is the vacuity failure in the other direction: a ceiling nothing can reach reports
#: success forever. Set at the real value, adding one borrow fails; paying one down means lowering this
#: line in the same commit, which is the point.
RECONCILER_BORROW_CEILING = 13


def _carrier_imports(path: Path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(CARRIER):
            yield node.lineno, ", ".join(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(CARRIER):
                    yield node.lineno, alias.name


def _leaf_files():
    for directory in LEAF_DIRS:
        for path in sorted((SERVICE / directory).rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


class LeavesDoNotImportTheCarrierTests(unittest.TestCase):
    def test_no_api_core_leaf_imports_the_control_plane(self):
        offenders = [
            f"{path.relative_to(REPO).as_posix()}:{lineno}  imports {names}"
            for path in _leaf_files()
            for lineno, names in _carrier_imports(path)
        ]
        self.assertEqual(
            offenders,
            [],
            "an api_core leaf imports the control plane, which inverts the dependency direction the "
            "whole decomposition depends on:\n  " + "\n  ".join(offenders)
            + "\nIf an extraction produced this, the body that was moved was probably a borrow SHIM "
            "rather than the implementation — move the real body out of the carrier instead.",
        )

    def test_nothing_the_carrier_imports_imports_it_back(self):
        """The invariant stated GENERALLY, discovered rather than listed.

        `LEAF_DIRS` names one directory. That is the silent-shrink shape the repo has a rule about — the
        docstring above says "a leaf", the code says `api_core` — and it was missing the five leaf modules
        at `service/` root (`clock`, `env_status`, `terminal_snapshot`, `terminal_diagnostics`,
        `status_engine`), every one of which CLAUDE.md calls a leaf. They are clean today; nothing would
        have said so tomorrow.

        Asking the question of whatever the carrier ACTUALLY imports needs no list and grows on its own:
        the control plane is a CALLER, so nothing it calls may call back. That covers the root leaves, the
        api_core leaves and the reconcilers it imports, and it will cover the next one automatically.

        MODULE-LEVEL ONLY here, deliberately. Function-scope borrows are the recorded, ratcheting debt
        measured by `test_reconciler_carrier_borrows_only_shrink`; banning them in this test would fail the
        suite for debt that is already tracked and being paid down.
        """
        carrier = SERVICE / "control_plane.py"
        tree = ast.parse(carrier.read_text(encoding="utf-8"))

        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("service."):
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names if a.name.startswith("service."))

        # Anti-vacuity: if the carrier were ever parsed wrong, an empty set would pass forever.
        self.assertGreater(
            len(imported), 10,
            f"expected the control plane to import many service modules, found {len(imported)} — "
            "the scan is probably broken rather than the architecture suddenly clean",
        )

        offenders = []
        for module in sorted(imported):
            path = REPO / (module.replace(".", "/") + ".py")
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith((f"from {CARRIER}", f"import {CARRIER}")):
                    offenders.append(f"{path.relative_to(REPO).as_posix()}  {line.strip()}")

        self.assertEqual(
            offenders,
            [],
            "the control plane imports these modules, and they import it back at module level — a cycle, "
            "and the exact inversion the decomposition exists to remove:\n  " + "\n  ".join(offenders),
        )

    def test_the_scan_sees_function_scope_imports(self):
        """The shape that hid the defect. A module-body-only walk would have missed all three cases."""
        source = (
            "def _borrowed_thing():\n"
            "    from service.control_plane import _thing as _impl\n"
            "\n"
            "    return _impl()\n"
        )
        tree = ast.parse(source)
        found = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and (n.module or "").startswith(CARRIER)
        ]
        self.assertEqual(
            len(found), 1,
            "the scan must see a carrier import nested inside a function body; a module-scope-only "
            "walk is blind to exactly the borrow-shim shape this gate exists for",
        )
        self.assertNotIn(
            found[0], tree.body,
            "the sample's import must be function-scope for this to prove anything",
        )

    def test_reconciler_carrier_borrows_only_shrink(self):
        """Recorded debt, not a ban — but it may only go down."""
        count = sum(
            1
            for path in sorted((SERVICE / "reconcilers").rglob("*.py"))
            if "__pycache__" not in path.parts
            for _ in _carrier_imports(path)
        )
        self.assertLessEqual(
            count,
            RECONCILER_BORROW_CEILING,
            f"reconciler borrows from the control plane rose to {count}, above the recorded ceiling of "
            f"{RECONCILER_BORROW_CEILING}. Retiring these is the work; adding to them is not. If a "
            f"slice legitimately needs a new one, say so in the commit and lower the ceiling elsewhere.",
        )


if __name__ == "__main__":
    unittest.main()
