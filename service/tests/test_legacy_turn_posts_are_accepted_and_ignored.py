"""A launcher not yet relaunched still posts turn events to comms; since 0.9.9 S2 they are answered and ignored.

aify-env owns turns, and the comms turn tables are gone (0.9.9 S3c), so there is no turn state left for these posts
to change. What stays is what the agent authored: presence (`last_present_at`), and the 404 / 410 answers for an unknown or removed agent.
"""
import sqlite3
from contextlib import closing

from service.tests._base import FastApiTestCase

AGENT = 'legacy-poster'


class LegacyTurnPostsAreAcceptedAndIgnored(FastApiTestCase):
    DB_NAME = 'aify-legacy-turn-posts.db'

    def setUp(self):
        super().setUp()
        response = self.client.post('/api/v1/agents', json={'agentId': AGENT, 'role': 'coder', 'runtime': 'claude-code'})
        self.assertEqual(response.status_code, 200, response.text)

    def present_at(self):
        with closing(sqlite3.connect(self._db_path)) as db:
            db.execute("UPDATE agents SET last_present_at = '' WHERE id = ?", (AGENT,))
            db.commit()

        def read():
            with closing(sqlite3.connect(self._db_path)) as db:
                return db.execute('SELECT last_present_at FROM agents WHERE id = ?', (AGENT,)).fetchone()[0]
        return read

    def test_each_turn_post_answers_ok_and_marks_presence(self):
        posts = (('turn-start', {}), ('turn-end', {'runId': 'r1'}), ('status-event', {'kind': 'turn_start'}),
                 ('status-event', {'kind': 'blocked'}))
        for route, body in posts:
            with self.subTest(route=route, body=body):
                read = self.present_at()
                answer = self.client.post(f'/api/v1/agents/{AGENT}/{route}', json=body)
                self.assertEqual(answer.status_code, 200, answer.text)
                self.assertEqual(answer.json()['ignored'], 'aify-env-owns-turns')
                self.assertTrue(read(), 'the agent authored the post, so it was present')

    def test_a_heartbeat_carrying_turn_busy_is_answered(self):
        answer = self.client.post(f'/api/v1/agents/{AGENT}/heartbeat',
                                  json={'bridgeId': 'b1', 'turnBusy': True, 'turnRunId': 'r1', 'turnRuntime': 'claude-code'})
        self.assertEqual(answer.status_code, 200, answer.text)

    def test_an_unknown_agent_is_404_and_a_removed_one_410(self):
        for route in ('turn-start', 'turn-end'):
            self.assertEqual(self.client.post(f'/api/v1/agents/nobody/{route}', json={}).status_code, 404, route)
        self.assertEqual(self.client.post('/api/v1/agents/nobody/status-event', json={'kind': 'blocked'}).status_code, 404)
        removed = self.client.delete(f'/api/v1/agents/{AGENT}')
        self.assertIn(removed.status_code, (200, 204), removed.text)
        self.assertEqual(self.client.post(f'/api/v1/agents/{AGENT}/turn-start', json={}).status_code, 410)
