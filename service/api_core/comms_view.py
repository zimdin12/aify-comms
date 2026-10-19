"""C6's `holdsDelivery` as the status shadow's candidate, beside today's busy gate (status-switch S2c slice 3).

Diagnostic only: nothing here decides delivery. Each observation compares today's answer with C6's, under the strict
law and under P-1, and records both through the shadow recorder.

`holdsDelivery` is the OR of three disjuncts, each true, false or unknown:
  - a fresh record's busy: `turn.busyIf.strict` under strict, `turn.busyIf.verifiedRenewal` under P-1 (C3:135-143);
  - a stale record's open turn, until the fold's `strictExpiry` for that turn;
  - the delivery turn, open and still live by today's timer law (`turn_is_still_live`) on its own anchors.
A known true decides it whatever else is unknown; false needs every disjunct known false; anything else is unknown,
counted as unavailable with the first unknown input in REASON_ORDER, never as false.

EVERYTHING IS READ BEFORE THE ONE AWAIT. The frame (association, view, delivery turn, expiries, clock) is frozen first;
the only await is the ownership read for the frozen turn's own owner, and nothing is read again after it.

A disagreement keeps its BASIS (S2c-E E2): today's decision with its receipt, the frame, the renewal and where it came
from, and the env census from the frame's own binding. Queue and claim read the same basis, so each of their items
carries the whole of it, not a reference to a busy item that may not have been kept.
"""
import asyncio
from dataclasses import dataclass

from service.api_core import delivery_turns as dt, partial_status_shadow as shadow
from service.api_core.claim_gating import turn_lease_renewal
from service.api_core.delivery_turns import NOT_KNOWN
from service.api_core.shadow_owners import variants_of
from service.api_core.liveness import TURN_BUSY_BACKSTOP_SECONDS
from service.api_core.shadow_evidence import item
from service.api_core.turn_liveness_policy import turn_is_still_live
from service.clock import iso_to_epoch

OWNER = 'busy'
LAWS = variants_of(OWNER)  # strict and P-1, as the recorder's roster names them
REASON_ORDER = ('record-knowledge', 'strict-expiry', 'delivery-turn', 'renewal')


@dataclass(frozen=True)
class Holder:
    """One publisher's record of the agent, as the frame saw it: 'fresh', 'stale' or 'unknown'."""
    freshness: str
    busy_if: tuple | None = None  # (strict, verifiedRenewal)
    clock: tuple | None = None  # (open, startedAtUs, lastEventAtUs)
    expiry: object = NOT_KNOWN  # the fold's strictExpiry for that turn, epoch seconds
    advanced_at: object = NOT_KNOWN  # when that open turn last advanced, on the application clock (slice 4)
    publisher: tuple | None = None  # (machine, instance) that published the record: what the holder is joined on
    lifetime: str | None = None


@dataclass(frozen=True)
class Frame:
    at: float
    holders: tuple
    others_unknown: bool  # a publisher on the machine whose rows cannot be read might hold the agent
    delivery: dt.DeliveryTurn
    binding: object = None  # the one bind this frame was read from: custody for the env census, never serialized


def capture(machine, agent_id):
    """The frame, read once: the mirror's view and clock, the agent's records, its delivery turn and expiries."""
    binding = shadow.bind()
    at = iso_to_epoch(binding.at, None)
    turn = dt.FOLD.turn(machine, agent_id)
    if binding.loss or at is None:
        return Frame(at, (), True, turn, binding)
    holders, others_unknown, publishers = [], False, 0
    for (mid, instance), publisher in binding.view.items():
        if mid != machine:
            continue
        publishers += 1
        age = shadow.age_class(publisher.data_applied_at, binding.at)
        readable = (publisher.complete and not publisher.unavailable and not publisher.loss
                    and age in ('fresh', 'stale'))
        rows = [r for r in publisher.rows if r.agent_id == agent_id]
        if not readable:
            holders += [Holder('unknown', publisher=(mid, instance), lifetime=r.lifetime) for r in rows]
            others_unknown = others_unknown or not rows
            continue
        for r in rows:
            is_open = bool(r.clock and r.clock[0])
            expiry = dt.FOLD.strict_expiry(machine, agent_id, r.lifetime, r.clock[1]) if is_open else NOT_KNOWN
            advanced = dt.FOLD.advanced_at(machine, agent_id, r.lifetime, r.clock[1]) if is_open else NOT_KNOWN
            holders.append(Holder(age, r.busy_if, r.clock, expiry, advanced, (mid, instance), r.lifetime))
    return Frame(at, tuple(holders), others_unknown or not publishers, turn, binding)


def needs_renewal(frame):
    """Only a known-open delivery turn its strict bound no longer holds asks who still claims it."""
    turn = frame.delivery
    known = turn.open is True and all(v is not NOT_KNOWN for v in (turn.started_at, turn.touched_at, turn.owner))
    return known and frame.at is not None and not _live(turn, False, frame.at)


def _live(turn, renewable, at):
    return turn_is_still_live(started_epoch=turn.started_at, touched_epoch=turn.touched_at, renewable=renewable,
                              now_epoch=at, strict_seconds=TURN_BUSY_BACKSTOP_SECONDS)


def _record_disjunct(holder, variant, at):
    if holder.freshness == 'unknown':
        return NOT_KNOWN, 'record-knowledge'
    if holder.freshness == 'fresh':
        return bool(holder.busy_if and holder.busy_if[0 if variant == 'strict' else 1]), None
    if not (holder.clock and holder.clock[0]):
        return False, None
    if holder.expiry is NOT_KNOWN:
        return NOT_KNOWN, 'strict-expiry'
    return at < holder.expiry, None


def _delivery_disjunct(frame, renewal):
    turn = frame.delivery
    if turn.open is False:
        return False, None
    if turn.open is NOT_KNOWN or any(v is NOT_KNOWN for v in (turn.started_at, turn.touched_at, turn.owner)):
        return NOT_KNOWN, 'delivery-turn'
    if frame.at is None:  # a lost binding has no clock to age the turn against
        return NOT_KNOWN, 'delivery-turn'
    if _live(turn, False, frame.at):
        return True, None
    if renewal not in ('verified', 'negative'):
        return NOT_KNOWN, 'renewal'
    return _live(turn, renewal == 'verified', frame.at), None


def holds_delivery(frame, renewal, variant):
    """PURE. (True | False | NOT_KNOWN, reason): the three-valued OR of C6's disjuncts under one law."""
    answers = [_record_disjunct(h, variant, frame.at) for h in frame.holders]
    if frame.others_unknown:
        answers.append((NOT_KNOWN, 'record-knowledge'))
    answers.append(_delivery_disjunct(frame, renewal))
    if any(value is True for value, _ in answers):
        return True, None
    unknown = {reason for value, reason in answers if value is NOT_KNOWN}
    if not unknown:
        return False, None
    return NOT_KNOWN, next(r for r in REASON_ORDER if r in unknown)


async def _renewal(db, agent_id, frame, decision):
    """(renewal, source): the frozen turn's owner, asked once and only when needed, and where the answer came from:
    'not-needed', 'today' (today's answer for the same owner), 'read', or 'error' (a read that failed is unknown,
    never a known negative)."""
    if not needs_renewal(frame):
        return None, 'not-needed'
    owner = str(frame.delivery.owner).strip()
    if decision.owner == owner and decision.renewal in ('verified', 'negative'):
        return decision.renewal, 'today'
    try:
        return await turn_lease_renewal(db, agent_id, owner), 'read'
    except asyncio.CancelledError:
        raise
    except Exception:
        return NOT_KNOWN, 'error'


@dataclass(frozen=True)
class Basis:
    """What one busy evaluation consumed, on both sides, for the evidence of every owner that reads it."""
    decision: object  # today's BusyDecision, with its receipt
    frame: Frame
    renewal: object
    renewal_source: str
    association: tuple  # (registered machine, agent id): the key the frame was captured under


@dataclass(frozen=True)
class BusyObservation:
    """observe_busy's result: C6's answer under each law ({law: (value, reason)}, or None when it could not be
    computed), and the basis it was computed from (None with it)."""
    answers: dict | None
    basis: Basis | None = None


def basis_body(basis):
    """(today, candidate inputs, missing): the explicit projection of a basis. The frame's binding is custody for the
    census and is never serialized itself."""
    if basis is None:
        return None, None, ['candidate-basis']
    decision, frame = basis.decision, basis.frame
    today = dict(holds=decision.holds, owner=decision.owner, renewal=decision.renewal, receipt=decision.receipt)
    holders = [dict(publisher=h.publisher, lifetime=h.lifetime, freshness=h.freshness, busyIf=h.busy_if, clock=h.clock,
                    expiry=h.expiry, advancedAt=h.advanced_at) for h in frame.holders]
    candidate = dict(at=frame.at, holders=holders, othersUnknown=frame.others_unknown, delivery=frame.delivery,
                     renewal=basis.renewal, renewalSource=basis.renewal_source)
    return today, candidate, [] if decision.receipt is not None else ['comms-receipt']


def basis_item(owner, variant, site, basis, today, candidate, comms_extra=None, candidate_extra=None):
    """One item of an owner that reads a busy basis: the whole basis, plus that owner's own inputs."""
    busy_today, busy_candidate, missing = basis_body(basis)
    binding, association = (basis.frame.binding, basis.association) if basis else (None, None)
    return item(owner, variant, site, association[1] if association else None, binding, association, today,
                candidate, dict(comms_extra or {}, busy=busy_today), dict(candidate_extra or {}, busy=busy_candidate),
                missing)


async def _compute(db, agent_row, decision):
    agent_id = agent_row['id']
    frame = capture(agent_row['machine_id'], agent_id)
    renewal, source = await _renewal(db, agent_id, frame, decision)
    answers = {variant: holds_delivery(frame, renewal, variant) for variant in LAWS}
    return answers, Basis(decision, frame, renewal, source, (agent_row['machine_id'], agent_id))


def record_answer(owner, variant, site, today, answer, evidence=None):
    """One observation, contained: a recording that raises is recorded once more as 'shadow-error', then dropped.
    `evidence` builds a counted disagreement's item (S2c-E)."""
    value, reason = answer
    try:
        shadow.recorder.record_owner(owner, variant, site, today, None if value is NOT_KNOWN else value, reason,
                                     evidence=evidence)
    except Exception:
        try:
            shadow.recorder.record_owner(owner, variant, site, today, None, 'shadow-error')
        except Exception:
            pass  # best effort: a broken recorder records nothing


async def observe_busy(db, agent_row, decision, site):
    """Record today's busy answer beside C6's under both laws, and return a BusyObservation for the consumers that
    read the same frame. Never raises (cancellation excepted) and never changes today's answer; a variant already
    recorded is not recorded again, and a recording that fails never takes the computed answers with it."""
    try:
        results, basis = await _compute(db, agent_row, decision)
    except Exception:
        results, basis = None, None
    for variant in LAWS:
        answer = results[variant] if results else (NOT_KNOWN, 'shadow-error')
        record_answer(OWNER, variant, site, decision.holds, answer,
                      lambda variant=variant, answer=answer: basis_item(
                          OWNER, variant, site, basis, decision.holds, answer[0], candidate_extra=dict(reason=answer[1])))
    return BusyObservation(results, basis)
