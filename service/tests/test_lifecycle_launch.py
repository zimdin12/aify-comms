"""Queue-free lifecycle preparation and attachment through private HTTP/SQLite."""
import json
import sqlite3
from unittest.mock import patch

from service.tests._base import FastApiTestCase
from service.tests.test_agent_definition_push import A, valid, snapshot_digest


class LifecycleLaunch(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.client.post('/api/v1/environments/heartbeat', json={
            'id': A['env'], 'machineId': A['machine'], 'os': 'win32', 'kind': 'win32',
            'bridgeId': A['bridge'], 'cwdRoots': ['/work'], 'runtimes': [],
            'metadata': {'terminal': True, 'pty': True, 'terminalRuntimes': ['claude-code', 'pi']}}).raise_for_status()
        self.push()

    def sql(self, query, params=()):
        db = sqlite3.connect(self._db_path)
        try:
            db.row_factory = sqlite3.Row
            rows = [dict(r) for r in db.execute(query, params)]
            db.commit()
            return rows
        finally:
            db.close()

    def push(self, **over):
        entries = [valid('coder', incarnation=2, revision=5, model='chosen', effort='high', env={'CUSTOM': 'value'}, **over)]
        self.client.put(f"/api/v1/environments/{A['env']}/agent-definitions", json={
            'bridgeId': A['bridge'], 'machineId': A['machine'], 'storeId': 's1',
            'revision': 1, 'snapshotDigest': snapshot_digest(entries), 'entries': entries}).raise_for_status()

    def queue(self, claim=True, **over):
        self.client.post('/api/v1/agents/coder/lifecycle-requests', json={
            'requestId': 'launch-1', 'action': 'start', 'requestedBy': 'peer',
            'expectedLifetime': None, **over}).raise_for_status()
        if claim:
            self.client.post(f"/api/v1/environments/{A['env']}/lifecycle-requests/claim",
                json={'bridgeId': A['bridge'], 'machineId': A['machine']}).raise_for_status()

    def post(self, suffix, **over):
        return self.client.post(f"/api/v1/environments/{A['env']}/lifecycle-requests/launch-1/{suffix}",
            json={'bridgeId': A['bridge'], 'machineId': A['machine'], **over})

    def prepare(self):
        response = self.post('launch')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['launch']

    def queues(self):
        return {t: self.sql(f'SELECT * FROM {t}') for t in ('spawn_requests', 'terminal_controls', 'messages', 'dispatch_runs')}

    def test_preparation_is_persistent_queue_free_definition_bound_and_not_running(self):
        self.queue(action='restart', expectedLifetime='old-lifetime')
        before = self.queues()
        launch = self.prepare()
        self.assertEqual(self.prepare(), launch)
        self.assertEqual(launch['agentId'], 'coder')
        self.assertEqual(launch['definition'], {'storeId': 's1', 'incarnation': 2, 'revision': 5})
        self.assertEqual(launch['env']['AIFY_MANAGED_MODEL'], 'chosen')
        self.assertEqual(launch['env']['AIFY_MANAGED_EFFORT'], 'high')
        self.assertEqual(launch['env']['CUSTOM'], 'value')
        self.assertTrue(launch['argv'])
        self.assertEqual(before, self.queues())
        self.assertEqual(len(self.sql('SELECT * FROM spawn_specs')), 1)
        self.assertEqual(len(self.sql('SELECT * FROM agent_sessions')), 1)
        self.assertEqual(self.sql('SELECT status,process_id FROM agent_sessions'), [{'status': 'starting', 'process_id': ''}])
        self.assertEqual(self.sql('SELECT status FROM agent_lifecycle_requests'), [{'status': 'claimed'}])

    def test_no_claim_wrong_host_and_definition_drift_refuse_before_writes_or_replay(self):
        self.queue(claim=False)
        self.assertEqual(self.post('launch').status_code, 409)
        self.assertEqual(self.sql('SELECT * FROM agent_sessions'), [])
        self.client.post(f"/api/v1/environments/{A['env']}/lifecycle-requests/claim",
            json={'bridgeId': A['bridge'], 'machineId': A['machine']}).raise_for_status()
        for body in ({'bridgeId': 'stale'}, {'machineId': 'foreign'}):
            self.assertEqual(self.post('launch', **body).status_code, 409)
        launch = self.prepare()
        self.sql("UPDATE agent_definitions SET revision=6 WHERE agent_id='coder'")
        self.assertEqual(self.post('launch').status_code, 409)
        self.assertEqual(self.sql('SELECT id FROM terminal_sessions'), [{'id': launch['terminalId']}])

    def test_fresh_context_clears_native_handle_before_argv_and_env(self):
        self.sql("UPDATE agents SET session_handle='old-native', runtime_state='{}' WHERE id='coder'")
        self.queue(freshContext=True)
        launch = self.prepare()
        self.assertNotIn('old-native', launch['argv'])
        self.assertEqual(launch['sessionHandle'], '')
        self.assertEqual(launch['env']['AIFY_SESSION_HANDLE'], '')
        self.assertEqual(launch['env']['AIFY_HERMES_FRESH_CONTEXT'], '1')
        self.assertNotIn('old-native', launch['env'].values())

    def test_native_resume_handle_is_not_service_session_id(self):
        self.sql("UPDATE agents SET session_handle='old-native' WHERE id='coder'")
        self.queue()
        launch = self.prepare()
        self.assertEqual(launch['sessionHandle'], 'old-native')
        self.assertIn('old-native', launch['argv'])
        self.assertNotEqual(self.sql('SELECT id FROM agent_sessions')[0]['id'], launch['sessionHandle'])

    def test_unsupported_definition_harness_refuses_before_native_handle_guard(self):
        self.sql("UPDATE agent_definitions SET body=json_set(body,'$.harness','pi') WHERE agent_id='coder'")
        self.queue(freshContext=True)
        response = self.post('launch')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail'], 'definition runtime or native prerequisites are unsupported or unreadable')
        self.assertEqual(self.sql('SELECT * FROM agent_sessions'), [])
        self.assertEqual(self.sql('SELECT * FROM spawn_specs'), [])

    def test_pi_handle_guard_seam_after_injected_typed_runtime_binding(self):
        # Pi is not an admitted definition harness. This isolates the later existing guard,
        # without claiming a supported ordinary Pi definition path or a native Pi process.
        from dataclasses import replace
        from service.api_core import lifecycle_launch
        actual_binding = lifecycle_launch.binding_for
        def pi_binding(*args):
            return replace(actual_binding(*args), runtime='pi')
        self.sql("UPDATE agents SET runtime='pi',session_handle='' WHERE id='coder'")
        self.queue()
        before = self.queues()
        with patch('service.api_core.lifecycle_launch.binding_for', side_effect=pi_binding):
            response = self.post('launch')
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()['detail'], 'pi launch requires an actual native session handle')
            for table in ('spawn_specs', 'agent_sessions', 'terminal_sessions', 'lifecycle_launches'):
                self.assertEqual(self.sql(f'SELECT * FROM {table}'), [])
            self.assertEqual(before, self.queues())
            self.sql("UPDATE agents SET session_handle='C:/fixture/pi-session.json' WHERE id='coder'")
            launch = self.prepare()
            self.assertEqual(launch['runtime'], 'pi')
            self.assertEqual(launch['sessionHandle'], 'C:/fixture/pi-session.json')
            self.assertEqual(len(self.sql('SELECT * FROM lifecycle_launches')), 1)
            self.assertEqual(len(self.sql('SELECT * FROM agent_sessions')), 1)
            self.assertEqual(before, self.queues())

    def test_attachment_is_positive_exact_immutable_and_does_not_complete_request(self):
        self.queue()
        launch = self.prepare()
        body = dict(terminalId=launch['terminalId'], handle='runner-1', processId=4321, lifetime='birth-1', cols=100, rows=30)
        for change in ({'terminalId': 'foreign'}, {'processId': 0}, {'processId': True}, {'processId': '4321'}, {'handle': ''}, {'lifetime': ''}, {'rows': 0}):
            self.assertIn(self.post('attachment', **(body | change)).status_code, (409, 422))
        self.assertEqual(self.sql('SELECT process_id FROM terminal_sessions'), [{'process_id': ''}])
        first = self.post('attachment', **body)
        self.assertEqual(first.status_code, 200, first.text)
        receipt = first.json()['attachment']
        self.assertEqual(receipt['requestId'], 'launch-1')
        self.assertEqual(receipt['lifetime'], 'birth-1')
        self.assertEqual(self.post('attachment', **body).json(), first.json())
        for field, value in [('handle', 'runner-2'), ('processId', 999), ('lifetime', 'birth-2'), ('cols', 101)]:
            self.assertEqual(self.post('attachment', **(body | {field: value})).status_code, 409)
        self.assertEqual(self.sql('SELECT status FROM terminal_sessions'), [{'status': 'attached'}])
        self.assertEqual(self.sql('SELECT status FROM agent_sessions'), [{'status': 'running'}])
        self.assertEqual(self.sql('SELECT status FROM agent_lifecycle_requests'), [{'status': 'claimed'}])
        self.sql("UPDATE terminal_sessions SET status='ended'")
        self.assertEqual(self.post('attachment', **body).json(), first.json())
        self.assertEqual(self.sql('SELECT status FROM terminal_sessions'), [{'status': 'ended'}])
        self.assertEqual(self.post('attachment', **(body | {'bridgeId': 'stale'})).status_code, 409)

    def test_composition_failure_rolls_back_all_prepared_rows(self):
        self.queue()
        from service.api_core import lifecycle_launch
        insert = lifecycle_launch._insert
        async def fail_after_write(db, table, values):
            await insert(db, table, values)
            if table == 'agent_sessions':
                raise RuntimeError('injected after tracking write')
        with patch('service.api_core.lifecycle_launch._insert', side_effect=fail_after_write):
            self.assertEqual(self.post('launch').status_code, 500)
        for table in ('spawn_specs', 'agent_sessions', 'terminal_sessions', 'lifecycle_launches'):
            self.assertEqual(self.sql(f'SELECT * FROM {table}'), [])

    def test_revision_moved_host_refusal_settles_only_exact_unstarted_tracking(self):
        self.queue()
        self.prepare()
        for table in ('agent_sessions', 'terminal_sessions'):
            row = self.sql(f'SELECT * FROM {table}')[0] | {'id': 'unrelated'}
            self.sql(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
        response = self.post('result', status='refused', outcome='revision-moved', resultLifetime=None, finishedAt='2026-10-07T00:00:00Z')
        self.assertEqual(response.status_code, 200, response.text)
        for table in ('agent_sessions', 'terminal_sessions'):
            self.assertEqual(self.sql(f"SELECT status FROM {table} WHERE id!='unrelated'"), [{'status': 'failed'}])
            self.assertEqual(self.sql(f"SELECT status FROM {table} WHERE id='unrelated'"), [{'status': 'starting'}])
        self.assertEqual(self.sql('SELECT * FROM terminal_controls'), [])

    def test_completed_attachment_replay_is_immutable_and_current_fenced(self):
        self.queue()
        launch = self.prepare()
        body = dict(terminalId=launch['terminalId'], handle='runner-1', processId=4321, lifetime='birth-1')
        first = self.post('attachment', **body)
        first.raise_for_status()
        self.post('result', status='done', outcome='start', resultLifetime='birth-1', finishedAt='2026-10-07T00:00:00Z').raise_for_status()
        before = {t: self.sql(f'SELECT * FROM {t}') for t in ('lifecycle_launches', 'agent_sessions', 'terminal_sessions', 'agent_lifecycle_requests')}
        replay = self.post('attachment', **body)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json(), first.json())
        for change in ({'handle': 'changed'}, {'processId': 999}, {'lifetime': 'other'}, {'bridgeId': 'stale'}, {'machineId': 'foreign'}, {'terminalId': 'foreign'}):
            self.assertEqual(self.post('attachment', **(body | change)).status_code, 409)
        self.sql("UPDATE agent_definitions SET revision=6 WHERE agent_id='coder'")
        self.assertEqual(self.post('attachment', **body).status_code, 409)
        self.sql("UPDATE agent_definitions SET revision=5 WHERE agent_id='coder'")
        self.sql("UPDATE definition_stores SET store_id='other'")
        self.assertEqual(self.post('attachment', **body).status_code, 409)
        self.sql("UPDATE definition_stores SET store_id='s1'")
        self.assertEqual(before, {t: self.sql(f'SELECT * FROM {t}') for t in before})

    def test_first_attachment_after_closed_request_is_refused(self):
        self.queue()
        launch = self.prepare()
        body = dict(terminalId=launch['terminalId'], handle='runner-1', processId=4321, lifetime='birth-1')
        self.post('result', status='done', outcome='already-running', resultLifetime='existing', finishedAt='2026-10-07T00:00:00Z').raise_for_status()
        self.assertEqual(self.post('attachment', **body).status_code, 409)
        self.assertEqual(self.sql('SELECT attachment_json FROM lifecycle_launches'), [{'attachment_json': None}])
        self.assertEqual(self.sql('SELECT status,process_id FROM terminal_sessions'), [{'status': 'starting', 'process_id': ''}])

    def test_refusal_never_settles_attached_tracking(self):
        self.queue()
        launch = self.prepare()
        self.post('attachment', terminalId=launch['terminalId'], handle='runner-1', processId=4321, lifetime='birth-1').raise_for_status()
        self.post('result', status='refused', outcome='revision-moved', resultLifetime=None, finishedAt='2026-10-07T00:00:00Z').raise_for_status()
        self.assertEqual(self.sql('SELECT status,process_id FROM terminal_sessions'), [{'status': 'attached', 'process_id': '4321'}])
        self.assertEqual(self.sql('SELECT status,process_id FROM agent_sessions'), [{'status': 'running', 'process_id': '4321'}])

    def test_unknown_execution_does_not_settle_prepared_tracking_as_dead(self):
        self.queue()
        self.prepare()
        self.post('result', status='failed', outcome='execution-unknown', resultLifetime=None, finishedAt='2026-10-07T00:00:00Z').raise_for_status()
        self.assertEqual(self.sql('SELECT status FROM agent_sessions'), [{'status': 'starting'}])
        self.assertEqual(self.sql('SELECT status FROM terminal_sessions'), [{'status': 'starting'}])

    def refused(self, response, code, text):
        self.assertEqual(response.status_code, code, response.text)
        self.assertIn(text, response.json()['detail'])

    def test_each_refusal_names_its_cause(self):
        self.refused(self.post('launch'), 404, 'no lifecycle request')
        self.queue(claim=False)
        self.refused(self.post('launch'), 409, 'launch requires a claimed start, restart or spawn')
        self.client.post(f"/api/v1/environments/{A['env']}/lifecycle-requests/claim",
            json={'bridgeId': A['bridge'], 'machineId': A['machine']}).raise_for_status()
        self.sql("UPDATE agent_definitions SET revision=6 WHERE agent_id='coder'")
        self.refused(self.post('launch'), 409, 'lifecycle definition ownership moved')
        self.sql("UPDATE agent_definitions SET revision=5 WHERE agent_id='coder'")
        self.refused(self.post('attachment', terminalId='t'), 409, 'attachment requires the exact prepared terminal')
        launch = self.prepare()
        body = dict(terminalId=launch['terminalId'], handle='runner-1', processId=4321, lifetime='birth-1')
        self.refused(self.post('attachment', **(body | {'handle': ''})), 422, 'handle: nonempty actual runner value required')
        self.refused(self.post('attachment', **(body | {'processId': 0})), 422, 'processId: positive integer required')
        self.sql("UPDATE terminal_sessions SET status='ended'")
        self.refused(self.post('attachment', **body), 409, 'prepared terminal has already ended')
        self.sql("UPDATE terminal_sessions SET status='starting'")
        self.post('attachment', **body).raise_for_status()
        self.refused(self.post('attachment', **(body | {'handle': 'runner-2'})), 409, 'lifecycle attachment receipt is immutable')

    def test_spawn_prepares_a_launch_of_the_existing_definition(self):
        self.queue(action='spawn')
        launch = self.prepare()
        self.assertEqual(launch['definition'], {'storeId': 's1', 'incarnation': 2, 'revision': 5})
        self.assertEqual(self.sql('SELECT status FROM agent_lifecycle_requests'), [{'status': 'claimed'}])

    def test_preparation_refuses_an_environment_that_cannot_run_the_runtime(self):
        self.queue()
        self.sql("UPDATE environments SET metadata=json_set(metadata,'$.terminal',json('false'))")
        self.refused(self.post('launch'), 409, 'environment cannot run this terminal runtime')

    def test_a_prepared_launch_stays_with_the_bridge_that_prepared_it(self):
        self.queue()
        self.prepare()
        self.sql("UPDATE lifecycle_launches SET bridge_id='earlier-bridge'")
        self.refused(self.post('launch'), 409, 'prepared launch belongs to another environment or bridge')
