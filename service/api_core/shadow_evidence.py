"""The status shadow's disagreement evidence (S2c-E E1): one immutable item per disagreeing evaluation.

A count says how often comms and C6 disagreed; an ITEM says what each side consumed that one time, so a disagreement
can be explained after the fact. The recorder captures one in the same call that counts the disagreement, from what
the observer already holds: no SQL, no clock read, no retry and no second bind. Every disagreement of an instrumented
owner becomes an item or a GAP (a counted reason it could not be kept), so per window, owner, variant and site,
    partialDisagreed = complete items + incomplete items + gaps.
An item is incomplete when a required input was not kept (a record over its raw limit, a lost view); it is retained
and labelled, never counted as complete. Owners outside EVIDENCE_OWNERS are 'not-instrumented' (E2), never complete.

Bounded in memory (PENDING_ITEMS, PENDING_BYTES, ITEM_BYTES); on disk by shadow_evidence_store's rotation.
Diagnostic only: building an item never changes today's answer or the comparison's counts.
"""
from collections import Counter
from dataclasses import asdict, dataclass
import json
import pathlib
import uuid

from service.api_core.shadow_owners import OWNERS, WORD
from service.config import get_config

#: E1's owners. Readiness and the turn-row owners need acquisition receipts first (E2).
EVIDENCE_OWNERS = frozenset((WORD, 'live counts', 'running-run promotion'))
if not EVIDENCE_OWNERS <= set(OWNERS):
    raise ValueError('an evidence owner is not a C8 owner')
GAP_REASONS = ('pending-overflow', 'item-too-large', 'builder-error', 'encode-error')
PENDING_ITEMS = 2000
PENDING_BYTES = 4 * 1024 * 1024
ITEM_BYTES = 64 * 1024
ENV_NOT_PUBLISHED = 'not-published'  # aify-env publishes no build until B6
BOOT_ID = uuid.uuid4().hex  # one per process: (BOOT_ID, seq) names an item, and a window never spans two processes


def encode(body):
    """Canonical JSON bytes: the payload stored, measured and served."""
    return json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def _wrapper_pin():
    """The aify-wrapper commit this build pins. A PIN, not what any running launcher runs: that stays 'unknown'."""
    try:
        package = pathlib.Path(__file__).resolve().parents[2] / 'mcp' / 'stdio' / 'package.json'
        spec = json.loads(package.read_text(encoding='utf-8'))['dependencies']['aify-wrapper']
        return spec.rsplit('#', 1)[1] if '#' in spec else 'unknown'
    except Exception:
        return 'unknown'


def _process_versions():
    """Frozen for the process. The comms stamp names the build; dirty=True does not name the dirty bytes."""
    config = get_config()
    return dict(comms=dict(build=config.build_short, dirty=config.build_dirty, builtAt=config.built_at),
                wrapperPin=dict(sha=_wrapper_pin(), label='pinned'), runningWrapper='unknown', env='per-publisher')


VERSIONS = _process_versions()


def row_facts(agent_row):
    """The association row's facts the candidate reads: who, where, and the operator-stop flag's status."""
    return dict(id=agent_row['id'], machineId=agent_row['machine_id'], status=agent_row['status'])


def census(binding, association):
    """PURE. What the candidate consulted from its one frozen binding: every publisher on the agent's machine, with
    its knowledge state and lineage, and each record held for the agent with its frozen bytes and origin. Returns
    (env, missing). None means the candidate consulted no record."""
    if binding is None:
        return dict(consulted=False), []
    if binding.loss or binding.view is None:
        return dict(consulted=True, at=binding.at, lost=True), ['env-view']
    machine, agent_id = association
    publishers, records, missing = [], [], []
    for (mid, instance), p in sorted(binding.view.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        if mid != machine:
            continue
        held = [r for r in p.rows if r.agent_id == agent_id]
        publishers.append(dict(machine=mid, instance=instance, generation=p.generation, incarnation=p.incarnation,
                               publication=p.publication, dataAppliedAt=p.data_applied_at, complete=p.complete,
                               unavailable=p.unavailable, loss=p.loss, holds=bool(held), envBuild=ENV_NOT_PUBLISHED))
        for r in held:
            generation, incarnation, publication, applied_at = r.origin or (None, None, None, None)
            records.append(dict(machine=mid, instance=instance, raw=None if r.raw is None else r.raw.decode('utf-8'),
                                origin=dict(generation=generation, incarnation=incarnation, publication=publication,
                                            appliedAt=applied_at, envBuild=ENV_NOT_PUBLISHED)))
            if r.raw is None or r.origin is None:
                missing.append('env-raw')
    return dict(consulted=True, at=binding.at, lost=False, publishers=publishers, records=records), missing


def item(owner, variant, site, agent_id, binding, association, today, candidate, comms):
    """One evaluation's evidence: both answers, what comms consumed, and the env census."""
    env, missing = census(binding, association)
    return dict(owner=owner, variant=variant, site=site, agentId=agent_id,
                evalAt=None if binding is None else binding.at, today=today, candidate=candidate, comms=comms, env=env,
                versions=VERSIONS, incomplete=sorted(set(missing)))


def word_item(binding, association, inputs, actual, candidate, source):
    """The word: comms' full StatusInputs (config_defect's real text, not the projection's bool) and its word."""
    return item(WORD, '-', source, association[1], binding, association, actual, candidate,
                dict(word=actual, inputs=asdict(inputs)))


@dataclass(frozen=True)
class Item:
    seq: int
    owner: str
    variant: str
    site: str
    agent_id: str
    eval_at: str | None
    complete: bool
    payload: bytes


class EvidenceBuffer:
    """The items and gaps recorded since the last take(). seq keeps rising across takes, for the process."""

    def __init__(self):
        self._seq = 0
        self._items = []
        self._bytes = 0
        self._gaps = Counter()

    def capture(self, key, build):
        """Keep one disagreement's item, or count why not. Never raises."""
        try:
            try:
                body = build()
            except Exception:
                self._gaps[(*key, 'builder-error')] += 1
                return
            try:
                body = dict(body, event=dict(bootId=BOOT_ID, seq=self._seq + 1))
                payload = encode(body)
            except Exception:
                self._gaps[(*key, 'encode-error')] += 1
                return
            if len(payload) > ITEM_BYTES:
                self._gaps[(*key, 'item-too-large')] += 1
            elif len(self._items) >= PENDING_ITEMS or self._bytes + len(payload) > PENDING_BYTES:
                self._gaps[(*key, 'pending-overflow')] += 1
            else:
                self._seq += 1
                self._items.append(Item(self._seq, *key, body['agentId'], body['evalAt'], not body['incomplete'],
                                        payload))
                self._bytes += len(payload)
        except Exception:
            pass  # best effort: the count was already taken, and the join then shows the shortfall

    def take(self):
        """(items, gaps keyed (owner, variant, site, reason)) since the last take. Both reset."""
        taken = tuple(self._items), dict(self._gaps)
        self._items, self._bytes, self._gaps = [], 0, Counter()
        return taken
