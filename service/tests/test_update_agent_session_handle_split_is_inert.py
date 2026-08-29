"""The `update_agent_session_handle` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: the two things a handle update does that are not the update itself — deciding
whether a self-reported id can be trusted, and mirroring an accepted one onto the live session row.

THE MIRROR WAS BLOCKED until `_session_capabilities_replacing_handle` stopped living in
`service/routers/agents/shared.py`. That is the THIRD time in v0.5.4: `_apply_status_event` blocked
the turn-busy extraction for a release, `_dispatch_requires_reply` blocked the send-message dispatch
start, and this one blocked the mirror. Each was leaf-shaped and had several router importers, so the
choice each time was relocate or abandon. The pattern is named in the leaf's own docstring because it
will recur.

THE SUBSTITUTION, declared rather than left to be noticed: both helpers live in
`service/api_core/session_handle_change.py`. The extract-method gate needs the caller and the helpers
in one tree, so the sources are CONCATENATED for the proof.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
# `update_agent_session_handle` moved to `agents/session_handle.py` in v0.5.4 — a mode switch
# changes how an agent is DRIVEN, a handle change changes WHICH conversation it drives, and the
# file kept the name that fits what stayed. A round-trip proof names the module holding the
# CALLER, so a relocation must touch it — see the one-line pin below.
CALLER = REPO / "service" / "routers" / "agents" / "session_handle.py"
CHANGE = REPO / "service" / "api_core" / "session_handle_change.py"
CAPABILITIES = REPO / "service" / "api_core" / "session_capabilities.py"
AGENTS_SHARED = REPO / "service" / "routers" / "agents" / "shared.py"
FIXTURE = (Path(__file__).resolve().parent / "data"
           / "update_agent_session_handle_before_split.py")

#: DECLARED EDIT, 2026-08-29. Sixteen live-terminal filters spelled their status set out by hand;
#: they now interpolate a fragment from `api_core/terminal_status.py`. Undone here rather than
#: re-captured, so the pre-split baseline survives.
#: DECLARED EDIT, 2026-08-29. A `settings = await _load_settings(db)` whose result was never
#: read -- one of eleven discarded results removed across the service. Undone here rather than
#: re-captured, so the pre-split baseline survives.
EDITED_SINCE = [
    (
        '        updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()\n        status = await _compute_agent_status(updated, db)',
        '        updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()\n        settings = await _load_settings(db)\n        status = await _compute_agent_status(updated, db)',
    ),
    (
        '            updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()\n            status = await _compute_agent_status(updated, db)',
        '            updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()\n            settings = await _load_settings(db)\n            status = await _compute_agent_status(updated, db)',
    ),
    (
        '                updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()\n                status = await _compute_agent_status(updated, db)',
        '                updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()\n                settings = await _load_settings(db)\n                status = await _compute_agent_status(updated, db)',
    ),
    (
        '\nfrom service.api_core.terminal_status import TERMINAL_ACTIVE_STATUS_SQL\nfrom service.api_core.dispatch_state import _get_dispatch_state_for_agent',
        '\nfrom service.api_core.dispatch_state import _get_dispatch_state_for_agent',
    ),
    (
        '                    "SELECT command FROM terminal_sessions WHERE agent_id = ? "\n                    f"AND status IN {TERMINAL_ACTIVE_STATUS_SQL} "\n                    "AND id NOT LIKE \'vterm_%\' ORDER BY datetime(COALESCE(updated_at, created_at)) DESC LIMIT 1",',
        '                    "SELECT command FROM terminal_sessions WHERE agent_id = ? "\n                    "AND status IN (\'starting\',\'attached\',\'running\',\'active\',\'idle\') "\n                    "AND id NOT LIKE \'vterm_%\' ORDER BY datetime(COALESCE(updated_at, created_at)) DESC LIMIT 1",',
    ),
]


SOURCE_FUNCTION = "update_agent_session_handle"
EXTRACTIONS = [
    "_detect_fresh_start_terminal",
    "_mirror_handle_onto_live_session",
    # Both early exits, unprovable until v0.5.4's call-site-shape rule.
    "_refuse_colliding_session_handle",
    "_park_pending_session_handle_change",
]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {
    "_detect_fresh_start_terminal": CHANGE,
    "_mirror_handle_onto_live_session": CHANGE,
    "_refuse_colliding_session_handle": CHANGE,
    "_park_pending_session_handle_change": CHANGE,
}

MODULES = (CALLER, CHANGE)

#: Relocated to unblock the mirror. Byte-identical; asserted because nothing else would notice it
#: drifting back into a router.
RELOCATED = "_session_capabilities_replacing_handle"


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


def _declared(path: Path) -> set[str]:
    return {
        n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class UpdateAgentSessionHandleSplitIsInertTests(unittest.TestCase):
    def test_the_extraction_inlines_back_to_the_original(self):
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), _combined_split_source(), EXTRACTIONS, EDITED_SINCE)

    def test_the_source_function_is_still_where_this_proof_looks(self):
        """`CALLER` is a location pin, and a relocation is what breaks it.

        Added when `update_agent_session_handle` moved out of `session_mode.py` in v0.5.4. The round
        trip already fails then — it cannot find the caller to inline into — but it fails as a
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
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash."""
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helpers_are_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        for helper in EXTRACTIONS:
            self.assertNotIn(
                helper, _declared(CALLER),
                f"{helper} is back in session_handle.py; this proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaves_do_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for leaf in (CHANGE, CAPABILITIES):
            for node in ast.walk(ast.parse(leaf.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(
                        node.module.startswith("service.routers")
                        or node.module == "service.control_plane",
                        f"{leaf.name} imports upward from {node.module}",
                    )

    def test_the_relocation_that_unblocked_the_mirror_still_holds(self):
        """`_session_capabilities_replacing_handle` must stay OUT of the router.

        Moving it back would fail no behavioural test, and the upward-import check above would then
        start failing somewhere confusing.
        """
        self.assertIn(RELOCATED, _declared(CAPABILITIES))
        self.assertNotIn(RELOCATED, _declared(AGENTS_SHARED),
                         f"{RELOCATED} is declared in a router again")

    def test_the_fresh_start_verdict_is_RETURNED_not_mutated(self):
        """The one live-out. The caller's parking decision reads it on the very next statement."""
        helper = next(
            n for n in ast.parse(CHANGE.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "_detect_fresh_start_terminal"
        )
        returned = helper.body[-1]
        self.assertIsInstance(returned, ast.Return)
        self.assertEqual("_fresh_start_terminal", returned.value.id)
        call = next(
            n for n in ast.walk(ast.parse(CALLER.read_text(encoding="utf-8")))
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Await)
            and getattr(n.value.value.func, "id", "") == "_detect_fresh_start_terminal"
        )
        self.assertEqual("_fresh_start_terminal", call.targets[0].id,
                         "the caller must rebind the same name")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
