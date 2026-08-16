"""The pi and claude idle-run closers are twins that have DRIFTED. Pinned, not ruled.

`_close_idle_pi_terminal_run_without_reply` was written as a mirror of
`_close_idle_claude_terminal_run_without_reply` — its own docstring says "Pi analog of" and the commit
that added it (`ac3460dd`) says "Same shape as the claude analog" and "Mirrors the existing
_close_idle_claude_terminal_run_without_reply pattern".

It is not the same shape, in two ways, and both were found by diffing the bodies (0.82 line
similarity) rather than by any test:

  1. **Pending terminal controls.** The pi closer calls `_fail_pending_terminal_controls` for the
     terminal; the claude closer does not. BOTH require the terminal to still be in an ACTIVE status
     to run at all — so pi fails the outstanding controls of a terminal it has just confirmed is
     alive. `_fail_pending_terminal_controls`'s own docstring scopes that to "the terminal-CLOSED
     callers below ... once the process is genuinely gone a pending stop is moot", which is exactly
     the condition that does NOT hold here. Measured consequence, by calling the real functions with
     a pending `input` control on an `attached` terminal: pi marks it `failed`, claude leaves it
     `pending`. On pi, a console input enqueued shortly before the idle sweep is silently discarded
     while the terminal could still have run it.

  2. **Event name.** The same occurrence is recorded as `terminal_closed` by pi and
     `terminal_idle_reconciled` by claude, so anything reading dispatch events by name sees one
     runtime and not the other.

WHY THIS FILE DOES NOT PICK A WINNER. Divergence 1 changes delivery behaviour on a live path in
whichever direction it is resolved — either pi stops discarding queued input, or claude starts
failing controls it currently leaves alone. That is an operator/reviewer call, so this pins today's
behaviour EXACTLY: the shared contract is asserted as agreement, and the two divergences are asserted
as the specific facts they are. A third divergence, or a silent change to either of these, fails.

This is the repo's standing answer to duplication findings — an agreement test, not a merge.
"""

from __future__ import annotations

import asyncio
import sqlite3

from service.db import get_db
from service.reconcilers.terminal_runs import (
    _close_idle_claude_terminal_run_without_reply,
    _close_idle_pi_terminal_run_without_reply,
)
from service.tests._base import FastApiTestCase

OLD = "2020-01-01T00:00:00Z"

#: The omp input box the pi detector looks for (same sample the hint's own test uses).
PI_IDLE = (
    "Some prior conversation\n"
    "more output\n"
    "╭── π  > ⬢ GPT-5.5 · high > 📁 C:\\tmp > ◫ 49.1%/272K ⟲ > $6.53 ▶──╮\n"
    "╰─                                                                       ─╯\n"
)
#: The bottom-of-screen furniture claude leaves at its prompt.
CLAUDE_IDLE = "ran the tests, all green\n\n❯ "

CASES = {
    "pi": (PI_IDLE, _close_idle_pi_terminal_run_without_reply),
    "claude-code": (CLAUDE_IDLE, _close_idle_claude_terminal_run_without_reply),
}


class IdleTerminalRunClosersAgreeTests(FastApiTestCase):
    DB_NAME = "aify-idle-closer-agreement-test.db"

    def _seed(self, agent_id: str, runtime: str, output: str) -> tuple[str, str, str]:
        """An ACTIVE terminal showing an idle prompt, a running terminal-mode run, one pending control."""
        self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": runtime, "sessionMode": "managed"})
        sid, tid, eid = f"s_{agent_id}", f"t_{agent_id}", f"e_{agent_id}"
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "INSERT INTO environments (id, machine_id, bridge_id, registered_at, last_seen)"
                " VALUES (?,?,?,?,?)", (eid, "h", f"b_{agent_id}", OLD, OLD))
            conn.execute(
                "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, workspace,"
                " started_at, last_seen, terminal_id, terminal_status, owner_mode)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (sid, agent_id, eid, runtime, "running", "/w", OLD, OLD, tid, "attached", "managed"))
            conn.execute(
                "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id,"
                " runtime, workspace, command, status, requested_by, created_at, updated_at, output)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, sid, agent_id, eid, f"b_{agent_id}", runtime, "/w", "x", "attached",
                 "dashboard", OLD, OLD, output))
            conn.execute(
                "INSERT INTO dispatch_runs (id, from_agent, target_agent, dispatch_mode, runtime,"
                " message_type, subject, body, priority, status, requested_at, started_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"run_{agent_id}", "mgr", agent_id, "terminal", runtime, "request", "s", "b",
                 "normal", "running", OLD, OLD))
            conn.execute(
                "INSERT INTO terminal_controls (id, terminal_id, environment_id, action, body, status,"
                " requested_by, requested_at) VALUES (?,?,?,?,?,?,?,?)",
                (f"ctl_{agent_id}", tid, eid, "input", "hello", "pending", "dashboard", OLD))
            conn.commit()
        finally:
            conn.close()
        return f"run_{agent_id}", f"ctl_{agent_id}", tid

    def _close(self, runtime: str) -> dict:
        """Run the real closer and report what it did. No mocking — the drift is in the bodies."""
        output, closer = CASES[runtime]
        agent_id = f"idl-{runtime.split('-')[0]}"
        run_id, ctl_id, terminal_id = self._seed(agent_id, runtime, output)

        async def _run():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))).fetchone()
                closed = await closer(db, row, quiet_seconds=0)
                await db.commit()
                run = await (await db.execute(
                    "SELECT status FROM dispatch_runs WHERE id = ?", (run_id,))).fetchone()
                ctl = await (await db.execute(
                    "SELECT status FROM terminal_controls WHERE id = ?", (ctl_id,))).fetchone()
                term = await (await db.execute(
                    "SELECT status FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
                events = await (await db.execute(
                    "SELECT event_type FROM dispatch_events WHERE run_id = ?", (run_id,))).fetchall()
                return {
                    "closed": closed,
                    "run_status": str(run["status"]),
                    "control_status": str(ctl["status"]),
                    "terminal_status": str(term["status"]),
                    "events": [str(e["event_type"]) for e in events],
                }
            finally:
                await db.close()

        return asyncio.run(_run())

    # ── what they must keep doing the same ───────────────────────────────────────────────────

    def test_both_close_an_idle_run_and_leave_the_terminal_alone(self):
        """The shared contract. If either half stops holding, the twins have drifted further."""
        for runtime in CASES:
            with self.subTest(runtime=runtime):
                result = self._close(runtime)
                self.assertTrue(result["closed"], f"{runtime}: did not report closing the run")
                self.assertEqual(result["run_status"], "completed", runtime)
                self.assertEqual(
                    result["terminal_status"], "attached",
                    f"{runtime}: the closer ended the TERMINAL, not just the run. Both are gated on "
                    f"an ACTIVE terminal and neither is supposed to tear it down.",
                )

    # ── where they differ, pinned as the facts they are ──────────────────────────────────────

    def test_pi_fails_pending_controls_on_a_LIVE_terminal_and_claude_does_not(self):
        """UNRULED DIVERGENCE 1 — the one with a user-visible consequence.

        Not an assertion that this is correct. It is an assertion that this is what happens, so the
        difference cannot widen or quietly flip while the question is open. Resolving it means
        deciding whether an idle prompt justifies discarding a queued input on a terminal that is
        still attached — `_fail_pending_terminal_controls` says that reasoning belongs to callers
        acting on a terminal whose process is GONE.
        """
        self.assertEqual(
            self._close("pi")["control_status"], "failed",
            "pi no longer fails pending controls — if that was the intended fix, this test and the "
            "claude side should have been settled together",
        )
        self.assertEqual(
            self._close("claude-code")["control_status"], "pending",
            "claude now fails pending controls too — if that was the intended fix, say so here; it "
            "means a queued console input is dropped when the PTY looks idle",
        )

    def test_the_two_closers_record_differently_named_events(self):
        """UNRULED DIVERGENCE 2. Cheap to align, but anything already reading these names would
        break, so it is pinned rather than fixed in passing."""
        self.assertIn("terminal_closed", self._close("pi")["events"])
        self.assertIn("terminal_idle_reconciled", self._close("claude-code")["events"])
