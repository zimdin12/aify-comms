"""Unsending a message ends the reply contract its delivered run still held, and nothing else.

Observed once, 2026-10-19: a request was unsent after delivery. Unsend cancelled only QUEUED runs, the
message delete NULLed the delivered run's `message_id`, and the run kept `require_reply=1`, so the
sender was reminded about it until it was closed by hand. Unsend now withdraws the contract of a run
that is `delivered`, owes a reply and has no result: cancelled, `require_reply=0`, one event naming
the actor and the source id. No settlement runs, so no reply is posted on the target's behalf and no
control on the run is failed. Claimed and running runs, answered runs, terminal runs and runs on other
messages keep what they had.
"""

from __future__ import annotations

import asyncio
import sqlite3
import unittest

from service.api_core.dispatch_run_state import WITHDRAWN_REPLY_SUMMARY
from service.api_core.dispatch_sweeps import _mirror_missing_dispatch_handoff
from service.db import get_db
from service.tests import env_records
from service.tests._base import FastApiTestCase

NOW = "2026-10-20T00:00:00Z"


class UnsendWithdrawsADeliveredReplyContract(FastApiTestCase):
    DB_NAME = "aify-unsend-withdraws-reply.db"

    def setUp(self):
        super().setUp()
        for agent_id in ("lead", "manager"):
            r = self.client.post("/api/v1/agents", json={"agentId": agent_id, "role": "coder"})
            self.assertEqual(r.status_code, 200, r.text)
        env_records.report(self, "lead", "manager")  # known idle, so the reminder CONTROL below is due (R3)

    def _sql(self, query: str, params=()):
        conn = sqlite3.connect(self._db_path)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    def _send(self, subject: str) -> str:
        r = self.client.post("/api/v1/messages/send", json={
            "from_agent": "lead", "to": "manager", "type": "request", "subject": subject, "body": "do it",
        })
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["messageId"]

    def _run(self, run_id: str, message_id: str, status: str, *, result: str = "", require_reply: int = 1) -> str:
        self._sql(
            "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, execution_mode, message_type,"
            " subject, status, result_message_id, require_reply, requested_at)"
            " VALUES (?, ?, 'lead', 'manager', 'resident', 'request', 'a request', ?, ?, ?, ?)",
            (run_id, message_id, status, result, require_reply, NOW),
        )
        return run_id

    def _row(self, run_id: str):
        return self._sql("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))[0]

    def _unsend(self, message_id: str) -> dict:
        r = self.client.delete(f"/api/v1/messages/{message_id}?requestedBy=lead")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _contract(self, run_id: str) -> dict:
        r = self.client.get("/api/v1/contracts", params={"agentId": "manager", "includeClosed": True, "limit": 500})
        self.assertEqual(r.status_code, 200, r.text)
        found = [item for item in r.json()["contracts"] if item["id"] == run_id]
        return found[0] if found else {}

    def test_a_delivered_run_owing_a_reply_has_its_contract_withdrawn(self):
        message_id = self._send("withdraw me")
        run_id = self._run("run-delivered", message_id, "delivered")
        self._sql(
            "INSERT INTO dispatch_controls (id, run_id, action, status, requested_at) VALUES ('ctl-1', ?, 'steer', 'pending', ?)",
            (run_id, NOW),
        )

        # CONTROL: the same reminder read names this run while its contract is open.
        due = self.client.post("/api/v1/contracts/reminders/run", params={"runId": run_id, "dryRun": True}).json()
        self.assertEqual([r["runId"] for r in due["reminded"]], [run_id], due)

        out = self._unsend(message_id)

        # Each reader is its own subtest, so a defect shows at every reader it reaches, not only the first.
        with self.subTest("the unsend answer names the withdrawn run"):
            self.assertEqual(out["withdrawnReplyRunIds"], [run_id])
            self.assertEqual(out["cancelledDispatchRunIds"], [], "the delivered run is not a queued cancellation")
        with self.subTest("the run row"):
            row = self._row(run_id)
            self.assertEqual((row["status"], row["require_reply"], row["summary"]), ("cancelled", 0, WITHDRAWN_REPLY_SUMMARY))
            self.assertTrue(row["finished_at"])
            self.assertIsNone(row["message_id"], "the message delete NULLs the link, as for every unsent message")
        with self.subTest("one reply_withdrawn event naming the actor and the source"):
            events = self._sql("SELECT body FROM dispatch_events WHERE run_id = ? AND event_type = 'reply_withdrawn'", (run_id,))
            self.assertEqual(len(events), 1)
            self.assertIn("lead", events[0]["body"])
            self.assertIn(message_id, events[0]["body"], "the event keeps the source id the NULLed link lost")
        with self.subTest("the run read says no reply is owed"):
            run = self.client.get(f"/api/v1/dispatch/runs/{run_id}").json()
            self.assertEqual(run["run"]["replyState"], "not_required")
        with self.subTest("the contract read says no reply is owed"):
            contract = self._contract(run_id)
            self.assertTrue(contract, "the closed contract is still listed with includeClosed")
            self.assertEqual((contract["replyExpected"], contract["actionable"], contract["replyState"]), (False, False, "not_required"))
        with self.subTest("the open contract list and the reminder sweep drop it"):
            open_ids = [i["id"] for i in self.client.get("/api/v1/contracts", params={"agentId": "manager"}).json()["contracts"]]
            self.assertNotIn(run_id, open_ids)
            reminded = self.client.post("/api/v1/contracts/reminders/run", params={"runId": run_id, "dryRun": True}).json()
            self.assertEqual(reminded["reminded"], [])
        with self.subTest("no settlement ran: the steer on the run is still pending"):
            self.assertEqual(self._sql("SELECT status FROM dispatch_controls WHERE id = 'ctl-1'")[0]["status"], "pending")

        async def mirror_later():
            db = await get_db()
            try:
                cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
                return await _mirror_missing_dispatch_handoff(db, await cursor.fetchone())
            finally:
                await db.close()

        with self.subTest("a later handoff repair invents no answer for the target"):
            self.assertIsNone(asyncio.run(mirror_later()))
            self.assertEqual(self._sql("SELECT COUNT(*) AS n FROM messages WHERE from_agent = 'manager'")[0]["n"], 0)

    def test_only_a_delivered_unanswered_run_on_that_message_is_withdrawn(self):
        message_id = self._send("withdraw me")
        other_id = self._send("keep me")
        cols = ("status", "require_reply", "summary", "result_message_id")
        before = {}
        for run_id, mid, status, result in (
                ("run-claimed", message_id, "claimed", ""),
                ("run-running", message_id, "running", ""),
                ("run-answered", message_id, "delivered", "reply-1"),
                ("run-completed", message_id, "completed", ""),
                ("run-failed", message_id, "failed", ""),
                ("run-cancelled", message_id, "cancelled", ""),
                ("run-other-message", other_id, "delivered", ""),
        ):
            row = self._row(self._run(run_id, mid, status, result=result))
            before[run_id] = tuple(row[c] for c in cols)
        self._run("run-not-owing", message_id, "delivered", require_reply=0)
        self._run("run-queued", message_id, "queued")

        out = self._unsend(message_id)

        self.assertEqual(out["withdrawnReplyRunIds"], [])
        self.assertEqual(out["cancelledDispatchRunIds"], ["run-queued"], "queued cancellation is unchanged")
        self.assertEqual(self._row("run-queued")["summary"], "Cancelled because source message was unsent.")
        for run_id, kept in before.items():
            row = self._row(run_id)
            self.assertEqual(tuple(row[c] for c in cols), kept, run_id)
        self.assertEqual(self._row("run-not-owing")["status"], "delivered", "a run owing nothing has no contract to withdraw")
        withdrawn_events = self._sql("SELECT COUNT(*) AS n FROM dispatch_events WHERE event_type = 'reply_withdrawn'")[0]["n"]
        self.assertEqual(withdrawn_events, 0)


if __name__ == "__main__":
    unittest.main()
