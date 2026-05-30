"""Replies must never be dropped by the live-wake hard-gate.

BUG (feature/session-status-robustness): a REPLY message (`inReplyTo` set, or
`type=="response"`) was HARD-REJECTED with
  {"ok": false, "error": "Message was not sent because one or more recipients
   cannot start live work now."}
when the recipient's bridge was stale/not-startable — the message row was never
inserted and the originating `require_reply` dispatch run never closed. This
dropped legitimate replies (it broke managed-hermes self-reply: the agent
called comms_send but the service refused it because the original sender's
resident bridge was stale).

DESIRED SEMANTIC: a reply must ALWAYS be persisted + threaded (and close its
require_reply run) regardless of whether the recipient can be live-woken — the
recipient simply sees it in their inbox. The live-wake hard-gate applies only to
NEW dispatches (requests/etc.), never to replies.

These tests drive the fix in service/routers/api_v2.send_message.
"""

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.db import init_db
from service.routers.api_v2 import router


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _DummyWS:
    async def broadcast(self, *_args, **_kwargs):
        return None

    async def notify_agent(self, *_args, **_kwargs):
        return None


class ReplyBypassesLiveGateTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "aify-test.db"
        asyncio.run(init_db(self._db_path))

        app = FastAPI()
        app.state.ws_manager = _DummyWS()
        app.state.config = SimpleNamespace(data_dir=self._tmpdir.name)
        app.state.testing = True
        app.include_router(router, prefix="/api/v1")
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self._tmpdir.cleanup()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _register_resident(self, agent_id: str) -> None:
        """Register a resident agent. With no fresh resident bridge it is
        classified `stale` (not_started) by _preflight_live_send_recipients —
        i.e. it cannot be live-woken now."""
        res = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "manager",
                "runtime": "claude-code",
                "sessionMode": "resident",
                "machineId": "linux:test-host",
                "bridgeId": f"bridge-{agent_id}",
                "capabilities": ["resume", "interrupt"],
            },
        )
        self.assertEqual(res.status_code, 200, res.text)

    def _seed_original_message_and_run(
        self, *, sender: str, recipient: str, message_id: str, run_id: str
    ) -> None:
        """Seed the ORIGINAL request: a message row + a require_reply dispatch
        run targeting `recipient` (so `recipient` owes a reply back to
        `sender`). The run is in a live ('running', channel) state that
        _mark_dispatch_run_answered will flip to completed when answered."""
        now = _iso(datetime.now(timezone.utc))
        ts = int(datetime.now(timezone.utc).timestamp() * 1000)
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute(
                "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, in_reply_to, timestamp) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (message_id, sender, recipient, "direct", "request", "Do the thing",
                 "please do the thing", "normal", 1, None, ts),
            )
            conn.execute(
                "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, dispatch_mode, "
                "execution_mode, message_type, subject, body, priority, status, require_reply, requested_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, message_id, sender, recipient, "start_if_possible",
                 "channel", "request", "Do the thing", "please do the thing",
                 "normal", "running", 1, now),
            )
            conn.commit()
        finally:
            conn.close()

    def _run_status(self, run_id: str) -> tuple[str, str]:
        conn = sqlite3.connect(str(self._db_path))
        try:
            row = conn.execute(
                "SELECT status, COALESCE(result_message_id, '') FROM dispatch_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        finally:
            conn.close()
        return (row[0], row[1]) if row else ("", "")

    def _message_exists(self, message_id: str) -> bool:
        conn = sqlite3.connect(str(self._db_path))
        try:
            row = conn.execute("SELECT 1 FROM messages WHERE id = ?", (message_id,)).fetchone()
        finally:
            conn.close()
        return row is not None

    # ------------------------------------------------------------------
    # the fix
    # ------------------------------------------------------------------
    def test_reply_to_not_startable_recipient_is_persisted_and_closes_run(self):
        # `manager` owes `worker` nothing; `worker` owes `manager` a reply.
        # We model: manager sent a request to worker (original run). worker now
        # replies to manager — but manager (resident) has a stale bridge, so the
        # live-wake gate would have hard-rejected the reply. It must NOT.
        self._register_resident("manager")  # the REPLY recipient — not live-wakeable
        self._register_resident("worker")
        self._seed_original_message_and_run(
            sender="manager", recipient="worker",
            message_id="orig-1", run_id="run-1",
        )

        res = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "worker",
                "to": "manager",
                "type": "response",
                "subject": "Done",
                "body": "Finished the thing.",
                "inReplyTo": "orig-1",
                "trigger": True,
                "requireReply": False,
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(
            body.get("ok"),
            f"reply to a not-startable recipient must be persisted, not hard-rejected; got {body}",
        )
        msg_id = body.get("messageId")
        self.assertTrue(msg_id, f"reply must return a messageId; got {body}")
        self.assertTrue(
            self._message_exists(msg_id),
            "reply message row must be written to the inbox",
        )

        status, result_message_id = self._run_status("run-1")
        self.assertEqual(
            status, "completed",
            f"the original require_reply run must close when the reply lands; got status={status!r}",
        )
        self.assertEqual(
            result_message_id, msg_id,
            "the closed run must reference the reply message as its result",
        )

    def test_reply_via_inreplyto_only_also_bypasses_gate(self):
        """A reply identified solely by inReplyTo (type left as a generic
        handoff type) is still persisted to a not-startable recipient."""
        self._register_resident("manager")
        self._register_resident("worker")
        self._seed_original_message_and_run(
            sender="manager", recipient="worker",
            message_id="orig-2", run_id="run-2",
        )
        res = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "worker",
                "to": "manager",
                "type": "review",
                "subject": "Reviewed",
                "body": "Looks good.",
                "inReplyTo": "orig-2",
                "trigger": True,
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body.get("ok"), f"inReplyTo reply must be persisted; got {body}")
        self.assertTrue(self._message_exists(body.get("messageId")))
        status, _ = self._run_status("run-2")
        self.assertEqual(status, "completed")

    # ------------------------------------------------------------------
    # regression — NON-reply still hard-gated (unchanged)
    # ------------------------------------------------------------------
    def test_non_reply_request_to_not_startable_recipient_is_still_rejected(self):
        self._register_resident("manager")  # not live-wakeable
        self._register_resident("worker")
        res = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "worker",
                "to": "manager",
                "type": "request",
                "subject": "New ask",
                "body": "Please start something fresh.",
                "trigger": True,
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertFalse(
            body.get("ok"),
            f"a NON-reply request to a not-startable recipient must still hard-reject; got {body}",
        )
        self.assertIn("cannot start live work now", str(body.get("error", "")))
        self.assertTrue(body.get("notStarted"), "rejection must report notStarted recipients")


if __name__ == "__main__":
    unittest.main()
