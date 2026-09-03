"""The host's "this terminal is still mine" has to leave a mark, or the guard reading it is a comment.

THE DEFECT, measured on the operator's fleet 2026-09-03, and it made an earlier fix inert without
reddening a single test.

aify-env posts one empty frame per terminal per control pass: no output, no status. No status
DELIBERATELY -- a heartbeat carrying one would let a host REOPEN a terminal an operator or a
reconciler had closed. But both guards downstream dropped exactly that shape:
`TerminalOutputWriteQueue.enqueue` returns 0 when there is neither output nor status, and
`_append_terminal_output` returns before its UPDATE. The service answered `200 {"ok": true}` and
wrote nothing at all.

WHAT READ IT. `_active_terminal_for_agent` releases a terminal whose `bridge_id` no longer matches
its environment row -- and every aify-env start mints a fresh bridge id, so after a restart EVERY
terminal mismatches. `fc8d4c52` added the guard that stops that: a terminal its host is still
REPORTING is not released, read from `updated_at`. With nothing refreshing `updated_at`, that guard
could never be true. It released `sc-coder`'s terminal at 06:27:52 while aify-env was running the
process and streaming its console; an ended terminal cannot go back to active by design, so the
worker's own output could never undo it. A live claude session, unaddressable and unrestartable,
with three refused restarts behind it.

WHAT THESE PIN. That the frame WRITES, that it writes only the one column it may, and -- the test
that would have caught the whole thing -- that the release guard actually sees it. The last is the
point: a unit test of the write alone would have passed while the guard it exists to feed stayed
dead.
"""

from __future__ import annotations

import asyncio

from service.api_core.terminal_ownership import _active_terminal_for_agent
from service.clock import now as _now
from service.db import get_db
from service.tests._base import FastApiTestCase


class ALivenessFrameIsActuallyRecordedTests(FastApiTestCase):
    DB_NAME = "aify-test-liveness-frame.db"
    ENV = "windows:liveness-host:default"
    AGENT = "sc-coder"
    SESSION = "sess-liveness"
    TERMINAL = "term-liveness"

    def setUp(self):
        super().setUp()
        self._seed(env_bridge="bridge-new", terminal_bridge="bridge-old",
                   terminal_updated="2026-09-02T00:00:00Z")

    def _seed(self, *, env_bridge: str, terminal_bridge: str, terminal_updated: str) -> None:
        beat = self._client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV, "kind": "windows", "os": "windows", "machineId": "win32:liveness",
            "bridgeId": env_bridge, "cwdRoots": ["C:/work"],
            "runtimes": [{"runtime": "claude-code", "available": True}],
            "metadata": {"bridgeStartedAt": "2026-09-03T05:00:00Z"},
        })
        self.assertEqual(beat.status_code, 200, beat.text)
        registered = self._client.post("/api/v1/agents", json={
            "agentId": self.AGENT, "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "machineId": "win32:liveness", "bridgeId": env_bridge,
        })
        self.assertEqual(registered.status_code, 200, registered.text)

        async def go():
            db = await get_db()
            try:
                fresh = _now()
                await db.execute(
                    "INSERT OR REPLACE INTO agent_sessions (id, agent_id, environment_id, runtime, "
                    "status, owner_mode, terminal_id, terminal_status, started_at, last_seen, "
                    "spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (self.SESSION, self.AGENT, self.ENV, "claude-code", "running", "console",
                     self.TERMINAL, "attached", fresh, fresh, None, None),
                )
                await db.execute(
                    "INSERT OR REPLACE INTO terminal_sessions (id, agent_id, session_id, "
                    "environment_id, runtime, bridge_id, command, workspace, status, output, error, "
                    "output_seq, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (self.TERMINAL, self.AGENT, self.SESSION, self.ENV, "claude-code",
                     terminal_bridge, "claude-aify --aify-agent sc-coder", "C:/work", "attached",
                     "", "", 7, fresh, terminal_updated),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _row(self) -> dict:
        async def go():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT status, output, output_seq, updated_at, stopped_at "
                    "FROM terminal_sessions WHERE id = ?", (self.TERMINAL,),
                )).fetchone()
                return dict(row) if row else {}
            finally:
                await db.close()

        return asyncio.run(go())

    def _beat(self):
        """Exactly what aify-env sends: empty output, no status, its own bridge id."""
        return self._client.post(
            f"/api/v1/terminals/{self.TERMINAL}/output",
            json={"output": "", "bridgeId": "bridge-old"},
        )

    def test_THE_FRAME_IS_RECORDED(self):
        """THE DEFECT. It answered 200 and wrote nothing."""
        before = self._row()["updated_at"]
        answer = self._beat()
        self.assertEqual(answer.status_code, 200, answer.text)
        after = self._row()["updated_at"]
        self.assertNotEqual(after, before, "the host reported a terminal alive and nothing recorded it")
        self.assertGreater(after, "2026-09-02T00:00:00Z")

    def test_THE_GUARD_THAT_READS_IT_ACTUALLY_SEES_IT(self):
        """THE TEST THAT WOULD HAVE CAUGHT IT. A unit test of the write alone passes while the guard
        it feeds stays dead -- which is what happened: `fc8d4c52` shipped with its own green suite
        and could never fire in production."""
        async def resolve():
            db = await get_db()
            try:
                return await _active_terminal_for_agent(db, self.AGENT)
            finally:
                await db.close()

        # CONTROL FIRST: unreported, the bridge-id mismatch releases it. If this stops being true the
        # assertion below proves nothing, because both states would answer the same.
        self.assertIsNone(asyncio.run(resolve()), "an unreported terminal was kept, so the pair below is vacuous")

        self._seed(env_bridge="bridge-new", terminal_bridge="bridge-old",
                   terminal_updated="2026-09-02T00:00:00Z")
        self.assertEqual(self._beat().status_code, 200)
        self.assertIsNotNone(
            asyncio.run(resolve()),
            "a terminal its host had just reported was still released over a bridge id -- which is "
            "how a live worker becomes unaddressable and unrestartable",
        )

    def test_it_writes_the_one_column_it_may_and_no_other(self):
        """No status, so it cannot REOPEN a terminal an operator or a reconciler deliberately closed
        -- the reason the frame carries none in the first place. No output, so it cannot disturb the
        stream or its sequence numbers."""
        before = self._row()
        self.assertEqual(self._beat().status_code, 200)
        after = self._row()
        self.assertEqual(after["status"], before["status"])
        self.assertEqual(after["output"], before["output"])
        self.assertEqual(after["output_seq"], before["output_seq"])
        self.assertEqual(after["stopped_at"], before["stopped_at"])

    def test_a_beat_cannot_reopen_an_ENDED_terminal(self):
        """The property the empty frame was designed around, asserted directly rather than inferred
        from the absence of a status field."""
        async def end_it():
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE terminal_sessions SET status = 'failed', stopped_at = ? WHERE id = ?",
                    (_now(), self.TERMINAL),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(end_it())
        self.assertEqual(self._beat().status_code, 200)
        self.assertEqual(self._row()["status"], "failed", "a heartbeat reopened a closed terminal")

    def test_the_answer_carries_the_rows_own_sequence(self):
        """The queue would have answered 0 for this frame. A client taking that for a real seq sees
        the stream jump backwards on the next chunk, and the dashboard's seq-dedupe then drops real
        frames -- the scrambled-console failure, from a heartbeat."""
        answer = self._beat()
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json()["terminal"]["outputSeq"], 7)

    def test_a_frame_WITH_output_still_goes_through_the_queue(self):
        """CONTROL for the short-circuit. It must catch only the empty shape: a real chunk that
        stopped being coalesced would put the console back in front of the single SQLite writer at
        forty frames a second."""
        answer = self._client.post(
            f"/api/v1/terminals/{self.TERMINAL}/output",
            json={"output": "hello\r\n", "bridgeId": "bridge-old"},
        )
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertGreater(answer.json()["terminal"]["outputSeq"], 7,
                           "a real chunk did not take the queue's sequence")

    def test_a_frame_with_only_a_STATUS_still_transitions(self):
        """The other half of the short-circuit's condition. A status-only frame is how a host reports
        `attached` after a start, and routing it to the liveness path would silently stop that."""
        answer = self._client.post(
            f"/api/v1/terminals/{self.TERMINAL}/output",
            json={"output": "", "status": "stopped", "bridgeId": "bridge-old"},
        )
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(self._row()["status"], "stopped")
