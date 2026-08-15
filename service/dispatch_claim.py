"""The dispatch claim funnel — one bridge, one BEGIN IMMEDIATE, one run.

SERVICE LEVEL, NOT api_core, and the reason is the whole shape of this module. Every api_core leaf takes
`db` and owns no transaction: no `get_db(`, no `.commit(`, no `.rollback(`. This function opens the
connection, takes `BEGIN IMMEDIATE`, and commits or rolls back at 13 different points — because SERIALISING
THE CLAIM IS ITS JOB. Two bridges must not claim the same run, and the only thing that guarantees that is
a write transaction held across the select-and-mark. So it sits beside `terminal_write_queue.py`, the other
service-level transaction owner, rather than diluting the api_core rule to fit.

The 422 lines are not one decision. They are: work out whether this bridge may claim at all (delegated
entirely to `api_core/claim_gating.py`), clean up whatever the previous claimant left, then select and mark
exactly one run — each step able to end the request with a different, specific response shape. A bridge
receiving "no work" when it was actually blocked by a stale console owner is how several production
mysteries started, which is why the block REASONS are plumbed through rather than collapsed.

WHY IT COULD NOT MOVE UNTIL NOW. It reached twelve route-layer names. Seven were re-exports of api_core
leaves that `dispatch_messages/shared.py` was merely forwarding — a dependency that resolved, satisfied
every mechanical gate, and was still a lie about the layering. The other five became
`api_core/claim_gating.py`. This function is unchanged; what changed is that everything under it now has an
owner.

THE ROUTE KEEPS THE LONG POLL. `claim_dispatch` still owns the `longpoll.longpoll` wrapper, the `_is_empty`
predicate, the fallback, the lock result and the disconnect hook; this module is only the attempt that
wrapper retries. That split is deliberate: the transaction belongs to whoever can commit it, and the
waiting belongs to whoever holds the HTTP request.
"""

from __future__ import annotations

import time

from fastapi import (
    HTTPException,
    Request,
)
from service.api_core.active_run_discard import _fail_pending_controls_for_run
from service.api_core.agent_sessions import (
    _adopt_live_resident_driver,
    _agent_tombstone,
    _touch_current_agent_session,
)
from service.api_core.channel_delivery import _CHANNEL_CLAIM_RUNTIMES
from service.api_core.claim_block_reason import _bridge_claim_block_reason
from service.api_core.claim_gating import (
    _dispatch_conversation_context,
    _has_claimable_steerable_run,
    _mark_dispatch_source_messages_read,
    _release_stale_console_owner_for_claim,
    _turn_busy_holds_delivery,
)
from service.api_core.dispatch_state import _get_dispatch_state_for_agent
from service.api_core.events import _append_dispatch_event
from service.api_core.claim_run_selection import _select_claimable_run
from service.api_core.liveness import ACTIVE_RUN_BRIDGE_STALE_SECONDS
from service.api_core.recovery_writes import _record_channel_sidecar_heartbeat
from service.api_core.runtime import (
    _normalize_runtime,
    _normalize_session_mode,
)
from service.api_core.serialization import (
    _json_loads_or,
    _machine_ids_same_host,
    _row_require_reply,
)
from service.api_core.settings import _load_settings
from service.api_core.ws import _get_ws
from service.clock import (
    iso_to_epoch as _iso_to_epoch,
    now as _now,
)
from service.db import (
    SQLITE_CLAIM_BUSY_TIMEOUT_MS,
    get_db,
)
from service.models import DispatchClaimRequest
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state


async def _claim_dispatch_once(req: DispatchClaimRequest, request: Request):
    db = await get_db(busy_timeout_ms=SQLITE_CLAIM_BUSY_TIMEOUT_MS)
    try:
        await db.execute("BEGIN IMMEDIATE")
        # Plan 5 (2026-05-25): settings is needed below for the `_agent_execution_mode` call, so
        # the wrapper-backed channel route fires symmetrically with the dispatch-create path. That
        # call now lives in `api_core/claim_run_selection.py`, which is why `claim_settings` is read
        # here and passed down rather than used in this function. (The comment also cited "line
        # 1047", from a file that no longer exists at that length — a line number is a pointer that
        # rots on the next edit, so it is not replaced with another one.)
        claim_settings = await _load_settings(db)
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (req.agentId,))
        agent = await cursor.fetchone()
        if not agent:
            tombstone = await _agent_tombstone(db, req.agentId)
            if tombstone:
                await db.rollback()
                raise HTTPException(410, f"Agent '{req.agentId}' was intentionally removed")
            await db.rollback()
            raise HTTPException(404, f"Agent '{req.agentId}' not found")

        if req.machineId and agent["machine_id"] and not _machine_ids_same_host(agent["machine_id"], req.machineId):
            await db.rollback()
            return {"ok": True, "run": None}

        # Phase H1 (status v2): an explicitly DISABLED agent (status "stopped")
        # must terminate its worker + polling bridge, not poll forever. Surface a
        # terminal `stopped` signal (reversible — unlike 410 removed, this does NOT
        # clear the agent's session binding) so the channel-sidecar / delivery loop
        # self-exits and the orphan terminal reaps itself.
        if str(agent["status"] or "").strip().lower() == "stopped":
            await db.commit()
            return {"ok": True, "run": None, "stopped": True}

        agent_runtime = _normalize_runtime(agent["runtime"] or "generic")

        # Mode FSM release signal (Task 4.1, 2026-05-30). A DISPLACED managed
        # sidecar (claude-channel.js / hermes-channel.js, bridgeKind="channel-sidecar")
        # must STOP driving once the operator switches the agent to resident.
        # We surface `release: true` in the claim response so the sidecar exits
        # its poll loop / goes idle. This is the one-driver invariant in action:
        # the managed driver releases so the resident TUI can take the session.
        #
        # driver_state guard (operator-reported 2026-05-31, sc-manager): the
        # original condition was the blunt `session_mode != managed`, which ALSO
        # fired for a NATIVELY-resident agent whose channel sidecar is its SOLE
        # delivery path — so every resident claude/hermes agent's sidecar was told
        # to release and queued runs never got claimed (delivery silently stalled).
        # A live resident driver has driver_state='driving' (set on resident
        # register/claim); a managed→resident switch sets driver_state='idle'
        # (the displaced managed driver, awaiting a resident takeover). Release
        # ONLY when not actively driven, so the resident delivery sidecar keeps
        # claiming for a live resident session.
        if (
            str(req.bridgeKind or "").strip().lower() == "channel-sidecar"
            and _normalize_session_mode(agent["session_mode"] or "resident") != "managed"
            and str((agent["driver_state"] if "driver_state" in agent.keys() else "") or "").strip().lower() != "driving"
        ):
            # Live resident bridge ⇒ this is the resident's OWN delivery sidecar, not a
            # displaced managed driver — adopt driving and keep claiming instead of
            # releasing (see _adopt_live_resident_driver; the sc-manager strand).
            if not await _adopt_live_resident_driver(db, agent["id"]):
                await db.commit()
                return {"ok": True, "run": None, "release": True, "sessionMode": _normalize_session_mode(agent["session_mode"] or "resident")}

        # Self-heal a superseded channel-sidecar (operator-reported 2026-05-31,
        # sc-claude). A managed agent's channel sidecar and the visible TUI's
        # managed-wrapper-child bridge legitimately COEXIST (complementary pair).
        # During managed-PTY churn the sidecar's row briefly goes stale and a
        # wrapper-child registration superseded it (the 5-min-stale clause
        # overrode the complementary-pair carve-out in _record_bridge_registration).
        # Once superseded, _bridge_claim_block_reason permanently BLOCKED the
        # still-live sidecar — and the block fires BEFORE the heartbeat upsert,
        # so it could never recover; queued channel runs were never delivered.
        # A live channel-sidecar poll is proof of life: un-supersede its OWN row
        # so it resumes claiming. The mode-FSM release above (driver_state-gated)
        # is the ONLY legitimate "stop driving" signal for a channel sidecar.
        # KEPT (Task A' #154, 2026-06-01): the 30s liveness beat does NOT revive a
        # superseded bridge (it short-circuits on superseded rows — see
        # test_liveness_beat_does_not_revive_superseded_bridge), so only this
        # claim-path self-heal can un-supersede a still-live sidecar. Removal
        # probe broke test_superseded_channel_sidecar_self_heals_on_claim.
        if str(req.bridgeKind or "").strip().lower() == "channel-sidecar" and req.bridgeId:
            await db.execute(
                """
                UPDATE bridge_instances
                SET superseded_by = '', superseded_at = NULL, last_seen = ?
                WHERE id = ? AND agent_id = ? AND COALESCE(superseded_by, '') != ''
                """,
                (_now(), req.bridgeId, req.agentId),
            )

        # Reject claims from stale stdio bridges. The bridge_instances row
        # catches normal supersession, while runtimeState.bridgeInstanceId
        # catches the more dangerous case where an old process keeps polling
        # after its bridge row has disappeared or been compacted away.
        blocked_by = await _bridge_claim_block_reason(
            db,
            bridge_id=req.bridgeId or "",
            agent_id=req.agentId,
            agent_row=agent,
            execution_modes=req.executionModes or [],
            bridge_kind_hint=req.bridgeKind or "",
        )
        if blocked_by:
            await db.commit()
            return {
                "ok": True,
                "run": None,
                "blockedBy": blocked_by,
            }

        # Update bridge liveness — the claim poll itself is the heartbeat.
        if req.bridgeId:
            is_channel_sidecar_claim = (
                str(req.bridgeKind or "").strip().lower() == "channel-sidecar"
            )
            if is_channel_sidecar_claim:
                # Task 1.6b: a standalone channel sidecar (hermes-channel.js /
                # claude-channel.js) has no bridge row until it claims a run, so
                # a plain UPDATE would no-op for an idle poller and status would
                # flap to `available`. Upsert its channel-sidecar liveness row so
                # the continuous idle poll keeps last_seen fresh and
                # `_has_live_channel_sidecar` stays true. Claude is unaffected
                # (its liveness is the wrapper PTY terminal_session) but this is
                # harmless if claude-channel.js also declares the flag.
                await _record_channel_sidecar_heartbeat(
                    db,
                    bridge_id=req.bridgeId,
                    agent_id=req.agentId,
                    machine_id=req.machineId or "",
                    runtime=agent_runtime,
                    session_mode=agent["session_mode"] or "managed",
                    now=_now(),
                )
            else:
                await db.execute(
                    "UPDATE bridge_instances SET last_seen = ? WHERE id = ? AND agent_id = ?",
                    (_now(), req.bridgeId, req.agentId),
                )

        # Stale-run cleanup.
        #
        # The bridge-side gate (ACTIVE_RUNS in server.js) prevents a live
        # bridge from calling /dispatch/claim while it has work in flight.
        # That only proves the *polling* bridge is idle. Wrapper-backed
        # managed agents have two bridge identities in play: the environment
        # bridge keeps polling, while the wrapper-child bridge owns the
        # active turn. Treat a different owner as stale only after the owner
        # bridge itself stops heartbeating or is superseded.
        active_state = await _get_dispatch_state_for_agent(db, req.agentId)
        active_run = active_state.get("activeRun")
        if active_run:
            owner = (active_run.get("claimBridgeId") or "").strip()
            if owner and owner == req.bridgeId:
                await db.commit()
                return {"ok": True, "run": None, "blockedBy": active_run}
            if owner:
                owner_cursor = await db.execute(
                    """
                    SELECT last_seen, superseded_by, bridge_kind
                    FROM bridge_instances
                    WHERE id = ? AND agent_id = ?
                    """,
                    (owner, req.agentId),
                )
                owner_bridge = await owner_cursor.fetchone()
                owner_superseded_by = str((owner_bridge["superseded_by"] if owner_bridge else "") or "").strip()
                owner_last_seen = _iso_to_epoch(str((owner_bridge["last_seen"] if owner_bridge else "") or ""))
                owner_heartbeat_age = time.time() - owner_last_seen if owner_last_seen else None
                if (
                    owner_bridge
                    and not owner_superseded_by
                    and owner_heartbeat_age is not None
                    and owner_heartbeat_age < ACTIVE_RUN_BRIDGE_STALE_SECONDS
                ):
                    await db.commit()
                    return {
                        "ok": True,
                        "run": None,
                        "blockedBy": {
                            **active_run,
                            "reason": "active_run_owner_bridge_still_heartbeating",
                            "ownerBridgeId": owner,
                            "ownerBridgeKind": str(owner_bridge["bridge_kind"] or ""),
                            "currentBridgeId": req.bridgeId or "",
                            "retryAfterSeconds": max(1, int(ACTIVE_RUN_BRIDGE_STALE_SECONDS - owner_heartbeat_age)),
                            "hint": "The active run owner bridge is still heartbeating; waiting avoids killing a live wrapper-managed turn.",
                        },
                    }
            active_since = _iso_to_epoch(active_run.get("startedAt") or active_run.get("requestedAt"))
            if owner:
                stale_seconds = ACTIVE_RUN_BRIDGE_STALE_SECONDS
                wait_hint = "A previous bridge claimed this run recently. Waiting avoids killing a run that may still complete."
            else:
                stale_seconds = max(300, int(claim_settings.get("active_run_stale_minutes", 30) or 30) * 60)
                wait_hint = "An unowned terminal turn is still within its stale timeout. Waiting avoids interrupting a visible PTY turn."
            active_age = time.time() - active_since if active_since else stale_seconds + 1
            if active_age < stale_seconds:
                await db.commit()
                return {
                    "ok": True,
                    "run": None,
                    "blockedBy": {
                        **active_run,
                        "reason": "active_run_owned_by_previous_bridge",
                        "ownerBridgeId": owner or "",
                        "currentBridgeId": req.bridgeId or "",
                        "retryAfterSeconds": max(1, int(stale_seconds - active_age)),
                        "hint": wait_hint,
                    },
                }
            finished_at = _now()
            owner_label = owner or "unowned"
            await db.execute(
                "UPDATE dispatch_runs SET status = 'failed', summary = ?, finished_at = ? WHERE id = ?",
                (
                    f'Auto-healed: bridge "{owner_label}" replaced by "{req.bridgeId}"',
                    finished_at,
                    active_run["runId"],
                ),
            )
            await _append_dispatch_event(db, active_run["runId"], "auto_heal", f"Stale run cleanup: {owner_label} -> {req.bridgeId}")
            await _fail_pending_controls_for_run(db, active_run["runId"], handled_at=finished_at, response_text=f'Stale run cleaned by live bridge "{req.bridgeId}".')
        owner_cursor = await db.execute(
            """
            SELECT id, environment_id, owner_mode, terminal_id, terminal_status
            FROM agent_sessions
            WHERE agent_id = ?
              AND status IN ('starting','running','recovering','restarting')
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (req.agentId,),
        )
        owner_session = await owner_cursor.fetchone()
        supported_modes = {str(mode or "").strip().lower() for mode in (req.executionModes or []) if str(mode or "").strip()}
        # See _CHANNEL_CLAIM_RUNTIMES and _bridge_claim_block_reason: managed
        # wrapper-backed Codex/Hermes channel runs are claimable only by the
        # wrapper PTY child bridge, not the main environment bridge.
        channel_claim = agent_runtime in _CHANNEL_CLAIM_RUNTIMES and "channel" in supported_modes
        if owner_session and str(owner_session["owner_mode"] or "").strip().lower() == "console" and not channel_claim:
            blocked_by_console = await _release_stale_console_owner_for_claim(db, owner_session, req)
            if blocked_by_console:
                await db.commit()
                return {
                    "ok": True,
                    "run": None,
                    "blockedBy": blocked_by_console,
                }
        # Turn-busy claim gate: if the raw harness state says the agent is mid-turn
        # (turn_busy=1),
        # don't return queued runs. Operator-asked 2026-05-22:
        # "queue should wait until agent stops working" — without this
        # gate, the SENDER's queueIfBusy=true correctly held the run
        # in 'queued' state, but the bridge's next claim cycle picked
        # it up and delivered immediately because the claim endpoint
        # didn't respect turn_busy. The turn-end event is the authoritative clear;
        # once that fires, next claim
        # picks up the queued run as designed.
        #
        # CHANNEL/RESIDENT STEER CARVE-OUT (2026-06-02, send-deadlock fix):
        # a channel/resident-mode run to a STEER-capable target (a managed or
        # channelEnabled Claude/Hermes — `steer` in _row_capabilities, the
        # same signal used by the send-time steer path) is INJECTED into the
        # agent's native mid-turn input path; these harnesses accept multiple injects
        # in order. Deferring an ordinary send behind turn_busy was the deadlock, so when the
        # target can steer AND a claimable channel/resident run is queued, do NOT
        # defer — fall through and let it claim + inject immediately. The gate is
        # PRESERVED for every other case (non-steer-capable runtimes, managed
        # headless runs) so a genuinely-uninjectable target still waits for the
        # turn to end.
        hold_explicit_queue = False
        try:
            # Explicit queue means exactly "after this turn": key on the RAW harness signal,
            # never on derived status (that reinterpretation is what let queued sends land
            # mid-turn, #236). The ONE bound is the anti-strand ceiling — see
            # _turn_busy_holds_delivery: an abandoned turn_busy=1 that the dead-bridge sweeper
            # cannot reach (hook-owned turns, live bridge) would otherwise hold queued work
            # FOREVER, and for a target without `steer` the early return below makes that agent
            # permanently deaf while status already reports it idle.
            if await _turn_busy_holds_delivery(db, req.agentId):
                hold_explicit_queue = True
                if not await _has_claimable_steerable_run(
                    db,
                    agent_row=agent,
                    supported_modes=supported_modes,
                    agent_runtime=agent_runtime,
                ):
                    await db.commit()
                    return {"ok": True, "run": None}
        except Exception:
            # If turn state is unreadable, fall through and let the normal claim
            # flow proceed — better to deliver than block.
            pass

        run_cursor = await db.execute(
            """
            SELECT * FROM dispatch_runs
            WHERE target_agent = ? AND status = 'queued'
              AND COALESCE(result_message_id, '') = ''
            ORDER BY requested_at ASC
            LIMIT 25
            """,
            # Exclude runs already answered from the inbox before being claimed
            # (bughunt 2026-07-03): a require_reply run answered while still 'queued'
            # kept status='queued' (the answered-run reconcile only scans 'delivered'),
            # so the claimer re-woke the target to redo already-answered work.
            (req.agentId,)
        )
        runs = await run_cursor.fetchall()
        selected_run = await _select_claimable_run(
            db, req, runs, agent,
            agent_runtime, claim_settings, hold_explicit_queue, supported_modes,
        )

        if not selected_run:
            await db.commit()
            return {"ok": True, "run": None}

        claimed_at = _now()
        await db.execute(
            "UPDATE dispatch_runs SET status = 'claimed', claimed_at = ?, claim_machine_id = ?, claim_bridge_id = ?, runtime = ? WHERE id = ?",
            (claimed_at, req.machineId or "", req.bridgeId or "", agent_runtime, selected_run["id"])
        )
        # One-driver FSM (Task 4.1): a managed sidecar that successfully claims a
        # run for a managed agent is the live driver -> mark driving so a
        # cross-mode resident attach is rejected by the collision guard.
        if (
            str(req.bridgeKind or "").strip().lower() == "channel-sidecar"
            and _normalize_session_mode(agent["session_mode"] or "resident") == "managed"
        ):
            await db.execute(
                "UPDATE agents SET driver_state = 'driving' WHERE id = ?",
                (req.agentId,),
            )
        await _invalidate_agent_live_state(db, req.agentId)
        await _touch_current_agent_session(
            db,
            req.agentId,
            _json_loads_or(agent["runtime_state"], {}),
            claimed_at,
        )
        marked_read = await _mark_dispatch_source_messages_read(db, selected_run, req.agentId, claimed_at)
        await _append_dispatch_event(db, selected_run["id"], "claimed", f"machine={req.machineId or ''}")
        if marked_read > 1:
            await _append_dispatch_event(db, selected_run["id"], "read_receipts", f"Marked {marked_read} dispatched source messages read")
        await db.commit()

        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_claimed", {"runId": selected_run["id"], "targetAgentId": req.agentId})

        return {
            "ok": True,
            "run": {
                "id": selected_run["id"],
                "messageId": selected_run["message_id"],
                "from": selected_run["from_agent"],
                "targetAgentId": selected_run["target_agent"],
                "type": selected_run["message_type"],
                "subject": selected_run["subject"],
                "body": selected_run["body"],
                "priority": selected_run["priority"],
                "inReplyTo": selected_run["in_reply_to"],
                "status": "claimed",
                "mode": selected_run["dispatch_mode"],
                "executionMode": selected_run["execution_mode"] or "managed",
                "queueIfBusy": bool(selected_run["queue_if_busy"]),
                "steerIfBusy": bool(selected_run["steer_if_busy"]),
                "runtime": agent_runtime,
                "requireReply": _row_require_reply(selected_run),
                "conversationContext": await _dispatch_conversation_context(db, selected_run),
                "claimBridgeId": req.bridgeId or "",
                "requestedRuntime": selected_run["requested_runtime"] or None,
                "claimedAt": claimed_at,
            }
        }
    finally:
        await db.close()
