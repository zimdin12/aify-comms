"""`recovering` is in the LIVE sets and out of the ACTIVE ones, on purpose. Do not "fix" it.

Two named sets differ by exactly one member:

    LIVE_SESSION_STATUSES     {active, attached, idle, recovering, running, starting}
    _TERMINAL_ACTIVE_STATUSES {active, attached, idle,             running, starting}

That is the signature of finding N7 — "two sweeps disagreeing about a status literal" — and a scan
for near-miss status sets flags it immediately. It is NOT that. The two answer different questions
about the same row, and `recovering` is the status where those questions legitimately diverge:

  * LIVE asks "does this worker still exist?" — used by the liveness and worker-presence queries that
    feed `worker_present`, and through it the agent's status and whether a dispatch is routed. A
    recovering terminal is coming back; calling it dead would report the agent as having no worker
    and re-route or refuse work that is about to have somewhere to go.

  * ACTIVE asks "is this worker ready to be acted on right now?" — used by `status_decision`, the
    `StatusInputs` byproduct, and both idle-run closers. A recovering terminal is not at a prompt, so
    reading its buffer for an idle prompt and closing the run would settle a run whose worker never
    finished coming back.

So the one-member difference is the design, and this file exists because the difference LOOKS like a
defect from the outside. A future reader — or a future me running a near-miss scan — will find this
pair and be tempted to unify them. Unifying in either direction breaks something:

    recovering INTO active  -> the idle closers may settle a run on a terminal that is not at a
                               prompt, and status_decision reads a recovering worker as ready
    recovering OUT of live  -> a recovering worker reads as ABSENT; the agent shows no worker and
                               dispatches are re-routed or refused while it is coming back

Measured context when written: eleven distinct "live-ish" status vocabularies are spelled out inline
across the service, twelve sites carrying the six-value LIVE set alone. That fragmentation is real
and is frozen elsewhere (`test_status_set_literal_twins_are_frozen.py`,
`test_ended_status_sets_agree.py`). This file covers only the one difference that is deliberate.
"""

from __future__ import annotations

import unittest

from service.api_core.terminal_status import _TERMINAL_ACTIVE_STATUSES, _TERMINAL_END_STATUSES
from service.api_core.tuning import LIVE_SESSION_STATUSES

RECOVERING = "recovering"


class RecoveringIsLiveButNotActiveTests(unittest.TestCase):
    def test_recovering_counts_as_live(self):
        self.assertIn(
            RECOVERING, {s.lower() for s in LIVE_SESSION_STATUSES},
            "a recovering worker would now read as ABSENT: the agent shows no worker and dispatches "
            "are re-routed or refused while it is coming back",
        )

    def test_recovering_does_not_count_as_active(self):
        self.assertNotIn(
            RECOVERING, {s.lower() for s in _TERMINAL_ACTIVE_STATUSES},
            "a recovering terminal would now be treated as ready: the idle-run closers may settle a "
            "run on a terminal that is not at a prompt, and status_decision reads it as actionable",
        )

    def test_the_two_sets_differ_by_exactly_this_and_nothing_else(self):
        """If they diverge FURTHER, the difference stops being one documented decision and becomes
        the drift this looks like. That is when it needs a ruling, not a test."""
        live = {s.lower() for s in LIVE_SESSION_STATUSES}
        active = {s.lower() for s in _TERMINAL_ACTIVE_STATUSES}
        self.assertEqual(
            live - active, {RECOVERING},
            "LIVE now holds something beyond ACTIVE other than `recovering`. One of the two moved "
            "and this file no longer describes the difference.",
        )
        self.assertEqual(
            active - live, set(),
            "ACTIVE now holds a status LIVE does not — a worker that is actionable but not alive is "
            "a contradiction, and every worker-presence query would miss it",
        )

    def test_recovering_is_not_an_ended_status(self):
        """Anti-vacuity in the direction that matters: the pin above is only meaningful while
        `recovering` means "coming back". If it were ever also an END status, both readings would be
        wrong and the sets would agree for the wrong reason."""
        self.assertNotIn(RECOVERING, {s.lower() for s in _TERMINAL_END_STATUSES})

    def test_the_sets_are_not_trivially_equal_or_empty(self):
        live = {s.lower() for s in LIVE_SESSION_STATUSES}
        active = {s.lower() for s in _TERMINAL_ACTIVE_STATUSES}
        self.assertNotEqual(live, active, "the whole subject of this file is that they differ")
        self.assertGreaterEqual(len(active), 4)
        self.assertTrue(active < live, "ACTIVE must be a strict subset of LIVE")
