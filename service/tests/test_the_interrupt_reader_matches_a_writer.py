"""The code that attributes an interrupt reads a table and action something actually writes.

The first version of the attribution queried `terminal_controls WHERE action = 'interrupt'`. Nothing
writes that combination, so it could never fire -- dead code that read as a feature, in a file whose
whole purpose is to stop a guess being reported as a determined cause.

Two paths issue an interrupt and neither produces that row:

  * the dashboard writes `dispatch_controls` with action 'interrupt', carrying run_id and requester;
  * `comms_interrupt` posts a raw Ctrl+C to /console/input, landing as a terminal control action
    'input'.

Found by COUNTING ROWS in the live database, not by re-reading the query: terminal_controls held ten
rows and every one was 'start'. A query is not evidence that its shape exists.

So this test pairs the reader with a writer by reading both out of the source. It is a source-level
check and knows it: what it can prove is that the two名 agree, not that the row arrives. The suite's
behavioural tests cover the rest.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
READER = ROOT / "service" / "reconcilers" / "dispatch_lifecycle.py"


def reader_source() -> str:
    return READER.read_text(encoding="utf-8")


def writer_sources() -> str:
    """Everything under service/ that could append a control, minus the tests."""
    text = []
    for path in (ROOT / "service").rglob("*.py"):
        if "tests" in path.parts:
            continue
        text.append(path.read_text(encoding="utf-8"))
    return "\n".join(text)


def test_the_reader_queries_dispatch_controls_for_an_interrupt():
    """The narrowest statement of the bug: it must not be looking at terminal_controls."""
    source = reader_source()
    attribution = source[source.index("interrupts: dict"):source.index("for row in (rows or [])")]
    assert "dispatch_controls" in attribution, "the attribution query moved off the table that has the rows"
    assert "terminal_controls" not in attribution, (
        "the attribution is reading terminal_controls again, where no interrupt row is ever written"
    )


def test_a_writer_produces_exactly_that_action_on_that_table():
    """The pairing. A reader whose action string no writer emits is the defect this file exists for."""
    writers = writer_sources()
    assert re.search(r'action\s*=\s*"interrupt"', writers), (
        "nothing writes action='interrupt' any more; the attribution now reads a shape nobody produces"
    )
    assert "_append_dispatch_control" in writers, "the dispatch-control writer is gone"


def test_the_attribution_keys_on_the_run_and_needs_no_join():
    """dispatch_controls carries run_id, so the failing run is named directly.

    The first version joined terminal_controls to terminal_sessions to recover an agent id -- a join
    whose right-hand side another reconciler prunes, so attribution could vanish for a busy agent.
    """
    source = reader_source()
    attribution = source[source.index("interrupts: dict"):source.index("for row in (rows or [])")]
    assert "run_id" in attribution
    assert "JOIN" not in attribution.upper(), "the attribution grew a join it does not need"
    assert "interrupts[run]" in attribution, "the map is keyed by something other than the run"


def test_the_lookup_uses_the_run_being_failed():
    source = reader_source()
    assert "interrupts.get(run_id)" in source, (
        "the reconciler looks the interrupt up by something other than the run it is failing"
    )
