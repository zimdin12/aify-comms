"""The env-owned sender examples reach actual comms admission, not a second fixture list."""
import copy
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from service.tests._base import FastApiTestCase

# Byte copies of aify-env c695b52a3f1a3601a7ac8b22641b9299cea126cc, docs/agent-state/.
_FIXTURE_ROOT = Path(__file__).parent
_SCHEMA = 'data/agent-state-env-c695b52a/schema.json'
_PUBLICATIONS = 'data/agent-state-env-c695b52a/publications.json'
_DIGESTS = {
    _SCHEMA: 'aa086ed2ecbadea0cca6e5e55d15752a4654e389c19683513b399cdfd249f7b4',
    _PUBLICATIONS: 'd6eadaa92c5b1d0e34c75e297af74e68cc69761b551c3f87907b912369eaac41',
}


def _checked_fixture(root, name, expected):
    raw = (root / name).read_bytes()
    if sha256(raw).hexdigest() != expected:
        raise ValueError(f'{name}: SHA-256 mismatch')
    return raw


class EnvPublicationsReachTheReceiver(FastApiTestCase):
    def publications(self, root=_FIXTURE_ROOT):
        fixtures = {name: _checked_fixture(root, name, digest) for name, digest in _DIGESTS.items()}
        examples = json.loads(fixtures[_PUBLICATIONS])
        self.assertGreaterEqual(len(examples), 20, 'the producer population must not disappear')
        self.assertEqual(len({entry['name'] for entry in examples}), len(examples))
        self.assertEqual({entry['body']['kind'] for entry in examples},
                         {'snapshot', 'changes', 'unavailable', 'retired'})
        return examples

    def test_mismatched_producer_artifacts_are_refused(self):
        original = {name: (_FIXTURE_ROOT / name).read_bytes() for name in _DIGESTS}
        for name, raw in original.items():
            with self.subTest(artifact=name), TemporaryDirectory() as directory:
                root = Path(directory)
                (root / name).parent.mkdir(parents=True)
                for file, content in original.items():
                    (root / file).write_bytes(content)
                self.assertEqual(self.publications(root), json.loads(original[_PUBLICATIONS]))
                changed = json.loads(raw)
                (changed[0] if isinstance(changed, list) else changed)['pinControl'] = True
                (root / name).write_text(json.dumps(changed), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch') as refused:
                    self.publications(root)
                self.assertIn(name, str(refused.exception))

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
