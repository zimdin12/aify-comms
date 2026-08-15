"""No module-level import cycle exists among service modules.

WHY A GATE AND NOT JUST "PYTHON WOULD RAISE": a cycle raises ImportError only for SOME entry
orderings. Whichever module gets imported first decides whether the loop closes on a
half-initialised module or resolves cleanly, so a latent cycle can sit through a green suite and
surface when a different entry point imports first — a container boot, a script, a test that happens
to run alone. The failure lands far from the edit that caused it.

THIS IS THE PROPERTY v0.5.x WAS BUYING. The whole decomposition is a claim about direction: the
control plane calls leaves, leaves do not call back. `test_leaves_do_not_import_the_carrier.py`
enforces that for one specific edge — api_core must not import the control plane. This asks the
general question of all 171 modules and 844 module-level edges at once, and needs no list of which
module may import which.

FUNCTION-SCOPE IMPORTS ARE EXCLUDED, DELIBERATELY. Deferring an import into a function body is this
repo's documented way of breaking a cycle, so counting those would report every RESOLVED cycle as
unresolved and make the gate red on correct code. The flip side is what makes this gate earn its
place: hoisting such an import back to module level is exactly the change that would close a real
cycle, and it is a change this series makes routinely — two in v0.5.4 alone, both verified by hand at
the time. Now they are verified by a test.

WHAT IT DOES NOT SAY: that the layering is GOOD. `api_core` and `reconcilers` import each other (50
edges one way, 15 the other) and are therefore peers rather than layers — acyclic per module, but not
a hierarchy. That is a design observation, not a violation, and it is recorded in
`test_the_bidirectional_package_pairs_are_known` so it is a noticed fact rather than a discovery.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SERVICE = REPO / "service"


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(REPO).with_suffix("").parts)


def module_level_imports(path: Path) -> set[str]:
    """Only imports in the module BODY — a function-scope import cannot close an import-time cycle."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:  # pragma: no cover - another gate's failure
        return set()
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            if node.module.startswith("service."):
                out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names if a.name.startswith("service."))
    return out


def build_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for path in sorted(SERVICE.rglob("*.py")):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        graph[_module_name(path)] = module_level_imports(path)
    # Keep only edges whose target we actually scanned, so a package-shorthand import does not
    # invent a node with no body and no outgoing edges.
    return {m: {d for d in deps if d in graph} for m, deps in graph.items()}


def cycles_in(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan's SCC. Every returned component has >1 member, i.e. is a genuine cycle.

    Iterative rather than recursive: 171 modules with an 844-edge graph is fine for recursion today,
    but a gate that starts failing with RecursionError as the repo grows would be read as a real
    cycle and debugged as one.
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = 0

    for root in sorted(graph):
        if root in index:
            continue
        work: list[tuple[str, list[str]]] = [(root, sorted(graph.get(root, ())))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, pending = work[-1]
            if pending:
                dep = pending.pop()
                if dep not in index:
                    index[dep] = low[dep] = counter
                    counter += 1
                    stack.append(dep)
                    on_stack.add(dep)
                    work.append((dep, sorted(graph.get(dep, ()))))
                elif dep in on_stack:
                    low[node] = min(low[node], index[dep])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    component.append(w)
                    if w == node:
                        break
                if len(component) > 1:
                    out.append(sorted(component))
    return out


def _package(module: str) -> str:
    parts = module.split(".")
    return ".".join(parts[:2]) if len(parts) > 2 else module


class NoImportCyclesTests(unittest.TestCase):
    def test_the_service_import_graph_is_acyclic(self):
        found = cycles_in(build_graph())
        self.assertEqual(
            [], found,
            "module-level import cycle(s). Python raises on these only for SOME entry orderings, so a "
            "green suite is not evidence of absence — break the loop by moving the shared name DOWN to "
            "a module both sides can import, not by deferring the import into a function body:\n"
            + "\n".join("  " + " <-> ".join(c) for c in found),
        )

    def test_the_graph_is_not_empty(self):
        """Anti-vacuity: a parser that matched nothing yields no edges and therefore no cycles."""
        graph = build_graph()
        edges = sum(len(v) for v in graph.values())
        self.assertGreater(len(graph), 100, f"only {len(graph)} service modules found")
        self.assertGreater(edges, 400, f"only {edges} module-level service->service edges found")

    def test_the_detector_finds_a_SYNTHETIC_cycle(self):
        """Proving a clean tree is clean cannot distinguish a working detector from a broken one."""
        self.assertEqual(
            [["a", "b"]],
            cycles_in({"a": {"b"}, "b": {"a"}, "c": {"a"}}),
            "a two-module loop must be reported",
        )
        self.assertEqual(
            [["x", "y", "z"]],
            cycles_in({"x": {"y"}, "y": {"z"}, "z": {"x"}}),
            "an indirect three-module loop must be reported",
        )
        self.assertEqual([], cycles_in({"a": {"b"}, "b": {"c"}, "c": set()}), "a chain is not a cycle")
        self.assertEqual([], cycles_in({"a": {"a"}}), "self-import is not a multi-module cycle")

    def test_function_scope_imports_are_not_counted(self):
        """The exclusion is the whole reason this gate can be green — assert it, do not assume it."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "m.py"
            sample.write_text(
                "from service.api_core.tuning import LIVE_SESSION_STATUSES\n"
                "def f():\n"
                "    from service.control_plane import _deferred\n"
                "    return _deferred\n",
                encoding="utf-8",
            )
            found = module_level_imports(sample)
        self.assertEqual({"service.api_core.tuning"}, found,
                         "only the module-body import may count toward the cycle graph")

    def test_the_bidirectional_package_pairs_are_known(self):
        """Two packages importing each other is acyclic per MODULE but is not a hierarchy.

        Recorded rather than banned. `api_core` and `reconcilers` are peers: reconcilers read api_core
        helpers, and several api_core leaves read the live-status cache, which reconcilers own. That
        is a deliberate arrangement — the cache has one owner and is reached, never copied — but it
        means neither package is 'below' the other, and a reader who assumed a clean layer ordering
        would be wrong. If a new pair appears here, decide whether it is peers or an accident.
        """
        graph = build_graph()
        directed: set[tuple[str, str]] = set()
        for module, deps in graph.items():
            for dep in deps:
                if _package(module) != _package(dep):
                    directed.add((_package(module), _package(dep)))
        mutual = {tuple(sorted(pair)) for pair in directed if (pair[1], pair[0]) in directed}
        self.assertEqual(
            {
                ("service.api_core", "service.reconcilers"),
                ("service.api_core", "service.terminal_write_queue"),
            },
            mutual,
            "the set of mutually-importing packages changed; that is a layering decision, not a "
            "detail — record the new pair here with why it is peers rather than a mistake",
        )


if __name__ == "__main__":
    sys.exit(unittest.main())
