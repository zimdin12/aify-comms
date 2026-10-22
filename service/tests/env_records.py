"""aify-env's reports, for tests of comms after the 0.9.8 switch.

After the switch every reader asks the operational view, and the view reads only what aify-env published. A test whose
obligation is not the switch itself (a claim's context, a reply contract, a console paste) still needs its agent to be
known, the way a healthy aify-env makes it known in production: by publishing a fresh record. `report` does exactly
that, through the real `/api/v1/agent-state` route, so the record goes through validation, the store, the mirror and
the report pair like any other. Nothing here stands in for the view.

An agent registered with no machine has nowhere aify-env could publish it from, so `report` places it on TEST_HOST
first; production agents always carry the machine their aify-env runs on.
"""
import json
import sqlite3
from contextlib import closing

TEST_HOST = 'test-host'
INSTANCE = 'test-env'
EVERY_MS = 60_000  # the announced full-report interval: fresh for three of them (decision 2)

_publication = {}  # (db path, machine, instance) -> the last publication posted, so each one advances
_held = {}  # (db path, machine, instance) -> {agent id: record}: a snapshot is the whole population, so it keeps them


def record(agent_id, *, busy=False, state=None, lifetime=None):
    """One agent's record, as aify-env publishes it. `state` defaults to what `busy` implies."""
    state = state or ('working' if busy else 'idle')
    return dict(agentId=agent_id, lifetime=lifetime or f'{agent_id}-life', state=state,
                stateCause='turn-open' if busy else 'at-prompt', busy=bool(busy),
                process=dict(state='running', verified='yes', pid=4242),
                turn=dict(open=bool(busy), startedAtUs=1, lastEventAtUs=1, awaitingInput=False,
                          busyIf=dict(strict=bool(busy), verifiedRenewal=bool(busy))))


def startable(agent_id):
    """The record aify-env publishes for a managed agent it defines and is not running: `available`, cause `startable`,
    with no lifetime and no process (aify-env's deriveAgentState, its managed-and-valid row)."""
    return dict(agentId=agent_id, lifetime=None, state='available', stateCause='startable', busy=False,
                process=dict(state='none', verified='unknown', pid=None), turn=None)


def _placed(db_path, agent_id):
    with closing(sqlite3.connect(db_path)) as db:
        row = db.execute('SELECT machine_id FROM agents WHERE id = ?', (agent_id,)).fetchone()
        if row is None:
            raise AssertionError(f'env_records.report: no agent {agent_id!r} to report')
        if str(row[0] or '').strip():
            return str(row[0]).strip()
        db.execute('UPDATE agents SET machine_id = ? WHERE id = ?', (TEST_HOST, agent_id))
        db.commit()
        return TEST_HOST


def report(case, *agent_ids, busy=False, state=None, instance=INSTANCE, records=None, headers=None):
    """aify-env on each agent's machine publishes a complete snapshot holding it, fresh now. `records` overrides the
    default record per agent id; `headers` adds to the request's (an API key, where the service has one). Returns the
    HTTP responses, each checked for acceptance."""
    by_machine = {}
    for agent_id in agent_ids:
        by_machine.setdefault(_placed(case._db_path, agent_id), []).append(agent_id)
    responses = []
    for machine, ids in by_machine.items():
        key = (str(case._db_path), machine, instance)
        _publication[key] = _publication.get(key, 0) + 1
        held = _held.setdefault(key, {})
        held.update((a, (records or {}).get(a) or record(a, busy=busy, state=state)) for a in ids)
        body = dict(kind='snapshot', machineId=machine, instance=instance, generation=1, incarnationId='test-boot',
                    publication=_publication[key], inputs={'operatorStop': 'not-tracked'}, complete=True,
                    removed=[], reportEveryMs=EVERY_MS,
                    agents=list(held.values()))
        response = case.client.post('/api/v1/agent-state', content=json.dumps(body).encode('utf-8'),
                                    headers={'content-type': 'application/json', **(headers or {})})
        if response.status_code != 204:
            raise AssertionError(f'env_records.report: {response.status_code} {response.text}')
        responses.append(response)
    return responses


def publish_state(case, machine, instance, kind, reason, headers=None):
    """A body that carries no rows: `unavailable` (the publisher cannot read its own state; `reason` says why) or
    `retired` (a clean shutdown; `reason` is 'shutdown'). It advances the same publication counter as `report`."""
    key = (str(case._db_path), machine, instance)
    _publication[key] = _publication.get(key, 0) + 1
    body = dict(kind=kind, machineId=machine, instance=instance, generation=1, incarnationId='test-boot',
                publication=_publication[key], inputs={'operatorStop': 'not-tracked'}, reason=reason)
    if kind == 'unavailable':
        body['reportEveryMs'] = EVERY_MS
    else:
        _held.pop(key, None)
    response = case.client.post('/api/v1/agent-state', content=json.dumps(body).encode('utf-8'),
                                headers={'content-type': 'application/json', **(headers or {})})
    if response.status_code != 204:
        raise AssertionError(f'env_records.publish_state: {response.status_code} {response.text}')
    return response


def reset_mirror():
    """A fresh mirror for a fresh database: the process-global mirror must not carry one test's records into the
    next."""
    from service.api_core import partial_status_shadow as shadow

    shadow.mirror = shadow.Mirror()


def rollback_agent(case, agent_id):
    """The agent as `GET /agents/<id>` would serve it after a revert of the switch.

    After the switch the served word is aify-env's. The legacy derivation still runs on every refresh, as the
    rollback's input, and its word and note are kept beside the served ones (`legacy_status`, `legacy_reason`). This
    assembles the record from them the way the reverted route did: the record path's old promotion, then the two
    read-boundary gates that corrected a cached word at the read. A test whose subject is that derivation reads it
    here, through the same route-driven refresh it always used; it dies with the derivation in SW7."""
    import asyncio

    from service.api_core.dispatch_state import _get_dispatch_state_for_agent
    from service.api_core.records import _agent_record_to_dict
    from service.api_core.registration_gates import _enforce_env_reachable_gate, _enforce_live_worker_gate
    from service.api_core.settings import _load_settings
    from service.db import get_db
    from service.reconcilers.status_cache import _live_state_get

    response = case.client.get(f'/api/v1/agents/{agent_id}')
    if response.status_code != 200:
        raise AssertionError(f'env_records.rollback_agent: {response.status_code} {response.text}')
    entry = _live_state_get(agent_id)
    if not entry or 'legacy_status' not in entry:
        raise AssertionError(f'env_records.rollback_agent: no refreshed entry for {agent_id!r}')

    async def assembled():
        db = await get_db()
        try:
            settings = await _load_settings(db)
            row = await (await db.execute('SELECT * FROM agents WHERE id = ?', (agent_id,))).fetchone()
            dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
            word = _legacy_promoted(entry['legacy_status'], dispatch_state)
            payload = dict(response.json()['agent'])
            legacy = _agent_record_to_dict(row, word, payload.get('unread', 0), dispatch_state,
                                           live_reason=entry.get('legacy_reason'), outbound=payload.get('outbound'))
            payload.update(status=legacy['status'], statusRaw=legacy['statusRaw'], statusNote=legacy['statusNote'])
            payload = await _enforce_live_worker_gate(payload, db, settings, agent_id)
            return await _enforce_env_reachable_gate(payload, db, settings, agent_id)
        finally:
            await db.close()

    return asyncio.run(assembled())


def _legacy_promoted(word, dispatch_state):
    """The record path's promotion before the switch (`records._status_with_dispatch` at 36028554): a running run made
    any word but an operator stop, a non-live word or `blocked` read `working`, an empty or unknown word included."""
    active = (dispatch_state or {}).get('activeRun') or {}
    if active.get('status') == 'running' and word not in ('stopped', 'offline', 'misconfigured', 'blocked'):
        return 'working'
    return word


def rollback_word(case, agent_id):
    """`rollback_agent`'s word alone."""
    return rollback_agent(case, agent_id)['status']
