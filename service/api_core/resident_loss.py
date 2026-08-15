"""What happens to an agent whose runtime bridge died and could NOT be returned to managed.

Extracted from `resident_lost` in `service/routers/agents/session_ops.py` in v0.5.4;
`test_resident_lost_split_is_inert.py` inlines it back and AST-compares against the pre-split
fixture. The body is at its original 8-space column so the SQL literals are preserved byte-for-byte.

TWO AGENTS REACH THIS BLOCK AND THEY MUST NOT REST IN THE SAME STATE.

A resident that lost its runtime with no managed backing is correctly STOPPED -- there is nothing
left to wake.

A `session_mode='managed'` agent reaching here is NOT that. It is a managed worker whose backing
died (the hermes managed-host reuses this signal via `reportGatewayDead` when its gateway port goes
dead), and the server can re-spawn it on the next message -- so it must rest COLD-STARTABLE.

THE DEFECT THAT SPLIT THEM (operator-reported 2026-07-06/07): the old code stopped BOTH. The
send-gate rejects `status='stopped'` outright, so a dead-gateway hermes could never wake; every send
bounced with `dispatchRuns:[]` and the only recovery was a manual `hermes-aify` restart. A whole
hermes team sat stopped. A managed agent now mirrors an idle-available worker (`status='active'`,
which derives `available` with no live worker, plus `launch_mode='detached'`) so the next send
cold-starts a fresh session. The bound environment still gates it through the send preflight, so an
offline environment yields a clean "env unavailable" wait rather than a permanent stop.
"""
from __future__ import annotations


async def _settle_lost_resident_when_no_transition(db, agent_id, row, req, now, returned, transition):
        """Rest the agent at the state its session mode can actually recover from.

        Guarded inside rather than at the call site, which is what the block looked like before it
        moved -- the extract-method gate splices this body back over its call verbatim, so hoisting
        the condition would break the round trip that proves the move changed nothing.

        `returned` and `transition` are taken as parameters and handed back rather than mutated,
        because after the split they would otherwise be HELPER locals that the caller still reads --
        the live-out defect the gate exists to refuse. Every argument is passed under the caller's
        own name: inline-back does not substitute arguments.
        """
        if not transition:
            # A session_mode='managed' agent reaching here is NOT a resident that lost
            # its runtime — it's a MANAGED worker whose backing died (the hermes
            # managed-host reuses this signal via reportGatewayDead when its gateway
            # port goes dead). The server can re-spawn a managed worker on the next
            # message, so it must rest at a COLD-STARTABLE state, not 'stopped'.
            #
            # The old code stopped it (status='stopped', launch_mode='none'), which the
            # send-gate rejects outright ("agent status is stopped") — so a dead-gateway
            # hermes could NEVER wake; every send bounced and the only recovery was a
            # manual hermes-aify restart (operator-reported: whole hermes team stuck
            # 'stopped', 2026-07-06/07). Wake test proved status='stopped' hard-blocks
            # delivery (dispatchRuns:[], reason "agent status is stopped").
            #
            # Fix: for a managed agent, mirror an idle-available managed worker
            # (stored status='active' → _compute_agent_status derives 'available' with
            # no live worker; launch_mode='detached') so the next send cold-starts a
            # fresh session (new gateway). The bound env still gates via the send
            # preflight, so an offline env yields a clean "env unavailable" wait rather
            # than a permanent stop. Resident agents keep the stop fallback (a resident
            # that lost its runtime with no managed backing is correctly stopped).
            agent_is_managed = str(row["session_mode"] or "").strip().lower() == "managed"
            if agent_is_managed:
                await db.execute(
                    """
                    UPDATE agents
                    SET status = 'active',
                        status_note = ?,
                        launch_mode = 'detached',
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (
                        (
                            "Managed worker backing ended ("
                            + str(req.reason or "runtime/gateway lost").strip()[:200]
                            + "); will cold-start a fresh session on the next message."
                        )[:500],
                        now,
                        agent_id,
                    ),
                )
                returned = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
                transition = "managed_worker_lost_available"
            else:
                await db.execute(
                    """
                    UPDATE agents
                    SET status = 'stopped',
                        status_note = ?,
                        launch_mode = 'none',
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (
                        str(req.reason or "Resident runtime bridge was lost and no managed backing was available.")[:500],
                        now,
                        agent_id,
                    ),
                )
                returned = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
                transition = "resident_to_stopped"
        return returned, transition
