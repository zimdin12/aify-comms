"""Bounded G6/G7 publication ingest. These tables confer no current-state authority."""
from dataclasses import dataclass
import hashlib
import asyncio
import re

from service.api_core import partial_status_shadow as shadow, switched_view
from service.api_core.report_age import is_report_interval

_INGEST_LOCK = asyncio.Lock()
import json

from service.db import get_db


class InvalidPublication(ValueError):
    """The entire publication must validate before it can participate in ordering."""


@dataclass(frozen=True)
class ApplyResult:
    applied: bool
    reason: str | None = None


#: Why a publisher retires its key (B5): only a clean shutdown that proved every lifetime it published ended.
RETIREMENT_REASONS = {'shutdown'}

# Pinned agent-state.mjs deriveAgentState and AgentStateHost.current vocabulary.
_STATES = {'unknown', 'stopped', 'misconfigured', 'working', 'blocked', 'shell', 'idle', 'starting', 'available', 'offline'}
_CAUSES = {'unrecognised', 'operator-stop', 'conflict', 'config', 'identity-unknown', 'screen', 'turn-open', 'at-prompt', 'starting', 'startable', 'absent', 'turn-unknown'}
_PROCESS_STATES = {'running', 'starting', 'exited', 'none', 'unknown'}
_VERIFIED = {'yes', 'no', 'unknown'}
_SAFE_INTEGER = 9007199254740991


def _require(condition, message):
    if not condition:
        raise InvalidPublication(message)


def _object(value, required, optional=()):
    _require(type(value) is dict, 'expected object')
    _require(set(required) <= value.keys() <= set(required) | set(optional), 'missing or unknown fields')


def _text(value):
    _require(type(value) is str and bool(value), 'expected nonempty string')


def _integer(value, minimum=0):
    _require(type(value) is int and minimum <= value <= _SAFE_INTEGER, 'expected safe integer')


def _flag(value):
    _require(type(value) is bool, 'expected boolean')


def _word(value, vocabulary):
    _require(type(value) is str and value in vocabulary, 'unrecognised vocabulary')


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value, 'duplicate JSON key')
        value[key] = item
    return value


def _constant(value):
    raise InvalidPublication('nonfinite JSON number')


def _decode(raw):
    _require(type(raw) is bytes, 'expected raw bytes')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, RecursionError) as error:
        raise InvalidPublication('invalid UTF8 JSON') from error


BUILD = re.compile(r'[\x21-\x7e]{1,200}')


def _build(value):
    """B6a: an aify-env build, when published, is printable ASCII with no space, 1-200 characters. An unknown build is
    omitted, never null."""
    _require(type(value) is str and BUILD.fullmatch(value) is not None, 'expected build string')


def _report_every(value):
    """Optional on every body but `retired`: absent is accepted (an aify-env from before decision 2), and a present
    value must be a whole number within the bounds. Never null."""
    _require(is_report_interval(value), 'expected reportEveryMs within bounds')


def _record(record):
    # name, role and launch arrived with C1: optional, so a publisher from before them is still read.
    _object(record, ('agentId', 'lifetime', 'state', 'stateCause', 'busy', 'process', 'turn'), ('name', 'role', 'launch'))
    for key in ('name', 'role'):
        if record.get(key) is not None:
            _text(record[key])
    launch = record.get('launch')
    if launch is not None:
        _object(launch, ('cwd', 'definition'))
        if launch['cwd'] is not None:
            _text(launch['cwd'])
        if launch['definition'] is not None:
            _object(launch['definition'], ('storeId', 'incarnation', 'revision'))
            _text(launch['definition']['storeId'])
            _integer(launch['definition']['incarnation'], 1)
            _integer(launch['definition']['revision'], 1)
    _text(record['agentId'])
    if record['lifetime'] is not None:
        _text(record['lifetime'])
    _word(record['state'], _STATES)
    _word(record['stateCause'], _CAUSES)
    _flag(record['busy'])
    process = record['process']
    _object(process, ('state', 'verified', 'pid'))
    _word(process['state'], _PROCESS_STATES)
    _word(process['verified'], _VERIFIED)
    if process['pid'] is not None:
        _integer(process['pid'], 1)
    turn = record['turn']
    if turn is not None:
        _object(turn, ('open', 'startedAtUs', 'lastEventAtUs', 'awaitingInput', 'busyIf'), ('ageMs',))
        if 'ageMs' in turn:  # B6: the turn's age on the publisher's clock when it published
            _integer(turn['ageMs'])
        for key in ('open', 'awaitingInput'):
            _flag(turn[key])
        for key in ('startedAtUs', 'lastEventAtUs'):
            _integer(turn[key])
        _object(turn['busyIf'], ('strict', 'verifiedRenewal'))
        for value in turn['busyIf'].values():
            _flag(value)


def _validate(body):
    _require(type(body) is dict, 'expected publication object')
    kind = body.get('kind')
    _word(kind, {'snapshot', 'changes', 'unavailable', 'retired'})
    common = ('kind', 'machineId', 'instance', 'generation', 'incarnationId', 'publication', 'inputs')
    if kind == 'unavailable':
        _object(body, (*common, 'reason'), ('build', 'reportEveryMs'))
        _text(body['reason'])
    elif kind == 'retired':
        _object(body, (*common, 'reason'), ('build',))
        _word(body['reason'], RETIREMENT_REASONS)
    elif kind == 'snapshot':
        _object(body, (*common, 'complete', 'agents'), ('removed', 'build', 'reportEveryMs'))
        _require(body['complete'] is True, 'snapshot must be complete')
    else:
        _object(body, (*common, 'agents', 'removed'), ('build', 'reportEveryMs'))
    if 'build' in body:
        _build(body['build'])
    if 'reportEveryMs' in body:
        _report_every(body['reportEveryMs'])
    for key in ('machineId', 'instance', 'incarnationId'):
        _text(body[key])
    for key in ('generation', 'publication'):
        _integer(body[key], 1)
    _require(type(body['inputs']) is dict and all(type(v) is str for v in body['inputs'].values()), 'inputs must be string object')
    if kind in ('unavailable', 'retired'):
        return
    _require(type(body['agents']) is list and type(body.get('removed', [])) is list, 'expected row arrays')
    ids = set()
    for record in body['agents']:
        _record(record)
        _require(record['agentId'] not in ids, 'duplicate agentId')
        ids.add(record['agentId'])
    for removed in body.get('removed', []):
        _object(removed, ('agentId', 'lifetime'))
        _text(removed['agentId'])
        _text(removed['lifetime'])


def _order(previous, body, digest):
    if previous is None or body['generation'] > previous['generation']:
        return None
    if body['generation'] < previous['generation']:
        return 'stale'
    if body['incarnationId'] != previous['incarnation_id']:
        return 'conflict'
    if body['publication'] < previous['publication']:
        return 'stale'
    if body['publication'] == previous['publication']:
        return 'duplicate' if digest == previous['digest'] else 'conflict'
    if previous['kind'] == 'retired':  # the publisher said this generation holds nothing: a later body of it is late
        return 'retired'
    return None


def _retire(key):
    """After a retirement's commit (B5): K leaves the mirror. If that fails, K stays absent and every other publisher
    becomes a known loss, each recovering on its own next snapshot."""
    try:
        before = shadow.mirror.view()
    except Exception:
        before = None
    try:
        shadow.mirror.retire(key)
    except Exception:
        shadow.retire_safe(key, before)


async def _expire_changed(db, before):
    """The served word is the operational view's (0.9.8 switch). Every agent on a machine whose publishers this
    publication changed is re-derived, not only the agents the publication names: an unavailable, lost, retired or
    recovered publisher changes what every agent on its machine can be known as (SW5 review R2). A failure here leaves
    words to their own refresh times, never a committed apply undone.

    RE-DERIVED NOW, not on the next read. The cache announces a word that moved (`_live_state_set`), so the dashboard
    hears a stop when aify-env publishes it; left to the next read it waited out the dashboard's poll (the operator,
    2026-10-25: "when i kill/stop agent then i see that agent be in old status for some time")."""
    from service.api_core.status_refresh import _refresh_expired_agent_live_states

    try:
        try:
            after = shadow.mirror.view()
        except Exception:
            after = None
        expired = await switched_view.expire_machines(db, switched_view.changed_machines(before, after))
        if expired:
            await _refresh_expired_agent_live_states(db, agent_ids=expired)
    except Exception:
        pass


def _json(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'))


class AgentStateShadowStore:
    """Own the connection and transaction, including cursor and data publication."""

    def __init__(self, connection_factory=None):
        self._connect = connection_factory or get_db

    async def apply(self, document: bytes, applied_at: str) -> ApplyResult:
        body = _decode(document)
        _validate(body)
        try:
            projection = shadow.prepare(body, applied_at)
        except Exception:
            projection = None
        digest = hashlib.sha256(document).hexdigest()
        async with _INGEST_LOCK:
            return await self._apply(body, digest, projection, applied_at)

    async def _apply(self, body, digest, projection, applied_at):
        db = await self._connect()
        try:
            try:
                await db.execute('BEGIN IMMEDIATE')
                key = (body['machineId'], body['instance'])
                previous = await (await db.execute(
                    'SELECT * FROM agent_state_shadow_publishers WHERE machine_id=? AND instance=?', key)).fetchone()
                refusal = _order(previous, body, digest)
                if refusal:
                    await db.rollback()
                    return ApplyResult(False, refusal)
                await self._write_cursor(db, key, body, digest, applied_at)
                if body['kind'] == 'retired':
                    await self._write_retirement(db, key, body, applied_at)
                elif body['kind'] != 'unavailable':
                    await self._write_data(db, key, body, previous, applied_at)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
            try:
                before = shadow.mirror.view()
            except Exception:
                before = None
            if body['kind'] == 'retired':
                _retire(key)
                await _expire_changed(db, before)
                return ApplyResult(True)
            # Outside transaction rollback handling, before the next await including close.
            try:
                if projection is None:
                    shadow.invalidate_safe(key)
                else:
                    shadow.mirror.feed(projection)
            except Exception:
                shadow.invalidate_safe(key)
                projection = None
            # Contact (decision 2): this applied, advancing, non-retired body renews the publisher's report pair, after
            # its data step so the pair is this body's. Refused, duplicate, stale and conflicting bodies returned above.
            try:
                shadow.mirror.renew_report(key, applied_at, body.get('reportEveryMs'))
            except Exception:
                pass  # contact unrenewed reads as not-reporting or stale: never fresher than it is
            await _expire_changed(db, before)
            return ApplyResult(True)
        finally:
            await db.close()

    async def _write_cursor(self, db, key, body, digest, applied_at):
        await db.execute('''INSERT INTO agent_state_shadow_publishers
            (machine_id,instance,generation,incarnation_id,publication,digest,kind,inputs,reason,
             report_received_at,report_every_ms)
            VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(machine_id,instance) DO UPDATE SET
            generation=excluded.generation,incarnation_id=excluded.incarnation_id,
            publication=excluded.publication,digest=excluded.digest,kind=excluded.kind,
            inputs=excluded.inputs,reason=excluded.reason,
            report_received_at=excluded.report_received_at,report_every_ms=excluded.report_every_ms''',
            (*key, body['generation'], body['incarnationId'], body['publication'], digest,
             body['kind'], _json(body['inputs']), body.get('reason'),
             applied_at if body['kind'] != 'retired' else None, body.get('reportEveryMs')))

    async def _write_retirement(self, db, key, body, applied_at):
        """K holds nothing from now on: its rows go, and its data columns name the retirement."""
        await db.execute('DELETE FROM agent_state_shadow_rows WHERE machine_id=? AND instance=?', key)
        await db.execute('''UPDATE agent_state_shadow_publishers SET data_generation=?,
            data_incarnation_id=?,data_publication=?,data_applied_at=?,data_inputs=?
            WHERE machine_id=? AND instance=?''',
            (body['generation'], body['incarnationId'], body['publication'], applied_at, _json(body['inputs']), *key))

    async def _write_data(self, db, key, body, previous, applied_at):
        # Unavailable can move the cursor ahead without changing the stored data generation.
        if body['kind'] == 'snapshot' or previous is None or previous['data_generation'] != body['generation']:
            await db.execute('DELETE FROM agent_state_shadow_rows WHERE machine_id=? AND instance=?', key)
        else:
            # An ended old lifetime must not remove the replacement in the same publication.
            for removed in body['removed']:
                await db.execute('''DELETE FROM agent_state_shadow_rows
                    WHERE machine_id=? AND instance=? AND agent_id=? AND lifetime=?''',
                    (*key, removed['agentId'], removed['lifetime']))
        for record in body['agents']:
            await db.execute('''INSERT INTO agent_state_shadow_rows
                (machine_id,instance,agent_id,lifetime,record_json,row_applied_at) VALUES (?,?,?,?,?,?)
                ON CONFLICT(machine_id,instance,agent_id) DO UPDATE SET lifetime=excluded.lifetime,
                record_json=excluded.record_json,row_applied_at=excluded.row_applied_at''',
                (*key, record['agentId'], record['lifetime'], _json(record), applied_at))
        await db.execute('''UPDATE agent_state_shadow_publishers SET data_generation=?,
            data_incarnation_id=?,data_publication=?,data_applied_at=?,data_inputs=?
            WHERE machine_id=? AND instance=?''',
            (body['generation'], body['incarnationId'], body['publication'], applied_at, _json(body['inputs']), *key))
