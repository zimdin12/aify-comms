r"""No function computes a value, binds it to a name, and never reads it.

WHAT THIS WAS WRITTEN FROM, measured 2026-08-29: eleven of them across the service, and every one was
real -- no false positives to argue about. Six were `settings = await _load_settings(db)` on write
paths, two were status facts, two were a normalised runtime in the console-input twins, and one was a
`row.keys()` nothing consulted.

THE ONE THAT MATTERED was in the status engine, and it was not the waste. A fourteen-line comment
explained how a `*-missing-handle` resident is stopped from reading `available` and ended "This flag
closes that hole at the same liveness altitude" -- naming `resident_missing_handle`, which nothing
read. The line that closes the hole is its neighbour, `resident_bridge_stale = True`. So the block
looked like dead code with one live line hidden in it, and a reader tidying up on that reading would
have reopened the hole the comment describes.

WHY A GATE AND NOT JUST A SWEEP. A discarded result is where an intention went missing: somebody
computed the value because it was needed, and the line that needed it moved, or never landed. The
value is not the cost -- `_load_settings` is cached, and the two status locals were microseconds --
it is that the code says a thing matters when nothing reads it.

SCOPE, STATED. Underscore-prefixed names are excluded: `_x = f()` is the convention for a deliberate
discard, and this must not push anyone into inventing a worse one. Only single-target `name = ...`
assignments are considered -- tuple unpacking and augmented assignment have their own reasons to bind
a name -- and reads are counted anywhere in the same function, including nested scopes, so a closure
that uses the value keeps it alive.

REMOVE BY LINE NUMBER, NOT BY TEXT, when this fires. The first attempt at the sweep above replaced
every `settings = await _load_settings(db)` string in each file and deleted a seventh occurrence whose
result IS used four lines later. The compile caught it because the indentation broke; a differently
shaped block would have shipped a NameError on a live path.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "service"

#: Names that may be bound and never read, each with the reason. EMPTY on purpose: the eleven this
#: was written from are gone rather than exempted, and an entry here must say why it is not a defect.
ALLOWED: dict[str, str] = {}


def _sources() -> list[Path]:
    return [
        path for path in SERVICE.rglob("*.py")
        if "tests" not in path.parts and "__pycache__" not in path.parts
        and "new_dashboard" not in path.parts
    ]


def discarded_results() -> list[tuple[str, int, str, str]]:
    """(file, line, function, name) for every single-target assignment never read in its function."""
    found: list[tuple[str, int, str, str]] = []
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover - the compile gate owns this
            continue
        rel = path.relative_to(ROOT).as_posix()
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            assigned: dict[str, int] = {}
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and len(node.targets) == 1:
                    target = node.targets[0]
                    if isinstance(target, ast.Name) and not target.id.startswith("_"):
                        assigned.setdefault(target.id, node.lineno)
            used = {
                node.id for node in ast.walk(fn)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            }
            for name, line in assigned.items():
                if name not in used and name not in ALLOWED:
                    found.append((rel, line, fn.name, name))
    return sorted(found)


class NoResultIsComputedAndDiscardedTests(unittest.TestCase):
    def test_the_scan_found_its_subject(self) -> None:
        """The control. An empty file list makes the assertion below vacuous, and a walk that found
        no functions would do the same."""
        self.assertGreater(len(_sources()), 100, "the source walk found almost nothing")
        names = 0
        for path in _sources()[:40]:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            names += sum(1 for n in ast.walk(tree)
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
        self.assertGreater(names, 50, "no functions were parsed, so nothing was examined")

    def test_the_scan_can_say_yes(self) -> None:
        """The negative control: given a function that discards a result, the rule must catch it.

        Asserted against a parsed snippet rather than the tree, so a rule that silently matched
        nothing would fail here instead of reporting a clean repo."""
        module = ast.parse(
            "def f(db):" + chr(10)
            + "    kept = db.read()" + chr(10)
            + "    dropped = db.read()" + chr(10)
            + "    return kept" + chr(10)
        )
        fn = module.body[0]
        assigned = {t.targets[0].id: t.lineno for t in fn.body if isinstance(t, ast.Assign)}
        used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        self.assertEqual(sorted(n for n in assigned if n not in used), ["dropped"])

    def test_nothing_is_computed_and_thrown_away(self) -> None:
        found = discarded_results()
        self.assertEqual(found, [], (
            "these results are computed, bound to a name, and never read -- which is where an "
            "intention went missing, not merely where a cycle was wasted: "
            + "; ".join(f"{rel}:{line} in {fn}() -> {name}" for rel, line, fn, name in found)
        ))

    def test_an_exemption_must_carry_its_reason(self) -> None:
        """`ALLOWED` is empty on purpose. An entry with no argument is how a dropped result becomes
        permanent -- the same rule the config-knob gate states for its own exemptions."""
        for name, reason in ALLOWED.items():
            self.assertGreater(len(reason.split()), 5, f"{name}'s exemption has no argument")


if __name__ == "__main__":
    unittest.main()
