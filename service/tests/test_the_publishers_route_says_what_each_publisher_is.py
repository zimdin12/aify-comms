"""`GET /api/v1/agent-state/publishers`: each aify-env publisher the mirror holds and its availability now.

The `agent-state-publishers` doctor row reads it (0.9.9 S1, replacing the comparison's `agent-state-shadow` row), so
what it lists must be what the operational view builds every word from: the mirror, judged by the view's own rule.
"""
from service.routers.api_v2 import router  # noqa: F401 -- the base builds the app from it
from service.tests import env_records
from service.tests._base import FastApiTestCase

AGENT = 'listed-agent'


class ThePublishersRouteSaysWhatEachPublisherIs(FastApiTestCase):
    DB_NAME = 'aify-publishers-route.db'

    def setUp(self):
        super().setUp()
        env_records.reset_mirror()
        response = self.client.post('/api/v1/agents', json={'agentId': AGENT, 'role': 'coder', 'runtime': 'claude-code'})
        self.assertEqual(response.status_code, 200, response.text)

    def publishers(self):
        response = self.client.get('/api/v1/agent-state/publishers')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_an_empty_mirror_lists_nothing(self):
        self.assertEqual(self.publishers(), {'readable': True, 'publishers': []})

    def test_a_fresh_snapshot_is_listed_fresh_with_its_agents_then_unavailable_when_it_says_so(self):
        env_records.report(self, AGENT)
        self.assertEqual(self.publishers()['publishers'], [dict(machineId=env_records.TEST_HOST,
            instance=env_records.INSTANCE, availability='fresh', agents=1)])
        env_records.publish_state(self, env_records.TEST_HOST, env_records.INSTANCE, 'unavailable', 'state-unavailable')
        self.assertEqual([p['availability'] for p in self.publishers()['publishers']], ['unavailable'])

    def test_a_lost_mirror_is_unreadable_not_empty(self):
        from service.api_core import agent_state_mirror as state_mirror
        original = state_mirror.mirror
        self.addCleanup(setattr, state_mirror, 'mirror', original)
        state_mirror.mirror = state_mirror._UnreadableMirror()
        self.assertEqual(self.publishers(), {'readable': False, 'publishers': []})
