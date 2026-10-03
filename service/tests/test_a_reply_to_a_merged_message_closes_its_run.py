"""A reply threaded to a message that was merged into a queued run closes that run.

THE DEFECT (live 2026-10-03, reported by sc-manager). A busy agent's queued run absorbs later messages
into one "Pending updates" run, which keeps the FIRST item's message_id. Replies were linked to a run by
`message_id = inReplyTo` only, so a reply to any later item matched no run: a review merged into a
reminder's run was answered, and the run stayed open, overdue, and reminding.

The fix records each merged item against its run (`dispatch_run_items`) and the reply lookup reads it.
The control: the same reply from an agent the run does not target closes nothing.
"""
from __future__ import annotations

from service.tests._base import PRE_PLAN4_SETTINGS, FastApiTestCase


class AReplyToAMergedMessageClosesItsRun(FastApiTestCase):
    DB_NAME = "aify-test-reply-to-merged-message.db"
    LEGACY_SETTINGS = PRE_PLAN4_SETTINGS  # the managed run queues without a live PTY, as the merge regression does

    def setUp(self):
        super().setUp()
        for agent_id, role in (("lead", "manager"), ("worker", "coder"), ("bystander", "coder")):
            registered = self.client.post("/api/v1/agents", json={
                "agentId": agent_id, "role": role, "runtime": "codex", "sessionMode": "managed"})
            self.assertEqual(registered.status_code, 200, registered.text)
        first = self.post("/api/v1/dispatch", from_agent="lead", to="worker", type="request", subject="first",
                          body="one", mode="start_if_possible", createMessage=True, requireReply=True)
        self.assertTrue(first["runs"], first)
        self.run_id = first["runs"][0]["runId"]
        second = self.post("/api/v1/messages/send", from_agent="lead", to="worker", type="request",
                           subject="second", body="two", trigger=True)
        self.assertTrue(second["dispatchRuns"][0]["merged"], "the second message merged into the first's run")
        self.assertEqual(second["dispatchRuns"][0]["runId"], self.run_id)
        self.second_id = second["messageId"]
        self.assertNotEqual(self.the_run()["messageId"], self.second_id, "the run keeps the first item's id")

    def post(self, path, **payload):
        response = self.client.post(path, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def the_run(self):
        response = self.client.get(f"/api/v1/dispatch/runs/{self.run_id}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["run"]

    def reply_from(self, agent_id):
        return self.post("/api/v1/messages/send", from_agent=agent_id, to="lead", type="response",
                         subject="Re: second", body="done", inReplyTo=self.second_id)["messageId"]

    def test_a_reply_to_the_merged_item_closes_the_run(self):
        reply_id = self.reply_from("worker")
        self.assertEqual(self.the_run()["resultMessageId"], reply_id)

    def test_CONTROL_the_same_reply_from_an_agent_the_run_does_not_target_closes_nothing(self):
        self.reply_from("bystander")
        self.assertEqual(self.the_run()["resultMessageId"] or "", "")
