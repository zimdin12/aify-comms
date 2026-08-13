"""A process-global singleton must have ONE production instance — and the count has to see both
call forms, which is how this gate came to exist.

WHAT I GOT WRONG. Relocating `TerminalOutputWriteQueue` out of the control plane in v0.5.4, I claimed
as a receipt: "exactly ONE constructor call site repo-wide". The probe behind that claim matched only

    TerminalOutputWriteQueue()          # ast.Call over ast.Name

and missed

    api_v2.TerminalOutputWriteQueue()   # ast.Call over ast.Attribute

so it did not see `test_api_v2_regressions.py`, which deliberately builds an isolated queue to test
write-lock serialisation. The claim was false. The reviewer found it, and it is the SAME blind spot
the stale-owner census had before it learned to look at alias-attribute reads: a name reached through
a module alias is invisible to a Name-only walk, and every count built that way is an undercount.

WHY IT MATTERS EVEN THOUGH THAT TEST IS HARMLESS. `TERMINAL_OUTPUT_WRITES` holds pending deques,
asyncio locks and scheduled flush tasks. A second PRODUCTION instance would split pending writes
across two queues with two independent flush timers — no exception, just terminal output that lands
late or never. So the invariant that needs guarding is not "nobody may ever call the constructor", it
is "exactly one instance is BOUND as production state". An isolated test constructing its own queue
and dropping it is legitimate and must stay legal; a second module-level binding must not.

That distinction is the gate: production declarations are counted under `service/` excluding the test
suite, and both call forms are counted everywhere so the numbers reported are real.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent
REPO = SERVICE.parent

#: singleton binding -> its class. Add a row when a new process-global instance appears.
PRODUCTION_SINGLETONS = {"TERMINAL_OUTPUT_WRITES": "TerminalOutputWriteQueue"}


def _sources(include_tests: bool):
    for path in sorted(SERVICE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if not include_tests and "tests" in path.parts:
            continue
        yield path


def _tree(path: Path):
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None


def _called_name(node: ast.Call) -> str:
    """The constructor name for BOTH call forms — bare and through a module alias.

    `ast.Name` catches `Cls()`; `ast.Attribute` catches `api_v2.Cls()`. Counting only the first is
    the undercount this module documents.
    """
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


class SingleProductionSingletonTests(unittest.TestCase):
    def test_exactly_one_production_binding_per_singleton(self):
        for binding, cls in PRODUCTION_SINGLETONS.items():
            declarations = []
            for path in _sources(include_tests=False):
                tree = _tree(path)
                if tree is None:
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Assign):
                        continue
                    if not any(isinstance(t, ast.Name) and t.id == binding for t in node.targets):
                        continue
                    declarations.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
            self.assertEqual(
                len(declarations), 1,
                f"{binding} must have exactly ONE production declaration; a second one splits the "
                f"process-global state silently. Found: {declarations}",
            )

    def test_production_code_constructs_the_singleton_class_exactly_once(self):
        """Once, in the declaration. A second production construction is the defect; tests are exempt."""
        for binding, cls in PRODUCTION_SINGLETONS.items():
            sites = []
            for path in _sources(include_tests=False):
                tree = _tree(path)
                if tree is None:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and _called_name(node) == cls:
                        sites.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
            self.assertEqual(
                len(sites), 1,
                f"{cls} must be constructed exactly once in production — at the {binding} "
                f"declaration. Found: {sites}",
            )

    def test_test_suite_constructions_are_visible_and_bind_nothing_global(self):
        """Isolated test instances are LEGAL. What is illegal is a test binding a second production name.

        This test is also the non-vacuity proof for the two above: it asserts the attribute call form is
        actually detected, since a count blind to `api_v2.Cls()` is exactly what made the original
        receipt false.
        """
        for binding, cls in PRODUCTION_SINGLETONS.items():
            attribute_form_seen = False
            for path in _sources(include_tests=True):
                if "tests" not in path.parts:
                    continue
                tree = _tree(path)
                if tree is None:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and _called_name(node) == cls:
                        if isinstance(node.func, ast.Attribute):
                            attribute_form_seen = True
                    # no test may create a module-level binding of the production name
                    if isinstance(node, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == binding for t in node.targets
                    ):
                        self.fail(
                            f"{path.relative_to(REPO).as_posix()}:{node.lineno} binds the production "
                            f"name {binding}; a test may construct its own instance but must not "
                            f"rebind the production singleton"
                        )
            # Information only. The attribute-call form is proved against SYNTHETIC input below, not
            # against whatever the suite happens to contain — see that test for why.
            del attribute_form_seen

    def test_the_detector_sees_every_call_form(self):
        """Non-vacuity for the two counts above, proved on a fixture rather than on production code.

        This used to require the real suite to contain at least one `<module>.Cls()` construction, on
        the grounds that a counter blind to the attribute form is exactly what made the original
        receipt false. That held until the work removed the last one: v0.5.4 repointed 127 `api_v2.X`
        uses in `test_api_v2_regressions.py` at their real owners, so `api_v2.TerminalOutputWriteQueue()`
        became a bare `TerminalOutputWriteQueue()` and this gate went red for a cleanup it should have
        welcomed.

        THAT IS THE THIRD GATE IN THIS SUITE TO FAIL BECAUSE THE WORK SUCCEEDED — after the borrow-shim
        floor (`test_no_forked_declarations`) and the allowlist basename fixture
        (`test_no_new_oversized_source_file`). The shape is always the same: an anti-vacuity check
        anchored to a production artefact the project is actively trying to eliminate. Anchor it to a
        fixture instead; a fixture cannot erode.
        """
        probe = ast.parse("Cls()\nmod.Cls()\npkg.mod.Cls()\nother()\n")
        names = [_called_name(n) for n in ast.walk(probe) if isinstance(n, ast.Call)]
        self.assertEqual(
            names.count("Cls"), 3,
            "the detector must recognise the bare call, the module-alias call AND the dotted-package "
            "call; missing the attribute forms is the undercount that made the original receipt false",
        )
        self.assertEqual(names.count("other"), 1, "…and must not conflate unrelated calls")
        self.assertNotIn("mod", names, "the ATTRIBUTE is the constructor name, not the module")


if __name__ == "__main__":
    unittest.main()
