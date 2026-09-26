"""A reply marks the message it answers read, for the agent that replied.

The notify hook shows unread messages with peek, so it marks nothing read (v0.7, H-A4). An agent that
acted on a message straight from the notice and replied with `inReplyTo` left it unread, and the next
session was shown it again as new work to process (v0.7.2, external review item 4).
"""

import sqlite3

from service.tests._base import FastApiTestCase


class AReplyMarksWhatItAnswersReadTests(FastApiTestCase):
    DB_NAME = "aify-reply-marks-read.db"

    def _seed(self, message_id, to_agent, from_agent="asker"):
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, timestamp)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (message_id, from_agent, to_agent, "direct", "request", "please", "do the thing", "normal", 0, "2026-09-26T00:00:00Z"),
            )
            conn.commit()
        finally:
            conn.close()

    def _unread(self, agent_id):
        r = self.client.get(f"/api/v1/messages/inbox/{agent_id}", params={"filter": "unread", "peek": "true"})
        self.assertEqual(r.status_code, 200, r.text)
        return [m["id"] for m in r.json()["messages"]]

    def _reply(self, from_agent, in_reply_to):
        r = self.client.post("/api/v1/messages/send", json={
            "from_agent": from_agent, "to": "asker", "type": "response", "subject": "Re: please",
            "body": "done", "inReplyTo": in_reply_to,
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("ok"), r.text)

    def test_the_answered_message_is_read_for_the_agent_that_replied(self):
        self._seed("m-1", "worker")
        self.assertEqual(self._unread("worker"), ["m-1"], "CONTROL: the message starts unread")
        self._reply("worker", "m-1")
        self.assertEqual(self._unread("worker"), [], "the message the worker answered is still unread")

    def test_a_reply_to_a_message_addressed_to_someone_else_marks_nothing(self):
        self._seed("m-2", "bystander")
        self._reply("worker", "m-2")
        self.assertEqual(self._unread("bystander"), ["m-2"], "a third party's reply marked the bystander's message read")
