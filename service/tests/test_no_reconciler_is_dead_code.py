"""Every function in the reconcilers package has a production caller.

The reconcilers are the self-healing paths: they requeue orphaned runs, fail spawns whose terminal
died, close delivered runs nobody will ever reply to, prune superseded bridges. A reconciler nothing
calls is not a dormant feature — it is drift that never gets repaired, and the symptom shows up
somewhere else entirely, weeks later, as a stuck row nobody can explain.

Measured 2026-08-25: 56 functions across 19 modules, every one referenced from production code, none
referenced only by its own tests. This holds that.

WHAT THIS DELIBERATELY DOES NOT DO. The obvious stronger test is reachability — walk the call graph
from `_periodic_dispatch_reconcile` and assert every reconciler is reached. I built that and did not
ship it, because it produced a FALSE UNREACHABLE: it reported `rebind_orphaned_live_consoles` as
never reached when sweep.py calls it directly inside `_run_dispatch_reconcile_once`, which the entry
point calls. Both links were verified by hand afterwards. A name-keyed call graph over 731 functions
also loses 19 names to shadowing, and cannot see a call made through a dict, a getattr or a partial.

So the shipped gate is the one whose answer can be trusted: a plain reference check. It cannot prove
a reconciler RUNS. It can prove nobody has orphaned one, which is the failure that actually happened
in this repo's history and is cheap to detect.

Reconcilers legitimately have several entry points, and the test does not care which: `_reconcile_
terminal_controls` runs once from init_db at startup while `_reconcile_ended_terminal_controls` runs
every 60s from the sweep; three `_repair_*` session helpers run on the GET /sessions read path on
purpose, documented in CLAUDE.md, precisely so a 60s lag cannot show a dead terminal as attached.
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "service"
RECONCILERS = SERVICE / "reconcilers"


def reconciler_functions() -> dict[str, str]:
    """Every top-level function the package defines, private ones included — the package's whole
    vocabulary is `_`-prefixed, so excluding them would have left three names and a vacuous test."""
    found: dict[str, str] = {}
    for path in sorted(RECONCILERS.glob("*.py")):
        if path.name == "__init__.py":
            continue
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[node.name] = path.name
    return found


def references(names: set[str]) -> dict[str, set[str]]:
    """Where each name appears, excluding the line that defines it. Test files are marked."""
    seen: dict[str, set[str]] = {name: set() for name in names}
    for path in SERVICE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        is_test = "tests" in path.parts
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            for name in names:
                if name not in line:
                    continue
                if re.match(rf"\s*(async\s+)?def\s+{re.escape(name)}\b", line):
                    continue
                seen[name].add(("test:" if is_test else "") + path.name)
    return seen


class NoReconcilerIsDeadCode(unittest.TestCase):
    def test_the_scan_finds_the_package(self):
        """The control. An empty vocabulary agrees with every assertion below."""
        functions = reconciler_functions()
        self.assertGreater(len(functions), 30, f"only {len(functions)} reconciler functions parsed")
        self.assertGreater(len(set(functions.values())), 10, "the module scan found almost nothing")
        self.assertIn("_prune_terminal_history", functions, "the scan missed a known reconciler")

    def test_the_scan_can_say_no(self):
        self.assertNotIn("_zzz_not_a_reconciler", reconciler_functions())

    def test_every_reconciler_is_referenced_somewhere(self):
        functions = reconciler_functions()
        seen = references(set(functions))
        orphans = sorted(name for name, where in seen.items() if not where)
        self.assertEqual(
            orphans, [],
            "these reconcilers are called by nothing at all, so the drift they exist to repair is "
            "never repaired: " + ", ".join(f"{n} ({functions[n]})" for n in orphans),
        )

    def test_no_reconciler_is_kept_alive_only_by_its_own_tests(self):
        """The sharper half. A function whose only callers are tests passes a green suite for ever
        while repairing nothing in production — the exact shape of the interrupt attribution that
        shipped dead earlier today."""
        functions = reconciler_functions()
        seen = references(set(functions))
        test_only = sorted(
            name for name, where in seen.items()
            if where and all(ref.startswith("test:") for ref in where)
        )
        self.assertEqual(
            test_only, [],
            "these reconcilers are referenced only by tests, so they never run in production: "
            + ", ".join(f"{n} ({functions[n]})" for n in test_only),
        )


if __name__ == "__main__":
    unittest.main()
