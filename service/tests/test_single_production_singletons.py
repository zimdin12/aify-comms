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

#: EAGER singleton binding -> its class. Constructed AT the declaration, so exactly one of each.
#: Add a row when a new process-global instance appears.
PRODUCTION_SINGLETONS = {"TERMINAL_OUTPUT_WRITES": "TerminalOutputWriteQueue"}

#: LAZY singleton binding -> (class, owning module). Declared `None` and constructed on demand, so
#: neither count above fits: at the declaration the construction count is ZERO, and the module
#: legitimately constructs more than once (first use, and an explicit reconfigure).
#:
#: The invariant that still holds, and is what actually matters, is CONFINEMENT: one module-level
#: declaration repo-wide, and the class constructed nowhere in production but its owning module. A
#: second `NtfyRelay` built elsewhere would carry its own queue and its own dedup window, so operator
#: pushes would be deduplicated against the wrong history and rate-limited independently — and it
#: would read its own URL from the environment, which for ntfy is a credential.
LAZY_SINGLETONS = {"_RELAY": ("NtfyRelay", "service/ntfy.py")}


def _module_level_bindings(tree: ast.Module, name: str) -> list[int]:
    """Line numbers of module-level assignments to `name`, BOTH plain and ANNOTATED.

    `ast.Assign` alone misses `X: T = ...`, and the real declaration this gate had to cover —
    `_RELAY: Optional[NtfyRelay] = None` — is exactly that form. A second binding written with an
    annotation would have sailed past an Assign-only count, which is the same undercount this file's
    docstring is otherwise about.
    """
    lines = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target.id] if isinstance(node.target, ast.Name) else []
        else:
            continue
        if name in targets:
            lines.append(node.lineno)
    return lines


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
                for lineno in _module_level_bindings(tree, binding):
                    declarations.append(f"{path.relative_to(REPO).as_posix()}:{lineno}")
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


class LazySingletonConfinementTests(unittest.TestCase):
    """A lazily-built singleton cannot be counted at its declaration, so confinement is the invariant.

    `_RELAY` is `None` at import and becomes an `NtfyRelay` on first use. The eager tests above do not
    apply — its declaration constructs nothing, and its module constructs more than once by design
    (first use, plus an explicit reconfigure with a new URL). What must still hold is that the state
    has ONE home: one module-level declaration repo-wide, and no production code outside the owning
    module building the class at all.
    """

    def test_exactly_one_module_level_declaration(self):
        for binding, (_cls, owner) in LAZY_SINGLETONS.items():
            declarations = []
            for path in _sources(include_tests=False):
                tree = _tree(path)
                if tree is None:
                    continue
                for lineno in _module_level_bindings(tree, binding):
                    declarations.append(f"{path.relative_to(REPO).as_posix()}:{lineno}")
            self.assertEqual(
                len(declarations), 1,
                f"{binding} must be declared at module level exactly once; a second declaration gives "
                f"each importer its own slot and one of them stays permanently None. Found: {declarations}",
            )
            self.assertTrue(
                declarations[0].startswith(owner),
                f"{binding} is declared in {declarations[0]}, not in its owner {owner}",
            )

    def test_production_constructions_are_confined_to_the_owning_module(self):
        for binding, (cls, owner) in LAZY_SINGLETONS.items():
            inside, outside = [], []
            for path in _sources(include_tests=False):
                tree = _tree(path)
                if tree is None:
                    continue
                rel = path.relative_to(REPO).as_posix()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and _called_name(node) == cls:
                        (inside if rel == owner else outside).append(f"{rel}:{node.lineno}")
            self.assertEqual(
                outside, [],
                f"{cls} is constructed outside {owner}. A second relay carries its own queue and dedup "
                f"window, so pushes are deduplicated against the wrong history and rate-limited "
                f"independently — and it reads its own URL from the environment.",
            )
            self.assertGreaterEqual(
                len(inside), 1,
                f"no production construction of {cls} found at all — the confinement check above would "
                f"pass over an empty set, which proves nothing",
            )

    def test_the_annotated_declaration_form_is_detected(self):
        """Non-vacuity for the declaration count, on a fixture rather than on production code.

        The real `_RELAY` line is annotated, and an `ast.Assign`-only walk cannot see it — so a gate
        built that way would report ZERO declarations and pass while guarding nothing.
        """
        annotated = ast.parse("_RELAY: object = None\n")
        plain = ast.parse("_RELAY = None\n")
        nested = ast.parse("def f():\n    _RELAY = None\n")
        self.assertEqual(_module_level_bindings(annotated, "_RELAY"), [1])
        self.assertEqual(_module_level_bindings(plain, "_RELAY"), [1])
        self.assertEqual(
            _module_level_bindings(nested, "_RELAY"), [],
            "a function-scope assignment is not a module-level declaration and must not be counted — "
            "the owning module reassigns under `global` on every lazy init",
        )
