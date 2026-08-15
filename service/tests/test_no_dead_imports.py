"""No product module may import a name that nothing anywhere reaches.

THE v0.5.x DECOMPOSITION MANUFACTURED 712 OF THEM. Every extraction takes a block out of a handler;
the names only that block used stay behind in the import list, referenced by nothing. Nothing fails,
because an unused import is valid Python. By the time this gate was written `liveness.py` carried 78,
`session_mode.py` 73, and the eight biggest routers carried 551 between them.

WHAT THEY ACTUALLY COST, beyond the lines. A dead import is a false statement about what a module
depends on, and this series measures import surfaces to decide what may move. The last slice found
`_session_capabilities_replacing_handle` imported by six routers and CALLED by one — the five dead
imports made it look like a widely-shared helper when it was a single-caller one, which is exactly
the kind of measurement that decides whether an extraction is available.

THE DETECTOR LIVES IN `service/tests/dead_imports.py` AND THE SWEEP CALLS IT.
`mcp/stdio/tests/no-dead-imports.test.js` records why: a sweep tool carrying its own copy of the rule
deleted four LIVE imports because the copy had drifted. There is one detector.

THIS IS THE TREE-WIDE COUNTERPART to `test_no_orphaned_imports_in_control_plane.py`, which is scoped
to one file and explains why: applied tree-wide, its HARDCODED module-alias list would need a
per-module table, and getting one wrong deletes a live patch target. The alias table here is DERIVED
from the source instead — `dispatch_router`, `agents_shared`, `terminals_router`, `channels_router`
and `health_router` are all real bindings in this repo's tests, and guessing at them was never an
option. Both gates stay: the control-plane one asks a narrower question of a file that earns it.
"""

from __future__ import annotations

import unittest

from service.tests.dead_imports import REPO, dead_bindings, python_files

#: `service.models` imports exist for ANNOTATIONS. Under postponed evaluation a missing model does
#: not fail import — FastAPI silently demotes the request body to a query parameter and the endpoint
#: 422s at request time. The sweep excluded them for that reason and so does this gate; three
#: bindings are affected and none is worth that risk to save three lines.
ALLOWED = {
    ("service/routers/contracts.py", "validate_model_shape"),
    ("service/routers/stats.py", "validate_model_shape"),
    ("service/routers/dispatch_messages/shared.py", "DispatchClaimRequest"),
}


def _product_files():
    for path in python_files():
        relative = path.relative_to(REPO).as_posix()
        if "/tests/" in relative or relative.startswith("scripts/"):
            continue
        yield path, relative


class NoDeadImportsTests(unittest.TestCase):
    def test_no_product_module_imports_a_name_nothing_reaches(self):
        offenders = []
        for path, relative in _product_files():
            for name, line in dead_bindings(path):
                if (relative, name) not in ALLOWED:
                    offenders.append(f"{relative}:{line} {name}")
        self.assertEqual(
            [], sorted(offenders),
            "these imports are reached by nothing — not the module body, not another file, not a "
            "patch target. An extraction that moved code out and left the import behind has not "
            "finished:\n  " + "\n  ".join(sorted(offenders)),
        )

    def test_the_scan_actually_reads_the_tree(self):
        """Anti-vacuity. A detector that found no files would pass the check above silently."""
        files = list(_product_files())
        self.assertGreater(len(files), 100, f"only {len(files)} product files scanned")
        self.assertTrue(any(r.startswith("service/routers/") for _, r in files))
        self.assertTrue(any(r.startswith("mcp/") for _, r in files))

    def test_the_detector_reports_a_SYNTHETIC_dead_import(self):
        """The check that the rule can fire at all, on an input it cannot have been tuned to.

        Scanning the real tree can never prove this: a clean tree and a broken detector look
        identical. The JS gate learned the same thing — its first version could not report
        `import fs from "fs"` at all, silently exempting the three commonest default imports.
        """
        import ast
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe_module.py"
            probe.write_text(
                "from collections import OrderedDict\nimport json\n\n\ndef f():\n    return json\n",
                encoding="utf-8")
            tree = ast.parse(probe.read_text(encoding="utf-8"))
            from service.tests.dead_imports import _loaded, bindings

            bound = {n for n, _ in bindings(tree)}
            self.assertEqual({"OrderedDict", "json"}, bound, "both bindings must be seen")
            self.assertNotIn(
                "OrderedDict", _loaded(tree), "an unused import must not count as loaded")
            self.assertIn("json", _loaded(tree), "a name used inside a function IS loaded")

    def test_a_RE_EXPORT_is_not_dead(self):
        """The rule that keeps this gate from deleting six routers' imports.

        `service/routers/agents/shared.py` imports `_now` and `apply_event` and calls neither; all
        six agent routers import those names FROM it. A detector that only asked "does this file use
        it" would report them dead, the sweep would remove them, and six modules would fail to
        import. Asserted against the live tree because that is where the shape actually lives.
        """
        shared = REPO / "service" / "routers" / "agents" / "shared.py"
        dead = {name for name, _ in dead_bindings(shared)}
        for re_exported in ("_now", "apply_event", "_json_loads_or"):
            self.assertNotIn(
                re_exported, dead,
                f"{re_exported} is re-exported to the agent routers and must not read as dead")


if __name__ == "__main__":
    unittest.main()
