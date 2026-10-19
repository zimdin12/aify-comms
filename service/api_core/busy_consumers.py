"""C6's four busy consumers as the status shadow's candidates, beside today's (status-switch S2c slice 4).

Diagnostic only: today's code computes and returns its own answer first, and each observer records C6's answer beside
it under P-1, the settled law, as the owner's one variant. Every observer is contained: it never raises (cancellation
excepted) and never changes today's answer.

Each candidate is true, false or unknown, and an unknown is never counted as false. An input is read only when it
can change the answer: an occupied or queued run decides the queue, a terminal block or an active run decides the
reminder skip, a known-false hold decides the claim, a delivery turn known to be on this run decides the reminder's
"other work". The first unknown input, in the order the expression reads it, names the reason; the reminder reads its
inputs in the approved order: association, record knowledge, the delivery turn.

A consumer that needs the fold associates the agent by its REGISTERED machine (`agents.machine_id`), the key the
heartbeat feeds the fold under: reused from a row the caller read for the same agent in the same evaluation, else one
contained lookup. A missing row, an empty machine or a failed read is unknown, never an inferred machine. Everything
the candidate reads after that is captured at once, synchronously, so no later await can change it.

A queue or claim disagreement keeps an item carrying busy's whole basis beside its own inputs (S2c-E E2); reminder and
stranded are counts-only by the operator's C8 ruling, so they keep none.
"""
from service.api_core import comms_view as cv, delivery_turns as dt
from service.api_core.delivery_turns import NOT_KNOWN
from service.api_core.shadow_owners import NO_VARIANT
from service.api_core.shadow_provenance import LOST
from service.api_core.turn_state import TURN_BUSY_STALE_SECONDS

QUEUE, CLAIM = 'send-time queue', 'claim'
REMINDER, STRANDED = 'reminder skip (B21)', 'stranded-reply guard (B26)'
LAW = 'P-1'
UNREAD = object()  # the caller read no agent row for this agent in this evaluation


def _any(*answers):
    """Three-valued OR of (value, reason) answers."""
    if any(value is True for value, _ in answers):
        return True, None
    return next(((NOT_KNOWN, reason) for value, reason in answers if value is NOT_KNOWN), (False, None))


def _all(*answers):
    """Three-valued AND of (value, reason) answers."""
    if any(value is False for value, _ in answers):
        return False, None
    return next(((NOT_KNOWN, reason) for value, reason in answers if value is NOT_KNOWN), (True, None))


def p1_hold(decision, busy):
    """The busy observer's P-1 answer, or why there is none: today's busy read failed, or C6's could not be computed."""
    if decision is None:
        return NOT_KNOWN, 'busy-unread'
    answers = busy.answers if busy else None
    return answers[LAW] if answers else (NOT_KNOWN, 'shadow-error')


def _basis(busy):
    return busy.basis if busy else None


def _steerability(value, unasked):
    """One steerability observation as kept: `unasked` when nobody asked, 'error' when the read raised, else its bool."""
    return unasked if value is None else 'error' if value is NOT_KNOWN else bool(value)


def queue_candidate(occupied, hold):
    """PURE. Send-time queue: queue when a run is occupied or queued, or delivery is held."""
    return (True, None) if occupied else hold


def claim_candidate(hold, steerable):
    """PURE. The BUSY EARLY-RETURN gate of /dispatch/claim: withheld while delivery is held and no queued run can be
    steered in. `steerable` is True, False or NOT_KNOWN."""
    return _all(hold, (NOT_KNOWN, 'steerable') if steerable is NOT_KNOWN else (not steerable, None))


def _fresh_since(at, then, reason):
    """Within 120 s of `at`; unknown when either side is: a lost binding has no clock."""
    if at is None or then is NOT_KNOWN or then is None:
        return NOT_KNOWN, reason
    return at - then <= TURN_BUSY_STALE_SECONDS, None


def _record_fresh(holder, at):
    if holder.freshness == 'unknown':
        return NOT_KNOWN, 'record-knowledge'
    if not (holder.clock and holder.clock[0]):
        return False, None
    return _fresh_since(at, holder.advanced_at, 'record-knowledge')


def other_work(frame, run_id):
    """PURE. C6's reminder "busy for other work": (an open delivery turn touched within 120 s, or a record's open turn
    last advanced within 120 s) and the open delivery turn's run, else '', is not this run."""
    turn = frame.delivery
    if turn.open is False:
        delivery_fresh, other_run = (False, None), (True, None)
    elif turn.open is True:
        delivery_fresh = _fresh_since(frame.at, turn.touched_at, 'delivery-turn')
        other_run = (NOT_KNOWN, 'delivery-turn') if turn.run_id is NOT_KNOWN else (turn.run_id != run_id, None)
    else:
        delivery_fresh = other_run = (NOT_KNOWN, 'delivery-turn')
    records = [_record_fresh(h, frame.at) for h in frame.holders]
    if frame.others_unknown:
        records.append((NOT_KNOWN, 'record-knowledge'))
    return _all(_any(*records, delivery_fresh), other_run)  # record knowledge before the delivery turn


def stranded_candidate(turn, run_id):
    """PURE. The guard skips while the delivery turn is open on this run."""
    if turn.open is False:
        return False, None
    if turn.open is NOT_KNOWN or turn.run_id is NOT_KNOWN:
        return NOT_KNOWN, 'delivery-turn'
    return turn.run_id == run_id, None


def _registered(agent_row):
    machine = str((agent_row['machine_id'] if agent_row is not None else '') or '').strip()
    return machine or NOT_KNOWN


async def _machine(db, agent_id, agent_row=UNREAD):
    if agent_row is not UNREAD:
        return _registered(agent_row)
    try:
        row = await (await db.execute('SELECT machine_id FROM agents WHERE id = ?', (agent_id,))).fetchone()
    except Exception:
        return NOT_KNOWN
    return _registered(row)


def observe_queue(occupied, today, decision, busy, occupancy=None):
    """`occupancy` is (hasActiveRun, queuedRuns) as the site read them for `occupied`."""
    try:
        hold = p1_hold(decision, busy)
        answer = queue_candidate(occupied, hold)
    except Exception:
        hold, answer = None, (NOT_KNOWN, 'shadow-error')
    active, queued = occupancy or (None, None)
    cv.record_answer(QUEUE, NO_VARIANT, 'send-time', bool(today), answer, lambda: cv.basis_item(
        QUEUE, NO_VARIANT, 'send-time', _basis(busy), bool(today), answer[0],
        dict(occupied=occupied, hasActiveRun=active, queuedRuns=queued),
        dict(occupied=occupied, hold=hold, reason=answer[1])), cv.binding_of(_basis(busy)), fold=True)


async def observe_claim(db, agent_row, withheld, steerable, decision, busy, read_steerable):
    """`steerable` is today's helper answer when today asked (NOT_KNOWN when it raised), else None; `read_steerable` is
    the same helper call, made at most once and only when its answer can change the candidate."""
    today_steerable, shadow_steerable, hold = steerable, None, None
    try:
        hold = p1_hold(decision, busy)
        if hold[0] is not False and steerable is None:
            try:
                shadow_steerable = bool(await read_steerable())
            except Exception:
                shadow_steerable = NOT_KNOWN
            steerable = shadow_steerable
        answer = claim_candidate(hold, False if steerable is None else steerable)
    except Exception:
        answer = NOT_KNOWN, 'shadow-error'
    cv.record_answer(CLAIM, NO_VARIANT, 'claim', bool(withheld), answer, lambda: cv.basis_item(
        CLAIM, NO_VARIANT, 'claim', _basis(busy), bool(withheld), answer[0],
        dict(withheld=bool(withheld), todaySteerable=_steerability(today_steerable, 'not-asked')),
        dict(hold=hold, shadowSteerable=_steerability(shadow_steerable, 'not-read'), reason=answer[1])),
        cv.binding_of(_basis(busy)), fold=True)


async def observe_reminder(db, agent_id, run_id, today, *, blocked, occupied, agent_row=UNREAD):
    binding = None  # blocked, occupied or unassociated: no env view consulted
    try:
        if blocked:
            answer = False, None
        elif occupied:
            answer = True, None
        else:
            machine = await _machine(db, agent_id, agent_row)
            if machine is NOT_KNOWN:
                answer = NOT_KNOWN, 'association'
            else:
                binding = LOST  # until the capture below holds one
                frame = cv.capture(machine, agent_id)
                binding = frame.binding if frame.binding is not None else LOST
                answer = other_work(frame, run_id)
    except Exception:
        answer = NOT_KNOWN, 'shadow-error'
    cv.record_answer(REMINDER, NO_VARIANT, 'reminder', bool(today), answer, binding=binding, fold=True)


async def observe_stranded(db, agent_id, run_id, today):
    try:
        machine = await _machine(db, agent_id)
        answer = ((NOT_KNOWN, 'association') if machine is NOT_KNOWN
                  else stranded_candidate(dt.FOLD.turn(machine, agent_id), run_id))
    except Exception:
        answer = NOT_KNOWN, 'shadow-error'
    cv.record_answer(STRANDED, NO_VARIANT, 'stranded', bool(today), answer, fold=True)  # the fold only: no env view
