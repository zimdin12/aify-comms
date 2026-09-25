"""A moved name gets exactly one owner. A copy is a fork waiting to drift.

FOUND BY ACCIDENT, WHICH IS THE PROBLEM. While measuring the analytics domain,
`_ENVIRONMENT_HEARTBEAT_STATUSES` turned out to be declared TWICE: in `service/env_status.py`, where
v0.5 slice 2 moved it, and still in the old router module, which was supposed to have given it up.
Equal values, two distinct objects, and nothing would have failed if either had been edited — the
two would simply have disagreed, and the symptom would have been an environment reading `online` on
one code path and not the other.

The reviewer's ruling on that very slice was that a moved constant gets one owner and never a second
copy, precisely because "no copies, no drift". The ruling was right and the execution missed it, and
nothing in the suite could tell.

`test_process_global_identity.py` does not cover this: it guards a hand-maintained registry of
mutable process state. This is the complementary check and needs no registry — it compares what
every leaf declares against every other leaf, and every disagreement is a defect.

WHAT IS NOT A FORK: `logger` and `router`, which every module legitimately has its own of.

Until v0.7.0 this also compared every leaf against the control-plane module and exempted delegating
"borrow shims" that imported from it. That module is deleted and no shim exists, so both went: a
second module-level declaration of a name is now a fork with no exception.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

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


def _leaf_paths() -> list[Path]:
    paths: list[Path] = []
    for pattern in LEAF_GLOBS:
        paths.extend(REPO.glob(pattern))
    return [p for p in paths if p.name not in {"api_v2.py", "__init__.py"}]


class NoForkedDeclarationsTests(unittest.TestCase):
    def test_no_name_is_declared_independently_in_TWO_LEAVES(self):
        """Two leaves declaring the same name independently: two bodies, nothing failing when they
        drift.

        Measured when this was added: exactly one name was declared in two leaves,
        `_managed_orphan_grace_seconds` in `reconcilers/managed_workers.py` and `reconcilers/terminals.py`,
        and both were delegating borrow shims reading one owner. Neither exists any more.
        """
        by_name: dict[str, list[str]] = {}
        for leaf in _leaf_paths():
            rel = leaf.relative_to(REPO).as_posix()
            for name in _module_level(leaf):
                if name.startswith("__") or name in PER_MODULE:
                    continue
                by_name.setdefault(name, []).append(rel)

        forks = [
            f"{name} — declared in " + " AND ".join(sorted(set(where)))
            for name, where in sorted(by_name.items())
            if len(set(where)) > 1
        ]
        self.assertEqual(
            forks,
            [],
            "A name has independent declarations in two LEAVES, so the two can drift apart with nothing "
            "failing:\n  "
            + "\n  ".join(forks)
            + "\nGive it ONE owner and import it.",
        )

    def test_the_leaf_pair_scan_is_not_vacuous(self):
        """It must actually be comparing many names across many leaves.

        A glob that stopped matching, or a filter that removed everything, would make the check above pass
        on an empty set — which is the failure mode this whole file exists to catch in production code.
        """
        leaves = _leaf_paths()
        self.assertGreater(len(leaves), 20, f"expected many leaves, found {len(leaves)}")
        total = sum(len(_module_level(leaf)) for leaf in leaves)
        self.assertGreater(total, 200, f"expected many module-level names, found {total}")

    def test_the_constant_that_was_actually_forked_has_one_owner(self):
        """Named explicitly, because this is the one that really happened.

        Asked of every service module, not only the leaf globs above: the copy it once had lived in a
        module those globs did not cover, which is how it was missed.
        """
        service = REPO / "service"
        declared = sorted(
            path.relative_to(REPO).as_posix()
            for path in service.rglob("*.py")
            if "tests" not in path.parts
            and "__pycache__" not in path.parts
            and "_ENVIRONMENT_HEARTBEAT_STATUSES" in _module_level(path)
        )
        self.assertEqual(
            declared,
            ["service/env_status.py"],
            "the environment heartbeat statuses must be declared in env_status and imported everywhere else",
        )

    def test_no_name_is_declared_twice_within_one_module(self):
        """A fork does not need two files. It can hide in one.

        The cross-module sweep above cannot see this class, and the reason is structural rather
        than an oversight: `_module_level()` returns a DICT, so a name declared twice collapses to
        whichever came last and the duplicate becomes invisible to every check built on it.

        Found in v0.5.3: `_ANSI_RE` was declared twice in the old router module with DIFFERENT
        patterns — the first stripped CSI and OSC, the second also stripped DCS/APC/PM/SOS strings.
        Python rebinds at import, so every function resolved the second one at call time and
        behaviour was never wrong. The hazard was that the dead first declaration sat four lines above
        `_terminal_text_compact` and read exactly like the definition governing it. Anyone editing
        that function would have tuned a regex with no readers, and anyone fixing an escape-handling
        bug there would have had two plausible places to fix it and one of them silent.

        A second module-level declaration of the same name in one module is always either dead code
        or a real shadowing bug. Both are worth failing on.
        """
        offenders = []
        for path in _leaf_paths():
            counts: dict[str, list[int]] = {}
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                names = []
                if isinstance(node, ast.Assign):
                    names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    names = [node.target.id]
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names = [node.name]
                for n in names:
                    counts.setdefault(n, []).append(node.lineno)
            for name, lines in sorted(counts.items()):
                if len(lines) > 1:
                    offenders.append(f"{path.relative_to(REPO).as_posix()}: {name} at lines {lines}")

        self.assertEqual(
            offenders,
            [],
            "a module-level name is declared more than once in the same module; the later "
            "declaration silently wins and the earlier one is dead or shadowing:\n  "
            + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
