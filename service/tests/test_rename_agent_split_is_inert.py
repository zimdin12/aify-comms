"""The `rename_agent` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: the fourteen statements that rewrite every reference to the old agent id — copy
the row under the new id, repoint the referencing tables, delete the old row, tombstone it.

THE SUBSTITUTION, declared rather than left to be noticed: the helper lives in
`service/api_core/agent_rename_writes.py`, because leaving it in the router would not have reduced it
— that was the point. The extract-method gate needs the caller and the helper in one tree, so the
sources are CONCATENATED for the proof. Concatenation changes no body and the gate re-parses the
result, but it is not the single-file comparison the analytics precedent makes.

WHAT THIS DOES NOT DO: it proves the rewrite is unchanged, not that it is COMPLETE. Which columns a
rename must touch is derived from the schema by
`test_agent_rename_covers_every_agent_reference.py`, which is where the coverage question lives.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
# `rename_agent` moved to `agents/rename.py` in v0.5.4. A round-trip proof names the module
# holding the CALLER, so a relocation must touch it — see the one-line pin below.
CALLER = REPO / "service" / "routers" / "agents" / "rename.py"
RENAME_WRITES = REPO / "service" / "api_core" / "agent_rename_writes.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "rename_agent_before_split.py"

SOURCE_FUNCTION = "rename_agent"
EXTRACTIONS = ["_rewrite_agent_references_for_rename"]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {"_rewrite_agent_references_for_rename": RENAME_WRITES}

MODULES = (CALLER, RENAME_WRITES)


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


def _declared(path: Path) -> set[str]:
    return {
        n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class RenameAgentSplitIsInertTests(unittest.TestCase):
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

        Added when `rename_agent` moved out of `identity.py` in v0.5.4. The round trip already fails
        then — it cannot find the caller to inline into — but it fails as a gate-internal error
        about a missing definition, which reads like the SPLIT broke. This says the true thing.
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
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash."""
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        for helper in EXTRACTIONS:
            self.assertNotIn(
                helper, _declared(CALLER), f"{helper} is back in identity.py; this proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for node in ast.walk(ast.parse(RENAME_WRITES.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"agent_rename_writes.py imports upward from {node.module}",
                )

    def test_the_helper_OWNS_NO_TRANSACTION(self):
        """`BEGIN IMMEDIATE`, the commit and the rollback must all stay in the route.

        These fourteen statements must all land or none of them. A helper that could commit part way
        through would be a way to leave an agent with its messages under the new id and its sessions
        under the old — and, unlike a crash, it would leave no error behind. The route already owns
        the transaction; this asserts the helper never learns how to.
        """
        # Asked of the CODE, not of the file. A substring search over the source matched this
        # module's own docstring, which names `BEGIN IMMEDIATE` in order to say it does not use it —
        # the same failure that once inflated the tracked shim count by quoting the grep string in a
        # docstring. Prose about a rule is not a violation of it.
        helper = next(
            n for n in ast.parse(RENAME_WRITES.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
        )
        called = {
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            for node in ast.walk(helper) if isinstance(node, ast.Call)
        }
        for forbidden in ("commit", "rollback", "get_db"):
            self.assertNotIn(
                forbidden, called,
                f"the rename rewrite must not call {forbidden}() — the route owns the transaction")
        sql = " ".join(
            node.value for node in ast.walk(helper)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
        self.assertNotIn(
            "BEGIN", sql.upper(), "the rename rewrite must not open a transaction of its own")

    def test_the_OLD_id_is_deleted_only_AFTER_the_references_move(self):
        """Ordering, which the round trip preserves but does not explain.

        Every repoint runs while the old `agents` row still exists. Several referencing tables
        declare `ON DELETE CASCADE` on that row, so deleting it first would take their rows with it
        instead of moving them — and the rename would report success having silently dropped the
        agent's sessions and bridge instances.
        """
        helper = next(
            n for n in ast.parse(RENAME_WRITES.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
        )
        sql_by_line = [
            (node.lineno, node.value) for node in ast.walk(helper)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        deletes = [line for line, sql in sql_by_line if "DELETE FROM agents" in sql]
        repoints = [line for line, sql in sql_by_line if "UPDATE" in sql and "SET" in sql]
        self.assertEqual(1, len(deletes), "expected exactly one delete of the old agents row")
        self.assertTrue(repoints, "expected the repointing statements to be found")
        self.assertGreater(
            deletes[0], max(repoints),
            "the old agents row is deleted before some references are repointed; cascading tables "
            "would lose their rows instead of moving them",
        )

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
