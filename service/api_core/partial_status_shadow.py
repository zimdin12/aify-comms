"""Process-only partial status comparison. Never supplies current status inputs."""
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime
from types import MappingProxyType
import copy
import json

from service.clock import now as clock
from service.status_engine import VALID_STATUSES
from service.api_core.definition_schema import MODES
from service.api_core.shadow_classes import classify, env_inputs
from service.api_core.shadow_evidence import EVIDENCE_OWNERS, EvidenceBuffer, word_item
from service.api_core.shadow_provenance import Provenance
from service.api_core.shadow_owners import (COUNT_NAMES, NO_VARIANT, OWNERS as PURPOSES, UNOBSERVED, WORD, OwnerTally,
                                            checked_key, variants_of)

SCOPE = 'partial-freshness-base-word'
OWNER_SCOPE = 'comms-view'
SOURCES = ('refresh', 'cache-broadcast', 'engine-status')
REASONS = ('unknown-knowledge', 'publisher-unavailable', 'missing-row', 'ambiguous-association',
           'stale', 'invalid-clock', 'future', 'unknown-state', 'unknown-loss')
CLASSES = ('partial-agree', 'partial-disagree', 'unavailable', 'non-evaluation', 'unknown-loss')


@dataclass(frozen=True)
class Record:
    agent_id: str
    lifetime: str | None
    state: str
    inputs: tuple = ()  # shadow_classes.env_inputs: the closed facts a disagreement class may read
    clock: tuple | None = None  # (open, startedAtUs, lastEventAtUs) of the record's turn, for the delivery-turn fold
    busy_if: tuple | None = None  # (strict, verifiedRenewal) of the record's turn, for comms_view's busy laws
    process: tuple | None = None  # (state, verified) of the record's process, for worker readiness (S2c slice 5)
    # S2c-E: the validated record as canonical JSON bytes, frozen at application (None over RAW_LIMIT), and the
    # (generation, incarnation, publication, applied_at, build) of the publication that set it (build: B6a).
    raw: bytes | None = None
    origin: tuple | None = None
    age_ms: int | None = None  # turn.ageMs as published (B6), for the fold's start estimate


@dataclass(frozen=True)
class Projection:
    key: tuple
    kind: str
    generation: int
    data_applied_at: str
    rows: tuple
    removed: tuple
    incarnation: str | None = None
    publication: int | None = None
    build: str | None = None  # the publishing aify-env's build, when it published one (B6a)


@dataclass(frozen=True)
class Publisher:
    generation: int | None
    data_applied_at: str | None
    complete: bool
    unavailable: bool
    rows: tuple
    loss: bool = False
    incarnation: str | None = None
    publication: int | None = None
    build: str | None = None  # of the last DATA publication: an unavailable one never changes it


RAW_LIMIT = 16384  # bytes of one record's canonical JSON the evidence keeps; a larger record keeps none (S2c-E)


def raw_bytes(record):
    """The validated record as canonical JSON bytes: lossless over its values, not the original HTTP bytes."""
    try:
        data = json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    except Exception:
        return None  # missing evidence, never a refused or changed record
    return data if len(data) <= RAW_LIMIT else None


def prepare(body, applied_at):
    """Detach only validated fields before opening the acceptance transaction."""
    build = body.get('build')
    origin = (body['generation'], body['incarnationId'], body['publication'], applied_at, build)
    return Projection((body['machineId'], body['instance']), body['kind'], body['generation'],
                      applied_at, tuple(Record(r['agentId'], r['lifetime'], r['state'], env_inputs(r), turn_clock(r),
                                               turn_busy_if(r), (r['process']['state'], r['process']['verified']),
                                               raw_bytes(r), origin, (r['turn'] or {}).get('ageMs'))
                                        for r in body.get('agents', ())),
                      tuple((r['agentId'], r['lifetime']) for r in body.get('removed', ())),
                      body['incarnationId'], body['publication'], build)


def turn_clock(record):
    turn = record['turn']
    return None if turn is None else (turn['open'], turn['startedAtUs'], turn['lastEventAtUs'])


def turn_busy_if(record):
    turn = record['turn']
    return None if turn is None else (turn['busyIf']['strict'], turn['busyIf']['verifiedRenewal'])


class Mirror:
    def __init__(self):
        self._view = MappingProxyType({})

    def view(self):
        return self._view

    def invalidate(self, key):
        view = dict(self._view)
        old = view.get(key)
        view[key] = Publisher(old.generation if old else None, old.data_applied_at if old else None,
                              False, True, old.rows if old else (), True,
                              old.incarnation if old else None, old.publication if old else None,
                              old.build if old else None)
        self._view = MappingProxyType(view)

    def retire(self, key):
        """Drop a retired publisher entirely (B5): it holds no record and no build from now on."""
        view = dict(self._view)
        view.pop(key, None)
        self._view = MappingProxyType(view)

    def feed(self, projection):
        p = projection
        view = dict(self._view)
        old = view.get(p.key)
        if p.kind == 'unavailable':  # carries no data, so it keeps the data's loss: only a data publication ends one
            view[p.key] = Publisher(old.generation if old else None, old.data_applied_at if old else None,
                                    old.complete if old else False, True, old.rows if old else (),
                                    old.loss if old else False,
                                    old.incarnation if old else None, old.publication if old else None,
                                    old.build if old else None)
        else:
            replace = p.kind == 'snapshot' or old is None or old.generation != p.generation
            rows = {} if replace else {r.agent_id: r for r in old.rows}
            if not replace:
                for aid, lifetime in p.removed:
                    if aid in rows and rows[aid].lifetime == lifetime:
                        del rows[aid]
            rows.update((r.agent_id, r) for r in p.rows)
            complete = p.kind == 'snapshot' or (not replace and old.complete)
            view[p.key] = Publisher(p.generation, p.data_applied_at, complete, False, tuple(rows.values()), False,
                                    p.incarnation, p.publication, p.build)
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
        'host_activity_fresh', 'background_work', 'wake_handle_absent')
    flags = tuple(getattr(inputs, name) for name in names)
    if any(type(value) is not bool for value in flags):
        raise ValueError('unknown boolean input')
    return (('mode', mode), ('host_activity', activity), *zip(names, flags),
            ('config_defect', bool(inputs.config_defect)))


def compare(binding, association):
    record, reason = record_for(binding, association)
    if reason:
        return None, reason
    word = candidate_word(record.state)
    return (word, None) if word else (None, 'unknown-state')


def record_for(binding, association):
    """PURE. The one fresh, complete, unambiguous record for the agent, or why there is none. The word comparison maps
    its state; other owners read its raw facts."""
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
    return record, None


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
        self._classes = Counter()  # disagreement class name -> count (S2b)
        self._owners = {}  # (owner, variant) -> OwnerTally, for the nine owners after the word (S2c)
        # What was recorded since the last `take()`, keyed (owner, variant, site, name), for the persisted windows.
        self._pending = Counter()
        self._lost = set()  # scopes whose loss was observed: publisher keys, ALL_PUBLISHERS, or None (unknown)
        self._healthy = set()  # publisher keys a comparison completed for
        # One item per counted disagreement of an evidence owner, taken beside `take()` with no await between (S2c-E).
        self.evidence = EvidenceBuffer()
        # The env builds and fold readers behind what was counted (B6a), taken beside `take()` the same way.
        self.provenance = Provenance()

    def record(self, source, actual=None, candidate=None, reason=None, non_evaluation=False, inputs=None, scope=None,
               env_inputs=None, evidence=None, binding=None):
        self.provenance.observe(WORD, binding, False)
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
                    # The cause, for admission (S2b): exactly one class, or unresolved. Unknown env facts match nothing.
                    cause = classify(dict(inputs or ()), observation.actual, dict(env_inputs or ()), observation.candidate)
                    self._classes[cause] += 1
                    names.append(f'class:{cause}')
                    self.evidence.capture((WORD, NO_VARIANT, source), evidence)
        counts = self._counts[source]
        for name in names:
            if name in counts:
                counts[name] += 1
            self._pending[(WORD, NO_VARIANT, source, name)] += 1

    def record_owner(self, owner, variant, site, today, candidate, reason=None, evidence=None, binding=None, fold=False,
                     explain=None):
        """One evaluation of a non-word owner: today's answer and the candidate's, or why the candidate has none.
        `evidence` builds the item a counted disagreement of an evidence owner keeps; `binding` is the env view this
        evaluation consulted and `fold` whether it read the delivery-turn fold, both counted with it (B6a). `explain`
        is (reason, name): `name` is counted only when this evaluation counted 'unavailable:<reason>', so an
        explanation never outnumbers what it explains (D-UNKNOWN)."""
        key = checked_key(owner, variant, site)
        self.provenance.observe(owner, binding, fold)
        tally = self._owners.setdefault(key[:2], OwnerTally())
        names = tally.record(site, today, candidate, reason)
        if explain and f'unavailable:{explain[0]}' in names:
            names = [*names, explain[1]]
        for name in names:
            self._pending[(*key, name)] += 1
        if 'partialDisagreed' in names and owner in EVIDENCE_OWNERS:
            self.evidence.capture(key, evidence)

    def lost(self, keys):
        """Loss scopes a failed retirement installed (B5): publisher keys, each recovering only on its own comparison,
        or ALL_PUBLISHERS when the key list itself was lost."""
        self._lost.update(keys)

    def take(self):
        """Since the last take: the counts keyed (owner, variant, site, name), the scopes a loss was observed for, and
        the publisher keys a comparison completed for. All three reset."""
        taken = dict(self._pending), frozenset(self._lost), frozenset(self._healthy)
        self._pending, self._lost, self._healthy = Counter(), set(), set()
        return taken

    def take_provenance(self):
        """(builds, fold owners, seed) since the last take, reseeded from the mirror as it stands now."""
        return self.provenance.take(bind())

    def report(self):
        counts = {name: sum(row[name] for row in self._counts.values()) for name in COUNT_NAMES}
        purposes = {}
        for owner in PURPOSES[1:]:
            variants = {v: self._owners[(owner, v)].report() if (owner, v) in self._owners else dict(UNOBSERVED)
                        for v in variants_of(owner)}
            instrumented = any(row['instrumented'] for row in variants.values())
            purposes[owner] = dict(instrumented=instrumented,
                                   comparisonScope=OWNER_SCOPE if instrumented else 'not-instrumented',
                                   variants=variants)
        purposes[WORD] = dict(instrumented=True, comparisonScope=SCOPE, counts=counts,
                                      bySource=copy.deepcopy(self._counts),
                                      neverObservedClasses=[c for c in CLASSES if c not in self._seen],
                                      lastBySource={s: {c: None if o is None else
                                          dict(asdict(o), inputs=None if o.inputs is None else dict(o.inputs))
                                          for c, o in row.items()} for s, row in self._last.items()},
                                      unavailableByReason=dict(self._reasons),
                                      disagreementsByClass=dict(sorted(self._classes.items())),
                                      disagreementsByKey=[
                                          dict(source=s, mode=m, comms=a, env=c, count=n,
                                               sample=None if i is None else dict(i))
                                          for (s, m, a, c), (n, i) in sorted(
                                              self._disagreements.items(),
                                              key=lambda kv: (-kv[1][0], *(str(part) for part in kv[0])))])
        return dict(schemaVersion=2, scope=SCOPE, window='process', coverageComplete=False,
                    qualification='UNAVAILABLE', fullQualifiedComparisons=0,
                    qualificationGaps=['turn.ageMs', 'runtime provenance', 'roster gates',
                                       'running-run promotion', 'other nine C8 owners'],
                    versions=dict(env=None, wrapper=None, comms=None),
                    byPurpose={owner: purposes[owner] for owner in PURPOSES})


mirror = Mirror()
recorder = Recorder()


class _UnreadableMirror(Mirror):
    """What a failed retirement leaves when the view it started from could not be read: every bind is a loss until
    comms restarts, because there is no list of keys whose snapshots would end it."""

    def view(self):
        raise RuntimeError('the mirror was lost with no view to rebuild it from')


#: Set for the life of the process once a failed retirement lost the key list (B5). Every runtime replacement of the
#: mirror goes through `_new_mirror`, so no later reset can hand back a readable view.
_quarantined = False


def _new_mirror():
    return _UnreadableMirror() if _quarantined else Mirror()


def invalidate_safe(key):
    """Keep committed acceptance even if diagnostic invalidation itself fails."""
    global mirror
    try:
        mirror.invalidate(key)
    except Exception:
        # Drop all live knowledge, but keep every loss already known: each ends only on its own publisher's next data
        # publication (B5 review 9fc557b3). Previously captured immutable views remain valid.
        try:
            carried = [k for k, p in mirror.view().items() if p.loss and k != key]
        except Exception:
            carried = []
        mirror = _new_mirror()
        for k in (*carried, key):
            Mirror.invalidate(mirror, k)


def retire_safe(key, before):
    """A retirement committed but dropping K failed (B5). K is absent, and every other publisher in `before` becomes the
    existing lost entry (no rows, no build, loss). Returns the loss scopes to record: those keys, or ALL_PUBLISHERS
    when there is no `before`. An empty Mirror would read as a healthy empty view, and `invalidate_safe(K)` would keep
    K's rows, so neither is used. With no `before` the process is quarantined: no key list exists whose snapshots could
    end the loss, so the loss is the whole view's."""
    global mirror, _quarantined
    if before is None:
        _quarantined = True
        mirror = _new_mirror()
        return (ALL_PUBLISHERS,)
    mirror = _new_mirror()
    lost = tuple(k for k in before if k != key)
    for k in lost:
        Mirror.invalidate(mirror, k)
    return lost


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
        projected, scope, env = None, None, None
        try:
            identity = agent_row if type(agent_row) is tuple else association(agent_row)
            projected = project_inputs(inputs)
            candidate, reason = compare(binding, identity)
            if actual not in VALID_STATUSES and reason is None:
                candidate, reason = None, 'unknown-state'
            if reason in (None, 'unknown-loss'):
                scope = scope_of(binding, identity, reason)
            if reason is None and scope is not None:
                env = next(r.inputs for r in binding.view[scope].rows if r.agent_id == identity[1])
        except Exception:
            candidate, reason = None, 'unknown-loss'  # scope stays None: what was lost cannot be told
        recorder.record(source, actual, candidate, reason, inputs=projected, scope=scope, env_inputs=env,
                        evidence=lambda: word_item(binding, identity, inputs, actual, candidate, source),
                        binding=binding)
    except Exception:
        # The recorder itself is observational. Never mutate or interrupt current status.
        pass
