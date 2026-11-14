"""The env-owned sender examples reach actual comms admission, not a second fixture list."""
import copy
from contextlib import closing
import json
import sqlite3

from service.tests._base import FastApiTestCase
from service.tests.test_the_env_plugin_addresses_routes_this_service_serves import env_repo


class EnvPublicationsReachTheReceiver(FastApiTestCase):
    def publications(self):
        repo, why = env_repo()
        self.assertIsNotNone(repo, why)
        source = repo / 'docs' / 'agent-state' / 'publications.json'
        self.assertTrue(source.is_file(), f'{source}: select the published env contract with AIFY_ENV_REPO')
        examples = json.loads(source.read_text(encoding='utf-8'))
        self.assertGreaterEqual(len(examples), 20, 'the producer population must not disappear')
        self.assertEqual(len({entry['name'] for entry in examples}), len(examples))
        self.assertEqual({entry['body']['kind'] for entry in examples},
                         {'snapshot', 'changes', 'unavailable', 'retired'})
        return examples

    def post(self, body):
        return self.client.post('/api/v1/agent-state', json=body)

    def stored(self):
        with closing(sqlite3.connect(self._db_path)) as db:
            db.row_factory = sqlite3.Row
            return ([dict(r) for r in db.execute('SELECT * FROM agent_state_publishers ORDER BY machine_id,instance')],
                    [dict(r) for r in db.execute('SELECT * FROM agent_state_rows ORDER BY machine_id,instance,agent_id')])

    def test_every_sender_body_is_accepted_with_its_cursor_and_row_effect(self):
        expected = {}
        for example in self.publications():
            with self.subTest(publication=example['name']):
                body = example['body']
                key = (body['machineId'], body['instance'])
                rows = expected.setdefault(key, {})
                if body['kind'] in ('snapshot', 'retired'):
                    rows.clear()
                for removed in body.get('removed', []):
                    held = rows.get(removed['agentId'])
                    if held and held['lifetime'] == removed['lifetime']:
                        del rows[removed['agentId']]
                rows.update((row['agentId'], row) for row in body.get('agents', []))
                answer = self.post(body)
                self.assertEqual((answer.status_code, answer.content), (204, b''), answer.text)
                publishers, stored = self.stored()
                cursor = next(p for p in publishers if (p['machine_id'], p['instance']) == key)
                self.assertEqual(tuple(cursor[k] for k in ('generation', 'incarnation_id', 'publication', 'kind', 'reason')),
                                 (body['generation'], body['incarnationId'], body['publication'], body['kind'], body.get('reason')))
                self.assertEqual(json.loads(cursor['inputs']), body['inputs'])
                self.assertTrue(all(r['lifetime'] == json.loads(r['record_json'])['lifetime'] for r in stored))
                actual = {(r['machine_id'], r['instance'], r['agent_id']): json.loads(r['record_json']) for r in stored}
                self.assertEqual(actual, {(machine, instance, agent): row
                    for (machine, instance), entries in expected.items() for agent, row in entries.items()})

    def test_wrong_body_and_operator_attested_retirement_refuse_without_writes(self):
        examples = self.publications()
        snapshot = next(entry['body'] for entry in examples if entry['body']['kind'] == 'snapshot')
        retired = next(entry['body'] for entry in examples if entry['body']['kind'] == 'retired')
        self.assertEqual(self.post(snapshot).status_code, 204)
        before = self.stored()
        wrong = copy.deepcopy(snapshot)
        wrong['kind'] = 'not-a-publication'
        attested = copy.deepcopy(retired)
        attested['reason'] = 'operator-attested'
        for body in (wrong, attested):
            with self.subTest(kind=body['kind'], reason=body.get('reason')):
                answer = self.post(body)
                self.assertEqual((answer.status_code, self.stored()), (422, before), answer.text)
        self.assertEqual(self.post(retired).status_code, 204, 'the unchanged shutdown body is the positive control')
        self.assertEqual(self.stored()[1], [])
