"""The stale-turn ceiling as the status shadow's candidate, beside today's (status-switch B6c).

Diagnostic only: nothing here decides delivery. TODAY is the strict arm of today's delivery hold, the one C6's stale
disjunct replaces: `turn_is_still_live` on the comms turn row's start anchor, never renewed, recomputed from what the
busy decision read (its receipt). The CANDIDATE is the fold's `strictExpiry` for the one open turn the env records
hold: `now < strictExpiry`, the comparison busy's stale disjunct makes.

ONE CLOCK, NOT ONE SNAPSHOT. Both sides are judged at the receipt's `nowEpoch`, so no disagreement comes from a clock
read later; the frame's own clock is kept as evidence only. That is not a common state snapshot, nor proof that both
sides see the same turn, and a disagreement is not only an anchor difference: the two laws also cut time differently.
Today requires a non-negative age and is inclusive (`0 <= age <= 1800`); the candidate is exclusive (`now < expiry`).
Equal anchors one second ahead of the receipt are false today (a future anchor) and true for the candidate. These
law and time-cut classes stay explicit in the evidence; which law is right is the operator's call.

AN EVALUATION IS WHERE TODAY APPLIES A CEILING: the receipt read a row, and the row is busy. A row that is not busy,
absent or unreadable records nothing, because today made no ceiling decision there.

The candidate reads only the frame busy already captured: no await, no query. It is unknown, never false, when the
receipt is missing, the record cannot be read, more than one record holds the agent, no open record turn pairs with
today's busy row, or the fold has no authoritative expiry for that turn. It reads the fold, so the owner stays
fold-unverified until fold provenance lands; this slice measures and does not qualify.
"""
from service.api_core import comms_view as cv
from service.api_core.delivery_turns import NOT_KNOWN
from service.api_core.liveness import TURN_BUSY_BACKSTOP_SECONDS
from service.api_core.shadow_owners import NO_VARIANT
from service.api_core.turn_liveness_policy import turn_is_still_live
from service.clock import iso_to_epoch

CEILING = 'stale-turn ceiling'
UNREADABLE = object()  # the decision's receipt is missing or cannot be read


def today_ceiling(receipt):
    """PURE. (holds, anchor, column), or None when today applied no ceiling: the receipt read no busy row."""
    if not receipt or receipt.get('read') != 'row' or not int(receipt.get('turn_busy') or 0):
        return None
    started = iso_to_epoch(str(receipt.get('turn_started_at') or ''))
    touched = iso_to_epoch(str(receipt.get('turn_updated_at') or ''))
    holds = turn_is_still_live(started_epoch=started, touched_epoch=touched, renewable=False,
                               now_epoch=receipt['nowEpoch'], strict_seconds=TURN_BUSY_BACKSTOP_SECONDS)
    return holds, started or touched, 'turn_started_at' if started else 'turn_updated_at'


def ceiling_candidate(frame, now):
    """PURE. (True | False | NOT_KNOWN, reason, holder): the env records' strict expiry, judged at `now`."""
    if frame.at is None or frame.others_unknown or any(h.freshness == 'unknown' for h in frame.holders):
        return NOT_KNOWN, 'record-knowledge', None
    if len(frame.holders) > 1:
        return NOT_KNOWN, 'ambiguous-association', None
    holder = frame.holders[0] if frame.holders else None
    if holder is None or not (holder.clock and holder.clock[0] is True):
        return NOT_KNOWN, 'unpaired', holder
    if holder.expiry is NOT_KNOWN:
        return NOT_KNOWN, 'strict-expiry', holder
    return now < holder.expiry, None, holder


def observe_ceiling(decision, busy, site):
    """Record today's strict ceiling beside the candidate's. Never raises and never changes today's answer: a failure
    anywhere before the recording is recorded once as 'shadow-error' (the recording itself is contained)."""
    try:
        _observe(decision, busy, site)
    except Exception:
        try:
            cv.record_answer(CEILING, NO_VARIANT, site, getattr(decision, 'holds', None), (NOT_KNOWN, 'shadow-error'),
                             fold=True)
        except Exception:
            pass  # best effort: a broken recorder records nothing


def _observe(decision, busy, site):
    try:
        today = today_ceiling(decision.receipt) if decision.receipt is not None else UNREADABLE
    except Exception:
        today = UNREADABLE
    if today is None:
        return
    basis = busy.basis if busy else None
    holder = None
    try:
        if today is UNREADABLE:  # counted, never compared: whether today applied a ceiling cannot be read
            today, answer = (decision.holds, None, None), (NOT_KNOWN, 'comms-receipt')
        elif basis is None:
            answer = NOT_KNOWN, 'shadow-error'
        else:
            value, reason, holder = ceiling_candidate(basis.frame, decision.receipt['nowEpoch'])
            answer = value, reason
    except Exception:
        answer = NOT_KNOWN, 'shadow-error'
    holds, anchor, column = today
    cv.record_answer(CEILING, NO_VARIANT, site, holds, answer, lambda: cv.basis_item(
        CEILING, NO_VARIANT, site, basis, holds, answer[0],
        dict(todayAnchor=anchor, anchorColumn=column,
             todayExpiry=None if anchor is None else anchor + TURN_BUSY_BACKSTOP_SECONDS),
        dict(candidateExpiry=holder.expiry if holder else NOT_KNOWN, holderFreshness=holder.freshness if holder else None,
             frameAt=basis.frame.at if basis else None, reason=answer[1])),
        cv.binding_of(basis), fold=True)
