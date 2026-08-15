"""The `environment_heartbeat` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: telling a superseded environment bridge to stop, and draining the stop requests
that were never claimed because the bridge it was addressed to had already died.

THE CONSTANT TRAVELLED WITH IT. `SUPERSEDE_STOP_STALE_SECONDS` had exactly one reader — this block —
and a constant whose only use is in another module is a fork waiting to happen. The round trip cannot
see that (it only reconstructs the names in EXTRACTIONS), so it is asserted separately.

THE SUBSTITUTION, declared rather than left to be noticed: the helper lives in
`service/api_core/superseded_bridge_stops.py`, because leaving it in the router would not have
reduced it — that was the point. The extract-method gate needs the caller and the helper in one tree,
so the sources are CONCATENATED for the proof.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
ENVIRONMENTS = REPO / "service" / "routers" / "environments.py"
STOPS = REPO / "service" / "api_core" / "superseded_bridge_stops.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "environment_heartbeat_before_split.py"

SOURCE_FUNCTION = "environment_heartbeat"
EXTRACTIONS = ["_queue_stop_for_superseded_bridge"]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {"_queue_stop_for_superseded_bridge": STOPS}

MODULES = (ENVIRONMENTS, STOPS)

TRAVELLING_CONSTANT = "SUPERSEDE_STOP_STALE_SECONDS"


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


def _declared(path: Path) -> set[str]:
    return {
        n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _module_constants(path: Path) -> set[str]:
    return {
        t.id for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)
    }


class EnvironmentHeartbeatSplitIsInertTests(unittest.TestCase):
    def test_the_extraction_inlines_back_to_the_original(self):
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), _combined_split_source(), EXTRACTIONS)

    def test_the_fixture_is_the_function_it_claims_to_be(self):
        """A fixture that stopped containing the function would make the test above vacuous."""
        self.assertIn(SOURCE_FUNCTION, _declared(FIXTURE))

    def test_the_fixture_was_not_captured_with_a_mangled_decode(self):
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash."""
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        for helper in EXTRACTIONS:
            self.assertNotIn(
                helper, _declared(ENVIRONMENTS),
                f"{helper} is back in environments.py; this proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for node in ast.walk(ast.parse(STOPS.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"superseded_bridge_stops.py imports upward from {node.module}",
                )

    def test_the_constant_TRAVELLED_and_did_not_fork(self):
        """Exactly one declaration, in the module that reads it.

        The round trip cannot see this: it reconstructs the names in EXTRACTIONS, so a copy of the
        constant left behind in the router would keep the proof green while the two drifted — and a
        drifted TTL is silent, since both values produce a plausible drain.
        """
        self.assertIn(TRAVELLING_CONSTANT, _module_constants(STOPS))
        self.assertNotIn(
            TRAVELLING_CONSTANT, _module_constants(ENVIRONMENTS),
            "the constant is declared in both modules; one of them will go stale")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
