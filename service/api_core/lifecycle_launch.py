"""Queue-free tracking for a claimed lifecycle launch. The host owns all effects."""
import json
import uuid
from fastapi import HTTPException
from service.api_core.definition_snapshot import fence_refusal
from service.api_core.definition_start import binding_for, spec_columns, StartRefused
from service.api_core.capabilities import _default_console_argv, _environment_supports_terminal
from service.api_core.workspace import _workspace_for_environment
from service.api_core.records import _environment_record_to_dict, herdr_space_of
from service.api_core.model_effort import as_its_spec_declares
from service.api_core.launch_env import managed_launch_env, NEVER_INHERITED, launches_via_wrapper
from service.api_core.settings import _load_settings
from service.api_core.terminal_status import _terminal_status_transition


async def _row(db, table, column, value):
    return await (await db.execute(f'SELECT * FROM {table} WHERE {column}=?', (value,))).fetchone()


async def _admitted(db, environment, request_id, body, *, attachment_replay=False):
    why = fence_refusal(environment, body.get('bridgeId'), body.get('machineId'))
    if why:
        raise HTTPException(409, why)
    request = await _row(db, 'agent_lifecycle_requests', 'id', request_id)
    if not request:
        raise HTTPException(404, 'no lifecycle request')
    if (request['status'] != 'claimed' and not (attachment_replay and request['status'] in ('done', 'refused', 'failed'))) or request['action'] not in ('start', 'restart', 'spawn'):
        raise HTTPException(409, 'launch requires a claimed start, restart or spawn')
    definition = await _row(db, 'agent_definitions', 'agent_id', request['agent_id'])
    store = await _row(db, 'definition_stores', 'machine_id', request['machine_id'])
    expected = (request['machine_id'], request['store_id'], request['expected_incarnation'], request['expected_revision'])
    actual = tuple(definition[k] for k in ('machine_id', 'store_id', 'incarnation', 'revision')) if definition else None
    if request['machine_id'] != body.get('machineId') or expected != actual or not store or store['store_id'] != request['store_id']:
        raise HTTPException(409, 'lifecycle definition ownership moved')
    agent = await _row(db, 'agents', 'id', request['agent_id'])
    try:
        binding = binding_for(request['agent_id'], definition, agent)
    except StartRefused as error:
        raise HTTPException(409, str(error)) from error
    except (KeyError, ValueError, TypeError) as error:
        raise HTTPException(409, 'definition runtime or native prerequisites are unsupported or unreadable') from error
    return request, binding, dict(agent) if agent else {}


async def _insert(db, table, values):
    await db.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))


async def prepare_launch(db, environment, request_id, body, now):
    request, binding, agent = await _admitted(db, environment, request_id, body)
    settings = await _load_settings(db)
    env = _environment_record_to_dict(environment, offline_seconds=settings.get('environment_offline_seconds', 90))
    if env['status'] != 'online' or not _environment_supports_terminal(env, binding.runtime):
        raise HTTPException(409, 'environment cannot run this terminal runtime')
    old = await _row(db, 'lifecycle_launches', 'request_id', request_id)
    if old:
        if (old['environment_id'], old['bridge_id']) != (environment['id'], body['bridgeId']):
            raise HTTPException(409, 'prepared launch belongs to another environment or bridge')
        return json.loads(old['launch_json'])
    workspace, _ = _workspace_for_environment(env, binding.workspace)
    handle = '' if request['fresh_context'] or agent.get('runtime') != binding.runtime else str(agent.get('session_handle') or '')
    if binding.runtime == 'pi' and not handle.strip():
        raise HTTPException(409, 'pi launch requires an actual native session handle')
    session_id, terminal_id, spec_id = ('life_session_' + uuid.uuid4().hex, 'life_term_' + uuid.uuid4().hex, 'life_spec_' + uuid.uuid4().hex)
    spec = spec_columns(binding)
    runtime_config = json.loads(spec['metadata']).get('runtimeConfig', {})
    projection = as_its_spec_declares({
        'id': request['agent_id'], 'runtime': binding.runtime, 'role': binding.role,
        'sessionHandle': handle, 'runtimeConfig': json.loads(agent.get('runtime_config') or '{}'),
        'runtimeState': {'resumePolicy': 'fresh_context'} if request['fresh_context'] else {},
        'herdrSpace': herdr_space_of(agent),
    }, spec['model'], runtime_config)
    session = {'agent_id': request['agent_id'], 'runtime': binding.runtime, 'session_handle': handle}
    argv = _default_console_argv(session, workspace)
    terminal = {'id': terminal_id, 'agentId': request['agent_id'], 'runtime': binding.runtime, 'sessionHandle': handle, 'role': binding.role}
    launch = {
        'terminalId': terminal_id, 'agentId': request['agent_id'], 'runtime': binding.runtime,
        'command': ' '.join(argv), 'argv': argv, 'cwd': workspace, 'cols': 0, 'rows': 0,
        'sessionHandle': handle,
        'env': managed_launch_env(terminal=terminal, agent=projection, workspace=workspace,
            terminal_id=terminal_id, managed_via_wrapper=launches_via_wrapper(settings, binding.runtime),
            spawn_env=json.loads(spec['env_vars']), start_intent='start'),
        'unsetEnv': list(NEVER_INHERITED), 'herdrSpace': projection['herdrSpace'],
        'definition': {'storeId': binding.store_id, 'incarnation': binding.incarnation, 'revision': binding.revision},
    }
    await _insert(db, 'spawn_specs', dict(id=spec_id, agent_id=request['agent_id'], environment_id=environment['id'], workspace=workspace, created_at=now, updated_at=now, **spec))
    await _insert(db, 'agent_sessions', dict(id=session_id, agent_id=request['agent_id'], environment_id=environment['id'], runtime=binding.runtime, workspace=workspace, owner_bridge_id=body['bridgeId'], terminal_id=terminal_id, terminal_status='starting', terminal_command=launch['command'], terminal_workspace=workspace, session_handle=handle, spawn_spec_id=spec_id, spawn_request_id=None, status='starting', started_at=now, last_seen=now))
    await _insert(db, 'terminal_sessions', dict(id=terminal_id, session_id=session_id, agent_id=request['agent_id'], environment_id=environment['id'], bridge_id=body['bridgeId'], runtime=binding.runtime, workspace=workspace, command=launch['command'], argv=json.dumps(argv), status='starting', requested_by=request['requested_by'], created_at=now, updated_at=now, start_intent='start'))
    await _insert(db, 'lifecycle_launches', dict(request_id=request_id, environment_id=environment['id'], bridge_id=body['bridgeId'], session_id=session_id, terminal_id=terminal_id, spec_id=spec_id, launch_json=json.dumps(launch), created_at=now))
    return launch


async def attach(db, environment, request_id, body, now):
    prepared = await _row(db, 'lifecycle_launches', 'request_id', request_id)
    await _admitted(db, environment, request_id, body, attachment_replay=bool(prepared and prepared['attachment_json']))
    if not prepared or (prepared['environment_id'], prepared['bridge_id'], prepared['terminal_id']) != (environment['id'], body['bridgeId'], body.get('terminalId')):
        raise HTTPException(409, 'attachment requires the exact prepared terminal')
    for key in ('handle', 'lifetime'):
        if not isinstance(body.get(key), str) or not body[key].strip():
            raise HTTPException(422, f'{key}: nonempty actual runner value required')
    for key in ('processId', 'cols', 'rows'):
        if key == 'processId' or key in body:
            if type(body.get(key)) is not int or body[key] <= 0:
                raise HTTPException(422, f'{key}: positive integer required')
    receipt = {'requestId': request_id, 'terminalId': prepared['terminal_id'], 'handle': body['handle'], 'processId': body['processId'], 'lifetime': body['lifetime']}
    receipt.update({k: body[k] for k in ('cols', 'rows') if k in body})
    if prepared['attachment_json']:
        old = json.loads(prepared['attachment_json'])
        if old != receipt:
            raise HTTPException(409, 'lifecycle attachment receipt is immutable')
        return old
    terminal = await _row(db, 'terminal_sessions', 'id', prepared['terminal_id'])
    if not terminal or not _terminal_status_transition(terminal['status'], 'attached'):
        raise HTTPException(409, 'prepared terminal has already ended')
    await db.execute('UPDATE lifecycle_launches SET attachment_json=? WHERE request_id=?', (json.dumps(receipt), request_id))
    await db.execute("UPDATE terminal_sessions SET status='attached',process_id=?,cols=?,rows=?,updated_at=? WHERE id=?", (str(body['processId']), body.get('cols',terminal['cols']), body.get('rows',terminal['rows']), now, terminal['id']))
    await db.execute("UPDATE agent_sessions SET status='running',terminal_status='attached',process_id=?,last_seen=? WHERE id=?", (str(body['processId']), now, prepared['session_id']))
    return receipt


async def settle_unstarted(db, request_id, body, now):
    # A failed/unknown report is not proof that a possibly started worker is dead.
    # Only canonical pre-effect refusal, with no attachment, can close prepared tracking.
    if body['status'] != 'refused' or body['resultLifetime'] is not None:
        return
    prepared = await _row(db, 'lifecycle_launches', 'request_id', request_id)
    if not prepared or prepared['attachment_json']:
        return
    await db.execute("UPDATE terminal_sessions SET status='failed',error=?,stopped_at=?,updated_at=? WHERE id=? AND status='starting' AND process_id=''", (body['outcome'], now, now, prepared['terminal_id']))
    await db.execute("UPDATE agent_sessions SET status='failed',terminal_status='failed',ended_at=?,last_seen=? WHERE id=? AND status='starting' AND process_id=''", (now, now, prepared['session_id']))
