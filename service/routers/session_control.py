"""Controlling a live session: interrupt it, stop it, restart it.

Extracted from `service/routers/sessions.py` in v0.5.4. Closure measured before the move —
`api_core` and `service` leaves only, nothing local to that router.

A SESSION CONTROL IS NOT A TERMINAL CONTROL, even though it often becomes one. The request names a
SESSION; this handler decides what that means for the thing actually running — an active dispatch
run gets a dispatch control, an attached terminal gets a terminal control, and a restart has to
prepare a spawn before anything is torn down. Three different rows, from one verb, chosen by what is
live at the time.

RESTART IS THE ONE THAT HAS BURNED THIS REPO. `_prepare_restart_spawn` runs BEFORE the teardown for
a reason: two separate defects have produced "restart produced no worker", one where a rotation
adopted the very terminal the restart was killing, one where a dying sidecar claimed the brief. The
ordering here is load-bearing, not stylistic.

Body and route decorator byte-identical.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from service.api_core.abandoned_spawn import (
    ABANDONED_SPAWN_SECONDS,
    _spawn_request_is_abandoned,
)
from service.api_core.active_run_lookup import _get_blocking_active_run
from service.api_core.agent_sessions import _settle_agent_for_session_control
from service.api_core.dispatch_run_state import _append_dispatch_control
from service.api_core.events import _append_terminal_control
# THE SET aify-env's OWN `restartTargetFor` USES, imported rather than retyped: two copies of
# "which sessions are live" agree until one is corrected, and this one gates a stop.
from service.api_core.liveness import _LIVE_SESSION_STATUSES
from service.api_core.records import _agent_session_to_dict
from service.api_core.routing import domain_router
from service.api_core.session_restart import _prepare_restart_spawn
from service.api_core.spawn_requests_io import _spawn_request_to_dict, _spawn_spec_to_dict
from service.api_core.terminal_status import _TERMINAL_ACTIVE_STATUSES
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import SessionControlRequest

router = domain_router()



async def _live_session_for(db, agent_id: str):
    """The agent's first LIVE session row, or None.

    THE NARROW SET, and which set this is matters. `_LIVE_SESSION_STATUSES` is the one aify-env's
    own `restartTargetFor` uses, so the route answers the question the caller ASKED. The wider
    `tuning.LIVE_SESSION_STATUSES` counts `idle` and `attached` as live, and refusing on those
    would block restarts the caller legitimately intends -- a different decision from closing this
    race, and not one to make inside it.

    LOWERED IN SQL, because a status is written by several producers and this comparison is the
    whole guard: a row saying `Running` must not read as not-live.
    """
    placeholders = ",".join("?" for _ in _LIVE_SESSION_STATUSES)
    cursor = await db.execute(
        f"SELECT id, status FROM agent_sessions WHERE agent_id = ? "
        f"AND LOWER(TRIM(status)) IN ({placeholders}) LIMIT 1",
        (agent_id, *sorted(_LIVE_SESSION_STATUSES)),
    )
    return await cursor.fetchone()


@router.post("/sessions/{session_id}/control")
async def control_session(session_id: str, req: SessionControlRequest, request: Request):
    action = str(req.action or "").strip().lower()
    # Lifecycle cleanup (2026-06-03): `recover` + `resume` were byte-identical
    # aliases of `restart` with NO dashboard caller — dropped. (Resident
    # wake-resume lives on POST /agents/{id}/control, a different endpoint.)
    if action not in {"stop", "restart", "recreate", "cli_takeover"}:
        raise HTTPException(400, f'Unsupported session control action "{req.action}"')

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))
        session = await cursor.fetchone()
        if not session:
            raise HTTPException(404, f'Session "{session_id}" not found')

        now = _now()
        agent_id = session["agent_id"]

        # ── THE CALLER'S PRECONDITION, ASKED WHERE THE STATE IS.
        #
        # aify-env's "start available agent" reads which agents have no live session and then
        # restarts the one it chose. Those are two round trips, and a worker starting in between
        # turned a start into a STOP of somebody's live terminal. Review traced it and asked for
        # conditional semantics at the authority rather than a third client-side reading -- which
        # is right: every check the caller makes is separated from the act by a network hop, and
        # only the thing performing the act can ask and act on one set of rows.
        #
        # BEFORE THE DISPATCH INTERRUPT BELOW -- but the ORDER is tidiness, not the guarantee,
        # and saying so keeps a later reader from defending the wrong thing. This route has one
        # commit, at the end, so a refusal raised anywhere before it discards every write of the
        # request: a refused restart cannot interrupt anybody in either order. Established by
        # mutation -- moving this check after the interrupt left a test of that ordering green.
        if req.only_if_no_live_session:
            # RESERVE THE WRITER BEFORE THE READ THAT DECIDES THIS REQUEST.
            #
            # `get_db` returns a plain connection with `isolation_level=''`, so a SELECT starts
            # no transaction and the first version of this guard ran with `in_transaction=False`.
            # Review reproduced the consequence against this route: another connection committed
            # `session=running` AFTER the guard read and BEFORE the first write, and the restart
            # queued a stop for the terminal that had just come live. A check that is not atomic
            # with the act it authorises is a check with a window in it.
            #
            # `BEGIN IMMEDIATE` takes the RESERVED lock at once rather than on the first write,
            # so nothing can commit between this read and the writes that depend on it. The
            # commit at the end releases it; every failure path closes the connection in the
            # `finally` below, which discards the transaction -- and each request owns its own
            # connection, so an open one can neither outlive the request nor reach another.
            await db.execute("BEGIN IMMEDIATE")
            live = await _live_session_for(db, agent_id)
            if live:
                raise HTTPException(
                    409,
                    f'Agent "{agent_id}" has a live session "{live["id"]}" ({live["status"]}), '
                    "so this conditional restart was refused. It was requested on the belief that "
                    "nothing was running; something is.",
                )

        active_run = await _get_blocking_active_run(db, agent_id)
        control_id = ""
        if active_run:
            control_id = await _append_dispatch_control(
                db,
                active_run["runId"],
                from_agent=req.from_agent or "dashboard",
                action="interrupt",
                body=req.body or f"Session {action} requested from dashboard.",
            )

        spawn_request_row = None
        spawn_spec_row = None
        cancelled_spawns = 0
        coldstart_warnings: list[str] = []
        if action in {"restart", "recreate"}:
            pending_cursor = await db.execute(
                """
                SELECT *
                FROM spawn_requests
                WHERE agent_id = ?
                  AND status IN ('queued', 'claimed', 'starting')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (agent_id,),
            )
            pending_spawn = await pending_cursor.fetchone()
            if pending_spawn and _spawn_request_is_abandoned(pending_spawn):
                # ABANDONED, so it does not get to block the remedy. Reported by sc-manager
                # 2026-08-18 as a deadlock, with the timeline: a spawn stuck `queued` for ~30
                # minutes, surviving an operator bridge+wrapper restart, while `comms_restart` —
                # the exact action the backstop's own message prescribes — refused BECAUSE that
                # spawn was pending. No worker, so the spawn stayed queued; spawn pending, so the
                # restart 409'd. From inside a session there was no way out.
                #
                # The guard is right to exist: two concurrent spawns for one agent is worse than a
                # slow one. It was simply fail-safe in ONE direction, protecting against
                # double-spawn at the cost of making a stuck spawn permanent.
                #
                # A TTL rather than a `force` flag, which was the other option offered. A flag needs
                # a caller to know the situation and choose correctly under pressure, and can be
                # passed by habit; a TTL needs nobody to know anything, and cannot be misused. The
                # window is generous — a spawn that is genuinely progressing updates its row, so
                # only one that has not moved AT ALL for the whole window is superseded here.
                await db.execute(
                    "UPDATE spawn_requests SET status = 'cancelled', error = ?, finished_at = ? WHERE id = ?",
                    (
                        f"superseded by a {action} after {ABANDONED_SPAWN_SECONDS}s with no progress "
                        f"— the spawn never produced a worker and was blocking the documented remedy",
                        now,
                        pending_spawn["id"],
                    ),
                )
                pending_spawn = None
            if pending_spawn:
                raise HTTPException(
                    409,
                    f'Agent "{agent_id}" already has pending spawn request "{pending_spawn["id"]}" ({pending_spawn["status"]}). '
                    f'If it never produces a worker it is superseded automatically after '
                    f'{ABANDONED_SPAWN_SECONDS}s with no progress; retry then.',
                )

        spawn_request_row, spawn_spec_row = await _prepare_restart_spawn(
            db, req, session, session_id, agent_id, action, now, coldstart_warnings,
            spawn_request_row, spawn_spec_row,
        )

        next_status = {
            "stop": "stopped",
            "restart": "restarting",
            "recreate": "ended",
            "cli_takeover": "cli-takeover",
        }[action]
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = ?, last_seen = ?, ended_at = CASE WHEN ? IN ('stopped','restarting','recovering','ended') THEN ? ELSE ended_at END
            WHERE id = ?
            """,
            (next_status, now, next_status, now, session_id),
        )
        cancelled_spawns = await _settle_agent_for_session_control(
            db, session_id, agent_id, action, now, cancelled_spawns,
        )

        # Halt the running backing (2026-06-07): Stop/Restart/Reset/CLI-takeover must PROMPTLY
        # kill the live managed PTY, not just flip DB status. Previously only the agent-control
        # stop enqueued a terminal stop, so the UI's session-control Stop left the worker running
        # as a headless orphan until a reaper / the next Restart's reap-prior. Enqueue a terminal
        # 'stop' for the session's live terminal(s). For restart/recreate the new spawn_request
        # was already queued above (and an env-offline target 409'd before reaching here), so we
        # never kill the old backing without a replacement queued. Resume is unaffected — it
        # carries via the durable session_handle, not the live PTY.
        live_terminals = await (await db.execute(
            "SELECT id, environment_id, bridge_id, status FROM terminal_sessions WHERE session_id = ?",
            (session_id,),
        )).fetchall()
        for term_row in live_terminals:
            if str(term_row["status"] or "").strip().lower() in _TERMINAL_ACTIVE_STATUSES:
                await _append_terminal_control(
                    db,
                    terminal_id=term_row["id"],
                    environment_id=term_row["environment_id"] or "",
                    bridge_id=term_row["bridge_id"] or "",
                    action="stop",
                    requested_by=req.from_agent or "dashboard",
                    body=f"Session {action} from dashboard.",
                )

        await db.commit()
        updated = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("session_control_requested", {"sessionId": session_id, "agentId": agent_id, "action": action})
            if spawn_request_row:
                await ws.broadcast(
                    "spawn_request_created",
                    {"spawnRequestId": spawn_request_row["id"], "environmentId": spawn_request_row["environment_id"]},
                )
        return {
            "ok": True,
            "action": action,
            "session": _agent_session_to_dict(updated),
            "interruptControlId": control_id,
            "cancelledSpawns": cancelled_spawns,
            "warnings": coldstart_warnings,
            "spawnRequest": _spawn_request_to_dict(spawn_request_row, _spawn_spec_to_dict(spawn_spec_row) if spawn_spec_row else None) if spawn_request_row else None,
        }
    finally:
        await db.close()
