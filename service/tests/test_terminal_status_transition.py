"""A terminal that has ended can never read active again.

`_terminal_status_transition` is the guard on the one direction that must not happen. A terminal
session moves through active states (starting/attached/running/active/idle) and eventually reaches a
MONOTONIC one (stopping/stopped/failed/lost/ended/completed/cancelled). From there it is done, and the
guard's job is to reject any writer that tries to put it back.

WHY THAT MATTERS HERE SPECIFICALLY. `terminal_sessions` has ~26 writers. DECISIONS.md records a
`stopped` row written over a process that never died, and the stuck-stopping reaper writing `stopped`
900 seconds after a stop that was itself cancelled by a sweep. The failure mode of the OPPOSITE
direction — a dead row resurrected to `running` — is the same "state that lies" class one step
further: every liveness gate downstream keys on the terminal being active, so a resurrected row
manufactures a live worker that does not exist, and the dashboard reports a console nobody can attach
to. Nothing raises either way.

The guard returns "" for a REJECTED transition, which the callers read as "write nothing". That is
the whole contract, and it is three lines of conditions — so each is exercised, including the two
directions that must NOT be blocked: monotonic-to-monotonic (a stopping row must still reach stopped)
and active-to-active.
"""
from __future__ import annotations

import pytest

from service.api_core.terminal_status import (
    _TERMINAL_ACTIVE_STATUSES,
    _TERMINAL_MONOTONIC_STATUSES,
    _terminal_status_transition,
)


# ── the rejection this exists for ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("dead", sorted(_TERMINAL_MONOTONIC_STATUSES))
@pytest.mark.parametrize("alive", sorted(_TERMINAL_ACTIVE_STATUSES))
def test_a_finished_terminal_is_never_resurrected(dead, alive):
    """The full cross product, derived from the module's own sets rather than listed here — a status
    added to either set joins this matrix automatically."""
    assert _terminal_status_transition(dead, alive) == "", f"{dead} -> {alive} must be refused"


def test_rejection_is_an_empty_string_because_callers_read_it_as_write_nothing():
    assert _terminal_status_transition("stopped", "running") == ""
    assert _terminal_status_transition("stopped", "running") is not None, (
        "None would be a different sentinel; the callers test the string"
    )


# ── the transitions that must NOT be blocked ─────────────────────────────────────────────────
@pytest.mark.parametrize("later", sorted(_TERMINAL_MONOTONIC_STATUSES))
@pytest.mark.parametrize("earlier", sorted(_TERMINAL_MONOTONIC_STATUSES))
def test_one_finished_state_may_still_move_to_another(earlier, later):
    """`stopping` -> `stopped` is the ordinary path, and `stopped` -> `failed` records a cause. A
    guard that froze the row at its first monotonic status would strand every stop mid-flight."""
    assert _terminal_status_transition(earlier, later) == later


@pytest.mark.parametrize("later", sorted(_TERMINAL_ACTIVE_STATUSES))
@pytest.mark.parametrize("earlier", sorted(_TERMINAL_ACTIVE_STATUSES))
def test_active_states_move_freely_between_themselves(earlier, later):
    assert _terminal_status_transition(earlier, later) == later


@pytest.mark.parametrize("alive", sorted(_TERMINAL_ACTIVE_STATUSES))
@pytest.mark.parametrize("dead", sorted(_TERMINAL_MONOTONIC_STATUSES))
def test_an_active_terminal_may_always_finish(alive, dead):
    """The guard is one-directional on purpose. Blocking this would make a terminal impossible to
    stop, which is a worse failure than the one being prevented."""
    assert _terminal_status_transition(alive, dead) == dead


# ── the two sets are disjoint, or the guard is incoherent ────────────────────────────────────
def test_the_active_and_monotonic_sets_do_not_overlap():
    """A status in both would be simultaneously resurrectable and final — the guard would accept or
    refuse it depending only on which side of the comparison it landed."""
    assert not (_TERMINAL_ACTIVE_STATUSES & _TERMINAL_MONOTONIC_STATUSES)
    assert _TERMINAL_ACTIVE_STATUSES and _TERMINAL_MONOTONIC_STATUSES


# ── normalisation and the degenerate inputs ──────────────────────────────────────────────────
def test_both_sides_are_trimmed_and_lowercased_before_comparing():
    """These arrive from a database column and from bridge payloads. A `STOPPED` that failed to
    match the monotonic set would silently disable the guard for that row."""
    assert _terminal_status_transition("  STOPPED  ", "running") == ""
    assert _terminal_status_transition("Stopped", "  RUNNING  ") == ""
    assert _terminal_status_transition("running", "  STOPPED ") == "stopped", (
        "the accepted value is returned NORMALISED, not as the caller spelled it"
    )


def test_an_empty_next_status_is_refused_rather_than_written():
    """A writer with nothing to say must not blank the column."""
    for empty in ("", "   ", None):
        assert _terminal_status_transition("running", empty) == ""
        assert _terminal_status_transition("stopped", empty) == ""


def test_an_unknown_current_status_does_not_block_anything():
    """The guard keys on the CURRENT status being final. An unrecognised one — a legacy value, or a
    row written before a rename — is not evidence of death, so it must not freeze the row. Failing
    the other way would make such a terminal unstoppable AND unstartable."""
    for unknown in ("", "   ", None, "brand-new-status"):
        assert _terminal_status_transition(unknown, "running") == "running"
        assert _terminal_status_transition(unknown, "stopped") == "stopped"


def test_an_unknown_next_status_is_REFUSED():
    """REVERSED 2026-08-16. This asserted the opposite, and the reason did not survive a trace.

    It said: "this function is a guard, not a vocabulary check, and rejecting an unrecognised target
    would silently drop writes from a newer writer." The bridge IS host-side and routinely a
    different build, so the concern was real — but it assumes the dropped write carries information
    this service could use, and it cannot: a service that does not recognise a status has no code
    that acts on it.

    WHAT THE TRACE FOUND. Every terminal reaper selects `WHERE status IN (...)` — `managed_workers`,
    `terminals` (both the active and the end query), `terminal_consistency`. Not one keys on age. So
    a row holding an undeclared status matches NO reaper, is not counted live by the status engine,
    and is never closed out — while `agent_terminal_ops.py` reads it as "still running". It is on the
    live side of every read and the invisible side of every cleanup.

    Both concrete futures favour refusing. A bridge that INVENTS a status: keeping it strands the row
    forever, dropping it leaves the last known status, which the reapers still act on. A bridge that
    RENAMES one (`stopped` -> `exited`): keeping it hides the row from both the active and end lists,
    dropping it leaves the row `running` and `managed_workers.py` reaps it as a ghost when the worker
    dies.

    A no-op today — `test_terminal_status_vocabulary.py` enumerates both writers and every literal
    either side sends is already a member.
    """
    assert _terminal_status_transition("running", "brand-new-status") == ""
    assert _terminal_status_transition("stopped", "brand-new-status") == ""
    assert _terminal_status_transition("running", "exited") == "", (
        "the rename case: `exited` is not a declared terminal status, so the row keeps `running` "
        "and stays reapable instead of becoming invisible to every sweep"
    )
    # …and the refusal reuses the existing "no change" channel rather than inventing a signal.
    assert _terminal_status_transition("running", "") == ""
