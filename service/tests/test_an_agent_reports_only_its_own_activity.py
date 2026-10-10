"""D12: an agent's status is not anyone's to set. PATCH /agents/{id} takes only the words comms_status offers.

A stored `stopped` is the manual stop that derivation defers to, so before D12 any key holder could stop any agent
through this route. The refusal is checked against the row, not only the HTTP answer.
"""
from __future__ import annotations

import sqlite3

from service.api_core.vocabulary import AGENT_STATUSES, SELF_REPORTED_STATUSES
from service.tests._base import FastApiTestCase


class AnAgentReportsOnlyItsOwnActivity(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.client.post("/api/v1/agents", json={"agentId": "coder", "role": "coder", "runtime": "claude-code"}).raise_for_status()

    def stored(self):
        conn = sqlite3.connect(str(self._db_path))
        try:
            return conn.execute("SELECT status, status_note FROM agents WHERE id = 'coder'").fetchone()
        finally:
            conn.close()

    def test_a_derived_word_is_refused_and_changes_nothing(self):
        before = self.stored()
        for word in sorted(set(AGENT_STATUSES) - set(SELF_REPORTED_STATUSES)) + ["Stopped", "manual", ""]:
            with self.subTest(word=word):
                refused = self.client.patch("/api/v1/agents/coder", json={"status": word, "note": "x"})
                self.assertEqual(refused.status_code, 422, refused.text)
                self.assertIn("; stopped, offline and the other derived words are not anyone's to set", refused.json()["detail"])
                self.assertEqual(self.stored(), before)

    def test_each_self_reported_word_is_stored(self):
        for word in SELF_REPORTED_STATUSES:
            with self.subTest(word=word):
                answered = self.client.patch("/api/v1/agents/coder", json={"status": word.upper(), "note": "on it"})
                self.assertEqual(answered.status_code, 200, answered.text)
                self.assertEqual(self.stored(), (word, "on it"))

    def test_the_refused_words_include_the_manual_stop(self):
        """CONTROL on the population: the set refused above must contain the word the incident was about."""
        self.assertIn("stopped", set(AGENT_STATUSES) - set(SELF_REPORTED_STATUSES))
