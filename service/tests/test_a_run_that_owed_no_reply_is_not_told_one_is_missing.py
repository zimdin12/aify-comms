"""A terminal death fails every open run, and only says "no reply" to the runs that owed one.

WHEN A TERMINAL ENDS, `_close_active_terminal_runs_for_terminal` fails or cancels EVERY claimed or
running terminal run for that agent. That is correct: the terminal died and none of them finished.
What was not correct is the sentence it stamped on all of them --

    Terminal stopped before an explicit reply was recorded.

-- which names an obligation that, for some of those runs, never existed. A sender setting
`requireReply=false` is told its run failed for want of a reply nobody asked for.

MEASURED on the live database 2026-08-27: 16 runs carry that sentence and 5 have `require_reply = 0`
-- three `response` and two `info`. One of the five was a message I had sent myself twenty minutes
earlier; reading its failure sent me hunting a reply-contract defect that was not there. A reason that
names the wrong obligation costs its reader the same hour it cost me, which is the entire argument for
fixing a string.

THE RUN IS STILL FAILED. Nothing here argues the run succeeded -- the terminal died mid-run and the
work did not complete. Only the stated cause changes.

WHY THIS TEST DRIVES THE ROUTE AND NOT THE HELPER. `without_reply_claim` is a pure function and would
pass its own tests while nothing called it; this project has shipped exactly that -- a feature with six
green tests that could never fire, because the call site was never wired. So this posts a real terminal
exit and reads what landed in `dispatch_runs`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

BRIDGE = "reply-claim-bridge"
ENVIRONMENT = "linux:test-host:default"


class ARunThatOwedNoReplyIsNotToldOneIsMissing(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    AGENT = "reply-claim-agent"
    TERMINAL = "term_reply_claim"
    OWED = "run-owed-a-reply"
    NOT_OWED = "run-owed-nothing"

    def setUp(self) -> None:
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENVIRONMENT, "machineId": "linux:test-host", "os": "linux", "kind": "linux",
            "bridgeId": BRIDGE, "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        registered = self.client.post("/api/v1/agents", json={
            "agentId": self.AGENT, "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "machineId": "linux:test-host", "bridgeId": BRIDGE,
        })
        self.assertEqual(registered.status_code, 200, registered.text)
        self._seed()

    def _seed(self) -> None:
        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, "
                    "started_at, last_seen, spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"sess-{self.AGENT}", self.AGENT, ENVIRONMENT, "claude-code", "running",
                     "2026-08-27T02:00:00Z", "2026-08-27T02:00:00Z", None, None),
                )
                await db.execute(
                    "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, "
                    "runtime, bridge_id, command, status, output, error, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (self.TERMINAL, self.AGENT, f"sess-{self.AGENT}", ENVIRONMENT, "claude-code",
                     BRIDGE, "claude-aify --aify-agent x", "attached", "", "",
                     "2026-08-27T02:00:00Z", "2026-08-27T02:00:00Z"),
                )
                # Two runs, identical but for the one field under test.
                for run_id, require_reply, message_type in (
                    (self.OWED, 1, "request"),
                    (self.NOT_OWED, 0, "response"),
                ):
                    await db.execute(
                        "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, "
                        "dispatch_mode, execution_mode, message_type, subject, body, priority, "
                        "status, require_reply, requested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        # message_id is a nullable FK to messages(id); seeding a bare string fails the
                        # constraint, and this test needs no message row.
                        (run_id, None, "someone-else", self.AGENT,
                         "terminal", "terminal", message_type, "s", "b", "normal",
                         "running", require_reply, "2026-08-27T02:00:00Z"),
                    )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _end_the_terminal(self):
        return self.client.post(
            f"/api/v1/terminals/{self.TERMINAL}/output",
            json={"bridgeId": BRIDGE, "output": "\n[terminal exited]\n", "status": "stopped"},
        )

    def _runs(self) -> dict:
        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                rows = await (await db.execute(
                    "SELECT id, status, summary, error_text FROM dispatch_runs WHERE target_agent = ?",
                    (self.AGENT,),
                )).fetchall()
                return {
                    str(r["id"]): {
                        "status": r["status"],
                        "summary": str(r["summary"] or ""),
                        "error_text": str(r["error_text"] or ""),
                    }
                    for r in rows
                }
            finally:
                await db.close()

        return asyncio.run(go())

    def test_the_fixture_starts_with_both_runs_OPEN(self) -> None:
        """POSITIVE CONTROL. If the seed did not land, both runs would be absent and every assertion
        below would pass against an empty dict."""
        runs = self._runs()
        self.assertEqual(set(runs), {self.OWED, self.NOT_OWED}, runs)
        self.assertEqual(runs[self.OWED]["status"], "running")
        self.assertEqual(runs[self.NOT_OWED]["status"], "running")

    def test_BOTH_runs_are_closed_when_the_terminal_ends(self) -> None:
        """The behaviour that must NOT change. A run whose sender wanted no reply is still a run that
        did not finish, and leaving it open would strand it."""
        self.assertEqual(self._end_the_terminal().status_code, 200)
        runs = self._runs()
        self.assertNotEqual(runs[self.OWED]["status"], "running", "the owed run was left open")
        self.assertNotEqual(runs[self.NOT_OWED]["status"], "running", "the un-owed run was left open")

    def test_a_run_that_OWED_a_reply_is_told_the_reply_is_missing(self) -> None:
        self.assertEqual(self._end_the_terminal().status_code, 200)
        said = self._runs()[self.OWED]["summary"]
        self.assertIn("before an explicit reply was recorded", said, said)

    def test_a_run_that_owed_NO_reply_is_not(self) -> None:
        """The defect. Same terminal, same instant, same closer -- a different sentence, because the
        two runs were owed different things."""
        self.assertEqual(self._end_the_terminal().status_code, 200)
        said = self._runs()[self.NOT_OWED]["summary"]
        self.assertNotIn(
            "before an explicit reply was recorded", said,
            "a run with require_reply=0 is still told a reply was missing",
        )
        self.assertIn("No reply was owed", said, said)

    def test_a_REQUEST_with_the_flag_off_is_still_owed_a_reply(self) -> None:
        """THE CASE THAT CAUGHT MY FIRST VERSION, pinned on purpose rather than by accident.

        `require_reply` DEFAULTS TO 0 and `message_type` defaults to 'request', so most rows inserted
        without an explicit flag are `request`/0 -- and the contract owes a reply for `request`
        REGARDLESS of the flag. Reading the flag alone therefore gets the COMMONEST row backwards. My
        first version did exactly that and `test_a_terminal_records_how_it_ended` refused it; without
        this, the next person to simplify the predicate back to `bool(require_reply)` gets a green run
        from this file.
        """
        from service.db import get_db

        async def seed_request_with_flag_off():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO dispatch_runs (id, target_agent, from_agent, subject, body, status, "
                    "dispatch_mode, execution_mode, message_type, require_reply, priority, requested_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("run-request-flag-off", self.AGENT, "someone-else", "s", "b", "running",
                     "terminal", "terminal", "request", 0, "normal", "2026-08-27T02:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(seed_request_with_flag_off())
        self.assertEqual(self._end_the_terminal().status_code, 200)
        said = self._runs()["run-request-flag-off"]["summary"]
        self.assertIn(
            "before an explicit reply was recorded", said,
            "a request with require_reply=0 is owed a reply by TYPE; it was told otherwise",
        )

    def test_the_two_runs_do_not_get_the_SAME_sentence(self) -> None:
        """ANTI-VACUITY. Both assertions above could be satisfied by a rewrite that changed the
        sentence for every run, which would swap one wrong claim for another."""
        self.assertEqual(self._end_the_terminal().status_code, 200)
        runs = self._runs()
        self.assertNotEqual(runs[self.OWED]["summary"], runs[self.NOT_OWED]["summary"])


if __name__ == "__main__":
    import unittest

    unittest.main()
