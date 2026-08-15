"""The `_replay_undelivered_channel_messages_on_env_recovery` split, re-proved on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: the query that decides which channel messages a recovering environment still owes
a delivery. Third query extraction in this series, after `reconcilable_runs_query.py` and
`orphaned_runs_query.py`, and for the same reason: in a sweep the query IS the decision and
everything after it is bookkeeping over whatever it returned.

THIS ONE HAS A KNOWN DEFECT IN ITS HISTORY, which is the argument for testing it by execution rather
than by reading it. `messages.timestamp` is epoch MILLISECONDS and the predicate called
`datetime()` on it directly, which returns NULL — so the comparison was never true and the
reconciler could not match a single row it exists to replay. 0 candidates on the live database under
the old predicate, 115 under the corrected one.

THE SUBSTITUTION, declared rather than left to be noticed: the helper lives in
`service/api_core/channel_replay_query.py`. The extract-method gate needs the caller and the helper
in one tree, so the sources are CONCATENATED for the proof.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
QUEUE = REPO / "service" / "reconcilers" / "dispatch_queue.py"
QUERY = REPO / "service" / "api_core" / "channel_replay_query.py"
FIXTURE = (Path(__file__).resolve().parent / "data"
           / "replay_undelivered_channel_messages_before_split.py")

SOURCE_FUNCTION = "_replay_undelivered_channel_messages_on_env_recovery"
EXTRACTIONS = ["_select_undelivered_channel_messages"]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {"_select_undelivered_channel_messages": QUERY}

MODULES = (QUEUE, QUERY)


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


def _declared(path: Path) -> set[str]:
    return {
        n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class ReplayUndeliveredChannelMessagesSplitIsInertTests(unittest.TestCase):
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
                helper, _declared(QUEUE),
                f"{helper} is back in dispatch_queue.py; this proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_query_leaf_imports_NOTHING(self):
        """The standard the other two query leaves are held to: `db` and a window, nothing else."""
        imports = [
            node for node in ast.walk(ast.parse(QUERY.read_text(encoding="utf-8")))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and getattr(node, "module", None) != "__future__"
        ]
        self.assertEqual([], imports, "the channel-replay query must stay dependency-free")

    def test_the_EPOCH_CONVERSION_comment_travelled_with_the_code(self):
        """The record of what this got wrong belongs beside the line that gets it right.

        A comment that stayed behind in the reconciler would describe a predicate that is no longer
        there, and the next person to touch the conversion would not see it.
        """
        source = QUERY.read_text(encoding="utf-8")
        self.assertIn("datetime(m.timestamp / 1000, 'unixepoch')", source)
        self.assertIn("MILLISECONDS", source, "the reason for the conversion must travel with it")
        self.assertNotIn(
            "epoch MILLISECONDS, not ISO", QUEUE.read_text(encoding="utf-8"),
            "the explanation is left behind in the reconciler, describing code that moved")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
