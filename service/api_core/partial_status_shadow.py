"""The mirror of what each aify-env published: the operational view's only source since the 0.9.8 switch.

The comparison recorder that shared this module while the switch was qualified was deleted in 0.9.9 (S1, plan
2026-10-25-0.9.9-deletions.md). The module keeps its name until S3 renames the tables and it together.
"""
from dataclasses import dataclass, replace
from types import MappingProxyType

from service.clock import now as clock
from service.status_engine import VALID_STATUSES


@dataclass(frozen=True)
class Record:
    agent_id: str
    lifetime: str | None
    state: str
    busy: bool | None = None  # the record's validated top-level busy: the operational view's only busy (SW2)


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
    restored: bool = False  # loaded from the saved tables at start (SW1): never fresh until a complete snapshot
    # (received_at, report_every_ms) of the last report the ingest APPLIED, whatever its kind (decision 2). Contact
    # only: it never makes data fresh. Saved for silence after restart; `restored` still prevents fresh data.
    report: tuple | None = None


def prepare(body, applied_at):
    """Detach only validated fields before opening the acceptance transaction."""
    return Projection((body['machineId'], body['instance']), body['kind'], body['generation'],
                      applied_at, tuple(record_of(r) for r in body.get('agents', ())),
                      tuple((r['agentId'], r['lifetime']) for r in body.get('removed', ())),
                      body['incarnationId'], body['publication'], body.get('build'))


def record_of(r):
    """One validated record's detached fields, from a publication or from its saved `record_json` (SW1)."""
    return Record(r['agentId'], r['lifetime'], r['state'], r['busy'])


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

    def restore(self, key, publisher):
        """Install a publisher loaded at start (SW1), never over one an ingest already applied."""
        if key in self._view:
            return False
        self._view = MappingProxyType({**self._view, key: publisher})
        return True

    def renew_report(self, key, received_at, report_every_ms):
        """The report pair of a publication the ingest just applied (decision 2): its receipt time and the interval it
        announced, None when it announced none, which withdraws an earlier one."""
        old = self._view.get(key)
        if old is not None:
            self._view = MappingProxyType({**self._view, key: replace(old, report=(received_at, report_every_ms))})

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
                                    old.build if old else None, bool(old and old.restored))
        else:
            replace = p.kind == 'snapshot' or old is None or old.generation != p.generation
            rows = {} if replace else {r.agent_id: r for r in old.rows}
            if not replace:
                for aid, lifetime in p.removed:
                    if aid in rows and rows[aid].lifetime == lifetime:
                        del rows[aid]
            rows.update((r.agent_id, r) for r in p.rows)
            complete = p.kind == 'snapshot' or (not replace and old.complete)
            # Restored rows stay restored until a complete snapshot: a changes publication completes nothing (SW1).
            view[p.key] = Publisher(p.generation, p.data_applied_at, complete, False, tuple(rows.values()), False,
                                    p.incarnation, p.publication, p.build, bool(old and old.restored) and not complete)
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


def candidate_word(state):
    """aify-env's state as the service's word: its `idle` is `online` here, and a state outside the vocabulary is none."""
    return 'online' if state == 'idle' else state if state in VALID_STATUSES else None


mirror = Mirror()


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


def quarantine():
    """No key list can be read: every bind is a loss for the life of the process (B5; SW1 when the saved tables cannot
    be read at start)."""
    global mirror, _quarantined
    _quarantined = True
    mirror = _new_mirror()
    return mirror


def invalidate_safe(key):
    """Keep committed acceptance even if invalidating the mirror itself fails."""
    global mirror, _quarantined
    try:
        mirror.invalidate(key)
    except Exception:
        # Drop all live knowledge, but keep every loss already known: each ends only on its own publisher's next data
        # publication (B5 review 9fc557b3). Previously captured immutable views remain valid.
        try:
            carried = [k for k, p in mirror.view().items() if p.loss and k != key]
        except Exception:
            # The known losses cannot be listed, so the population is unknown: fail closed as a failed retirement does,
            # never as an empty list (B5 review 07997643).
            _quarantined, carried = True, []
        mirror = _new_mirror()
        for k in (*carried, key):
            Mirror.invalidate(mirror, k)


def retire_safe(key, before):
    """A retirement committed but dropping K failed (B5). K is absent, and every other publisher in `before` becomes the
    existing lost entry (no rows, no build, loss). An empty Mirror would read as a healthy empty view, and
    `invalidate_safe(K)` would keep K's rows, so neither is used. With no `before` the process is quarantined: no key
    list exists whose snapshots could end the loss, so the loss is the whole view's."""
    global mirror
    if before is None:
        quarantine()
        return
    mirror = _new_mirror()
    for k in before:
        if k != key:
            Mirror.invalidate(mirror, k)
