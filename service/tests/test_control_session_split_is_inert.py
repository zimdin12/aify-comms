"""The `control_session` split, re-proved against the real code on every run.

Same shape and the same reasoning as `test_send_message_split_is_inert.py`: proving a split once at
refactor time proves the commit, while running the round trip in the suite proves it STAYS true. If
someone later edits the extracted helper or the call site and the two drift, the round trip stops
closing.

THE SUBSTITUTION, declared rather than left to be noticed: the helper deliberately lives in another
module (`service/api_core/agent_sessions.py`), because leaving it in `sessions.py` would not have
reduced that file, which was the point. The extract-method gate needs the caller and the helper in
one tree to inline one into the other, so the two sources are CONCATENATED for the proof. That is
sound — concatenation changes no body and the gate re-parses the result — but it is not the
single-file comparison the analytics precedent makes.

WHAT THIS DOES NOT DO: it proves the extraction is inert. It does not prove the helper landed in a
sensible module, and it says nothing about whether `control_session` is correct — the session-control
tests own that.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
SESSIONS = REPO / "service" / "routers" / "sessions.py"
AGENT_SESSIONS = REPO / "service" / "api_core" / "agent_sessions.py"
#: Its own module, and the reason is the import graph rather than taste — see its docstring: putting it
#: beside `_has_claimable_spawn_request` would have closed a cycle through `dispatch_start.py`.
SESSION_RESTART = REPO / "service" / "api_core" / "session_restart.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "control_session_before_split.py"

SOURCE_FUNCTION = "control_session"
#: EVERY extraction, inlined back TOGETHER against the ONE true original — not a chain of per-slice
#: fixtures. See the analytics precedent for why: a fixture per extraction is a second copy of a
#: function that is still being edited, and a stale one proves the wrong thing while staying green.
EXTRACTIONS = ["_settle_agent_for_session_control", "_prepare_restart_spawn"]

#: Where each helper is expected to be declared. Asserted per helper rather than as a set, and over
#: every module below, so a helper that moves to a third file cannot pass by being invisible.
OWNERS = {
    "_settle_agent_for_session_control": AGENT_SESSIONS,
    "_prepare_restart_spawn": SESSION_RESTART,
}

MODULES = (SESSIONS, AGENT_SESSIONS, SESSION_RESTART)


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


class ControlSessionSplitIsInertTests(unittest.TestCase):
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
        names = {
            n.name for n in ast.parse(FIXTURE.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(SOURCE_FUNCTION, names)

    def test_the_fixture_was_not_captured_with_a_mangled_decode(self):
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash.

        That produced a round-trip failure pointing at an untouched block once already in this repo.
        The captured comments contain em dashes, so their absence means the fixture is corrupt rather
        than merely different — and a corrupt fixture makes the comparison above compare the wrong
        thing while looking like a real failure.
        """
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        # A HARDCODED THRESHOLD WAS THE WRONG SHAPE and failed on capture: I copied ">5" from the
        # send_message proof, and this function simply has fewer em dashes. The signal is not "many"
        # — it is "as many as the source has", since a locale-mangled decode turns EVERY one into a
        # replacement character. So the live source is asked, which also keeps the check honest if
        # the dashes are ever edited away.
        live = SESSIONS.read_text(encoding="utf-8")
        expected = ast.get_source_segment(live, next(
            n for n in ast.parse(live).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )) or ""
        if expected.count("—"):
            self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        declared = {
            n.name for n in ast.parse(SESSIONS.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for helper in EXTRACTIONS:
            self.assertNotIn(helper, declared, f"{helper} is back in sessions.py; this proof is vacuous")

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

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for leaf in (AGENT_SESSIONS, SESSION_RESTART):
            for node in ast.walk(ast.parse(leaf.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(
                        node.module.startswith("service.routers")
                        or node.module == "service.control_plane",
                        f"{leaf.name} imports upward from {node.module}",
                    )

    def test_session_restart_does_not_close_a_cycle_through_dispatch_start(self):
        """The reason this helper got its own module, asserted rather than left in a docstring.

        `dispatch_start.py` imports `spawn_request_state.py`, and this helper calls into BOTH. Putting
        it beside `_has_claimable_spawn_request` — where it reads most naturally — would have made
        `spawn_request_state` import `dispatch_start`, closing the loop. The check is that nothing this
        helper's home is imported BY reaches back into it.
        """
        dispatch_start = REPO / "service" / "api_core" / "dispatch_start.py"
        spawn_state = REPO / "service" / "api_core" / "spawn_request_state.py"
        for upstream in (dispatch_start, spawn_state):
            imported = {
                node.module for node in ast.walk(ast.parse(upstream.read_text(encoding="utf-8")))
                if isinstance(node, ast.ImportFrom) and node.module
            }
            self.assertNotIn(
                "service.api_core.session_restart", imported,
                f"{upstream.name} imports session_restart, which is the cycle this module exists to avoid",
            )

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
