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

#: Written as a named constant because a bare newline literal inside a heredoc-authored edit keeps
#: getting expanded into a real line break; naming it removes the hazard entirely.
LF = chr(10)

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
        # THE OWNER IS NOT ALWAYS THE CARRIER. This looked only for `service.control_plane` until
        # v0.5.4, which made the gate report "imports nothing" for an accessor whose owner had MOVED to
        # an api_core leaf — the exact direction this series exists to produce. Any single owner module
        # counts; what is asserted downstream is unchanged and is the property that matters: the
        # accessor returns THAT module's object, by identity, never a copy.
        imported = None
        owner = None
        for sub in ast.walk(node):
            if isinstance(sub, ast.ImportFrom) and sub.module and sub.module.startswith("service."):
                imported = sub.names[0].asname or sub.names[0].name
                owner = sub.module
        if imported is None:
            # AND THE IMPORT NEED NOT BE INSIDE THE ACCESSOR ANY MORE. The function-scope import was
            # never the point — it was a workaround for a circular import with the carrier. v0.5.4
            # moved the constants to leaves, which have no cycle to dodge, so the imports hoisted to
            # module level and the accessors became one-line `return CONSTANT`.
            #
            # The property under test is unchanged and is asserted downstream: the accessor returns
            # THAT owner's object, by identity, never a copy. Only the place the owner is named moved.
            # Without this the gate reported "imports no constant from any owner" for seven accessors
            # that had just been made strictly better.
            imported, owner = _module_level_owner(tree, node)
        found.append((node.name, imported, owner))
    return found


def _module_level_owner(tree: ast.Module, node) -> "tuple[str | None, str | None]":
    """Resolve the name an accessor RETURNS to the module-level import that binds it.

    Deliberately narrow: only a bare `return NAME`, and only when a top-level
    `from service.… import NAME` binds it. Anything else returns (None, None) and is reported, which
    keeps a genuinely broken accessor visible rather than explained away.
    """
    returned = None
    for sub in ast.walk(node):
        if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Name):
            returned = sub.value.id
    if returned is None:
        return None, None
    for top in tree.body:
        if not isinstance(top, ast.ImportFrom) or not top.module or not top.module.startswith("service."):
            continue
        for alias in top.names:
            if (alias.asname or alias.name) == returned:
                return returned, top.module
    return None, None


def _accessors() -> list[tuple[str, str, str]]:
    """(module_path, accessor_name, borrowed_constant_name, owner_module) per `_borrowed_*` accessor."""
    found = []
    for pattern in SEARCH:
        for path in REPO.glob(pattern):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for name, imported, owner in _accessor_in(tree):
                found.append((path.relative_to(REPO).as_posix(), name, imported, owner))
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

    def test_the_scan_resolves_a_MODULE_LEVEL_owner_too(self):
        """The v0.5.4 shape, probed so the new branch cannot silently stop working.

        The function-scope import was a workaround for a circular import with the carrier, never the
        property under test. Once the constants moved to leaves there was no cycle to dodge, the
        imports hoisted, and the accessors became a one-line `return CONSTANT`. The detector has to
        recognise that or it reports "imports no constant from any owner" for the accessors that were
        just improved.

        The negative half matters as much: an accessor that returns something NO module-level import
        binds must still come back unresolved, or the fallback would explain away a genuinely broken
        one.
        """
        hoisted = LF.join([
            "from service.api_core.tuning import _SYNTHETIC_PROBE_CONSTANT",
            "",
            "",
            "def _borrowed_synthetic_probe():",
            "    return _SYNTHETIC_PROBE_CONSTANT",
            "",
        ])
        self.assertEqual(
            _accessor_in(ast.parse(hoisted)),
            [("_borrowed_synthetic_probe", "_SYNTHETIC_PROBE_CONSTANT", "service.api_core.tuning")],
        )

        unbound = LF.join([
            "def _borrowed_synthetic_probe():",
            "    return _SOMETHING_NOBODY_IMPORTS",
            "",
        ])
        self.assertEqual(
            _accessor_in(ast.parse(unbound)),
            [("_borrowed_synthetic_probe", None, None)],
            "an unresolvable accessor must stay unresolved, or the fallback hides real breakage",
        )

    def test_every_accessor_returns_the_routers_own_object(self):
        """Identity, not equality. A copy would pass `==` and be a forked constant."""
        import importlib

        # RESOLVED AGAINST THE ACCESSOR'S OWN DECLARED OWNER, not against the control plane. This read
        # `getattr(api_v2, constant)` until v0.5.4, which kept passing for a moved constant only because
        # the carrier re-imports it and therefore holds the same object — a coincidence, not the property
        # under test. Once the carrier stops importing a constant it no longer uses, that comparison would
        # raise AttributeError on a perfectly correct accessor.
        mismatched = []
        for module_path, accessor, constant, owner in _accessors():
            if constant is None:
                mismatched.append(f"{module_path}: {accessor} imports no constant from any owner")
                continue
            module = importlib.import_module(module_path.removesuffix(".py").replace("/", "."))
            try:
                value = getattr(module, accessor)()
            except Exception as error:  # noqa: BLE001 - a raising accessor is the failure
                mismatched.append(f"{module_path}: {accessor}() raised {type(error).__name__}")
                continue
            if value is not getattr(importlib.import_module(owner), constant):
                mismatched.append(f"{module_path}: {accessor}() is not {owner}.{constant}")
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
            [("_borrowed_synthetic_probe", "_SYNTHETIC_PROBE_CONSTANT", "service.control_plane")],
            "the accessor detector no longer recognises the borrowed-accessor shape, so the two "
            "checks above would pass over an empty list while real accessors went unexamined",
        )
        # A SECOND PROBE whose owner is a leaf, not the carrier. The detector matched only
        # `service.control_plane` until v0.5.4 and so reported "imports nothing" for an accessor whose
        # owner had moved — which is the direction this series produces, making the gate fail on correct
        # code. This probe fails if that narrowing ever comes back.
        moved = synthetic.replace("service.control_plane", "service.api_core.liveness")
        self.assertEqual(
            _accessor_in(ast.parse(moved)),
            [("_borrowed_synthetic_probe", "_SYNTHETIC_PROBE_CONSTANT", "service.api_core.liveness")],
            "the detector only recognises borrows from the carrier; an accessor whose owner moved to a "
            "leaf would be reported as importing nothing",
        )
        # Self-consistency of the live scan: every discovered accessor must carry all three parts.
        for module_path, accessor, constant, owner in _accessors():
            self.assertTrue(accessor, f"{module_path}: discovered an accessor with no name")
            self.assertTrue(owner, f"{module_path}: {accessor} has no resolvable owner module")


if __name__ == "__main__":
    unittest.main()
