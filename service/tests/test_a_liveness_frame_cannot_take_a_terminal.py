"""An empty liveness frame must not adopt a terminal, nor revive a stopped one.

EXTERNAL REVIEW, Round 8 M6. `POST /terminals/{id}/output` ran `_settle_bridge_takeover_for_output`
BEFORE the liveness-frame branch, and for a virtual-rpc terminal that settlement adopts the row and
REVIVES it -- `SET bridge_id = ?, status = 'running', stopped_at = NULL`. A frame carrying
`{"output": ""}` and no status was enough.

THE SETTLEMENT ITSELF IS RIGHT AND IS NOT WEAKENED HERE. Its reasoning is documented and was paid
for: on 2026-05-22 supersession cleanup raced an in-flight dispatch, the row read stopped while frames
kept arriving, and the dashboard said "terminal is not running" while the agent was replying. The
rescue rests on one claim -- an arriving POST is HARD PROOF the new bridge is actively driving this
terminal.

AN EMPTY FRAME IS EXACTLY WHAT THAT IS NOT. It carries no bytes and no status; it is the host saying
"this process is still mine". A claim, not evidence. So the guard is on CONTENT, and every case the
rescue was built for still settles, because all of them carry real output -- which the second test
here is the control for.

NOT AN AUTHORISATION TEST. The operator ruled on 2026-09-04 that there are no per-agent tokens --
everybody with the key is trusted -- so a key holder can still drive terminals and this does not
pretend otherwise. What is asserted is that a CONTENTLESS message is not treated as work.
"""

from __future__ import annotations

import asyncio

from service.db import get_db
from service.tests._base import FastApiTestCase

#: A real member of `VIRTUAL_RPC_COMMAND_SET`. The adopt-and-revive branch is scoped to these, so a
#: made-up command would take the 409 path and every assertion below would pass for the wrong reason.
VIRTUAL_RPC = "aify://virtual-rpc/codex"
TERMINAL = "term-m6"


class ALivenessFrameCannotTakeATerminalTests(FastApiTestCase):
    DB_NAME = "aify-test-liveness-frame-takeover.db"

    def _seed(self, *, status: str, bridge_id: str):
        # THE AGENT AND SESSION EXIST FIRST. `terminal_sessions` carries foreign keys, so seeding the
        # terminal alone fails with `FOREIGN KEY constraint failed` -- which reads like the route
        # refusing the frame rather than the fixture being incomplete.
        beat = self._client.post("/api/v1/environments/heartbeat", json={
            "id": "e1", "kind": "windows", "os": "windows", "machineId": "win32:m6",
            "bridgeId": bridge_id, "metadata": {"bridgeStartedAt": "2026-09-04T00:00:00Z"},
        })
        assert beat.status_code == 200, beat.text
        registered = self._client.post("/api/v1/agents", json={
            "agentId": "a1", "role": "coder", "runtime": "codex",
            "sessionMode": "managed", "machineId": "win32:m6", "bridgeId": bridge_id,
        })
        assert registered.status_code == 200, registered.text

        async def go():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT OR REPLACE INTO agent_sessions (id, agent_id, environment_id, runtime, "
                    "status, owner_mode, terminal_id, terminal_status, started_at, last_seen, "
                    "spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("s1", "a1", "e1", "codex", "running", "console", TERMINAL, status,
                     "2026-09-04T00:00:00Z", "2026-09-04T00:00:00Z", None, None),
                )
                await db.execute(
                    "INSERT OR REPLACE INTO terminal_sessions (id, agent_id, session_id, "
                    "environment_id, runtime, bridge_id, command, workspace, status, output, error, "
                    "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (TERMINAL, "a1", "s1", "e1", "codex", bridge_id, VIRTUAL_RPC, "C:/work",
                     status, "", "", "2026-09-04T00:00:00Z", "2026-09-04T00:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _row(self):
        async def go():
            db = await get_db()
            try:
                cursor = await db.execute(
                    "SELECT bridge_id, status FROM terminal_sessions WHERE id = ?", (TERMINAL,))
                row = await cursor.fetchone()
                return (str(row["bridge_id"] or ""), str(row["status"] or "")) if row else (None, None)
            finally:
                await db.close()

        return asyncio.run(go())

    def _frame(self, bridge_id: str, **body):
        return self._client.post(
            f"/api/v1/terminals/{TERMINAL}/output", json={"bridgeId": bridge_id, **body})

    def test_an_empty_frame_from_ANOTHER_bridge_does_not_revive_a_stopped_terminal(self):
        self._seed(status="stopped", bridge_id="bridge-A")
        self._frame("bridge-B", output="")
        owner, status = self._row()
        self.assertEqual(
            status, "stopped",
            "an empty liveness frame revived a stopped terminal. `{output: \"\"}` is a claim that a "
            "process is still alive, not evidence that anything is driving it.",
        )
        self.assertEqual(owner, "bridge-A", "an empty liveness frame moved the terminal's owner")

    def test_a_frame_WITH_OUTPUT_still_adopts_and_revives(self):
        # THE CONTROL, and it is the whole reason the guard is on CONTENT rather than on the caller.
        # The adopt-and-revive exists for a measured incident -- supersession racing an in-flight
        # dispatch on 2026-05-22 -- and a fix that broke it would trade a visible defect for the
        # invisible one it was built to end: a console reading dead while the agent replies.
        self._seed(status="stopped", bridge_id="bridge-A")
        self._frame("bridge-B", output="the agent is writing")
        owner, status = self._row()
        self.assertEqual(
            status, "running",
            "a frame carrying real output no longer revives a stopped virtual terminal. That is the "
            "2026-05-22 rescue, undone.",
        )
        self.assertEqual(owner, "bridge-B", "the driving bridge did not take ownership")

    def test_a_frame_carrying_only_a_STATUS_also_settles(self):
        # A status is a statement about the terminal, so it is content in the sense that matters: the
        # sender is saying something happened, not merely that it still exists.
        self._seed(status="stopped", bridge_id="bridge-A")
        self._frame("bridge-B", output="", status="running")
        owner, _ = self._row()
        self.assertEqual(owner, "bridge-B", "a frame reporting a status did not settle ownership")

    def test_an_empty_frame_from_the_SAME_bridge_is_untouched_by_this(self):
        # The ordinary case, and the one that must not regress: a host reporting its OWN terminal
        # alive is what the liveness frame is for. Nothing here should reach it at all.
        self._seed(status="running", bridge_id="bridge-A")
        answer = self._frame("bridge-A", output="")
        self.assertEqual(answer.status_code, 200, answer.text)
        owner, status = self._row()
        self.assertEqual((owner, status), ("bridge-A", "running"),
                         "a host reporting its own terminal alive changed the row")

    def test_a_liveness_frame_RECORDS_the_host_that_is_running_an_active_terminal(self):
        """The other half of the M6 guard, added 2026-09-05, and the boundary between them is status.

        The guard above is right that a contentless frame is not evidence anything is being DRIVEN.
        But the service separately RULES that a reporting host is the owner --
        `_active_terminal_for_agent` says "A HOST THAT IS STILL REPORTING THIS TERMINAL OWNS IT,
        WHATEVER ITS ID SAYS", and rejects `bridge_id` as a proxy because aify-env mints a fresh one
        on every start, so after a restart EVERY terminal mismatches. That rule is what stops a
        restart releasing every live terminal.

        WHAT IT LEFT BEHIND was a row naming a bridge that no longer exists, and one layer along
        nothing re-derives the rule: `terminal_controls` are queued with the ROW's `bridge_id` and
        claimed by an EXACT match, so console input and resize are addressed to a bridge nobody
        presents. The terminal is alive, the dashboard shows it, and typing into it does nothing.

        So the row learns what the service already believes -- one owner of the ownership question,
        recorded once, rather than every reader deriving it and one of them getting it wrong.
        """
        self._seed(status="running", bridge_id="bridge-A")
        self._frame("bridge-B", output="")
        owner, status = self._row()
        self.assertEqual(
            owner, "bridge-B",
            "the host actively reporting this terminal is not recorded as its owner, so a control "
            "queued for it is addressed to a bridge that no longer exists and can never be claimed",
        )
        self.assertEqual(
            status, "running",
            "recording the owner must not touch status -- a liveness frame carries none precisely so "
            "it cannot reopen what an operator or a reconciler closed",
        )

    def test_an_ANONYMOUS_liveness_frame_claims_nothing(self):
        """A sender that does not identify itself is not making a claim. Reading absence as a
        takeover would let any caller blank a terminal's owner."""
        self._seed(status="running", bridge_id="bridge-A")
        self._frame("", output="")
        owner, _status = self._row()
        self.assertEqual(owner, "bridge-A")
