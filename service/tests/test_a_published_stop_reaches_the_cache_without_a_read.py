"""A word aify-env publishes is re-derived when the publication lands, so the dashboard hears it then.

THE OPERATOR, 2026-10-25: "when i kill/stop agent then i see that agent be in old status for some time". The ingest
expired the cached words of the publisher's machine and left them for the next read; the cache announces a moved word
only when it re-derives one (`_live_state_set`), so a stop waited out the dashboard's poll. Now the ingest re-derives
the words it expired, and the cache's own announcement carries the stop to the dashboard.
"""
from service.change_feed import CHANGE_FEED
from service.reconcilers import status_cache
from service.routers.api_v2 import router  # noqa: F401 -- the base builds the app from it
from service.tests import env_records
from service.tests._base import FastApiTestCase

AGENT = 'stopped-by-hand'


def stopped(agent_id):
    """aify-env's record for an agent its operator stopped: deriveAgentState's operator-stop row, no process."""
    return dict(agentId=agent_id, lifetime=None, state='stopped', stateCause='operator-stop', busy=False,
                process=dict(state='none', verified='unknown', pid=None), turn=None)


class APublishedStopReachesTheCacheWithoutARead(FastApiTestCase):
    DB_NAME = 'aify-published-stop.db'

    def setUp(self):
        super().setUp()
        env_records.reset_mirror()
        response = self.client.post('/api/v1/agents', json={
            'agentId': AGENT, 'role': 'coder', 'runtime': 'claude-code', 'sessionMode': 'managed'})
        self.assertEqual(response.status_code, 200, response.text)
        env_records.report(self, AGENT)
        self.assertEqual(self.client.get(f'/api/v1/agents/{AGENT}').json()['agent']['status'], 'online')
        self.announced = []
        original = CHANGE_FEED.agent_status_moved
        CHANGE_FEED.agent_status_moved = lambda agent_id: (self.announced.append(agent_id), original(agent_id))
        self.addCleanup(setattr, CHANGE_FEED, 'agent_status_moved', original)

    def cached(self):
        return (status_cache._live_state_get(AGENT) or {}).get('status')

    def test_a_published_stop_is_the_cached_word_and_announced_before_anything_reads_it(self):
        self.assertEqual(self.cached(), 'online', 'control: the read above cached the running word')
        env_records.report(self, AGENT, records={AGENT: stopped(AGENT)})
        self.assertEqual(self.cached(), 'stopped', 'the publication itself re-derived the word')
        self.assertIn(AGENT, self.announced, 'the moved word was announced, which is what the dashboard hears')

    def test_a_publication_that_moves_nothing_announces_nothing(self):
        env_records.report(self, AGENT)
        self.assertEqual(self.cached(), 'online')
        self.assertEqual(self.announced, [], 'the same word again is not a change, so the dashboard is not woken')
