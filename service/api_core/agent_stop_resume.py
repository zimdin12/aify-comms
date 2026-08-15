"""What the dashboard's Stop and Resume buttons actually do to an agent.

Extracted from `control_agent` in `service/routers/agents/session_ops.py` in v0.5.4;
`test_control_agent_split_is_inert.py` inlines it back and AST-compares against the pre-split
fixture. The body is at its original 8-space column so the SQL literals are preserved byte-for-byte.

STOP IS FOUR THINGS, NOT ONE, and the order is what makes it safe to press. Cancel the runs queued
for this agent -- each with an event, so a cancelled run is distinguishable from one that failed --
then mark the agent stopped with a note the operator can act on, then tear down its managed console.

THE MANAGED-ONLY TEARDOWN IS DELIBERATE (operator-reported 2026-05-31). aify-comms is the lifecycle
driver for a MANAGED session, so a Stop that left the TUI running abandoned a live process nobody
owned. A RESIDENT window is the operator's own process and is not ours to kill; its live bridge
terminates the CLI host, which is what the resident stop note tells them.

RESUME IS DELIBERATELY NARROWER than an inverse of stop. It clears the status and note and restores
`launch_mode` ONLY if stop had set it to `none`; it starts nothing. The next send cold-starts a
worker, which is the path that already knows how.

`cancelled_queued` IS RETURNED RATHER THAN MUTATED so the caller can report it. After the split it
would otherwise be a HELPER local the caller still reads -- the live-out defect the gate refuses.
"""
from __future__ import annotations

from service.api_core.agent_terminal_ops import _request_stop_agent_terminals
from service.api_core.events import _append_dispatch_event
from service.api_core.runtime import _normalize_session_mode


async def _apply_agent_stop_or_resume(db, agent_id, agent, req, action, now, cancelled_queued):
        """Apply the requested control, and hand back how many queued runs it cancelled.

        Guarded inside rather than at the call site, which is what the block looked like before it
        moved -- the extract-method gate splices this body back over its call verbatim, so hoisting
        the condition would break the round trip that proves the move changed nothing. Every
        argument is passed under the caller's own name for the same reason: inline-back does not
        substitute arguments.
        """
        if action == "stop":
            queued_cursor = await db.execute(
                "SELECT id FROM dispatch_runs WHERE target_agent = ? AND status = 'queued'",
                (agent_id,),
            )
            queued_rows = await queued_cursor.fetchall()
            for row in queued_rows:
                await db.execute(
                    "UPDATE dispatch_runs SET status = 'cancelled', summary = ?, finished_at = ? WHERE id = ?",
                    (f'Agent "{agent_id}" was stopped from the dashboard before the run could start.', now, row["id"]),
                )
                await _append_dispatch_event(db, row["id"], "agent_stopped", "Agent stopped from dashboard")
                cancelled_queued += 1
            stop_note = "Stopped from dashboard. Resume to allow wake/dispatch again."
            if _normalize_session_mode(agent["session_mode"] or "resident") == "resident":
                stop_note = "Resident session stop requested from dashboard; live bridge should terminate the CLI host."
            await db.execute(
                """
                UPDATE agents
                SET status = 'stopped', status_note = ?, launch_mode = 'none', last_seen = ?
                WHERE id = ?
                """,
                (stop_note, now, agent_id),
            )
            # Kill the managed console/TUI too — aify-comms is the lifecycle driver
            # for managed sessions, so Stop must tear down the running terminal
            # instead of leaving an abandoned TUI (operator-reported 2026-05-31).
            # Resident windows are the operator's OWN process; the bridge teardown
            # handles those (see stop_note), so this is managed-only.
            if _normalize_session_mode(agent["session_mode"] or "resident") == "managed":
                await _request_stop_agent_terminals(
                    db, agent_id, requested_by=req.from_agent or "dashboard", now=now,
                )
        elif action == "resume":
            await db.execute(
                """
                UPDATE agents
                SET status = 'idle', status_note = '', launch_mode = CASE WHEN launch_mode = 'none' THEN 'detached' ELSE launch_mode END,
                    last_seen = ?
                WHERE id = ?
                """,
                (now, agent_id),
            )
        return cancelled_queued
