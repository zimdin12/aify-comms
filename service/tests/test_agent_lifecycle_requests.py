"""D9 service lifecycle receipts, driven through private HTTP and SQLite."""
import asyncio
import sqlite3
from fastapi import HTTPException
from service.api_core import agent_lifecycle_requests as lifecycle_core
from service.api_core.operator_authz import LifecycleProof
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, patch

from service.tests._base import FastApiTestCase
from service.tests.test_agent_definition_push import A, B, snapshot_digest, valid


class LifecycleRequests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        for host in (A, B):
            self.client.post('/api/v1/environments/heartbeat', json={
                'id': host['env'], 'machineId': host['machine'], 'os': 'win32',
                'kind': 'win32', 'bridgeId': host['bridge'], 'cwdRoots': [],
                'runtimes': [], 'metadata': {}}).raise_for_status()
        self.push('s1', 1, [valid('coder', incarnation=2, revision=5)])

    def push(self, store, revision, entries):
        self.client.put(f"/api/v1/environments/{A['env']}/agent-definitions", json={
            'bridgeId': A['bridge'], 'machineId': A['machine'], 'storeId': store,
            'revision': revision, 'snapshotDigest': snapshot_digest(entries), 'entries': entries}).raise_for_status()

    def ask(self, **body):
        return self.client.post('/api/v1/agents/coder/lifecycle-requests', json={
            'requestId': 'life-1', 'action': 'stop', 'requestedBy': 'peer',
            'expectedLifetime': 'worker-1', **body})

    def claim(self, host=A):
        return self.client.post(f"/api/v1/environments/{host['env']}/lifecycle-requests/claim",
            json={'bridgeId': host['bridge'], 'machineId': host['machine']})

    def report(self, host=A, **body):
        return self.client.post(f"/api/v1/environments/{host['env']}/lifecycle-requests/life-1/result",
            json={'bridgeId': host['bridge'], 'machineId': host['machine'],
                  'status': 'done', 'outcome': 'stopped', 'resultLifetime': None,
                  'finishedAt': '2026-10-07T00:00:00Z', **body})

    def sql(self, query, params=()):
        conn = sqlite3.connect(self._db_path)
        try:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute(query, params)]
            conn.commit()
            return rows
        finally:
            conn.close()

    def test_receipt_capture_unclaimed_visibility_and_replay_before_drift(self):
        first = self.ask()
        self.assertEqual(first.status_code, 200, first.text)
        receipt = first.json()['request']
        self.assertEqual([receipt[k] for k in ('id', 'machineId', 'storeId', 'expectedIncarnation', 'expectedRevision', 'expectedLifetime', 'status')],
            ['life-1', A['machine'], 's1', 2, 5, 'worker-1', 'pending'])
        self.push('s2', 1, [valid('coder', incarnation=3, revision=7)])
        self.assertEqual(self.ask().json()['request'], receipt)
        self.assertEqual(self.ask(action='kill').status_code, 409)
        self.assertEqual(self.client.get('/api/v1/agents/coder/lifecycle-requests').json()['requests'], [receipt])
        self.assertEqual(self.client.get('/api/v1/agent-lifecycle-requests/life-1').json()['request'], receipt)

    def test_explicit_lifetime_revision_and_bounded_id(self):
        missing = self.client.post('/api/v1/agents/coder/lifecycle-requests', json={
            'requestId': 'x', 'action': 'start', 'requestedBy': 'peer'})
        self.assertEqual(missing.status_code, 422, missing.text)
        for body in ({'expectedRevision': 4}, {'requestId': ''}, {'requestId': 'x'*201}, {'expectedLifetime': 42}):
            self.assertIn(self.ask(**body).status_code, (409, 422))
        self.assertEqual(self.ask(expectedLifetime=None).status_code, 200)

    def test_request_id_matches_host_grammar_without_normalization(self):
        for token in ('x'*129, ' x', 'x ', 'x/y', '.x', '-x', 'é', 'x\n'):
            with self.subTest(token=token):
                response = self.ask(requestId=token)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.sql('SELECT id FROM agent_lifecycle_requests'), [])
        token = 'A' + 'x'*124 + '._-'
        response = self.ask(requestId=token)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['request']['id'], token)

    def test_one_open_transactional_and_index_enforced(self):
        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda n: self.ask(requestId=f'life-{n}'), (1, 2)))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
        # A copy of the open request under a new id, whatever columns the table has.
        copied = [c['name'] for c in self.sql("PRAGMA table_info(agent_lifecycle_requests)") if c['name'] != 'id']
        self.assertIn('agent_id', copied, 'the column read found the table')
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql(f"INSERT INTO agent_lifecycle_requests (id, {', '.join(copied)}) "
                     f"SELECT 'duplicate', {', '.join(copied)} FROM agent_lifecycle_requests")

    def test_claim_and_report_fences_and_immutable_result(self):
        self.assertEqual(self.ask().status_code, 200)
        self.assertEqual(self.report().status_code, 409)
        self.assertEqual(self.claim(B).json()['requests'], [])
        self.assertEqual(self.claim({**A, 'bridge': 'old'}).status_code, 409)
        claimed = self.claim().json()['requests']
        self.assertEqual(claimed[0]['status'], 'claimed')
        self.sql("UPDATE agent_lifecycle_requests SET created_at='2000-01-01' WHERE id='life-1'")
        self.assertEqual(self.claim().json()['requests'][0]['id'], 'life-1')
        self.assertEqual(self.report(B).status_code, 409)
        self.assertEqual(self.report({**A, 'bridge': 'old'}).status_code, 409)
        first = self.report()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.report().json(), first.json())
        for changed in ({'outcome': 'other'}, {'finishedAt': 'other'}, {'resultLifetime': 'new'}, {'status': 'failed'}):
            self.assertEqual(self.report(**changed).status_code, 409)

    def test_pending_custody_drift_refuses_but_claimed_remains_reserved_for_late_report(self):
        self.ask().raise_for_status()
        self.claim().raise_for_status()
        self.push('s2', 1, [valid('coder', incarnation=3)])
        self.assertEqual(self.claim().json()['requests'], [])
        self.assertEqual(self.ask(requestId='other').status_code, 409)
        self.assertEqual(self.report(status='failed', outcome='execution unknown').status_code, 200)
        self.ask(requestId='other').raise_for_status()
        self.push('s3', 1, [valid('coder', incarnation=4)])
        self.assertEqual(self.claim().json()['requests'], [])
        rows = self.client.get('/api/v1/agents/coder/lifecycle-requests').json()['requests']
        self.assertEqual(next(r for r in rows if r['id']=='other')['status'], 'refused')

    def test_canonical_submission_requires_actor_before_queue_effects(self):
        missing = object()
        actors = [missing, None, False, True, 0, 1, 1.5, [], {}, '', ' \t\n ']
        for configured in ('', 'fixture-key'):
            self._app.state.config.operator_key = configured
            for index, actor in enumerate(actors):
                with self.subTest(configured=bool(configured), actor='missing' if actor is missing else repr(actor)):
                    self.sql('DELETE FROM agent_lifecycle_requests')
                    body = {'requestId': f'actor-{index}', 'action': 'start', 'expectedLifetime': None}
                    if actor is not missing:
                        body['requestedBy'] = actor
                    response = self.client.post('/api/v1/agents/coder/lifecycle-requests',
                        headers={'X-Aify-Operator-Key': configured}, json=body)
                    rows = self.sql('SELECT id,requested_by,status FROM agent_lifecycle_requests')
                    self.assertEqual({'status': response.status_code, 'rows': rows}, {'status': 422, 'rows': []})
                    self.assertEqual(self.sql('SELECT id FROM definition_requests'), [])
                    self.assertEqual(self.sql('SELECT id FROM terminal_sessions'), [])
                    self.assertEqual(self.sql('SELECT id FROM terminal_controls'), [])
                    self.assertEqual(self.sql('SELECT id FROM agents WHERE id=\'coder\''), [{'id': 'coder'}])
                    self.assertEqual(self.sql('SELECT incarnation,revision FROM agent_definitions WHERE agent_id=\'coder\''), [{'incarnation': 2, 'revision': 5}])

    def test_core_binds_actor_proof_before_database_use(self):
        attempted = []
        class PoisonDb:
            async def execute(self, query, *args):
                attempted.append(query)
                raise RuntimeError('database must not be reached by a mismatched actor proof')
        observed = None
        try:
            asyncio.run(lifecycle_core.admit_lifecycle(PoisonDb(), 'coder',
                {'requestId': 'proof-mismatch', 'action': 'start', 'expectedLifetime': None, 'requestedBy': 'peer'},
                LifecycleProof('different'), 'fixture-time'))
        except Exception as error:
            observed = error
        self.assertEqual(attempted, [], repr(observed))
        self.assertIsInstance(observed, HTTPException)
        self.assertEqual(observed.status_code, 403)

    def test_valid_caller_actor_is_preserved_in_receipt_and_replay(self):
        first = self.ask(requestedBy='  peer  ')
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()['request']['requestedBy'], '  peer  ')
        self.assertEqual(self.ask(requestedBy='  peer  ').json(), first.json())
        self.assertEqual(self.ask(requestedBy='peer').status_code, 409)
        self.assertEqual(self.sql('SELECT requested_by FROM agent_lifecycle_requests'), [{'requested_by': '  peer  '}])

    def test_the_operator_key_locks_only_what_cannot_be_undone(self):
        """Steven, 2026-10-11: the operator key is an optional lock on irreversible actions. With one set, the
        API key may still start, stop, restart and spawn a defined agent as itself; kill and delete, and any
        word outside the vocabulary, need the key; and naming the operator needs its proof whatever the
        action. Each action is asked on its own request id, so the one open request per agent decides nothing."""
        self._app.state.config.operator_key = 'fixture-key'
        for action in ('start','stop','restart','spawn','kill','delete','pause'):
            with self.subTest(action=action):
                self.sql('DELETE FROM agent_lifecycle_requests')
                answered = self.ask(action=action, requestId=f'peer-{action}', requestedBy='peer')
                if action in ('start','stop','restart','spawn'):
                    self.assertEqual(answered.status_code, 200, answered.text)
                else:
                    self.refused(answered, 403, 'kill, delete and any unknown lifecycle action require a valid '
                                                'X-Aify-Operator-Key header')
                self.assertEqual(self.ask(action=action, requestId=f'op-{action}', requestedBy='operator').status_code,
                                 403, 'naming the operator needs its proof whatever the action')
        self.sql('DELETE FROM agent_lifecycle_requests')
        accepted = self.client.post('/api/v1/agents/coder/lifecycle-requests',
            headers={'X-Aify-Operator-Key':'fixture-key'}, json={
                'requestId':'life-1', 'action':'start', 'expectedLifetime':None, 'requestedBy':'peer'})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        edit = self.client.post('/api/v1/agent-definitions/coder/requests',
            headers={'X-Aify-Operator-Key':'fixture-key'}, json={'patch':{'role':'other'}, 'requestedBy':'peer'})
        self.assertEqual(edit.status_code, 403)

    def test_delete_and_spawn_are_queued_like_any_action(self):
        for action in ('delete', 'spawn'):
            self.sql('DELETE FROM agent_lifecycle_requests')
            response = self.ask(action=action, requestId=f'{action}-1')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['request']['status'], 'pending', 'queued is not done')
        self.assertEqual(self.sql('SELECT id FROM definition_requests'), [])

    def deleted(self, **report):
        self.ask(action='delete', expectedLifetime=None).raise_for_status()
        self.claim().raise_for_status()
        return self.report(outcome='delete', **report)

    def test_a_done_delete_removes_the_agent_and_signals_no_stop(self):
        self.sql("UPDATE agents SET session_mode='managed' WHERE id='coder'")
        # A signalled stop leaves no row to inspect (the delete cascades it away), so watch the signaller.
        with patch('service.api_core.agent_remove._request_stop_agent_terminals', new=AsyncMock(return_value=0)) as stop:
            first = self.deleted(resultIncarnation=2, resultRevision=5)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()['removal'], '')
        self.assertEqual(self.sql("SELECT id FROM agents WHERE id='coder'"), [])
        self.assertEqual([r['agent_id'] for r in self.sql('SELECT agent_id FROM agent_tombstones')], ['coder'])
        stop.assert_not_called()
        again = self.report(outcome='delete', resultIncarnation=2, resultRevision=5)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()['removal'], '', 'a repeated report re-runs the removal harmlessly')

    def test_a_done_delete_removes_nothing_once_the_id_is_defined_again(self):
        self.ask(action='delete', expectedLifetime=None).raise_for_status()
        self.claim().raise_for_status()
        self.push('s1', 2, [valid('coder', incarnation=3, revision=1)])
        response = self.report(outcome='delete', resultIncarnation=2, resultRevision=5)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('not the one this removal was for', response.json()['removal'])
        self.assertEqual(self.sql("SELECT id FROM agents WHERE id='coder'"), [{'id': 'coder'}])

    def test_refused_failed_are_durable_and_changed_store_cannot_claim(self):
        for status in ('refused','failed'):
            self.ask(requestId='life-1' if status=='refused' else 'second').raise_for_status()
            self.claim().raise_for_status()
            if status=='refused':
                self.report(status=status, outcome='identity unknown').raise_for_status()
        self.push('s2', 1, [valid('coder')])
        self.assertEqual(self.claim().json()['requests'], [])
        self.assertEqual(self.client.get('/api/v1/agent-lifecycle-requests/life-1').json()['request']['status'], 'refused')

    def refused(self, response, code, text):
        self.assertEqual(response.status_code, code, response.text)
        self.assertIn(text, response.json()['detail'])

    def test_each_refusal_names_its_cause(self):
        # Submission, in the order admit checks it.
        self._app.state.config.operator_key = 'fixture-key'
        self.refused(self.ask(action='kill'), 403, 'kill, delete and any unknown lifecycle action require a valid')
        self._app.state.config.operator_key = ''
        self.refused(self.ask(requestedBy=''), 422, 'requestedBy: explicitly name a nonempty caller actor string')
        self.refused(self.ask(requestId='-x'), 422,
                     'requestId: 1 to 128 ASCII letters, digits, dot, underscore or hyphen')
        self.refused(self.ask(action='pause'), 422, 'action: start, stop, restart, kill, spawn or delete')
        self.refused(self.ask(expectedLifetime=42), 422, 'expectedLifetime: explicitly name a lifetime string or null')
        self.refused(self.ask(freshContext='yes'), 422, 'freshContext: a boolean')
        self.refused(self.ask(expectedRevision='5'), 422, 'expectedRevision: a definition revision counter')
        self.refused(self.client.post('/api/v1/agents/nobody/lifecycle-requests', json={
            'requestId': 'life-1', 'action': 'stop', 'requestedBy': 'peer', 'expectedLifetime': None}),
            404, 'agent has no owning definition')
        self.refused(self.ask(expectedRevision=4), 409, 'definition revision moved')
        self.ask().raise_for_status()
        self.refused(self.ask(action='kill'), 409, 'requestId already records a different intent')
        self.refused(self.ask(requestId='life-2'), 409, 'agent has an open lifecycle request life-1')
        self.refused(self.client.get('/api/v1/agent-lifecycle-requests/nothing'), 404, 'no lifecycle request')
        # Claim and report.
        self.refused(self.report(), 409, 'lifecycle request was never claimed')
        self.refused(self.claim({**A, 'bridge': 'old'}), 409, 'lifecycle claim refused: ')
        self.claim().raise_for_status()
        self.refused(self.report({**A, 'bridge': 'old'}), 409, 'lifecycle report refused: ')
        self.refused(self.report(status='pending'), 422,
                     'status, outcome, resultLifetime and finishedAt: explicit terminal result required')
        self.refused(self.client.post(f"/api/v1/environments/{A['env']}/lifecycle-requests/nothing/result", json={
            'bridgeId': A['bridge'], 'machineId': A['machine'], 'status': 'done', 'outcome': 'stopped',
            'resultLifetime': None, 'finishedAt': '2026-10-07T00:00:00Z'}), 404, 'no lifecycle request')
        self.refused(self.report(B), 409, 'lifecycle request belongs to another machine')
        self.report().raise_for_status()
        self.refused(self.report(outcome='other'), 409, 'terminal lifecycle result is immutable')

    def test_a_done_delete_must_carry_its_removal_receipt(self):
        self.ask(action='delete', expectedLifetime=None).raise_for_status()
        self.claim().raise_for_status()
        for receipt in ({}, {'resultIncarnation': 2}, {'resultIncarnation': 0, 'resultRevision': 5}):
            self.refused(self.report(outcome='delete', **receipt), 422,
                         'a done delete reports resultIncarnation and resultRevision from its removal')
        self.assertEqual(self.sql("SELECT id FROM agents WHERE id='coder'"), [{'id': 'coder'}])

    def test_core_requires_a_lifecycle_proof(self):
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(lifecycle_core.admit_lifecycle(None, 'coder', {'requestedBy': 'peer'}, None, 'fixture-time'))
        self.assertEqual((caught.exception.status_code, caught.exception.detail), (403, 'lifecycle proof required'))
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(lifecycle_core.admit_lifecycle(None, 'coder', {'requestedBy': 'peer'},
                                                       LifecycleProof('different'), 'fixture-time'))
        self.assertEqual(caught.exception.detail, 'lifecycle proof does not match caller actor')
