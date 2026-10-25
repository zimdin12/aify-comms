"""The agent's row note records the last event written to its row, not why it has its word (0.9.9 S3a).

`agents.status_note` is stamped by events such as the session-handle heartbeat ("Session handle set by
bridge-heartbeat."). The served note is `switched_view.served_note`; an empty one is the answer, so the row note must
not fill it. The operator's own note beside their stop still shows, because served_note returns it.
"""
import sqlite3
from contextlib import closing

from service.routers.api_v2 import router  # noqa: F401 -- the base builds the app from it
from service.tests import env_records
from service.tests._base import FastApiTestCase

AGENT = 'noted-agent'
ROW_NOTE = 'Session handle set by bridge-heartbeat.'


class ARowNoteIsNotServedBesideAViewWord(FastApiTestCase):
    DB_NAME = 'aify-row-note.db'

    def setUp(self):
        super().setUp()
        env_records.reset_mirror()
        response = self.client.post('/api/v1/agents', json={'agentId': AGENT, 'role': 'coder', 'runtime': 'claude-code'})
        self.assertEqual(response.status_code, 200, response.text)

    def note_row(self, note, status=None):
        with closing(sqlite3.connect(self._db_path)) as db:
            db.execute('UPDATE agents SET status_note = ?, status = COALESCE(?, status) WHERE id = ?', (note, status, AGENT))
            db.commit()
        from service.reconcilers.status_cache import _live_state_drop
        _live_state_drop(AGENT)

    def served(self):
        agent = self.client.get(f'/api/v1/agents/{AGENT}').json()['agent']
        return agent['status'], agent['statusNote']

    def test_a_known_word_carries_no_row_note(self):
        env_records.report(self, AGENT)
        self.note_row(ROW_NOTE)
        status, note = self.served()
        self.assertNotIn(status, ('unknown', 'stopped'), 'CONTROL: aify-env decides a known word')
        self.assertEqual(note, '', 'the row note was served beside the view word')

    def test_the_operators_stop_still_shows_their_note(self):
        env_records.report(self, AGENT)
        self.note_row('paused for the night', status='stopped')
        self.assertEqual(self.served(), ('stopped', 'paused for the night'))
