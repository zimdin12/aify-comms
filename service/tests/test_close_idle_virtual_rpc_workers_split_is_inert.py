"""The `_close_idle_virtual_rpc_workers` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: the query that decides which managed worker terminals have been idle long enough
to close. Fourth query extraction in this series, and the one where being wrong is most expensive:
closing a worker is destructive, so every clause in it is a reason NOT to close.

THE SUBSTITUTION, declared rather than left to be noticed: the helper lives in
`service/api_core/idle_worker_query.py`. The extract-method gate needs the caller and the helper in
one tree, so the sources are CONCATENATED for the proof.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
RECONCILER = REPO / "service" / "reconcilers" / "terminals.py"
QUERY = REPO / "service" / "api_core" / "idle_worker_query.py"
FIXTURE = (Path(__file__).resolve().parent / "data"
           / "close_idle_virtual_rpc_workers_before_split.py")

#: DECLARED EDIT, 2026-08-29. Sixteen live-terminal filters spelled their status set out by hand;
#: they now interpolate a fragment from `api_core/terminal_status.py`. Undone here rather than
#: re-captured, so the pre-split baseline survives.
EDITED_SINCE = [
    (
        '\nfrom service.api_core.terminal_status import TERMINAL_LIVE_FILTER_SQL\nfrom service.api_core.virtual_rpc import VIRTUAL_RPC_COMMAND_SET',
        '\nfrom service.api_core.virtual_rpc import VIRTUAL_RPC_COMMAND_SET',
    ),
    (
        '        LEFT JOIN agents a ON a.id = t.agent_id\n        WHERE t.status IN {TERMINAL_LIVE_FILTER_SQL}\n          AND (',
        "        LEFT JOIN agents a ON a.id = t.agent_id\n        WHERE t.status IN ('starting', 'attached', 'running', 'recovering', 'active', 'idle')\n          AND (",
    ),
    (
        '\nfrom service.api_core.terminal_status import TERMINAL_LIVE_FILTER_SQL\nfrom service.api_core.agent_sessions import ENDED_AGENT_SESSION_STATUS_SQL',
        '\nfrom service.api_core.agent_sessions import ENDED_AGENT_SESSION_STATUS_SQL',
    ),
    (
        '    cursor = await db.execute(\n        f"""\n        SELECT t.id AS terminal_id, t.agent_id',
        '    cursor = await db.execute(\n        """\n        SELECT t.id AS terminal_id, t.agent_id',
    ),
    (
        '        WHERE a.session_mode = \'resident\'\n          AND t.status IN {TERMINAL_LIVE_FILTER_SQL}\n        """',
        '        WHERE a.session_mode = \'resident\'\n          AND t.status IN (\'starting\',\'attached\',\'running\',\'active\',\'idle\',\'recovering\')\n        """',
    ),
]


SOURCE_FUNCTION = "_close_idle_virtual_rpc_workers"
EXTRACTIONS = ["_select_idle_virtual_rpc_workers"]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {"_select_idle_virtual_rpc_workers": QUERY}

MODULES = (RECONCILER, QUERY)


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


def _declared(path: Path) -> set[str]:
    return {
        n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class CloseIdleVirtualRpcWorkersSplitIsInertTests(unittest.TestCase):
    def test_the_extraction_inlines_back_to_the_original(self):
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), _combined_split_source(), EXTRACTIONS, EDITED_SINCE)

    def test_the_fixture_is_the_function_it_claims_to_be(self):
        """A fixture that stopped containing the function would make the test above vacuous."""
        self.assertIn(SOURCE_FUNCTION, _declared(FIXTURE))

    def test_the_fixture_was_not_captured_with_a_mangled_decode(self):
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash.

        Asked of the live leaf rather than hardcoded: this function has exactly one em dash, and a
        sibling proof failed on capture because it copied a `> 5` threshold from a neighbour.
        """
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        if QUERY.read_text(encoding="utf-8").count("—"):
            self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        for helper in EXTRACTIONS:
            self.assertNotIn(
                helper, _declared(RECONCILER),
                f"{helper} is back in reconcilers/terminals.py; this proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaf_reaches_the_command_set_from_its_OWNER(self):
        """One import, and it is the single owner of the worker-command set.

        The three sibling query leaves import nothing at all; this one needs the command set, and
        the only safe way to have it is from `service/api_core/virtual_rpc.py`. A local copy of that
        set is the forked-constant class: the copies agree until a runtime is added to one, and then
        this sweep stops recognising — or starts closing — the wrong workers.

        WIDENED 2026-08-29 to a SET OF OWNERS rather than one name, because the same argument
        produced a second import. This leaf's `WHERE t.status IN (...)` was one of sixteen hand-typed
        live-terminal filters, and it now takes that set from `api_core/terminal_status.py` for
        exactly the reason the paragraph above gives about the command set. The claim that matters is
        unchanged and still enforced: every import here is an OWNER of a vocabulary this query needs,
        and nothing else gets in.
        """
        OWNERS = {"service.api_core.virtual_rpc", "service.api_core.terminal_status"}
        modules = {
            node.module for node in ast.walk(ast.parse(QUERY.read_text(encoding="utf-8")))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertEqual(modules - {"__future__"} - OWNERS, set(), (
            "this leaf imports something that is not a vocabulary owner; the point of the rule is "
            "that it reaches sets from where they are DECLARED and depends on nothing else"
        ))
        self.assertIn("service.api_core.virtual_rpc", modules,
                      "the command set is no longer read from its owner")
        declared = {
            t.id for n in ast.parse(QUERY.read_text(encoding="utf-8")).body
            if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)
        }
        self.assertNotIn("VIRTUAL_RPC_COMMAND_SET", declared, "the leaf declares its own copy")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
