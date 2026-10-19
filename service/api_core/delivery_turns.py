"""The C6 delivery turn as the status shadow's candidate knows it (status-switch S2c slice 2). Diagnostic only.

A FOLD, never a copy of today's row: it never reads or writes `agent_turn_state`. It folds C6's merge rules from
their own inputs: a delivery reporter's statement (busy or clear for run R), and aify-env's records as the receiver
applies them. What it has not seen it does not know, so every field is known or UNKNOWN, and an agent it has never
heard of is entirely unknown, exactly as a seed with every field unknown would be.

ENV EVIDENCE is read per lineage, (machineId, agentId, lifetime), and only while one publisher on that machine holds
the agent. A record another stream also holds is withheld, and the agent's delivery turn degrades to unknown, since an
end may have happened where it cannot be attributed. Between two observations of one lineage, an end is evidenced only
by an open turn closing (a tied lastEventAtUs included) or a new startedAtUs: aify-env keeps the start while a turn is
open. A closed turn whose clock moved is an unblock or a late turn-end, indistinguishable on the wire, so it makes an
open or unknown delivery turn unknown.

THE START ESTIMATE (`appliedAt - ageMs`, kept per turn at its minimum) is authoritative only for a turn this fold saw
begin after a closed observation, and only while every applied publication of it was consumed and carried an age. A
withheld or ageless one makes it non-authoritative for good: a later or smaller age cannot prove nothing earlier was
missed. aify-env does not publish ageMs yet (B6), so live every estimate is unknown.
"""
from dataclasses import dataclass, replace

from service.clock import iso_to_epoch


class _Unknown:
    def __repr__(self):
        return 'UNKNOWN'


UNKNOWN = _Unknown()
STRICT_SECONDS = 1800  # C6 strictExpiry: the earliest start estimate plus the strict turn bound


@dataclass(frozen=True)
class DeliveryTurn:
    open: object = UNKNOWN
    run_id: object = UNKNOWN
    started_at: object = UNKNOWN  # epoch seconds
    touched_at: object = UNKNOWN
    owner: object = UNKNOWN  # the reporting bridge, which today's renewal check asks about


NOTHING_KNOWN = DeliveryTurn()
CLOSED = DeliveryTurn(open=False, run_id='', started_at=None, touched_at=None, owner='')


@dataclass(frozen=True)
class EnvClock:
    """One applied record's turn, as the wire carries it."""
    open: bool
    started_us: int
    last_event_us: int


@dataclass(frozen=True)
class Estimate:
    minimum: float | None
    authoritative: bool


def reporter_busy(turn, run_id, owner, at):
    """C6: open, runId R, touched now, started now only if it was (known to be) closed."""
    started = at if turn.open is False else turn.started_at if turn.open is True else UNKNOWN
    return DeliveryTurn(open=True, run_id=run_id, started_at=started, touched_at=at, owner=owner)


def reporter_clear(turn, run_id):
    """C6: closes only if the turn's runId is R. With the run unknown, whether it closes is unknown."""
    if turn.open is False:
        return turn
    if turn.run_id is UNKNOWN:
        return NOTHING_KNOWN
    return CLOSED if turn.run_id == run_id else turn


def env_change(previous, now):
    """PURE. What one lineage's consecutive observations prove: first, quiet, ended, advanced, started, or
    ambiguous-closed."""
    if previous is None:
        return 'first'
    if previous.open and not now.open:
        return 'ended'
    if previous.open and now.open:
        if now.started_us != previous.started_us:
            return 'ended'
        return 'advanced' if now.last_event_us != previous.last_event_us else 'quiet'
    if now.open:
        return 'started'
    return 'ambiguous-closed' if now.last_event_us != previous.last_event_us else 'quiet'


def env_effect(turn, change, at):
    """C6's record-driven merge rules on the delivery turn."""
    if change == 'ended':
        return CLOSED
    if change == 'advanced':
        return replace(turn, touched_at=at) if turn.open is True else turn
    if change == 'ambiguous-closed':
        return turn if turn.open is False else NOTHING_KNOWN
    return turn


class DeliveryTurns:
    """Per agent, the delivery turn; per env lineage, its last consumed clock; per turn, its start estimate."""

    def __init__(self):
        self._turns = {}
        self._lineages = {}  # (machine, agent, lifetime) -> EnvClock, or None after a null turn
        self._estimates = {}  # (machine, agent, lifetime, started_us) -> Estimate

    def turn(self, agent_id):
        return self._turns.get(agent_id, NOTHING_KNOWN)

    def strict_expiry(self, machine, agent_id, lifetime, started_us):
        estimate = self._estimates.get((machine, agent_id, lifetime, started_us))
        if estimate is None or not estimate.authoritative or estimate.minimum is None:
            return UNKNOWN
        return estimate.minimum + STRICT_SECONDS

    def reporter(self, agent_id, busy, run_id, owner, at):
        turn = self.turn(agent_id)
        self._turns[agent_id] = reporter_busy(turn, run_id, owner, at) if busy else reporter_clear(turn, run_id)

    def env_applied(self, projection, view, age_ms=None):
        """One publication the receiver committed, with the view it produced. `age_ms` maps an agent to its turn age;
        the wire carries none yet (B6)."""
        machine = projection.key[0]
        at = iso_to_epoch(projection.data_applied_at, None)
        for record in projection.rows:
            holders = [key for key, p in view.items()
                       if key[0] == machine and any(r.agent_id == record.agent_id for r in p.rows)]
            clock = record.clock and EnvClock(*record.clock)
            age = (age_ms or {}).get(record.agent_id)
            if holders != [projection.key] or at is None:
                self._withhold(machine, record, clock)
            else:
                self._consume(machine, record, clock, at, age)

    def lose_everything(self):
        """An applied publication the fold could not read: no knowledge survives it, and no estimate stays
        authoritative (a first sighting of an open turn never is)."""
        self.__init__()

    def _withhold(self, machine, record, clock):
        self._turns[record.agent_id] = NOTHING_KNOWN
        if clock and clock.open:
            key = (machine, record.agent_id, record.lifetime, clock.started_us)
            old = self._estimates.get(key)
            self._estimates[key] = Estimate(old.minimum if old else None, False)

    def _consume(self, machine, record, clock, at, age):
        lineage = (machine, record.agent_id, record.lifetime)
        previous = self._lineages.get(lineage)
        self._lineages[lineage] = clock
        if clock is None:
            return
        if clock.open:
            self._estimate(lineage, previous, clock, at, age)
        turn = self.turn(record.agent_id)
        self._turns[record.agent_id] = env_effect(turn, env_change(previous, clock), at)

    def _estimate(self, lineage, previous, clock, at, age):
        key = (*lineage, clock.started_us)
        old = self._estimates.get(key)
        if old is None:
            old = Estimate(None, previous is not None and previous.open is False)
        sample = None if age is None else at - age / 1000
        if sample is None:
            self._estimates[key] = Estimate(old.minimum, False)
        else:
            self._estimates[key] = Estimate(sample if old.minimum is None else min(old.minimum, sample),
                                            old.authoritative)


FOLD = DeliveryTurns()


def reporter_statement(body, bridge_id, at):
    """The bridge's turn report, detached before any helper may refuse it: (busy, run, owner, at) or None."""
    if 'turnBusy' not in body:
        return None
    return (bool(body.get('turnBusy')), str(body.get('turnRunId', '') or '').strip(), bridge_id, iso_to_epoch(at, None))


def publish_reporter(agent_id, statement):
    """After the beat's commit. Best effort: the fold is diagnostic and never interrupts a heartbeat."""
    try:
        if statement is not None and statement[3] is not None:
            FOLD.reporter(agent_id, *statement)
    except Exception:
        FOLD.lose_everything()


def publish_applied(projection, view):
    """After the receiver's commit and mirror feed. A publication the fold cannot read loses everything."""
    try:
        if projection is None:
            FOLD.lose_everything()
        else:
            FOLD.env_applied(projection, view)
    except Exception:
        FOLD.lose_everything()
