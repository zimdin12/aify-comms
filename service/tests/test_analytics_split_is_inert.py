"""The `get_analytics` split, re-proved against the real code on every run.

The extract-method gate proves a split is inert by INLINING IT BACK: substitute the helper's body
over the call and the result must reproduce the pre-split function exactly. Running that once at
refactor time proves the commit. Running it in the suite proves it stays true — if someone later
edits `_hourly_message_series` or the call site and the two drift, the round trip stops closing.

The pre-split source is committed as a FIXTURE rather than recovered from git, deliberately: the
route gates in v0.5 shipped unable to run from a clean clone because their snapshots were
gitignored, and a proof that needs `.git` to run is the same mistake. `test_fixtures_are_tracked`
covers the file itself.

WHAT THIS DOES NOT DO: it does not re-verify the whole handler. It verifies the ONE extraction named
here. `test_analytics_characterization.py` is the behavioural net around the endpoint; this is the
structural proof of the split.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
ANALYTICS = REPO / "service" / "routers" / "analytics.py"
#: The eight helpers were RELOCATED out of the router in v0.5.4 — byte-identical, so the round trip
#: still closes, but only if the proof reads the file they live in now.
#:
#: THE THIRD PROOF IN THIS SERIES TO NEED THIS FIX. Each of them named the one or two modules it
#: expected to find things in, so a helper landing anywhere else made the round trip find nothing to
#: inline while the test kept passing. One tuple, read by every check, is the shape that survives the
#: next relocation.
SERIES = REPO / "service" / "api_core" / "analytics_series.py"
MODULES = (ANALYTICS, SERIES)


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)
FIXTURE = Path(__file__).resolve().parent / "data" / "get_analytics_before_split.py"

#: The function every extraction below came out of, and the helpers extracted from it.
#:
#: ONE fixture and ONE comparison, not a chain of per-extraction fixtures. Verifying extraction N
#: against "the state just before extraction N" needs a second copy of the function per split, each
#: of which rots independently and proves the wrong thing while staying green. Inlining ALL the
#: helpers back and comparing once against the TRUE original is both a stronger claim and the one
#: that keeps working as more blocks come out.
SOURCE_FUNCTION = "get_analytics"

#: Edits made SINCE the split, as (NOW, WAS): the helper rewrites today's text back to the original
#: before comparing, so the current block comes first. Declared rather than folded into the fixture,
#: which is history -- editing that would prove the wrong thing while staying green.
#:
#: THE EDIT. The per-agent status loop was given the shared context `GET /api/v1/agents` and the
#: reconcile sweep already had, so the request resolves the fleet's environment once instead of once
#: per agent. Measured cold: 16N + 79 round-trips before, 11N + 81 after, exact at four fleet sizes.
_FLEET_LOOP_NOW = chr(10).join([
    '        # Built ONCE for the whole loop, not once per agent. Every status below resolves the same',
    '        # two questions, and both answers are constant across a single request: the owning',
    '        # environment depends on machine_id alone, and the session environment is one table read for',
    '        # the whole fleet. Measured 2026-08-26 by counting aiosqlite execute() calls through one',
    '        # GET /api/v1/analytics on a COLD live-state cache: 463 round-trips at 24 agents, of which',
    '        # `SELECT * FROM environments WHERE machine_id = ?` and `SELECT environment_id FROM',
    '        # agent_sessions ...` were 48 each and `SELECT * FROM agents WHERE id = ?` 24 -- five per',
    '        # agent, re-reading answers this request already had. `GET /api/v1/agents` was given the same',
    '        # request-scoped dicts in fab4204c and the reconcile sweep a sweep-scoped pair; this is the',
    '        # third caller of the same derivation and the last one still asking per agent.',
    '        #',
    '        # `agent_row=row` is safe HERE specifically: these rows come from the `SELECT * FROM agents`',
    '        # four lines above, which is the same query the refresh would issue for itself.',
    '        environments_by_machine: dict = {}',
    '        session_environment_by_agent = await load_session_environment_by_agent(db)',
    '        # ONE PREFETCH for the whole loop. Three of the four batch parameters were already',
    '        # threaded here; `status_signals` was not, so this endpoint paid `agent_status_state`',
    '        # and `agent_console_signal` per agent -- the same two the pulse board stopped',
    '        # re-reading. Measured: 7.0 round-trips per agent before.',
    '        status_signals = None',
    '        if len(agent_rows) > 1:',
    '            status_signals = await PrefetchedStatusSignals.load(db, [r["id"] for r in agent_rows])',
    '        for row in agent_rows:',
    '            mode = _agent_wake_mode(row)',
    '            if mode != "message-only" and mode != "disabled":',
    '                live_agents += 1',
    '            status = await _compute_agent_status(',
    '                row,',
    '                db,',
    '                environments_by_machine=environments_by_machine,',
    '                session_environment_by_agent=session_environment_by_agent,',
    '                agent_row=row,',
    '                status_signals=status_signals,',
    '            )',
    '            # THROUGH THE DECLARED PARTITION. This read `not offline and not stale`, which',
    '            # counts a STOPPED agent as online -- measured live, /analytics reported 30 while',
    '            # /analytics/pulse reported 27 on a fleet with exactly 3 stopped agents, and',
    '            # `online_agents` is the utilization denominator below. It also excluded `stale`,',
    '            # a status this engine stopped producing, so that half guarded nothing.',
    '            if is_live_agent_status(status):',
    '                online_agents += 1',
]) + chr(10)

_FLEET_LOOP_WAS = chr(10).join([
    '        for row in agent_rows:',
    '            mode = _agent_wake_mode(row)',
    '            if mode != "message-only" and mode != "disabled":',
    '                live_agents += 1',
    '            status = await _compute_agent_status(row, db)',
    '            if not status.startswith("offline") and not status.startswith("stale"):',
    '                online_agents += 1',
]) + chr(10)

# The overdue window is the operator's `reply_reminder_minutes`, not a hardcoded 30 minutes: two
# analytics tiles disagreed with the Work Loop and the reminder sweep, which both read the setting.
_OVERDUE_WINDOW_NOW = chr(10).join([
    "        # The OPERATOR'S window, not a literal: the reminder sweep and the Work Loop filter both",
    '        # read this setting, and a tile labelled "overdue" that uses a different number is a',
    '        # second answer to one question.',
    '        overdue_cut = now_s - reply_reminder_minutes(settings) * 60',
])
_OVERDUE_WINDOW_WAS = chr(10).join([
    '        overdue_cut = now_s - 30 * 60',
])

EDITED_SINCE = [(_FLEET_LOOP_NOW, _FLEET_LOOP_WAS), (_OVERDUE_WINDOW_NOW, _OVERDUE_WINDOW_WAS)]
EXTRACTIONS = [
    "_hourly_message_series",
    "_append_daily_message_buckets",
    "_monthly_message_series",
    "_fleet_median_reply_minutes",
    "_dispatch_outcomes_series",
    "_agent_leaderboard",
    "_busiest_channels",
    "_failure_reasons",
]


class AnalyticsSplitIsInertTests(unittest.TestCase):
    def test_every_extraction_together_inlines_back_to_the_original(self):
        split_src = _combined_split_source()
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), split_src, EXTRACTIONS,
            edited_since=EDITED_SINCE)

    def test_the_fixture_is_the_function_it_claims_to_be(self):
        """A fixture that stopped containing the function would make the test above vacuous."""
        names = {
            n.name for n in ast.parse(FIXTURE.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(SOURCE_FUNCTION, names)

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip above would pass by having nothing to inline.

        Two claims, and the relocation separated them. Each helper must still EXIST somewhere the
        proof reads — otherwise the inline-back has nothing to substitute — and it must no longer be
        declared in the ROUTER, or the split has been undone. Checking only the first against only
        analytics.py conflated them and went red on a move that changed no behaviour.
        """
        declared_by_module = {
            path: {
                n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for path in MODULES
        }
        for helper in EXTRACTIONS:
            self.assertTrue(
                any(helper in names for names in declared_by_module.values()),
                f"{helper} is gone — was the split reverted?",
            )
            self.assertNotIn(
                helper, declared_by_module[ANALYTICS],
                f"{helper} is back in the router; it was moved to {SERIES.name} in v0.5.4",
            )


if __name__ == "__main__":
    unittest.main()
