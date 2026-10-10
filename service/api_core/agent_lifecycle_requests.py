"""Service-owned lifecycle intent. All writes run in the caller's transaction.

Claim redelivery is receipt recovery, never permission to repeat a host effect.
Claimed custody drift keeps the reservation until the original host reports.
"""
import json
import re
from fastapi import HTTPException
from service.api_core.operator_authz import LifecycleProof, lifecycle_actor
from service.api_core.definition_snapshot import fence_refusal, is_counter
from service.api_core import definition_requests as definitions
from service.api_core.lifecycle_launch import settle_unstarted
from service.api_core.agent_remove import remove_agent
from service.lifecycle_models import TERMINAL

ACTIONS = ('start', 'stop', 'restart', 'kill', 'spawn', 'delete')


def lifecycle_record(row):
    return {key: row[column] for key, column in {
        'id':'id', 'agentId':'agent_id', 'machineId':'machine_id', 'storeId':'store_id',
        'expectedIncarnation':'expected_incarnation', 'expectedRevision':'expected_revision',
        'expectedLifetime':'expected_lifetime', 'action':'action', 'requestedBy':'requested_by',
        'status':'status', 'outcome':'outcome', 'resultLifetime':'result_lifetime',
        'resultIncarnation':'result_incarnation', 'resultRevision':'result_revision',
        'createdAt':'created_at', 'claimedAt':'claimed_at', 'finishedAt':'finished_at'
    }.items()} | {'freshContext': bool(row['fresh_context'])}


async def lifecycle_request_by_id(db, request_id):
    row = await (await db.execute('SELECT * FROM agent_lifecycle_requests WHERE id=?', (request_id,))).fetchone()
    return lifecycle_record(row) if row else None


async def lifecycle_requests_for(db, agent_id):
    rows = await (await db.execute('SELECT * FROM agent_lifecycle_requests WHERE agent_id=? ORDER BY created_at DESC,id DESC', (agent_id,))).fetchall()
    return [lifecycle_record(row) for row in rows]


async def admit_lifecycle(db, agent_id, body, proof, now):
    if not isinstance(proof, LifecycleProof):
        raise HTTPException(403, 'lifecycle proof required')
    if proof.actor != lifecycle_actor(body):
        raise HTTPException(403, 'lifecycle proof does not match caller actor')
    request_id = body.get('requestId')
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', request_id):
        raise HTTPException(422, 'requestId: 1 to 128 ASCII letters, digits, dot, underscore or hyphen; first character must be alphanumeric')
    old = await (await db.execute('SELECT * FROM agent_lifecycle_requests WHERE id=?', (request_id,))).fetchone()
    held = await (await db.execute('SELECT * FROM agent_definitions WHERE agent_id=?', (agent_id,))).fetchone()
    if not old and not held:
        # Before the body's other fields: an agent nobody defines is the answer whatever was asked.
        raise HTTPException(404, 'agent has no owning definition')
    if body.get('action') not in ACTIONS:
        raise HTTPException(422, 'action: start, stop, restart, kill, spawn or delete')
    if 'expectedLifetime' not in body or (body['expectedLifetime'] is not None and
            (not isinstance(body['expectedLifetime'], str) or not body['expectedLifetime'])):
        raise HTTPException(422, 'expectedLifetime: explicitly name a lifetime string or null')
    if 'freshContext' in body and not isinstance(body['freshContext'], bool):
        raise HTTPException(422, 'freshContext: a boolean')
    if 'expectedRevision' in body and not is_counter(body['expectedRevision']):
        raise HTTPException(422, 'expectedRevision: a definition revision counter')
    intent = json.dumps({'agentId':agent_id, 'action':body['action'], 'requestedBy':proof.actor,
        'expectedLifetime':body['expectedLifetime'], 'expectedRevision':body.get('expectedRevision'),
        'freshContext':body.get('freshContext', False)}, sort_keys=True)
    if old:
        if old['intent'] != intent:
            raise HTTPException(409, 'requestId already records a different intent')
        return lifecycle_record(old)
    if body.get('expectedRevision', held['revision']) != held['revision']:
        raise HTTPException(409, 'definition revision moved')
    waiting = await (await db.execute("SELECT id FROM agent_lifecycle_requests WHERE agent_id=? AND status IN ('pending','claimed')", (agent_id,))).fetchone()
    if waiting:
        raise HTTPException(409, f"agent has an open lifecycle request {waiting['id']}")
    await db.execute('INSERT INTO agent_lifecycle_requests (id,agent_id,machine_id,store_id,expected_incarnation,expected_revision,expected_lifetime,action,requested_by,fresh_context,intent,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        (request_id,agent_id,held['machine_id'],held['store_id'],held['incarnation'],held['revision'],body['expectedLifetime'],body['action'],proof.actor,body.get('freshContext',False),intent,now))
    return await lifecycle_request_by_id(db, request_id)


async def claim_lifecycle_requests(db, environment, bridge_id, machine_id, now):
    why = fence_refusal(environment, bridge_id, machine_id)
    if why:
        raise HTTPException(409, f'lifecycle claim refused: {why}')
    store = await (await db.execute('SELECT store_id FROM definition_stores WHERE machine_id=?', (machine_id,))).fetchone()
    rows = await (await db.execute("SELECT * FROM agent_lifecycle_requests WHERE machine_id=? AND status IN ('pending','claimed') ORDER BY created_at,id", (machine_id,))).fetchall()
    found = []
    for row in rows:
        held = await (await db.execute('SELECT * FROM agent_definitions WHERE agent_id=?', (row['agent_id'],))).fetchone()
        why = definitions._undeliverable(row, held, store['store_id'] if store else '')
        if not why and (held['incarnation'], held['revision']) != (row['expected_incarnation'],row['expected_revision']):
            why = 'definition incarnation or revision moved'
        if why:
            if row['status']=='pending':
                await db.execute("UPDATE agent_lifecycle_requests SET status='refused',outcome=?,finished_at=? WHERE id=?", (why,now,row['id']))
            continue
        await db.execute("UPDATE agent_lifecycle_requests SET status='claimed',claimed_at=CASE WHEN claimed_at='' THEN ? ELSE claimed_at END WHERE id=?", (now,row['id']))
        found.append(await lifecycle_request_by_id(db,row['id']))
    return found


async def report_lifecycle_result(db, environment, request_id, body, now):
    why = fence_refusal(environment, body.get('bridgeId',''), body.get('machineId',''))
    if why:
        raise HTTPException(409, f'lifecycle report refused: {why}')
    if body.get('status') not in TERMINAL or not isinstance(body.get('outcome'),str) or not isinstance(body.get('finishedAt'),str) or not body['finishedAt'] or 'resultLifetime' not in body or (body['resultLifetime'] is not None and not isinstance(body['resultLifetime'],str)):
        raise HTTPException(422, 'status, outcome, resultLifetime and finishedAt: explicit terminal result required')
    row = await (await db.execute('SELECT * FROM agent_lifecycle_requests WHERE id=?',(request_id,))).fetchone()
    if not row:
        raise HTTPException(404, 'no lifecycle request')
    if row['machine_id'] != body['machineId']:
        raise HTTPException(409, 'lifecycle request belongs to another machine')
    counters = (None, None)
    if row['action'] == 'delete' and body['status'] == 'done':
        # The host's removal receipt: the definition's last incarnation and revision, as it removed them.
        counters = (body.get('resultIncarnation'), body.get('resultRevision'))
        if not all(is_counter(value) for value in counters):
            raise HTTPException(422, 'a done delete reports resultIncarnation and resultRevision from its removal')
    result = (body['status'],body['outcome'],body['resultLifetime'],body['finishedAt'],*counters)
    if row['status'] in TERMINAL:
        original = tuple(row[key] for key in ('status','outcome','result_lifetime','finished_at','result_incarnation','result_revision'))
        if original != result:
            raise HTTPException(409,'terminal lifecycle result is immutable')
        return lifecycle_record(row)
    if row['status']!='claimed':
        raise HTTPException(409,'lifecycle request was never claimed')
    await db.execute('UPDATE agent_lifecycle_requests SET status=?,outcome=?,result_lifetime=?,finished_at=?,result_incarnation=?,result_revision=? WHERE id=?', (*result,request_id))
    await settle_unstarted(db, request_id, body, now)
    return await lifecycle_request_by_id(db, request_id)


async def finish_lifecycle_removal(db, request):
    """The service's consequence of a done delete: remove the agent behind the definition-removal fence.
    The host verified the worker's death before it removed the file, so no stop is signalled here; a
    repeated report re-runs it harmlessly. Owns its commits, so it runs after the report's commit."""
    fenced = {'agentId': request['agentId'], 'machineId': request['machineId'],
              'storeId': request['storeId'], 'expectedIncarnation': request['expectedIncarnation']}
    _, why = await remove_agent(db, request['agentId'], actor=request['requestedBy'], reason='definition_removed',
                                refusal=lambda conn: definitions.removal_refusal(conn, fenced), stop_worker=False)
    return why
