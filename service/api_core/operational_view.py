"""The operational view: what comms reads after the 0.9.8 switch (plan 2026-10-20-s4-hard-switch, SW2).

Built beside the status shadow and not yet called by any live reader; SW5 rewires the readers to it in one commit.

Two layers, so the only clock comms keeps is visible in one place:
  - `holders_of` classifies each publisher by REPORT age (`report_age`, decision 2: three of the intervals aify-env
    announced, on comms' own receipt clock; no announcement reads `not-reporting`, so unknown). It never reads a turn's
    age, and contact never makes data fresh: the loss, unavailable, restored and incomplete guards come first.
  - `comms_view` is pure over those holders and the messaging facts. It takes no clock that it ages anything against:
    aify-env alone decides `busy` (DECISIONS: "comms never times turns").

Task #5 lets a provably silent publisher yield only where a fresh peer on that machine lists the same agent.
Availability and busy are separate typed facts. Eligibility is known only when every remaining holder is fresh and
no publisher on its machine is unreadable; unknown withholds before busy and before steer.
"""
from dataclasses import dataclass

from service.api_core import partial_status_shadow as shadow
from service.api_core.report_age import report_age
from service.api_core.view_owners import PROMOTABLE

FRESH = 'fresh'  # the only availability a known answer is built from; every other class reads unknown
# 'incomplete' is a publisher with no accepted complete snapshot. SW1's restored publishers read 'restored'.


@dataclass(frozen=True)
class Holder:
    """One publisher's record of the agent: its availability, the record's top-level busy, its state and applied time."""
    availability: str
    busy: bool | None
    state: str
    lifetime: str | None
    applied_at: str | None


@dataclass(frozen=True)
class Holders:
    """Every record of the agent on its machine, and whether a publisher there could hold it unseen."""
    holders: tuple
    unreadable: bool  # a publisher on the machine whose rows cannot be read, or the binding failed


@dataclass(frozen=True)
class Messaging:
    """comms' own facts about the agent: runs, the operator's stop of an undefined agent, its wake handle."""
    run_occupied: bool = False  # a run claimed or running (today's hasActiveRun)
    run_running: bool = False  # a run comms delivered is running (row 5's promotion)
    operator_stopped: bool = False  # an undefined agent the operator stopped (D8 row 1)
    wake_handle_absent: bool = False  # Q3: a proven-absent wake handle reads offline
    delivery_path_missing: str = ''  # why no delivery path exists, or ''


@dataclass(frozen=True)
class View:
    status: str
    last_known: tuple | None  # (word, applied_at) of the last applied record, shown only beside `unknown`
    deliverable: bool
    eligibility: str  # 'known' | 'unknown'
    busy: object  # True | False | None (unknown)
    holds_delivery: object  # True | False | None (unknown)
    run_occupied: bool


def _availability(publisher, at):
    if publisher.loss:
        return 'lost'
    if publisher.unavailable:
        return 'unavailable'
    if getattr(publisher, 'restored', False):
        return 'restored'
    if not publisher.complete:
        return 'incomplete'
    return report_age(publisher, at)


def holders_of(binding, machine, agent_id):
    """The agent's holders, classified by receipt age at the binding's time. A failed binding is all unknown."""
    if binding.loss or binding.view is None:
        return Holders((), True)
    candidates = []
    for (mid, _instance), publisher in binding.view.items():
        if mid != machine:
            continue
        availability = _availability(publisher, binding.at)
        rows = [r for r in publisher.rows if r.agent_id == agent_id]
        candidates.append((publisher, availability, rows))
    covered = any(availability == FRESH and rows for _, availability, rows in candidates)
    holders, unreadable = [], False
    for publisher, availability, rows in candidates:
        # Task #5: ignore silence only where a live peer actually lists this agent. No retirement or generation
        # ranking: an advancing report can rejoin immediately; missing cadence and invalid clocks never yield.
        if covered and report_age(publisher, binding.at) == 'stale':
            continue
        if availability != FRESH and not rows:
            unreadable = True  # its rows cannot be trusted to say the agent is absent
        holders += [Holder(availability, r.busy, r.state, r.lifetime, publisher.data_applied_at) for r in rows]
    return Holders(tuple(holders), unreadable)


def _busy(holders):
    """(eligibility, busy). Known only when every holder is fresh and nothing is unreadable; a fresh busy is true
    whatever else is unknown."""
    fresh = [h for h in holders.holders if h.availability == FRESH]
    if any(h.busy is True for h in fresh):
        known = holders.holders and not holders.unreadable and len(fresh) == len(holders.holders)
        return ('known' if known else 'unknown'), True
    if not holders.holders or holders.unreadable or len(fresh) != len(holders.holders):
        return 'unknown', None
    if any(h.busy is not False for h in fresh):  # a missing or malformed busy is not known idle
        return 'unknown', None
    return 'known', False


def _status_word(state):
    word = shadow.candidate_word(state)
    return word or 'unknown'


def _last_known(holders):
    applied = [h for h in holders.holders if h.applied_at]
    if not applied:
        return None
    latest = max(applied, key=lambda h: h.applied_at)
    return (_status_word(latest.state), latest.applied_at)


def comms_view(holders, messaging):
    """PURE. The status word, eligibility, busy and the delivery hold, from the agent's holders and comms' own facts."""
    eligibility, busy = _busy(holders)
    holds = busy if busy is True else (False if eligibility == 'known' else None)
    fresh = [h for h in holders.holders if h.availability == FRESH]
    last_known = None
    if messaging.operator_stopped:
        status = 'stopped'
    elif messaging.wake_handle_absent:
        status = 'offline'
    elif eligibility == 'unknown':
        status, last_known = 'unknown', _last_known(holders)
    elif len(fresh) > 1:
        status = 'unknown'  # C6 row 4: a conflict names both lifetimes, never one of them
    else:
        status = _status_word(fresh[0].state)
        if messaging.run_running and fresh[0].state in PROMOTABLE:
            status = 'working'  # C6 row 5: a run comms delivered is running
    return View(status, last_known, not messaging.delivery_path_missing, eligibility, busy, holds,
                messaging.run_occupied)


# The readers, eligibility first. Each is pure over a View.

def claim_decision(view, steerable_run):
    """The claim gate: 'withhold' on unknown (steer included), 'steer' or 'hold' on busy, 'proceed' on known idle."""
    if view.eligibility != 'known':
        return 'withhold'  # even beside a fresh busy holder: unknown eligibility is checked before steer
    if view.busy:
        return 'steer' if steerable_run else 'hold'
    return 'proceed'


def send_time_hold(view, queue_if_busy, occupied):
    """The send-time path's console hold. Unknown puts nothing on a console whatever the queue choice; with eligibility
    known an explicit queue waits for a run already occupied or queued, or for aify-env's busy. Backing is never
    skipped by it (SW4's ordering rule)."""
    if view.eligibility != 'known':
        return True
    return bool(queue_if_busy) and bool(occupied or view.busy)


def reminder_paused(view):
    """Operator decision R3 (adopted 2026-10-21): a reply-overdue reminder waits while the agent is busy or unknown,
    and while a run comms delivered is still claimed or running (the run is comms' own fact, as row 5 reads it)."""
    return view.busy is not False or view.eligibility != 'known' or view.run_occupied


def stranded_protected(view):
    """Operator decision R3 (adopted 2026-10-21): a run is not failed as stranded while the agent is busy or unknown."""
    return view.busy is not False or view.eligibility != 'known'


#: Words that prove the target cannot work the run, whatever aify-env last said: an operator stop of an undefined agent
#: (D8 row 1) or a proven-absent wake handle (Q3), or aify-env's own record saying so. Row 5 never promotes either.
GONE_WORDS = frozenset({'offline', 'stopped'})


def orphan_safety(view):
    """The orphaned-run reaper's safety call (SW5 review R1), from the typed facts and never from the displayed word:
    row 5 promotes a fresh idle record to `working` because of the very run the reaper is judging, so the word would
    protect that run from itself forever.

    'protect' while aify-env says busy or comms cannot read whether it is; 'gone' when the target provably cannot work
    it; 'idle' when aify-env knows it idle, the only state in which a run with no progress is the run's own fault."""
    if view.busy is True:
        return 'protect'
    if view.status in GONE_WORDS:
        return 'gone'
    if view.eligibility != 'known':
        return 'protect'
    return 'idle'


#: The words row 5 promotes, derived from the states it names (idle reads `online`). `unknown` is never among them.
PROMOTABLE_WORDS = frozenset(w for w in (shadow.candidate_word(s) for s in PROMOTABLE) if w)


def promoted(word, run_running):
    """Row 5 on a path that holds a word and the run, not the record: a running run makes a promotable word
    `working`; every other word, `unknown` included, stays as it is."""
    return 'working' if run_running and word in PROMOTABLE_WORDS else word


def counts_live(word):
    """Whether a word counts toward the live fleet: a known live word only. `unknown` is no evidence of a live worker."""
    from service.status_engine import is_live_agent_status

    return word != 'unknown' and is_live_agent_status(word)
