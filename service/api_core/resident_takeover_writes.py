"""Registering an agent that is TAKING OVER a resident session somebody else was driving.

Extracted from `service/api_core/agent_registration_writes.py` in v0.5.4. Closure measured before
the move: `api_core`, `reconcilers` and stdlib only, and the three functions form a closed group —
`_register_via_manual_resident_takeover` calls `_stage_manual_resident_takeover` and nothing outside
the group calls in except the registration handler.

THIS IS THE BRANCH WHERE REGISTRATION IS DESTRUCTIVE. The ordinary path writes a row; this one
supersedes terminals that belong to a resident session already in progress, and it has to do that
without stranding work: `_get_blocking_active_run` is consulted first because superseding a terminal
whose run is still active is how a dispatch loses its worker mid-turn. Every "restart produced no
worker" incident in this repo's history is a variant of that ordering going wrong.

`_supersede_stale_resident_terminals` IS THE STALE HALF and is not the same decision.
"Stale" means nothing has heartbeated for long enough that the terminal cannot be believed; the
takeover above means somebody is asking to replace a session that may be perfectly alive. They share
a file because they end at the same place — a superseded terminal and an agent whose cached live
state has to be invalidated — not because they answer the same question.

Bodies byte-identical to what stood in `agent_registration_writes.py`. The registration handler is
the only caller of all three, which is why they are a module rather than four more names in a file
that had grown to 528 lines.
"""

from __future__ import annotations

import json

from service.api_core.active_run_lookup import _get_blocking_active_run
from service.api_core.bridge_registration import _record_bridge_registration
from service.api_core.events import _append_terminal_control, _append_terminal_event
from service.api_core.resume_command import _resume_command_for
from service.api_core.runtime_state import _runtime_state_with_handle
from service.api_core.serialization import _json_loads_or
from service.api_core.ws import _get_ws
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state


async def _supersede_stale_resident_terminals(db, req, terminal_id: str, now: str, bridge_id: str) -> None:
            """A resident registration takes the agent over from whatever was running it.

            Extracted from `register_agent` in v0.5.4, and `test_register_agent_split_is_inert.py` inlines it
            back and AST-compares against the pre-split fixture to prove nothing changed.

            THE BODY IS INDENTED 12 SPACES, WHICH LOOKS WRONG AND IS DELIBERATE. It contains three multi-line
            SQL literals, and re-indenting the block would have re-indented their CONTENTS — changing the
            string values. SQLite would not have cared, but the gate compares ASTs and refused it, correctly:
            "the whitespace inside a query does not matter" is exactly the kind of reasoning a proof exists to
            make unnecessary. Python only requires a function body to be consistently indented, not minimally,
            so keeping the original column preserves every literal byte-for-byte.

            THREE THINGS HAPPEN PER STALE TERMINAL and all three are needed. The row is marked stopped, so the
            dashboard stops showing it; an event is appended, so the reason is recoverable afterwards; and a
            `stop` control is queued, so the owning bridge tears the wrapper subprocess down. Queuing is
            best-effort BY DESIGN — if that bridge is already dead the control is never claimed, which does not
            matter, because the row is marked stopped either way.

            The agent_sessions unbinding at the end is the part that is easy to leave out: a session row still
            pointing at a just-stopped terminal renders as a live Console the operator can click into and type
            at — a ghost of a process that no longer exists.
            """
            stale_terminals = await (
                await db.execute(
                    """
                    SELECT id, environment_id, bridge_id
                    FROM terminal_sessions
                    WHERE agent_id = ?
                      AND status IN ('starting','attached','running','active','idle','recovering')
                      AND (? = '' OR id != ?)
                    """,
                    (req.agentId, terminal_id, terminal_id),
                )
            ).fetchall()
            for term in stale_terminals:
                await db.execute(
                    """
                    UPDATE terminal_sessions
                    SET status = 'stopped',
                        stopped_at = ?,
                        updated_at = ?,
                        error = COALESCE(NULLIF(error, ''), 'superseded_by_resident_takeover')
                    WHERE id = ?
                    """,
                    (now, now, term["id"]),
                )
                await _append_terminal_event(
                    db,
                    term["id"],
                    "superseded_by_resident_takeover",
                    json.dumps({
                        "agentId": req.agentId,
                        "residentBridge": bridge_id,
                        "newSessionMode": "resident",
                    }),
                )
                # Best-effort kill: enqueue 'stop' so the owning bridge
                # tears down the wrapper subprocess if still alive. If
                # the bridge is dead, the row is already marked stopped
                # so it doesn't matter that the control is never claimed.
                await _append_terminal_control(
                    db,
                    terminal_id=term["id"],
                    environment_id=term["environment_id"] or "",
                    bridge_id=term["bridge_id"] or "",
                    action="stop",
                    requested_by="resident-takeover",
                    body="",
                )
            if stale_terminals:
                # Clear agent_sessions.terminal_id binding for sessions
                # that pointed at any of the just-stopped terminals so
                # the dashboard stops rendering a ghost Console.
                stopped_ids = [t["id"] for t in stale_terminals]
                placeholders = ",".join(["?"] * len(stopped_ids))
                await db.execute(
                    f"""
                    UPDATE agent_sessions
                    SET terminal_id = '',
                        terminal_status = ''
                    WHERE agent_id = ?
                      AND terminal_id IN ({placeholders})
                    """,
                    (req.agentId, *stopped_ids),
                )


async def _stage_manual_resident_takeover(db, req, row, bridge_id: str, normalized_runtime: str,
                                          session_handle: str, runtime_config, capabilities,
                                          resolved_cwd: str, now: str):
            """A MANAGED agent tried to register as resident. Record the candidate; do not switch it.

            Extracted from `register_agent` in v0.5.4 and re-proved on every run by
            `test_register_agent_split_is_inert.py`, which inlines it back and AST-compares against the
            pre-split fixture.

            Body left at its original 12-space column so the multi-line SQL literal inside it is preserved
            byte-for-byte — see `_supersede_stale_resident_terminals` for why that matters and why the gate
            refused the tidier version.

            THE ONE-DRIVER INVARIANT is what this is protecting. Two things cannot drive one agent, so a
            managed agent does not silently become resident because a CLI session registered: the candidate
            is stashed in `manualResidentCandidate` and the operator flips the mode deliberately. The stale
            `pendingResidentTakeover` is popped first, or a candidate from an earlier attempt would still be
            sitting there when this one is read.

            Returns the blocking active run, which the caller reports back to the registering session so it
            can say WHY the switch did not happen rather than just that it did not.
            """
            active_run = await _get_blocking_active_run(db, req.agentId)
            existing_state_dict = _json_loads_or(row["runtime_state"], {})
            existing_state_dict.pop("pendingResidentTakeover", None)
            existing_state_dict["manualResidentCandidate"] = {
                "bridgeId": bridge_id,
                "machineId": req.machineId or "",
                "runtime": normalized_runtime,
                "sessionHandle": session_handle,
                "runtimeConfig": runtime_config,
                "capabilities": capabilities or [],
                "cwd": resolved_cwd,
                "launchMode": req.launchMode or "detached",
                "registeredAt": now,
            }
            await db.execute(
                """
                UPDATE agents
                SET runtime_state = ?,
                    status_note = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    json.dumps(existing_state_dict),
                    (
                        f"Resident CLI registered, but agent remains managed. Use Switch to resident when ready."
                        + (f" Active run {active_run.get('runId') or ''} is still running." if active_run else "")
                    ),
                    now,
                    req.agentId,
                ),
            )
            if session_handle:
                await db.execute(
                    """
                    UPDATE agent_sessions
                    SET session_handle = ?,
                        telemetry = CASE
                            WHEN COALESCE(NULLIF(telemetry, ''), '{}') = '{}' THEN ?
                            ELSE telemetry
                        END,
                        last_seen = ?
                    WHERE id = (
                        SELECT id
                        FROM agent_sessions
                        WHERE agent_id = ?
                          AND runtime = ?
                          AND status = 'cli-takeover'
                        ORDER BY last_seen DESC
                        LIMIT 1
                    )
                    """,
                    (
                        session_handle,
                        json.dumps({"registeredHandle": _runtime_state_with_handle(normalized_runtime, {}, session_handle)}),
                        now,
                        req.agentId,
                        normalized_runtime,
                    ),
                )
            return active_run


async def _register_via_manual_resident_takeover(
    bridge_id, capabilities, db, normalized_runtime, now, req,
    request, resolved_cwd, row, runtime_config, session_handle, terminal_id,
):
    """The register path where a managed agent is being flipped to resident by hand.

    Extracted from `register_agent` in v0.5.4, byte-identical apart from the dedent. Like the
    console-terminal branch beside it, this is an early exit that ends in the handler's response
    and encloses an already-extracted helper (`_stage_manual_resident_takeover`), so it needed
    both the call-site-shape rule and dependency-ordered inlining before it could be proved.
    """
    active_run = await _stage_manual_resident_takeover(
        db, req, row, bridge_id, normalized_runtime, session_handle,
        runtime_config, capabilities, resolved_cwd, now,
    )
    if bridge_id:
        await _record_bridge_registration(
            db,
            bridge_id=bridge_id,
            agent_id=req.agentId,
            machine_id=req.machineId or "",
            runtime=normalized_runtime,
            session_mode="resident",
            session_handle=session_handle,
            terminal_id=terminal_id,
            now=now,
        )
    await _invalidate_agent_live_state(db, req.agentId)
    await db.commit()
    ws = await _get_ws(request)
    if ws:
        await ws.broadcast("agent_registered", {
            "agentId": req.agentId,
            "role": req.role,
            "runtime": normalized_runtime,
            "machineId": req.machineId or "",
            "sessionMode": "managed",
            "residentBridgeId": bridge_id,
        })
    return {
        "ok": True,
        "agentId": req.agentId,
        "role": req.role,
        "status": row["status"] or "active",
        "runtime": normalized_runtime,
        "machineId": req.machineId or "",
        "bridgeId": bridge_id,
        "sessionMode": "managed",
        "ownershipTransition": "manual_switch_required",
        # Task 4.1: the takeover command the operator runs after flipping
        # the agent to resident in the dashboard (one-driver invariant).
        "resumeCommand": _resume_command_for(normalized_runtime, session_handle, req.agentId),
        "blockedByRun": active_run,
    }
