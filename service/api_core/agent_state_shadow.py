"""Bounded G6/G7 publication ingest. These tables confer no current-state authority."""
from dataclasses import dataclass
import hashlib
import asyncio

from service.api_core import partial_status_shadow as shadow

_INGEST_LOCK = asyncio.Lock()
import json

from service.db import get_db


class InvalidPublication(ValueError):
    """The entire publication must validate before it can participate in ordering."""


@dataclass(frozen=True)
class ApplyResult:
    applied: bool
    reason: str | None = None


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
        _object(turn, ('open', 'startedAtUs', 'lastEventAtUs', 'awaitingInput', 'busyIf'))
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
    _word(kind, {'snapshot', 'changes', 'unavailable'})
    common = ('kind', 'machineId', 'instance', 'generation', 'incarnationId', 'publication', 'inputs')
    if kind == 'unavailable':
        _object(body, (*common, 'reason'))
        _text(body['reason'])
    elif kind == 'snapshot':
        _object(body, (*common, 'complete', 'agents'), ('removed',))
        _require(body['complete'] is True, 'snapshot must be complete')
    else:
        _object(body, (*common, 'agents', 'removed'))
    for key in ('machineId', 'instance', 'incarnationId'):
        _text(body[key])
    for key in ('generation', 'publication'):
        _integer(body[key], 1)
    _require(type(body['inputs']) is dict and all(type(v) is str for v in body['inputs'].values()), 'inputs must be string object')
    if kind == 'unavailable':
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
    return None


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
                await self._write_cursor(db, key, body, digest)
                if body['kind'] != 'unavailable':
                    await self._write_data(db, key, body, previous, applied_at)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
            # Outside transaction rollback handling, before the next await including close.
            try:
                if projection is None:
                    shadow.invalidate_safe(key)
                else:
                    shadow.mirror.feed(projection)
            except Exception:
                shadow.invalidate_safe(key)
            return ApplyResult(True)
        finally:
            await db.close()

    async def _write_cursor(self, db, key, body, digest):
        await db.execute('''INSERT INTO agent_state_shadow_publishers
            (machine_id,instance,generation,incarnation_id,publication,digest,kind,inputs,reason)
            VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(machine_id,instance) DO UPDATE SET
            generation=excluded.generation,incarnation_id=excluded.incarnation_id,
            publication=excluded.publication,digest=excluded.digest,kind=excluded.kind,
            inputs=excluded.inputs,reason=excluded.reason''',
            (*key, body['generation'], body['incarnationId'], body['publication'], digest,
             body['kind'], _json(body['inputs']), body.get('reason')))

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
