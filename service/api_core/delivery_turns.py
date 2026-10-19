"""The C6 delivery turn as the status shadow's candidate knows it (status-switch S2c slice 2). Diagnostic only.

A FOLD, never a copy of today's row: it never reads or writes `agent_turn_state`. It folds C6's merge rules from
their own inputs: a delivery reporter's statement (busy or clear for run R), and aify-env's records as the receiver
applies them. What it has not seen it does not know, so every field is known or NOT_KNOWN, and an agent it has never
heard of is entirely unknown, exactly as a seed with every field unknown would be.

A DELIVERY TURN IS KEYED BY (machine, agent), the agent's registered machine for a reporter and the publishing machine
for a record, as the word comparison associates them. A record from another machine naming the same agent id is
another agent's evidence and never touches this one.

ENV EVIDENCE is read per lineage, (machineId, agentId, lifetime), and only while one publisher on that machine holds
the agent. A record another stream also holds is withheld, and the agent's delivery turn degrades to unknown, since an
end may have happened where it cannot be attributed. Between two observations of one lineage, an end is evidenced only
by an open turn closing (a tied lastEventAtUs included) or a new startedAtUs: aify-env keeps the start while a turn is
open. A closed turn whose clock moved is an unblock or a late turn-end, indistinguishable on the wire, so it makes an
open or unknown delivery turn unknown.

THE START ESTIMATE (`appliedAt - ageMs`, kept per turn at its minimum) is authoritative only for a turn this fold saw
begin after a closed observation, and only while every applied publication of it was consumed and carried an age. A
withheld or ageless one makes it non-authoritative for good: a later or smaller age cannot prove nothing earlier was
missed. The application stamp is whole seconds, so the sample is (stamp + its resolution) - ageMs: never before
the true start, at most one resolution later than the receipt-minus-age it stands for (B6).

WHERE AN UNKNOWN CAME FROM (D-UNKNOWN, a diagnostic deleted with the shadow): each agent's delivery turn keeps the op
on its last known-to-unknown edge, or 'never-known'. A write that leaves it unknown never rewrites that origin, so a
later quiet or first sample cannot hide a withhold or a loss. Nothing reads it to decide anything.

RECORD FRESHNESS (slice 4, for the reminder skip) is when a lineage's open turn last advanced, on the application
clock, bound to that turn's startedAtUs. It has its own baseline, apart from the delivery effects: a lineage with no
baseline (first seen, after a withhold, after a loss) knows nothing of when its turn last moved, so a withheld clock that
reappears unchanged is not an advance. A quiet republication never refreshes it, and no turn inherits another's time.
"""
from dataclasses import dataclass, replace

from service.clock import NOW_RESOLUTION_SECONDS, iso_to_epoch


class _Unknown:
    def __repr__(self):
        return 'NOT_KNOWN'


NOT_KNOWN = _Unknown()
STRICT_SECONDS = 1800  # C6 strictExpiry: the earliest start estimate plus the strict turn bound


@dataclass(frozen=True)
class DeliveryTurn:
    open: object = NOT_KNOWN
    run_id: object = NOT_KNOWN
    started_at: object = NOT_KNOWN  # epoch seconds
    touched_at: object = NOT_KNOWN
    owner: object = NOT_KNOWN  # the reporting bridge, which today's renewal check asks about


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


NEVER_KNOWN = 'never-known'
#: The known-to-unknown edges the fold has, by the op that makes them, as reported; any other edge is unclassified.
EDGES = {'withheld': 'withheld', 'env-ambiguous-closed': 'ambiguous-closed', 'lost': 'lost', NEVER_KNOWN: NEVER_KNOWN}
DIAGNOSTIC_FAULT = 'deliveryUnknownFault'


def delivery_known(turn):
    """PURE. Whether C6's delivery disjunct can answer from this turn: known closed, or open with its start, touch and
    owner known. Otherwise it answers 'delivery-turn'."""
    return turn.open is False or (turn.open is True and all(
        v is not NOT_KNOWN for v in (turn.started_at, turn.touched_at, turn.owner)))


def origin_after(before, after, origin, op):
    """PURE. An agent's origin after one write: none once its turn is known, `op` on a known-to-unknown edge, and
    otherwise the origin it had."""
    if delivery_known(after):
        return None
    return op if delivery_known(before) else origin


def lineage_stratum(entries):
    """PURE. `entries` holds one (present, clock) per distinct lifetime the frame holds a record of: 'no-record',
    'multiple', or that one lineage's 'absent', 'null', 'open' or 'closed'."""
    if not entries:
        return 'no-record'
    if len(entries) > 1:
        return 'multiple'
    present, clock = entries[0]
    return 'absent' if not present else 'null' if clock is None else 'open' if clock.open else 'closed'


def reporter_busy(turn, run_id, owner, at):
    """C6: open, runId R, touched now, started now only if it was (known to be) closed."""
    started = at if turn.open is False else turn.started_at if turn.open is True else NOT_KNOWN
    return DeliveryTurn(open=True, run_id=run_id, started_at=started, touched_at=at, owner=owner)


def reporter_clear(turn, run_id):
    """C6: closes only if the turn's runId is R. With the run unknown, whether it closes is unknown."""
    if turn.open is False:
        return turn
    if turn.run_id is NOT_KNOWN:
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


def freshness_after(entry, clock, at):
    """PURE. A lineage's (baseline clock, (startedAtUs, appliedAt) | NOT_KNOWN) after one consumed observation.
    An absent entry is no baseline; an entry whose baseline is a closed or null turn is a known closed one."""
    if entry is None or clock is None or not clock.open:
        return clock, NOT_KNOWN
    base, value = entry
    if base is None or not base.open or base.started_us != clock.started_us or base.last_event_us != clock.last_event_us:
        return clock, (clock.started_us, at)  # closed to open, a new start, or an advance: each applied now
    return clock, value


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
        self._fresh = {}  # (machine, agent, lifetime) -> freshness_after's entry
        self._origin = {}  # (machine, agent) -> origin_after's value; outlives lose_everything
        self._reported = set()  # (machine, agent) a reporter wrote since boot

    def turn(self, machine, agent_id):
        return self._turns.get((machine, agent_id), NOTHING_KNOWN)

    def _write(self, key, after, op):
        """The turn first; its diagnostic origin after, contained, so a diagnostic failure never changes the fold."""
        before = self._turns.get(key, NOTHING_KNOWN)
        self._turns[key] = after
        try:
            self._origin[key] = origin_after(before, after, self._origin.get(key, NEVER_KNOWN), op)
        except Exception:
            self._origin[key] = 'diagnostic-fault'

    def diagnostic(self, machine, agent_id, lifetimes, at):
        """The count name explaining an unknown delivery turn: 'deliveryUnknown:<origin>:<lineage>:<reporter>', for
        the lifetimes the frame holds records of. A frame with no clock answers 'delivery-turn' from a turn it may
        know, so its origin is 'binding-lost'."""
        key = (machine, agent_id)
        if at is None:
            origin = 'binding-lost'
        elif delivery_known(self.turn(machine, agent_id)):
            origin = 'not-eligible'
        else:
            origin = self._origin.get(key, NEVER_KNOWN)
            origin = EDGES.get(origin, f'unclassified:{origin}')
        lineages = [(machine, agent_id, lifetime) for lifetime in sorted(set(lifetimes), key=str)]
        lineage = lineage_stratum([(k in self._lineages, self._lineages.get(k)) for k in lineages])
        return f"deliveryUnknown:{origin}:{lineage}:{'yes' if key in self._reported else 'no'}"

    def strict_expiry(self, machine, agent_id, lifetime, started_us):
        estimate = self._estimates.get((machine, agent_id, lifetime, started_us))
        if estimate is None or not estimate.authoritative or estimate.minimum is None:
            return NOT_KNOWN
        return estimate.minimum + STRICT_SECONDS

    def advanced_at(self, machine, agent_id, lifetime, started_us):
        """When that lineage's open turn last advanced, if it is the turn named; otherwise NOT_KNOWN."""
        entry = self._fresh.get((machine, agent_id, lifetime))
        value = entry[1] if entry else NOT_KNOWN
        return value[1] if value is not NOT_KNOWN and value[0] == started_us else NOT_KNOWN

    def reporter(self, machine, agent_id, busy, run_id, owner, at):
        turn = self.turn(machine, agent_id)
        self._reported.add((machine, agent_id))
        self._write((machine, agent_id), reporter_busy(turn, run_id, owner, at) if busy else reporter_clear(turn, run_id),
                    'reporter-busy' if busy else 'reporter-clear')

    def env_applied(self, projection, view, age_ms=None):
        """One publication the receiver committed, with the view it produced. `age_ms` maps an agent to its turn age,
        as the publication carried it (B6)."""
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
        """An applied publication the fold could not read: no knowledge survives it and no estimate stays
        authoritative, while each minimum is kept for diagnostics (Rule A)."""
        for key, turn in self._turns.items():
            try:
                if delivery_known(turn):
                    self._origin[key] = 'lost'
            except Exception:
                self._origin[key] = 'diagnostic-fault'
        self._turns, self._lineages, self._fresh = {}, {}, {}
        self._estimates = {key: Estimate(e.minimum, False) for key, e in self._estimates.items()}

    def _withhold(self, machine, record, clock):
        self._write((machine, record.agent_id), NOTHING_KNOWN, 'withheld')
        self._fresh.pop((machine, record.agent_id, record.lifetime), None)  # no baseline survives a withhold
        if clock:  # open or closed: any applied observation of that turn the fold did not consume
            key = (machine, record.agent_id, record.lifetime, clock.started_us)
            old = self._estimates.get(key)
            self._estimates[key] = Estimate(old.minimum if old else None, False)

    def _consume(self, machine, record, clock, at, age):
        lineage = (machine, record.agent_id, record.lifetime)
        previous = self._lineages.get(lineage)
        self._lineages[lineage] = clock
        self._fresh[lineage] = freshness_after(self._fresh.get(lineage), clock, at)
        if clock is None:
            return
        # Every consumed publication of a turn counts toward its estimate, open or closed; only an open one may begin
        # an estimate, so a closed sample never mints authority.
        if clock.open or (*lineage, clock.started_us) in self._estimates:
            self._estimate(lineage, previous, clock, at, age)
        turn = self.turn(machine, record.agent_id)
        change = env_change(previous, clock)
        self._write((machine, record.agent_id), env_effect(turn, change, at), f'env-{change}')

    def _estimate(self, lineage, previous, clock, at, age):
        key = (*lineage, clock.started_us)
        old = self._estimates.get(key)
        if old is None:
            old = Estimate(None, previous is not None and previous.open is False)
        sample = None if age is None else at + NOW_RESOLUTION_SECONDS - age / 1000
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


def publish_reporter(machine, agent_id, statement):
    """After the beat's commit, on the agent's registered machine. Best effort: the fold is diagnostic and never
    interrupts a heartbeat."""
    try:
        if statement is not None and statement[3] is not None:
            FOLD.reporter(machine, agent_id, *statement)
    except Exception:
        FOLD.lose_everything()


def publish_applied(projection, view):
    """After the receiver's commit and mirror feed. A publication the fold cannot read loses everything."""
    try:
        if projection is None:
            FOLD.lose_everything()
        else:
            FOLD.env_applied(projection, view, {r.agent_id: r.age_ms for r in projection.rows if r.age_ms is not None})
    except Exception:
        FOLD.lose_everything()
