"""The `assign_agent_environment` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: the spawn-spec upsert. Assigning an environment is a SPEC write — the spec is
what a future spawn reads to know where and how to start this agent — so an assignment that failed to
reach it would look correct in the UI and produce a worker on the OLD host at the next cold start.

THE SUBSTITUTION, declared rather than left to be noticed: the helper lives in
`service/api_core/spawn_spec_assignment.py`, because leaving it in the router would not have reduced
it — that was the point. The extract-method gate needs the caller and the helper in one tree, so the
sources are CONCATENATED for the proof.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
# `assign_agent_environment` moved to `agents/environment_assignment.py` in v0.5.4 — placing an
# agent in an environment is not a config PATCH, and at 186 lines it was why `config.py` was 444.
# A round-trip proof names the module holding the CALLER, so a relocation must touch it.
CALLER = REPO / "service" / "routers" / "agents" / "environment_assignment.py"
SPEC = REPO / "service" / "api_core" / "spawn_spec_assignment.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "assign_agent_environment_before_split.py"

SOURCE_FUNCTION = "assign_agent_environment"
EXTRACTIONS = ["_upsert_spawn_spec_for_assignment"]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {"_upsert_spawn_spec_for_assignment": SPEC}

MODULES = (CALLER, SPEC)


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


def _declared(path: Path) -> set[str]:
    return {
        n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _helper() -> ast.AST:
    return next(
        n for n in ast.parse(SPEC.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
    )


class AssignAgentEnvironmentSplitIsInertTests(unittest.TestCase):
    def test_the_extraction_inlines_back_to_the_original(self):
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), _combined_split_source(), EXTRACTIONS)

    def test_the_source_function_is_still_where_this_proof_looks(self):
        """`CALLER` is a location pin, and a relocation is what breaks it.

        Added when `assign_agent_environment` moved out of `config.py` in v0.5.4. The round trip
        already fails then — it cannot find the caller to inline into — but it fails as a
        gate-internal error about a missing definition. This says the true thing in one line.
        """
        declared = {
            n.name for n in ast.parse(CALLER.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(
            SOURCE_FUNCTION, declared,
            f"{SOURCE_FUNCTION} is not declared in {CALLER.name}. If it was relocated, repoint "
            "CALLER at its new module — this proof names the file holding the caller, so a move "
            "must touch it.",
        )

    def test_the_fixture_is_the_function_it_claims_to_be(self):
        """A fixture that stopped containing the function would make the test above vacuous."""
        self.assertIn(SOURCE_FUNCTION, _declared(FIXTURE))

    def test_the_fixture_was_not_captured_with_a_mangled_decode(self):
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash.

        Conditional on the live source having any: THIS function has none, and a sibling proof that
        hardcoded a threshold copied from a neighbour failed on capture for exactly that reason.
        """
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        if SPEC.read_text(encoding="utf-8").count("—"):
            self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        for helper in EXTRACTIONS:
            self.assertNotIn(
                helper, _declared(CALLER), f"{helper} is back in environment_assignment.py; this proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for node in ast.walk(ast.parse(SPEC.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"spawn_spec_assignment.py imports upward from {node.module}",
                )

    def test_the_spec_id_is_RETURNED_not_mutated(self):
        """The one live-out, and the reason the helper has a return at all.

        `spec_id` is either read off the existing row or generated for a new one, and the caller
        records it on the agent session afterwards. Left as a bare assignment it would be a helper
        local after the split and the caller would raise NameError — or, worse, silently read a
        stale outer binding.
        """
        returned = _helper().body[-1]
        self.assertIsInstance(returned, ast.Return)
        self.assertEqual("spec_id", returned.value.id)
        call = next(
            n for n in ast.walk(ast.parse(CALLER.read_text(encoding="utf-8")))
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Await)
            and getattr(n.value.value.func, "id", "") == EXTRACTIONS[0]
        )
        self.assertEqual("spec_id", call.targets[0].id, "the caller must rebind the same name")

    def test_BOTH_branches_set_it(self):
        """The upsert's real risk: one branch that forgets, and a NameError only on that path.

        An agent that already has a spec takes the UPDATE branch; one that never had a spawn takes
        the INSERT. Only the second is exercised by a fresh agent, so a missing assignment in either
        would be invisible until the other kind of agent came along.
        """
        assigned = [
            node for node in ast.walk(_helper()) if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "spec_id" for t in node.targets)
        ]
        self.assertEqual(2, len(assigned), "spec_id must be set on the update path AND the insert path")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
