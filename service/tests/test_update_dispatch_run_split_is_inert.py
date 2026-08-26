"""The `update_dispatch_run` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: the eight-step settlement that fires when a dispatch run reaches a terminal
status. It is the only part of `PATCH /dispatch/runs/{id}` that is not field-by-field UPDATE
assembly, and it runs at most once per run — which is exactly why a silent change to it is hard to
notice. Nothing raises; the run still ends. What goes missing is the handoff message, the contract
close, or the turn_busy clear, and each of those reads from the outside as an agent that stopped
answering rather than as a bug here.

THE SUBSTITUTION, declared rather than left to be noticed: the helper lives in
`service/api_core/dispatch_run_settlement.py`, because leaving it in the router would not have
reduced it — that was the point. The extract-method gate needs the caller and the helper in one tree,
so the sources are CONCATENATED for the proof. Concatenation changes no body and the gate re-parses
the result, but it is not the single-file comparison the analytics precedent makes.

ONE `MODULES` TUPLE, READ BY EVERY CHECK, and `OWNERS` asserted per helper. The alternative has gone
blind five times in this directory, twice in the same file.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
DISPATCH = REPO / "service" / "routers" / "dispatch_messages" / "dispatch.py"
SETTLEMENT = REPO / "service" / "api_core" / "dispatch_run_settlement.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "update_dispatch_run_before_split.py"

SOURCE_FUNCTION = "update_dispatch_run"

#: Edits made SINCE the split, as (NOW, WAS): the helper rewrites today's text back to the original
#: before comparing, so the current block comes first. Declared rather than folded into the fixture,
#: which is history -- editing that would prove the wrong thing while staying green.
#:
#: THE EDIT. The status that gets WRITTEN is now normalised, as the guard beside it always was. Three
#: consumers test it against lowercase literals -- the column, the started_at stamp and the terminal
#: membership -- so a mixed-case status was written verbatim, matched none of them, and matched no
#: reconciler either. See test_a_dispatch_status_is_normalised_before_it_is_stored.py.
_STATUS_NORMALISE_NOW = chr(10).join([
    '        requested_status = str(req.status or "").strip().lower()',
    '        # NORMALISED, because everything downstream assumes it already is. The guard below has always',
    '        # lowercased for its comparison; the value that gets WRITTEN did not, and it has three',
    '        # consumers that each test it against a lowercase literal: the column itself, the',
    '        # `== "running"` check that stamps started_at, and the `in _DISPATCH_TERMINAL_STATUSES`',
    '        # membership that settles the run. A status of "Completed" passed the guard, was written',
    '        # verbatim, matched neither check, and then matched no reconciler either -- every dispatch',
    '        # sweep in `service/reconcilers/dispatch_lifecycle.py` and `dispatch_queue.py` selects on the',
    '        # lowercase members of `_DISPATCH_TERMINAL_STATUSES` and its siblings. The run is stranded:',
    '        # require_reply never settles and cleanup never deletes it.',
    '        #',
    '        # The members are NAMED rather than quoted here on purpose: a comment that spells a status set',
    '        # out is a second copy of it, which is how the `lost` incident happened, and',
    '        # `test_status_set_literal_twins_are_frozen.py` catches exactly that -- it caught this comment.',
    '        #',
    '        # No live defect today -- the bridge sends five lowercase literals (completed, delivered,',
    '        # failed, queued, running) and nothing else writes here. But `status` is `Optional[str]` on',
    '        # the model with no validator, the bridge is host-side and routinely a different build, and',
    '        # the guard one line up already proves the author expected case to vary. This is the `lost`',
    "        # incident's exact shape on a table that has no status vocabulary gate to catch it.",
    '        effective_status = requested_status or None',
]) + chr(10)

_STATUS_NORMALISE_WAS = chr(10).join([
    '        requested_status = str(req.status or "").strip().lower()',
    '        effective_status = req.status',
]) + chr(10)

EDITED_SINCE = [(_STATUS_NORMALISE_NOW, _STATUS_NORMALISE_WAS)]
EXTRACTIONS = ["_settle_terminated_dispatch_run"]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {"_settle_terminated_dispatch_run": SETTLEMENT}

MODULES = (DISPATCH, SETTLEMENT)

#: Travelled WITH the block rather than being extracted from it: the settlement was its only caller,
#: so leaving it behind would have left a router-level definition with no reader in the router.
RELOCATED_WITH_THE_BLOCK = "_apply_pending_resident_takeover_if_ready"


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


def _declared(path: Path) -> set[str]:
    return {
        n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class UpdateDispatchRunSplitIsInertTests(unittest.TestCase):
    def test_the_extraction_inlines_back_to_the_original(self):
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), _combined_split_source(), EXTRACTIONS,
            edited_since=EDITED_SINCE)

    def test_the_fixture_is_the_function_it_claims_to_be(self):
        """A fixture that stopped containing the function would make the test above vacuous."""
        self.assertIn(SOURCE_FUNCTION, _declared(FIXTURE))

    def test_the_fixture_was_not_captured_with_a_mangled_decode(self):
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash.

        Asked of the LIVE source rather than hardcoded: a sibling proof used a fixed threshold copied
        from a neighbour and failed on capture because its function simply had fewer em dashes.
        """
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        live = SETTLEMENT.read_text(encoding="utf-8")
        if live.count("—"):
            self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        declared = _declared(DISPATCH)
        for helper in EXTRACTIONS:
            self.assertNotIn(
                helper, declared, f"{helper} is back in dispatch.py; this proof is vacuous")

    def test_the_relocated_callee_left_the_router_too(self):
        """The block's only remaining router-local callee moved with it.

        Worth its own assertion because the round trip cannot see it: inline-back only reconstructs
        the names in EXTRACTIONS, so a copy left behind in `dispatch.py` would keep the proof green
        while two definitions of the same function drifted apart.
        """
        self.assertNotIn(RELOCATED_WITH_THE_BLOCK, _declared(DISPATCH))
        self.assertIn(RELOCATED_WITH_THE_BLOCK, _declared(SETTLEMENT))

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent.

        Worth asserting rather than assuming here: three of the names this block calls are reachable
        through `dispatch_messages/shared.py`, which the router already imports, so importing them
        from there would have been the convenient move and an upward one.
        """
        for node in ast.walk(ast.parse(SETTLEMENT.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"dispatch_run_settlement.py imports upward from {node.module}",
                )

    def test_the_row_is_RE_READ_inside_the_settlement(self):
        """The one ordering fact the round trip proves but does not explain.

        Every mirror below the re-read consumes `refreshed_row`, not the `row` the route selected on
        entry — because the UPDATE immediately above changed the very columns they report. Reusing
        the stale row would mirror the run's PREVIOUS state, and the failure is silent: a handoff
        message that says the run is still running. Asserted structurally so a future edit that
        "simplifies" the second SELECT away fails here rather than in production.
        """
        helper = next(
            n for n in ast.parse(SETTLEMENT.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
        )
        assigned = {
            t.id for n in ast.walk(helper) if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)
        }
        self.assertIn("refreshed_row", assigned, "the settlement must re-read the row it reports on")
        names = {n.id for n in ast.walk(helper) if isinstance(n, ast.Name)}
        self.assertNotIn(
            "row", names,
            "the settlement reads the pre-UPDATE row; every consumer must use the re-read one",
        )

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
