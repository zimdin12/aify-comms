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
  * `offline`, `misconfigured` and `blocked` outrank it too — all three describe the agent's
    ability to work at all, and a run cannot argue with them. An offline agent showing `working` is
    the exact false green the status engine exists to prevent. The guard asks
    `status_engine.is_live_agent_status` rather than a hand-written literal, so a fourth non-live
    status added later is covered without anyone remembering this file.

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
# `stale` is NOT here any more. It is not a canonical status -- the vocabulary says "no time-decay
# states, no `idle`, no `stale`" -- and protecting it contradicted this file's own rule that an
# unrecognised status IS promotable (see the last test). `misconfigured` takes its place, which is
# the half of the drift below that the vocabulary settles outright.
PROTECTED = ["offline", "blocked", "stopped", "misconfigured"]


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


def test_the_protected_list_no_longer_DRIFTS_on_misconfigured():
    """HALF OF A DEFERRED DECISION, RESOLVED. The other half is still open and still pinned below.

    This was a characterization of a suspected defect: the guard was the hand-written literal
    `status not in {"stale", "offline", "blocked"}`, which protected `stale` (not a canonical status
    at all) and failed to protect `misconfigured` or `starting` (both canonical). It was left unfixed
    because "the correct protected set is a design decision... and the reviewer is unreachable".

    `misconfigured` is now protected, and that half needed no judgement: the vocabulary defines it as
    "Identity exists but can never start. Not send-recoverable; a human must fix the config." An
    agent that can never start cannot be `working`, which the vocabulary defines as "Live worker, open
    turn". The guard now asks `is_live_agent_status`, so this is settled by the partition rather than
    by a list somebody has to maintain.

    The visible consequence that is now gone: an agent that can never start displayed as `working`,
    and `send_preflight` told a sender "agent is working" -- so they waited -- when the true answer
    was that a human must fix the config.
    """
    from service.api_core.vocabulary import AGENT_STATUSES

    assert "stale" not in AGENT_STATUSES, "if `stale` became canonical, this analysis needs redoing"
    assert _status_with_dispatch("misconfigured", state("running")) == "misconfigured"


def test_STARTING_is_still_promoted_and_that_half_is_still_OPEN():
    """THE HALF NOT RESOLVED, pinned rather than decided quietly.

    `starting` means, in the vocabulary's own words, "A claimed spawn is coming up; no worker YET. Do
    NOT restart or re-send." Two readings, both defensible:

      * PROTECT IT. `working` means "Live worker, open turn" and `starting` says there is no worker,
        so the promotion asserts something the vocabulary denies. It also hides an instruction aimed
        at the operator -- do not restart -- behind a word that suggests nothing is wrong.
      * PROMOTE IT. The promotion exists so a just-delivered turn reads `working` before the bridge's
        turn-start event lands, and `available` -- which ALSO has no worker ("Managed and
        cold-startable, no worker. A send auto-starts it") -- is promoted for exactly that reason.
        An agent being started BY this run is the case the feature was written for.

    Unlike `misconfigured`, the vocabulary does not settle it: this is a choice about which word
    serves an operator better during a bounded transient, and it should be made deliberately rather
    than as a side effect of fixing the other half. Pinned here so the current behaviour is a
    recorded state and a future flip is a visible diff.
    """
    assert _status_with_dispatch("starting", state("running")) == "working"


def test_an_unknown_base_status_is_passed_through_rather_than_normalised():
    """This function decides ONE thing. A status it does not recognise is somebody else's business
    and must arrive at the caller intact rather than being coerced into a known word."""
    assert _status_with_dispatch("some-future-status", state("claimed")) == "some-future-status"
    assert _status_with_dispatch("some-future-status", state("running")) == "working", (
        "though an unrecognised status is still promotable — only the protected list is exempt"
    )
