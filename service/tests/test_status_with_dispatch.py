"""When a dispatch run may promote an agent's status to `working` — and when it must not.

`_status_with_dispatch` is the last step before a status reaches the dashboard and
`comms_agent_info`, and no test named it. It is four lines of conditions, each of which was paid for:

  * ONLY a `running` run promotes. A merely CLAIMED run does not — a bridge claimed it to deliver
    but the turn has not started, or the agent already finished and the run simply is not closed
    yet. Promoting on `claimed` was the root cause of agents showing `working` while actually idle
    (2026-06-18): a stale claim held them there indefinitely, because nothing ever closed it.
  * The promotion is still KEPT for `running`, so a just-delivered turn reads `working` before the
    bridge's turn-start event lands. Removing it would leave a genuinely working agent reading
    `online` for the gap between delivery and the first event.
  * A MANUAL status outranks it. `stopped` is an operator's decision, and a run arriving afterwards
    must not quietly un-stop the agent in the UI.
  * `stale`, `offline` and `blocked` outrank it too — all three describe the agent's ability to work
    at all, and a run cannot argue with them. An offline agent showing `working` is the exact false
    green the status engine exists to prevent.

Every one of these fails silently: the wrong word appears on a dashboard, and a status-sorted list
moves a row. Nothing raises.
"""
from __future__ import annotations

import pytest

from service.api_core.manual_status import _MANUAL_STATUSES
from service.api_core.records import _status_with_dispatch

# Statuses that describe an agent that CAN work, and so may be promoted.
PROMOTABLE = ["online", "available", "idle", "working", ""]
# Statuses that outrank a run: the agent cannot work, or an operator said so.
PROTECTED = ["stale", "offline", "blocked", "stopped"]


def state(status, **over):
    run = {"status": status}
    run.update(over)
    return {"hasActiveRun": True, "activeRun": run, "queuedRuns": 0}


# ── the promotion ────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("base", PROMOTABLE)
def test_a_running_run_promotes_a_workable_status_to_working(base):
    assert _status_with_dispatch(base, state("running")) == "working"


@pytest.mark.parametrize("run_status", ["claimed", "queued", "delivered", "completed", "failed", "cancelled", ""])
def test_only_running_promotes(run_status):
    """THE 2026-06-18 BUG. A stale `claimed` run held idle agents at `working` indefinitely, because
    nothing ever closed it — the status must come from the engine's turn_busy verdict instead."""
    assert _status_with_dispatch("online", state(run_status)) == "online"


def test_the_run_status_is_matched_exactly():
    """No trimming or case-folding here, deliberately: the value is written by this service, not by
    an agent. A near-miss must NOT promote — inventing tolerance would re-admit `Claimed`-style
    values that were never meant to mean running."""
    for near_miss in ("RUNNING", " running", "running ", "Running"):
        assert _status_with_dispatch("online", state(near_miss)) == "online"


# ── what outranks a run ──────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("base", PROTECTED)
def test_a_protected_status_is_never_overwritten_by_a_running_run(base):
    assert _status_with_dispatch(base, state("running")) == base


def test_the_manual_status_set_is_the_one_that_is_honoured():
    """Read from `manual_status.py` rather than restated, so adding a manual status here cannot
    diverge from the set the rest of the service uses."""
    for manual in _MANUAL_STATUSES:
        assert _status_with_dispatch(manual, state("running")) == manual, (
            f"{manual} is an operator decision — a run must not quietly undo it"
        )


# ── the shapes that carry no verdict ─────────────────────────────────────────────────────────
def test_no_dispatch_state_returns_the_status_unchanged():
    for empty in (None, {}):
        assert _status_with_dispatch("online", empty) == "online"
        assert _status_with_dispatch("offline", empty) == "offline"


def test_a_state_with_no_active_run_changes_nothing():
    assert _status_with_dispatch("online", {"hasActiveRun": False, "activeRun": None}) == "online"
    assert _status_with_dispatch("online", {"queuedRuns": 5}) == "online", (
        "QUEUED work is not the agent working — it is work waiting for the agent"
    )


def test_a_null_active_run_is_treated_as_absent_not_as_an_error():
    """`activeRun` is None on every idle agent, which is the commonest input this function sees."""
    assert _status_with_dispatch("online", {"activeRun": None}) == "online"


def test_the_protected_list_has_DRIFTED_from_the_canonical_vocabulary():
    """CHARACTERIZATION OF A SUSPECTED DEFECT — pinned, reported, NOT fixed here.

    The guard is a hand-written literal, `status not in {"stale", "offline", "blocked"}`, and it has
    drifted from `vocabulary.py` in both directions:

      * it protects `stale`, which is NOT a canonical agent status at all;
      * it does NOT protect `misconfigured` or `starting`, which ARE canonical and mean, in the
        vocabulary's own words, "Identity exists but can never start" and "no worker YET".

    So a running dispatch row promotes both to `working` — defined as "Live worker, open turn". By
    construction neither can have one: `status_engine.derive()` returns `working`/`online` FIRST when
    `alive and worker_present`, so reaching `misconfigured` or `starting` means it already decided
    there is no live worker. This promotion overrides that verdict using a raw dispatch_runs row,
    which is the same weaker-authority-overrides-derive() shape the engine was built to end.

    The visible consequences: an agent that can never start displays as `working`, and `send_preflight`
    tells a sender "agent is working" — so they wait — when the true answer is that a human must fix
    the config.

    NOT FIXED HERE because the correct protected set is a design decision in a subsystem whose
    docstring calls `derive()` the sole authority, and the reviewer is unreachable. This test pins
    what the code does today so the fix is a deliberate flip with a visible diff.
    """
    from service.api_core.vocabulary import AGENT_STATUSES

    assert "stale" not in AGENT_STATUSES, "if `stale` became canonical, this analysis needs redoing"

    promoted = [s for s in AGENT_STATUSES if _status_with_dispatch(s, state("running")) != s]
    assert promoted == ["online", "available", "starting", "misconfigured"], (
        f"the promoted set changed to {promoted}. If `misconfigured`/`starting` were removed from it, "
        "the suspected defect is FIXED and this test should become the positive assertion."
    )


def test_an_unknown_base_status_is_passed_through_rather_than_normalised():
    """This function decides ONE thing. A status it does not recognise is somebody else's business
    and must arrive at the caller intact rather than being coerced into a known word."""
    assert _status_with_dispatch("some-future-status", state("claimed")) == "some-future-status"
    assert _status_with_dispatch("some-future-status", state("running")) == "working", (
        "though an unrecognised status is still promotable — only the protected list is exempt"
    )
