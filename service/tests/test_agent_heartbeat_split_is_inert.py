"""The `agent_heartbeat` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: the unconditional liveness beat — the branch that refreshes, or creates, the
`bridge_instances` row for a bridge reporting that it is alive.

AND the turn-busy branch below it, which was blocked for a release and is no longer. It calls
`_apply_status_event`, which was declared in `service/routers/agents/shared.py`; an api_core leaf
importing from a router is the cycle this layering exists to prevent, so it could not move while the
function sat there. Relocating `_apply_status_event` to `service/api_core/status_events.py` — where
its only dependencies, the clock and the pure status engine, already were — is what unblocked it.
Injecting the function as a parameter would have worked and would have papered over the layering
defect instead of fixing it.

BOTH EXTRACTIONS INLINE BACK TOGETHER against the ONE original fixture, not a chain of per-slice
fixtures. A fixture per extraction is a second copy of a function that is still being edited, and a
stale one proves the wrong thing while staying green.

THE SUBSTITUTION, declared rather than left to be noticed: the helper lives in
`service/api_core/bridge_liveness_beat.py`, because leaving it in the router would not have reduced
it — that was the point. The extract-method gate needs the caller and the helper in one tree, so the
sources are CONCATENATED for the proof.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
LIVENESS = REPO / "service" / "routers" / "agents" / "liveness.py"
BEAT = REPO / "service" / "api_core" / "bridge_liveness_beat.py"
TURN = REPO / "service" / "api_core" / "turn_busy_signal.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "agent_heartbeat_before_split.py"

SOURCE_FUNCTION = "agent_heartbeat"
EXTRACTIONS = ["_upsert_bridge_liveness_beat", "_apply_turn_busy_signal"]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {
    "_upsert_bridge_liveness_beat": BEAT,
    "_apply_turn_busy_signal": TURN,
}

MODULES = (LIVENESS, BEAT, TURN)


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


def _declared(path: Path) -> set[str]:
    return {
        n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class AgentHeartbeatSplitIsInertTests(unittest.TestCase):
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
                helper, _declared(LIVENESS), f"{helper} is back in liveness.py; this proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaves_do_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent.

        Not a formality here: the turn-busy block could not be extracted AT ALL until
        `_apply_status_event` stopped living in a router, so this rule is what shaped the work
        rather than something satisfied by accident. Over EVERY leaf — naming one module is how a
        check goes quietly blind when a second helper lands elsewhere.
        """
        for leaf in (BEAT, TURN):
            for node in ast.walk(ast.parse(leaf.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(
                        node.module.startswith("service.routers")
                        or node.module == "service.control_plane",
                        f"{leaf.name} imports upward from {node.module}",
                    )

    def test_the_relocation_that_unblocked_the_turn_busy_split_still_holds(self):
        """`_apply_status_event` must stay OUT of the router, or the split above becomes illegal.

        Asserted rather than trusted to review: moving it back would not fail any behavioural test,
        and the upward-import check above would then start failing somewhere confusing.
        """
        status_events = REPO / "service" / "api_core" / "status_events.py"
        self.assertIn("_apply_status_event", _declared(status_events))
        shared = REPO / "service" / "routers" / "agents" / "shared.py"
        self.assertNotIn("_apply_status_event", _declared(shared))

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
