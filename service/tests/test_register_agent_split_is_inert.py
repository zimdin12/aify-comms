"""The `register_agent` split, re-proved against the real code on every run.

Same shape as `test_analytics_split_is_inert.py`, and for the same reason: proving the split once at
refactor time proves the commit, while running the round trip in the suite proves it STAYS true. If
someone later edits the extracted gate or the call site and the two drift, the round trip stops closing.

ONE DIFFERENCE FROM THE ANALYTICS PRECEDENT, declared because it is a substitution in the proof rather
than a detail: the analytics helpers stayed inside `analytics.py`, so the gate could read one file. These
helpers deliberately live in OTHER modules — `service/api_core/registration_gates.py` and
`service/api_core/agent_sessions.py` — because leaving them in `identity.py` would not have reduced the
file at all, which was the point of moving them. The extract-method gate needs the caller and the helpers
in one tree to inline one into the other, so the sources are CONCATENATED for the proof. That is sound —
concatenation changes no body and the gate re-parses the result — but it is not the single-file comparison
the precedent makes, so it is named here rather than left for a reader to notice.

WHAT THIS DOES NOT DO: it verifies the extractions named in `EXTRACTIONS`, not the whole handler. The
route's behavioural net lives in the registration tests. It also says nothing about whether a helper
landed in a SENSIBLE module — only that it is not in `identity.py` and does not import upward.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
# `register_agent` moved to `agents/registration.py` in v0.5.4 — 209 lines and four gates against
# three short reads, which is why `identity.py` was 424. A round-trip proof names the module
# holding the CALLER, so a relocation must touch it — see the one-line pin below.
CALLER = REPO / "service" / "routers" / "agents" / "registration.py"
GATES = REPO / "service" / "api_core" / "registration_gates.py"
#: v0.5.4: the same-mode gate left with the freshness test it depends on — the pair is only
#: correct together, so they got their own module.
SAME_MODE = REPO / "service" / "api_core" / "same_mode_bridge_gate.py"
SESSIONS = REPO / "service" / "api_core" / "agent_sessions.py"
#: v0.5.4 split the registration-only writes out of `agent_sessions.py`, which had become the landing
#: site for six extractions in this series and reached 832 lines. Every function in the new module has
#: exactly one caller and it is this one.
REG_WRITES = REPO / "service" / "api_core" / "agent_registration_writes.py"
#: v0.5.4 split it AGAIN: the three resident-takeover writes went to their own module when
#: `agent_registration_writes.py` reached 528 lines. This proof concatenates its modules, so a
#: helper landing in a file MODULES does not mention makes the round trip inline nothing while
#: staying green — the failure the tuple comment above records happening five times already.
TAKEOVER = REPO / "service" / "api_core" / "resident_takeover_writes.py"

#: ONE tuple, read by every check below. The alternative — each check naming its own modules — has now
#: gone blind five times in this directory: a helper landing somewhere an inline list does not mention
#: makes the round trip inline NOTHING while the test keeps passing.
MODULES = (CALLER, GATES, SESSIONS, REG_WRITES, TAKEOVER, SAME_MODE)
FIXTURE = Path(__file__).resolve().parent / "data" / "register_agent_before_split.py"

#: WHAT HAS CHANGED IN `register_agent` SINCE THE SPLIT, declared so the round trip still closes on
#: everything else. This is the only mechanism that lets a behavioural fix land in a function whose
#: proof is byte-identity with a pre-split fixture, and it is verified verbatim -- a stale entry that
#: matched nothing would silently stop undoing anything, so the gate refuses one that does not appear
#: exactly once.
#:
#: 2026-08-29: a managed agent's own sidecar was overwriting the environment bridge that hosts its
#: delivery loop. `fresh_state` starts EMPTY and the upsert replaces `runtime_state`, so taking
#: `bridgeInstanceId` from the request wiped the environment's answer -- observed live, two different
#: ids for one agent within minutes, and `aify-comms doctor` calling an answering agent an orphaned
#: loop on the strength of it, with "relaunch the environment bridge" as the remedy. Writing the same
#: fresh dict also dropped `environmentId`, which `managed-environment-sync.mjs` reads to decide
#: whether an agent belongs to this environment at all.
#: THE TUPLE IS (NOW, WAS). `extract_method` reads `for now_text, was_text in edited_since` and
#: replaces the CURRENT text with the ORIGINAL before inlining. Written the other way round it
#: fails with "0 occurrences" naming text that is genuinely not there, which reads like the code
#: moved rather than like the declaration is inside out.
#: DECLARED EDIT, 2026-08-29. Sixteen live-terminal filters spelled their status set out by hand;
#: they now interpolate a fragment from `api_core/terminal_status.py`. Undone here rather than
#: re-captured, so the pre-split baseline survives.
EDITED_SINCE = [
    (
        '\nfrom service.api_core.terminal_status import TERMINAL_LIVE_FILTER_SQL\nfrom service.api_core.active_run_lookup import _get_blocking_active_run',
        '\nfrom service.api_core.active_run_lookup import _get_blocking_active_run',
    ),
    (
        '                await db.execute(\n                    f"""\n                    SELECT id, environment_id, bridge_id',
        '                await db.execute(\n                    """\n                    SELECT id, environment_id, bridge_id',
    ),
    (
        "                    WHERE agent_id = ?\n                      AND status IN {TERMINAL_LIVE_FILTER_SQL}\n                      AND (? = '' OR id != ?)",
        "                    WHERE agent_id = ?\n                      AND status IN ('starting','attached','running','active','idle','recovering')\n                      AND (? = '' OR id != ?)",
    ),
    (
        '\nfrom service.api_core.terminal_status import TERMINAL_ACTIVE_STATUS_SQL\nfrom service.api_core.ownership_authority import (',
        '\nfrom service.api_core.ownership_authority import (',
    ),
    (
        '                await db.execute(\n                    f"""\n                    SELECT *',
        '                await db.execute(\n                    """\n                    SELECT *',
    ),
    (
        '                      AND agent_id = ?\n                      AND status IN {TERMINAL_ACTIVE_STATUS_SQL}\n                    """,',
        '                      AND agent_id = ?\n                      AND status IN (\'starting\',\'attached\',\'running\',\'active\',\'idle\')\n                    """,',
    ),
    (
        """        prior_runtime_state = _json_loads_or(row["runtime_state"], {}) if row else {}
        # The environment's answers survive a registration; everything else still refreshes.
        fresh_state.update(preserved_environment_state(
            session_mode=normalized_session_mode,
            managed_wrapper_child=managed_wrapper_child,
            existing_runtime_state=prior_runtime_state,
        ))
        owner_bridge_id, _owner_reason = registration_owner_bridge_id(
            bridge_id=bridge_id,
            session_mode=normalized_session_mode,
            managed_wrapper_child=managed_wrapper_child,
            existing_bridge_instance_id=str(prior_runtime_state.get("bridgeInstanceId", "") or ""),
        )
        if owner_bridge_id:
            fresh_state["bridgeInstanceId"] = owner_bridge_id""",
        """        if bridge_id:
            fresh_state["bridgeInstanceId"] = bridge_id""",
    ),
]

SOURCE_FUNCTION = "register_agent"
#: EVERY extraction, inlined back TOGETHER against the ONE true original — not a chain of per-slice
#: fixtures. The analytics precedent records why: verifying extraction N against "the state just before
#: extraction N" needs a second copy of the function per split, each rotting independently while staying
#: green. One fixture and one comparison is both the stronger claim and the one that survives more slices.
EXTRACTIONS = [
    "_enforce_same_mode_bridge_gate",
    "_enforce_driving_mode_switch_gate",
    "_enforce_tombstone_registration_gate",
    "_enforce_tombstone_resurrection_gate",
    "_record_registered_session_handle",
    "_supersede_stale_resident_terminals",
    "_stage_manual_resident_takeover",
    "_adopt_console_terminal_on_register",
    "_upsert_registered_agent_row",
    # NESTED: this one ENCLOSES `_adopt_console_terminal_on_register`, which is already in this list.
    # Proving the pair together needed the dependency-ordered inlining that `extract_method` refused
    # until v0.5.4 — the console-terminal branch is an early exit, so it could not be extracted at all
    # before the call-site-shape rule, and could not be PROVED with its callee before the topological
    # order. Both landed in this release.
    "_register_via_adopted_console_terminal",
    # NESTED, same as above: this one encloses `_stage_manual_resident_takeover`.
    "_register_via_manual_resident_takeover",
]

#: Where each helper is expected to be declared. The four gates stayed; the five registration WRITES
#: moved to their own module in v0.5.4 when `agent_sessions.py` reached 832 lines.
OWNERS = {
    "_enforce_same_mode_bridge_gate": SAME_MODE,
    "_enforce_driving_mode_switch_gate": GATES,
    "_enforce_tombstone_registration_gate": GATES,
    "_enforce_tombstone_resurrection_gate": GATES,
    "_record_registered_session_handle": REG_WRITES,
    "_supersede_stale_resident_terminals": TAKEOVER,
    "_stage_manual_resident_takeover": TAKEOVER,
    "_adopt_console_terminal_on_register": REG_WRITES,
    "_upsert_registered_agent_row": REG_WRITES,
    "_register_via_adopted_console_terminal": REG_WRITES,
    "_register_via_manual_resident_takeover": TAKEOVER,
}


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


class RegisterAgentSplitIsInertTests(unittest.TestCase):
    def test_the_extraction_inlines_back_to_the_original(self):
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), _combined_split_source(), EXTRACTIONS,
            edited_since=EDITED_SINCE)

    def test_the_source_function_is_still_where_this_proof_looks(self):
        """`CALLER` is a location pin, and a relocation is what breaks it.

        Added when `register_agent` moved out of `identity.py` in v0.5.4. The round trip already
        fails then — it cannot find the caller to inline into — but it fails as a gate-internal
        assertion deep in `extract_method.py`, which reads like the ELEVEN extractions broke rather
        than like the file moved. This says the true thing in one line.
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
        names = {
            n.name for n in ast.parse(FIXTURE.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(SOURCE_FUNCTION, names)

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip above would pass by having nothing to inline."""
        declared = {
            n.name for n in ast.parse(CALLER.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for helper in EXTRACTIONS:
            self.assertNotIn(
                helper, declared,
                f"{helper} is declared in identity.py again; the split was reverted and this proof is vacuous",
            )

    def test_exactly_one_module_declares_EACH_helper(self):
        """Exactly one owner, asserted PER HELPER.

        This compared the owner SET to `[GATES, SESSIONS]`, which says nothing about WHICH helper is
        where — and it went red on a split that moved five of them to a fourth module without changing
        a byte of any body. A per-helper map is both the stronger claim and the one that survives the
        next move.
        """
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

    def test_the_gates_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router is the cycle this whole layering exists to prevent."""
        for path in (GATES, SESSIONS, REG_WRITES):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.ImportFrom) and node.module):
                    continue
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"{path.name} imports upward from {node.module}",
                )

    def test_the_fixture_is_tracked(self):
        """A gitignored fixture is why the v0.5 route gates could not run from a clean clone."""
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
