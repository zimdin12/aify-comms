"""C6's view beside today's for three owners: live counts, running-run promotion, worker readiness (S2c slice 5).

Diagnostic only: each site computes and keeps its own answer, and each observer records C6's answer beside it as the
owner's one variant, contained (it never raises, cancellation excepted) and after today's answer.

C6's word is first-match-wins, so a candidate honours every row above the one it reads:
  1. an undefined agent the operator stopped: comms' own flag on the row the site holds. Whether the agent is
     undefined is not known here, so a set flag makes row 1 POSSIBLE, and the answer is withheld (unknown,
     'operator-stop') only where row 1 would change it; an unset flag rules row 1 out;
  2/2b. no record: rows 2 and 2b need D8's facts, which this slice does not implement, so a missing record is unknown
     ('no-record') wherever the two rows would answer differently (a disclosed gap, not qualified);
  3. a stale record: offline;  4. two records: `unknown`;  5. a delivered run running beside a record whose state is
     idle, shell, starting or available: working;  6. the record's state.
Live counts are unchanged by row 5: every state it promotes is already live, and so is `working`.

Readiness is C6's `process` and `deliverable`: the record's process (aify-env's) and comms' own delivery component.
The site's probe says which component its branch names and what it read; an unread one is asked at most once, only
when the answer can change, and a read that raised is unknown, never asked again.
"""
from service.api_core import comms_view as cv, partial_status_shadow as shadow
from service.api_core.delivery_turns import NOT_KNOWN
from service.api_core.live_process_probes import PROBE_ERROR, channel_sidecar_state, wrapper_child_state
from service.api_core.manual_status import _MANUAL_STATUSES
from service.api_core.shadow_evidence import item, row_facts
from service.api_core.shadow_provenance import LOST
from service.api_core.shadow_owners import NO_VARIANT
from service.status_engine import is_live_agent_status

LIVE_COUNTS, PROMOTION, READINESS = 'live counts', 'running-run promotion', 'worker readiness'
NOT_REACHED = 'not-reached'  # a liveness input today's branch never read
PROMOTABLE = ('idle', 'shell', 'starting', 'available')  # C6 row 5's states
PRECEDING_ROW_5 = ('stale', 'missing-row', 'ambiguous-association')  # rows 2-4: each decides the word first
PROBES = {'sidecar': channel_sidecar_state, 'wrapper-child': wrapper_child_state}


def _row_1(answer, stopped):
    """Row 1 can only make the answer False (stopped is neither live nor promoted)."""
    return (NOT_KNOWN, 'operator-stop') if stopped and answer[0] is not False else answer


def conflict_age(binding, association):
    """PURE. Rows 3 and 4 for an agent more than one publisher holds, from the same frozen view and time: every holder
    stale is row 3 (offline), every holder fresh is row 4 (`unknown`), and anything this view cannot establish is
    unknown. Each predicate is asked of the whole population, in this order: a holder lost, incomplete or
    unavailable; any other publisher on the machine unreadable (it might hold a fresher record); an invalid then a
    future clock; then the ages. A mixed fresh and stale conflict is a disclosed gap, not a ruling."""
    machine, agent_id = association
    mine = [p for (mid, _), p in binding.view.items() if mid == machine]
    holds = [any(r.agent_id == agent_id for r in p.rows) for p in mine]
    holders = [p for p, held in zip(mine, holds) if held]
    others = [p for p, held in zip(mine, holds) if not held]
    for unreadable, reason in ((lambda p: p.loss, 'unknown-loss'), (lambda p: not p.complete, 'unknown-knowledge'),
                               (lambda p: p.unavailable, 'publisher-unavailable')):
        if any(unreadable(p) for p in holders):
            return NOT_KNOWN, reason
    if any(p.loss or not p.complete or p.unavailable for p in others):
        return NOT_KNOWN, 'record-knowledge'
    ages = {shadow.age_class(p.data_applied_at, binding.at) for p in holders}
    for clock in ('invalid-clock', 'future'):
        if clock in ages:
            return NOT_KNOWN, clock
    if ages == {'stale'}:
        return False, None
    if ages == {'fresh'}:
        return is_live_agent_status('unknown'), None
    return NOT_KNOWN, 'mixed-age-conflict'


def live_candidate(record, reason, stopped, conflict=None):
    """PURE. Today's `is_live_agent_status`, on C6's word. `conflict` is conflict_age's answer when more than one
    publisher holds the agent."""
    if reason is None:
        word = shadow.candidate_word(record.state)
        answer = (is_live_agent_status(word), None) if word else (NOT_KNOWN, 'unknown-state')
    elif reason == 'stale':
        answer = False, None
    elif reason == 'ambiguous-association':
        answer = conflict
    else:
        answer = NOT_KNOWN, 'no-record' if reason == 'missing-row' else reason
    return _row_1(answer, stopped)


def promotion_candidate(record, reason, running, stopped):
    """PURE. Whether C6 row 5 promotes: a running run, and no earlier row deciding the word."""
    if not running:
        return False, None
    if reason in PRECEDING_ROW_5:
        answer = False, None
    elif reason is None:
        answer = record.state in PROMOTABLE, None
    else:
        answer = NOT_KNOWN, reason
    return _row_1(answer, stopped)


def process_answer(record, reason):
    """PURE. C6's process: running and verified."""
    if reason is not None:
        return NOT_KNOWN, 'no-record' if reason == 'missing-row' else reason
    state, verified = record.process or ('unknown', 'unknown')
    if state == 'running':
        return {'yes': (True, None), 'no': (False, None)}.get(verified, (NOT_KNOWN, 'process'))
    return (NOT_KNOWN, 'process') if state == 'unknown' else (False, None)


def delivery_answer(component, state):
    """PURE. The site's delivery read: no component delivers by itself; an error or an unread one is unknown."""
    if component is None:
        return True, None
    return (state, None) if state is True or state is False else (NOT_KNOWN, 'delivery')


def readiness_candidate(process, delivery):
    """PURE. process AND deliverable, three-valued; a known False decides it whatever the other is."""
    if process[0] is False or delivery[0] is False:
        return False, None
    if process[0] is True and delivery[0] is True:
        return True, None
    return next(a for a in (process, delivery) if a[0] is NOT_KNOWN)


def _stopped(agent_row):
    return str(agent_row['status'] or '').strip().lower() in _MANUAL_STATUSES


def _agent_record(agent_row):
    """(binding, association, record, reason): one bind, by the association the site already holds, so every later
    read of this evaluation uses the same frozen view and time."""
    association = shadow.association(agent_row) if agent_row is not None else None
    if association is None:
        return None, None, None, 'association'
    binding = shadow.bind()
    return (binding, association, *shadow.record_for(binding, association))


def observe_live_count(agent_row, live, site, held):
    """`live` is the site's own answer and `held` the status it computed it from, which the association row's status
    (row 1's flag) need not equal."""
    evidence, binding = None, LOST
    try:
        binding, association, record, reason = _agent_record(agent_row)
        conflict = conflict_age(binding, association) if reason == 'ambiguous-association' else None
        answer = ((NOT_KNOWN, reason) if reason == 'association'
                  else live_candidate(record, reason, _stopped(agent_row), conflict))
        evidence = lambda: item(LIVE_COUNTS, NO_VARIANT, site, association[1], binding, association, bool(live),
                                answer[0], dict(held=held, live=bool(live), association=row_facts(agent_row)))
    except Exception:
        answer = NOT_KNOWN, 'shadow-error'
    cv.record_answer(LIVE_COUNTS, NO_VARIANT, site, bool(live), answer, evidence, binding)


def observe_promotion(agent_row, base, effective, dispatch_state, site):
    """`base` and `effective` are the site's own `_status_with_dispatch` input and result."""
    today, evidence, binding = effective == 'working' and base != 'working', None, LOST
    try:
        active = (dispatch_state or {}).get('activeRun') or {}
        running = active.get('status') == 'running'
        binding, association, record, reason = _agent_record(agent_row) if running else (None, None, None, None)
        answer = ((NOT_KNOWN, reason) if reason == 'association'
                  else promotion_candidate(record, reason, running, _stopped(agent_row)))
        evidence = lambda: item(PROMOTION, NO_VARIANT, site, agent_row['id'], binding, association, today, answer[0],
                                dict(base=base, effective=effective,
                                     activeRun=dict(runId=active.get('runId'), status=active.get('status')),
                                     association=row_facts(agent_row)))
    except Exception:
        answer = NOT_KNOWN, 'shadow-error'
    cv.record_answer(PROMOTION, NO_VARIANT, site, today, answer, evidence, binding)


def liveness_receipt(session_mode, live_session, worker_row, runtime, channel_enabled, branch, sidecar, console, child):
    """PURE and contained: what today's worker-liveness probe read, each input as read or NOT_REACHED. None when it
    cannot be built, so the probe's own answer never depends on it."""
    try:
        if worker_row not in (None, NOT_REACHED):
            worker_row = dict(status=worker_row['status'], command=worker_row['command'])
        return dict(sessionMode=session_mode, liveSession=live_session, terminalRow=worker_row, runtime=runtime,
                    channelEnabled=channel_enabled, branch=branch, sidecar=sidecar, console=console, wrapperChild=child)
    except Exception:
        return None


async def observe_readiness(db, agent_row, liveness, site):
    """`liveness` is the site's own `_WorkerLiveness` for this agent, from this evaluation."""
    association = process = component = state = None
    binding, probe = LOST, 'not-probed'
    try:
        binding, association, record, reason = _agent_record(agent_row)  # frozen before the one await below
        process = (NOT_KNOWN, reason) if reason == 'association' else process_answer(record, reason)
        component, state = liveness.delivery
        if state == 'unread' and process[0] is not False:
            try:
                state = await PROBES[component](db, agent_row['id'])
            except Exception:
                state = PROBE_ERROR
            probe = 'error' if state == PROBE_ERROR else state
        answer = readiness_candidate(process, delivery_answer(component, state))
    except Exception:
        answer = NOT_KNOWN, 'shadow-error'
    today = bool(liveness.has_live_worker)
    cv.record_answer(READINESS, NO_VARIANT, site, today, answer, lambda: item(
        READINESS, NO_VARIANT, site, agent_row['id'], binding, association, today, answer[0],
        dict(hasLiveWorker=today, delivery=liveness.delivery, receipt=liveness.receipt),
        dict(process=process, component=component, state=state, shadowProbe=probe, reason=answer[1]),
        [] if liveness.receipt is not None else ['comms-receipt']), binding)
