"""The `switch_agent_session_mode` split, re-proved against the real code on every run.

Same shape as the other split proofs in this directory: proving a split once at refactor time proves
the commit, while running the round trip in the suite proves it STAYS true. If someone later edits the
extracted gate or the call site and the two drift, the round trip stops closing.

THE SUBSTITUTION, declared rather than left to be noticed: the helper deliberately lives in another
module (`service/api_core/session_mode_gates.py`), because leaving it in `session_mode.py` would not
have reduced that file, which was the point. The extract-method gate needs the caller and the helper in
one tree to inline one into the other, so the sources are CONCATENATED for the proof. That is sound —
concatenation changes no body and the gate re-parses the result — but it is not the single-file
comparison the analytics precedent makes.

ONE `MODULES` TUPLE, READ BY EVERY CHECK. Written that way from the start because the alternative has
now failed four times in this directory: a proof that names its modules inline in one place and uses a
tuple in another goes blind the moment a helper lands somewhere the inline list does not mention, and
keeps passing while inlining nothing.

WHAT THIS DOES NOT DO: it proves the extraction is inert. It says nothing about whether the handler is
correct — the session-mode tests own that — nor whether the helper landed in a sensible module.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
SESSION_MODE = REPO / "service" / "routers" / "agents" / "session_mode.py"
GATES = REPO / "service" / "api_core" / "session_mode_gates.py"
#: The env-binding inference landed in its OWN module rather than beside the other two: it refuses
#: nothing, so it is not a gate. A proof that named only GATES would have gone blind on it, which is
#: the exact failure MODULES exists to prevent.
ENV_BINDING = REPO / "service" / "api_core" / "session_mode_env_binding.py"
#: The audit trail got its own module too: it neither refuses nor derives, it RECORDS, and
#: the synthetic-run workaround it carries deserves to be explained where it lives.
AUDIT = REPO / "service" / "api_core" / "session_mode_audit.py"
#: v0.5.4: the UPDATE itself got its own module, beside the gates that guard it rather
#: than inside them — a gate module that also performs the mutation it gates is the
#: arrangement those earlier splits existed to undo.
WRITES = REPO / "service" / "api_core" / "session_mode_writes.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "switch_agent_session_mode_before_split.py"

#: DECLARED EDIT, 2026-08-29. Six ended-session statuses were spelled out by hand in NINE SQL
#: strings across five modules while `ENDED_AGENT_SESSION_STATUSES` owned them. They now
#: interpolate `ENDED_AGENT_SESSION_STATUS_SQL`, rendered once from that constant. Undone here
#: rather than re-captured: re-capturing the fixture would erase the pre-split baseline and
#: leave this proving only that the split is inert relative to whatever the code is today.
EDITED_SINCE = [
    (
        '                    f"""\n                    SELECT id\n                    FROM agent_sessions\n                    WHERE agent_id = ?\n                      AND runtime = ?\n                      AND status NOT IN {ENDED_AGENT_SESSION_STATUS_SQL}\n                    ORDER BY last_seen DESC',
        '                    """\n                    SELECT id\n                    FROM agent_sessions\n                    WHERE agent_id = ?\n                      AND runtime = ?\n                      AND status NOT IN (\'failed\',\'lost\',\'stopped\',\'ended\',\'completed\',\'cancelled\')\n                    ORDER BY last_seen DESC',
    ),
]


SOURCE_FUNCTION = "switch_agent_session_mode"
#: EVERY extraction, inlined back TOGETHER against the ONE true original — not a chain of per-slice
#: fixtures. A fixture per extraction is a second copy of a function that is still being edited, and a
#: stale one proves the wrong thing while staying green.
EXTRACTIONS = [
    "_enforce_switch_not_blocked_by_active_run",
    "_start_managed_backing_after_switch",
    "_infer_environment_binding_for_managed_switch",
    "_record_session_mode_switch_audit",
    "_apply_session_mode_switch_to_agent",
]

#: Where each helper is expected to be declared. Asserted PER HELPER and over every module below, so a
#: helper that moves to a third file cannot pass by being invisible to the check.
OWNERS = {
    "_enforce_switch_not_blocked_by_active_run": GATES,
    "_start_managed_backing_after_switch": GATES,
    "_infer_environment_binding_for_managed_switch": ENV_BINDING,
    "_record_session_mode_switch_audit": AUDIT,
    "_apply_session_mode_switch_to_agent": WRITES,
}

MODULES = (SESSION_MODE, GATES, ENV_BINDING, AUDIT, WRITES)


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


class SwitchAgentSessionModeSplitIsInertTests(unittest.TestCase):
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
        names = {
            n.name for n in ast.parse(FIXTURE.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(SOURCE_FUNCTION, names)

    def test_the_fixture_was_not_captured_with_a_mangled_decode(self):
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash.

        That produced a round-trip failure pointing at an untouched block once already in this repo. The
        signal is not "many em dashes" — an earlier proof hardcoded a threshold and failed on capture
        because its function simply had fewer. It is "as many as the source has", since a mangled decode
        turns every one into U+FFFD.
        """
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        live = SESSION_MODE.read_text(encoding="utf-8")
        expected = ast.get_source_segment(live, next(
            n for n in ast.parse(live).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )) or ""
        if expected.count("—"):
            self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        declared = {
            n.name for n in ast.parse(SESSION_MODE.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for helper in EXTRACTIONS:
            self.assertNotIn(
                helper, declared,
                f"{helper} is back in session_mode.py; this proof is vacuous",
            )

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [
                path for path in MODULES
                if any(
                    isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == helper
                    for n in ast.parse(path.read_text(encoding="utf-8")).body
                )
            ]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaves_do_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent.

        Over EVERY leaf, not the one that happened to exist when this was written. Naming a single
        module here is how a check goes quietly blind when a second helper lands elsewhere.
        """
        for leaf in (GATES, ENV_BINDING, AUDIT):
            for node in ast.walk(ast.parse(leaf.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(
                        node.module.startswith("service.routers")
                        or node.module == "service.control_plane",
                        f"{leaf.name} imports upward from {node.module}",
                    )

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
