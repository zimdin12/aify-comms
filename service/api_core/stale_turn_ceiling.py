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
import math

from service.api_core import comms_view as cv
from service.api_core.delivery_turns import NOT_KNOWN
from service.api_core.liveness import TURN_BUSY_BACKSTOP_SECONDS
from service.api_core.shadow_owners import NO_VARIANT
from service.api_core.turn_liveness_policy import turn_is_still_live
from service.clock import iso_to_epoch

CEILING = 'stale-turn ceiling'
EXCLUDED, UNKNOWN_ELIGIBILITY = 'excluded', 'eligibility-unknown'
KNOWN_RELEASES = ('no-row', 'read-error')  # receipts of a decision that released delivery without a ceiling


def _finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def classify(receipt):
    """PURE. The population a receipt belongs to (the B6c population contract, design rev2):

    - EXCLUDED: a readable receipt of a decision that applied no ceiling: a row that is not busy, no row, or a read
      error. Nothing is recorded. It needs no clock, since it never had one.
    - UNKNOWN_ELIGIBILITY: no receipt, an unreadable or malformed discriminator or busy field, or a busy row without a
      finite numeric clock. Counted unavailable, never compared, with no today answer.
    - an eligible busy row: (holds, anchor, column), today's strict arm on the legacy anchor selection, `started or
      touched` as parsed; two unusable anchors keep anchor and column None (today's real answer is then False).
    """
    if not isinstance(receipt, dict):
        return UNKNOWN_ELIGIBILITY
    read = receipt.get('read')
    if read in KNOWN_RELEASES:
        return EXCLUDED
    if read != 'row':
        return UNKNOWN_ELIGIBILITY
    try:
        busy = int(receipt.get('turn_busy') or 0)  # the decision's own reading of the column
    except (TypeError, ValueError):
        return UNKNOWN_ELIGIBILITY
    if not busy:
        return EXCLUDED
    now = receipt.get('nowEpoch')
    if not _finite_number(now):
        return UNKNOWN_ELIGIBILITY
    started = iso_to_epoch(str(receipt.get('turn_started_at') or ''))
    touched = iso_to_epoch(str(receipt.get('turn_updated_at') or ''))
    holds = turn_is_still_live(started_epoch=started, touched_epoch=touched, renewable=False, now_epoch=now,
                               strict_seconds=TURN_BUSY_BACKSTOP_SECONDS)
    anchor = started or touched
    if not anchor:
        return holds, None, None
    return holds, anchor, 'turn_started_at' if started else 'turn_updated_at'


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
    anywhere before the recording is recorded once as 'shadow-error', with no today answer (the recording itself is
    contained)."""
    try:
        _observe(decision, busy, site)
    except Exception:
        try:
            cv.record_answer(CEILING, NO_VARIANT, site, None, (NOT_KNOWN, 'shadow-error'), fold=True)
        except Exception:
            pass  # best effort: a broken recorder records nothing


def _observe(decision, busy, site):
    today = classify(decision.receipt)
    if today == EXCLUDED:
        return
    basis = busy.basis if busy else None
    if today == UNKNOWN_ELIGIBILITY:  # counted, never compared, and no answer stands in for today's
        cv.record_answer(CEILING, NO_VARIANT, site, None, (NOT_KNOWN, UNKNOWN_ELIGIBILITY), binding=cv.binding_of(basis),
                         fold=True)
        return
    holder = None
    if basis is None:
        answer = NOT_KNOWN, 'shadow-error'
    else:
        value, reason, holder = ceiling_candidate(basis.frame, decision.receipt['nowEpoch'])
        answer = value, reason
    holds, anchor, column = today
    cv.record_answer(CEILING, NO_VARIANT, site, holds, answer, lambda: cv.basis_item(
        CEILING, NO_VARIANT, site, basis, holds, answer[0],
        dict(todayAnchor=anchor, anchorColumn=column,
             todayExpiry=None if anchor is None else anchor + TURN_BUSY_BACKSTOP_SECONDS),
        dict(candidateExpiry=holder.expiry if holder else NOT_KNOWN, holderFreshness=holder.freshness if holder else None,
             frameAt=basis.frame.at if basis else None, reason=answer[1])),
        cv.binding_of(basis), fold=True)
