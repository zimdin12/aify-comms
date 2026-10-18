"""Process-only partial status comparison. Never supplies current status inputs."""
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime
from types import MappingProxyType
import copy

from service.clock import now as clock
from service.status_engine import VALID_STATUSES
from service.api_core.definition_schema import MODES

SCOPE = 'partial-freshness-base-word'
SOURCES = ('refresh', 'cache-broadcast', 'engine-status')
PURPOSES = ('status word', 'busy', 'send-time queue', 'claim', 'worker readiness', 'live counts',
            'reminder skip (B21)', 'stranded-reply guard (B26)', 'running-run promotion', 'stale-turn ceiling')
COUNT_NAMES = ('observed', 'nonEvaluation', 'evaluated', 'partialCompared', 'partialAgreed',
               'partialDisagreed', 'unavailable')
REASONS = ('unknown-knowledge', 'publisher-unavailable', 'missing-row', 'ambiguous-association',
           'stale', 'invalid-clock', 'future', 'unknown-state', 'unknown-loss')
CLASSES = ('partial-agree', 'partial-disagree', 'unavailable', 'non-evaluation', 'unknown-loss')


@dataclass(frozen=True)
class Record:
    agent_id: str
    lifetime: str | None
    state: str


@dataclass(frozen=True)
class Projection:
    key: tuple
    kind: str
    generation: int
    data_applied_at: str
    rows: tuple
    removed: tuple


@dataclass(frozen=True)
class Publisher:
    generation: int | None
    data_applied_at: str | None
    complete: bool
    unavailable: bool
    rows: tuple
    loss: bool = False


def prepare(body, applied_at):
    """Detach only validated fields before opening the acceptance transaction."""
    return Projection((body['machineId'], body['instance']), body['kind'], body['generation'],
                      applied_at, tuple(Record(r['agentId'], r['lifetime'], r['state'])
                                        for r in body.get('agents', ())),
                      tuple((r['agentId'], r['lifetime']) for r in body.get('removed', ())))


class Mirror:
    def __init__(self):
        self._view = MappingProxyType({})

    def view(self):
        return self._view

    def invalidate(self, key):
        view = dict(self._view)
        old = view.get(key)
        view[key] = Publisher(old.generation if old else None, old.data_applied_at if old else None,
                              False, True, old.rows if old else (), True)
        self._view = MappingProxyType(view)

    def feed(self, projection):
        p = projection
        view = dict(self._view)
        old = view.get(p.key)
        if p.kind == 'unavailable':
            view[p.key] = Publisher(old.generation if old else None, old.data_applied_at if old else None,
                                    old.complete if old else False, True, old.rows if old else ())
        else:
            replace = p.kind == 'snapshot' or old is None or old.generation != p.generation
            rows = {} if replace else {r.agent_id: r for r in old.rows}
            if not replace:
                for aid, lifetime in p.removed:
                    if aid in rows and rows[aid].lifetime == lifetime:
                        del rows[aid]
            rows.update((r.agent_id, r) for r in p.rows)
            complete = p.kind == 'snapshot' or (not replace and old.complete)
            view[p.key] = Publisher(p.generation, p.data_applied_at, complete, False, tuple(rows.values()))
        self._view = MappingProxyType(view)


@dataclass(frozen=True)
class Binding:
    view: object
    at: object
    loss: bool = False


def bind():
    """Bind once before caller acquisition. A failed read must not skip acquisition."""
    try:
        return Binding(mirror.view(), clock())
    except Exception:
        return Binding(None, None, True)


def age_class(data_applied_at, at):
    try:
        start = datetime.fromisoformat(data_applied_at.replace('Z', '+00:00'))
        end = datetime.fromisoformat(at.replace('Z', '+00:00'))
        if start.tzinfo is None or end.tzinfo is None:
            return 'invalid-clock'
        seconds = (end - start).total_seconds()
        if seconds < 0:
            return 'future'
        return 'fresh' if seconds < 180 else 'stale'
    except (AttributeError, TypeError, ValueError, OverflowError):
        return 'invalid-clock'


def candidate_word(state):
    return 'online' if state == 'idle' else state if state in VALID_STATUSES else None


def project_inputs(inputs):
    """Only closed enums and detached boolean facts, never retain StatusInputs or config text."""
    mode = inputs.mode
    activity = inputs.host_activity
    if mode not in MODES or activity not in ('', 'working', 'idle', 'blocked', 'shell'):
        raise ValueError('unknown input vocabulary')
    names = (
        'alive', 'in_turn', 'awaiting_input', 'worker_present', 'env_reachable', 'disabled',
        'bridge_stale', 'has_live_session', 'console_booting', 'spawn_starting',
        'host_activity_fresh', 'background_work')
    flags = tuple(getattr(inputs, name) for name in names)
    if any(type(value) is not bool for value in flags):
        raise ValueError('unknown boolean input')
    return (('mode', mode), ('host_activity', activity), *zip(names, flags),
            ('config_defect', bool(inputs.config_defect)))


def compare(binding, association):
    if binding.loss:
        return None, 'unknown-loss'
    machine, aid = association
    publishers = [p for (mid, _), p in binding.view.items() if mid == machine]
    if not publishers:
        return None, 'unknown-knowledge'
    matches = [(p, r) for p in publishers for r in p.rows if r.agent_id == aid]
    if len(matches) > 1:
        return None, 'ambiguous-association'
    if not matches:
        return None, 'unknown-loss' if any(p.loss for p in publishers) else 'missing-row'
    publisher, record = matches[0]
    if publisher.loss:
        return None, 'unknown-loss'
    if not publisher.complete:
        return None, 'unknown-knowledge'
    if publisher.unavailable:
        return None, 'publisher-unavailable'
    freshness = age_class(publisher.data_applied_at, binding.at)
    if freshness != 'fresh':
        return None, freshness
    word = candidate_word(record.state)
    return (word, None) if word else (None, 'unknown-state')


ALL_PUBLISHERS = '*'


def scope_of(binding, association, reason):
    """PURE. Which publication a loss or a completed comparison is about, so a window ends a loss only on evidence for
    THAT publication: a publisher key, ALL_PUBLISHERS when the whole view was lost, or None when it cannot be told."""
    try:
        if binding.loss:
            return ALL_PUBLISHERS
        machine, aid = association
        mine = {key: p for key, p in binding.view.items() if key[0] == machine}
        holding = [key for key, p in mine.items() if any(r.agent_id == aid for r in p.rows)]
        if reason is None:
            return holding[0] if len(holding) == 1 else None
        lost = [key for key in holding if mine[key].loss] or (
            [] if holding else [key for key, p in mine.items() if p.loss])
        return lost[0] if len(lost) == 1 else None
    except Exception:
        return None


@dataclass(frozen=True)
class Observation:
    actual: str | None
    candidate: str | None
    reason: str | None
    inputs: tuple | None


class Recorder:
    def __init__(self):
        self._counts = {s: dict.fromkeys(COUNT_NAMES, 0) for s in SOURCES}
        self._reasons = dict.fromkeys(REASONS, 0)
        self._seen = set()
        self._last = {s: dict.fromkeys(CLASSES) for s in SOURCES}
        # (source, mode, comms word, env word) -> [count, latest inputs]. Bounded: every part is a fixed vocabulary.
        # Triage only (status-switch plan S1); admission classifies by input predicates, never by these keys.
        self._disagreements = {}
        # What was recorded since the last `take()`, for the persisted windows (status-switch plan S2a).
        self._pending = Counter()
        self._lost = set()  # scopes whose loss was observed: publisher keys, ALL_PUBLISHERS, or None (unknown)
        self._healthy = set()  # publisher keys a comparison completed for

    def record(self, source, actual=None, candidate=None, reason=None, non_evaluation=False, inputs=None, scope=None):
        observation = Observation(actual if actual in VALID_STATUSES else None,
                                  candidate if candidate in VALID_STATUSES else None, reason, inputs)
        names = ['observed']
        if non_evaluation:
            names.append('nonEvaluation'); self._seen.add('non-evaluation')
            self._last[source]['non-evaluation'] = observation
        else:
            names.append('evaluated')
            if reason:
                names += ['unavailable', f'unavailable:{reason}']; self._reasons[reason] += 1
                self._seen.add('unavailable')
                self._last[source]['unavailable'] = observation
                if reason == 'unknown-loss':
                    self._lost.add(scope)
                    self._seen.add('unknown-loss')
                    self._last[source]['unknown-loss'] = observation
            else:
                if scope is not None:
                    self._healthy.add(scope)  # a completed comparison: that publisher's view was present and fresh
                agreed = actual == candidate
                names += ['partialCompared', 'partialAgreed' if agreed else 'partialDisagreed']
                self._seen.add('partial-agree' if agreed else 'partial-disagree')
                self._last[source]['partial-agree' if agreed else 'partial-disagree'] = observation
                if not agreed:
                    mode = dict(inputs).get('mode') if inputs else None
                    entry = self._disagreements.setdefault((source, mode, observation.actual, observation.candidate), [0, None])
                    entry[0] += 1
                    entry[1] = inputs
                    names.append(f'disagree:{mode}:{observation.actual}:{observation.candidate}')
        counts = self._counts[source]
        for name in names:
            if name in counts:
                counts[name] += 1
            self._pending[(source, name)] += 1

    def take(self):
        """Since the last take: the counts keyed (source, name), the scopes a loss was observed for, and the publisher
        keys a comparison completed for. All three reset."""
        taken = dict(self._pending), frozenset(self._lost), frozenset(self._healthy)
        self._pending, self._lost, self._healthy = Counter(), set(), set()
        return taken

    def report(self):
        counts = {name: sum(row[name] for row in self._counts.values()) for name in COUNT_NAMES}
        purposes = {p: dict(instrumented=False, comparisonScope='not-instrumented', counts=None,
                            bySource=None, neverObservedClasses=None) for p in PURPOSES}
        purposes['status word'] = dict(instrumented=True, comparisonScope=SCOPE, counts=counts,
                                      bySource=copy.deepcopy(self._counts),
                                      neverObservedClasses=[c for c in CLASSES if c not in self._seen],
                                      lastBySource={s: {c: None if o is None else
                                          dict(asdict(o), inputs=None if o.inputs is None else dict(o.inputs))
                                          for c, o in row.items()} for s, row in self._last.items()},
                                      unavailableByReason=dict(self._reasons),
                                      disagreementsByKey=[
                                          dict(source=s, mode=m, comms=a, env=c, count=n,
                                               sample=None if i is None else dict(i))
                                          for (s, m, a, c), (n, i) in sorted(
                                              self._disagreements.items(),
                                              key=lambda kv: (-kv[1][0], *(str(part) for part in kv[0])))])
        return dict(schemaVersion=1, scope=SCOPE, window='process', coverageComplete=False,
                    qualification='UNAVAILABLE', fullQualifiedComparisons=0,
                    qualificationGaps=['turn.ageMs', 'runtime provenance', 'roster gates',
                                       'running-run promotion', 'other nine C8 owners'],
                    versions=dict(env=None, wrapper=None, comms=None), byPurpose=purposes)


mirror = Mirror()
recorder = Recorder()


def invalidate_safe(key):
    """Keep committed acceptance even if diagnostic invalidation itself fails."""
    global mirror
    try:
        mirror.invalidate(key)
    except Exception:
        # Drop all live knowledge. Previously captured immutable views remain valid.
        mirror = Mirror()
        Mirror.invalidate(mirror, key)


def association(agent_row):
    try:
        return (agent_row['machine_id'], agent_row['id'])
    except Exception:
        return None


def observe(binding, agent_row, inputs, actual, source):
    """Trusted best-effort boundary after the actual selection, before current publication."""
    try:
        if inputs is None:
            recorder.record(source, actual=actual, non_evaluation=True)
            return
        projected, scope = None, None
        try:
            identity = agent_row if type(agent_row) is tuple else association(agent_row)
            projected = project_inputs(inputs)
            candidate, reason = compare(binding, identity)
            if actual not in VALID_STATUSES and reason is None:
                candidate, reason = None, 'unknown-state'
            if reason in (None, 'unknown-loss'):
                scope = scope_of(binding, identity, reason)
        except Exception:
            candidate, reason = None, 'unknown-loss'  # scope stays None: what was lost cannot be told
        recorder.record(source, actual, candidate, reason, inputs=projected, scope=scope)
    except Exception:
        # The recorder itself is observational. Never mutate or interrupt current status.
        pass
