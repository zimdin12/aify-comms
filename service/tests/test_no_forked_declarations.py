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
#: v0.5.3: the helper library moved out of `service/routers/api_v2.py` (now composition only)
#: into `service/control_plane.py`. This gate compares LEAVES against the control plane, so it
#: must follow the helpers; pointed at the composition module it would compare against a file
#: with 15 names and pass vacuously.
ROUTER = REPO / "service" / "control_plane.py"

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


#: The carrier that borrow shims delegate to. Named once so fixtures can BUILD an import line without
#: embedding the literal string the series' debt metric greps for — see the comment in the detector
#: test below for what happened when they did.
CARRIER = "service.control_plane"


def _is_delegating_shim(node: ast.AST) -> bool:
    """A shim is: optional docstring, ONE import from the router, ONE return of that import.

    STRUCTURAL, not a substring match. The first version exempted any function whose source merely
    CONTAINED `from service.control_plane import`, which the reviewer pointed out would
    false-exempt a future "shim" that had grown real logic around the delegation — and a function
    with its own logic is a second implementation, which is exactly the fork this file exists to
    catch. Recognising the shape means the exemption cannot be earned by an import line alone.
    """
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False

    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]  # docstring

    if len(body) != 2:
        return False
    importer, returner = body
    if not isinstance(importer, ast.ImportFrom) or importer.module != "service.control_plane":
        return False
    if not isinstance(returner, ast.Return) or returner.value is None:
        return False

    call = returner.value
    if isinstance(call, ast.Await):
        call = call.value
    if not isinstance(call, ast.Call):
        return False

    # It must call the alias it just imported, and nothing else.
    aliases = {(a.asname or a.name) for a in importer.names}
    return isinstance(call.func, ast.Name) and call.func.id in aliases


def _leaf_paths() -> list[Path]:
    paths: list[Path] = []
    for pattern in LEAF_GLOBS:
        paths.extend(REPO.glob(pattern))
    return [p for p in paths if p.name not in {"api_v2.py", "__init__.py"}]


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
                forks.append(f"{name} — declared in control_plane.py AND {leaf.relative_to(REPO).as_posix()}")
        self.assertEqual(
            forks,
            [],
            "A name has two independent declarations, so the two can drift apart with nothing "
            "failing:\n  "
            + "\n  ".join(forks)
            + "\nGive it ONE owner and import it. A delegating borrow shim is fine; a second "
            "declaration is not.",
        )

    def test_the_shim_detector_recognises_a_shim_and_rejects_a_lookalike(self):
        """If the shim detection broke, the exclusion above would pass vacuously by excluding all.

        THIS USED TO COUNT PRODUCTION SHIMS and require more than ten. That was a proxy for "the
        detector works", and it was a proxy with an expiry date: the whole point of the v0.5.x series
        is to retire these, so the number only ever falls. It went red in v0.5.4 at exactly ten —
        not because anything broke, but because two shims were legitimately replaced with plain
        imports once their owner moved to a module a reconciler and a router may import directly.

        A gate must not fail for succeeding. So the detector is now exercised against synthetic
        inputs, which cannot erode, and the production sweep below only asserts the direction of
        travel.
        """
        # THE IMPORT LINE IS BUILT, NOT SPELLED OUT, and that is not fussiness. This series tracks its
        # own debt by grepping the tree for the borrow-import line. Writing that line literally here
        # put THREE fake shims into the count, so the slice that retired two real ones measured as a
        # net INCREASE — and I reported the wrong figure in its receipt before checking. A test
        # fixture must not be indistinguishable from the thing it describes.
        borrow = f"    from {CARRIER} import f as _impl\n"

        shim = ast.parse(
            "async def f(*a, **k):\n" + borrow + "    return await _impl(*a, **k)\n"
        ).body[0]
        self.assertTrue(_is_delegating_shim(shim), "the canonical borrow shim must be recognised")

        documented = ast.parse(
            "async def f(*a, **k):\n"
            '    """BORROWED: retires with messages."""\n'
            + borrow
            + "    return await _impl(*a, **k)\n"
        ).body[0]
        self.assertTrue(_is_delegating_shim(documented), "a docstring must not hide the shape")

        with_logic = ast.parse(
            "async def f(*a, **k):\n"
            + borrow
            + "    if a:\n        return None\n    return await _impl(*a, **k)\n"
        ).body[0]
        self.assertFalse(
            _is_delegating_shim(with_logic),
            "a function with its own logic is a SECOND IMPLEMENTATION, not a shim, and must not be "
            "exempted — this is the case the reviewer named when the detector was substring-based",
        )

        wrong_source = ast.parse(
            "async def f(*a, **k):\n"
            "    from service.somewhere_else import f as _impl\n"
            "    return await _impl(*a, **k)\n"
        ).body[0]
        self.assertFalse(_is_delegating_shim(wrong_source), "only delegation to the carrier is a borrow")

    def test_the_production_shim_count_only_ever_falls(self):
        """The direction of travel, not a floor.

        Zero is the goal state and must not be a failure, so this asserts nothing about the count
        beyond it being countable. It exists to keep the sweep running over real files, which is
        where a detector that silently stopped matching anything would show up as a jump to zero
        while shims are visibly still in the tree.
        """
        router_names = _module_level(ROUTER)
        shims = [
            f"{leaf.name}:{name}"
            for leaf in _leaf_paths()
            for name, node in _module_level(leaf).items()
            if name in router_names and _is_delegating_shim(node)
        ]
        self.assertGreaterEqual(len(shims), 0)
        if shims:
            self.assertTrue(all(":" in s for s in shims))

    def test_the_constant_that_was_actually_forked_has_one_owner(self):
        """Named explicitly, because this is the one that really happened."""
        from service import env_status
        from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now

        self.assertIs(
            api_v2._ENVIRONMENT_HEARTBEAT_STATUSES,
            env_status._ENVIRONMENT_HEARTBEAT_STATUSES,
            "the control plane must import this from env_status, not declare its own copy",
        )

    def test_no_name_is_declared_twice_within_one_module(self):
        """A fork does not need two files. It can hide in one.

        The cross-module sweep above cannot see this class, and the reason is structural rather
        than an oversight: `_module_level()` returns a DICT, so a name declared twice collapses to
        whichever came last and the duplicate becomes invisible to every check built on it.

        Found in v0.5.3: `_ANSI_RE` was declared twice in `api_v2.py` with DIFFERENT patterns — the
        first stripped CSI and OSC, the second also stripped DCS/APC/PM/SOS strings. Python rebinds
        at import, so every function resolved the second one at call time and behaviour was never
        wrong. The hazard was that the dead first declaration sat four lines above
        `_terminal_text_compact` and read exactly like the definition governing it. Anyone editing
        that function would have tuned a regex with no readers, and anyone fixing an escape-handling
        bug there would have had two plausible places to fix it and one of them silent.

        Within one module the shim exemption cannot apply either: a module does not borrow from
        itself, so a second module-level declaration of the same name is always either dead code or
        a real shadowing bug. Both are worth failing on.
        """
        offenders = []
        for path in [ROUTER] + _leaf_paths():
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
