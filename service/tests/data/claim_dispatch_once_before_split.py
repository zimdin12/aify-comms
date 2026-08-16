"""The pre-split `_claim_dispatch_once`, frozen — the round trip's reference.

EDITED ONCE SINCE CAPTURE, under the rule the other pre-split fixtures record: the proof shows the
split was a pure block-lift OF THE CODE AS IT STANDS, so a later change to a line the split did not
move must be applied here IDENTICALLY, or the proof forbids ever editing the function again. The one
change: the three raw `run["dispatch_mode"] == ...` comparisons became `run_dispatch_mode`, folded
once above them, when `dispatch_mode` stopped being read two different ways by ten different readers.
Same statements, same positions, a normalised operand. Anything larger belongs in a reviewed
re-capture, not a fixture nudge to go green.

Not an importable module: it reads names that were in scope in its original module and are not here.
"""


async def _claim_dispatch_once(req: DispatchClaimRequest, request: Request):
    db = await get_db(busy_timeout_ms=SQLITE_CLAIM_BUSY_TIMEOUT_MS)
    try:
        await db.execute("BEGIN IMMEDIATE")
        # Plan 5 (2026-05-25): settings is needed below for the
        # _agent_execution_mode call (so the wrapper-backed channel route
        # at line 1047 fires symmetrically with the dispatch-create path).
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
        selected_run = None
        for run in runs:
            if hold_explicit_queue and (
                bool(run["queue_if_busy"]) or not bool(run["steer_if_busy"])
            ):
                continue
            run_execution_mode = (run["execution_mode"] or "managed").strip().lower()
            if supported_modes and run_execution_mode not in supported_modes:
                continue
            # NORMALISED, like `execution_mode` three lines above. Raw, a `Message_Only` run was
            # not recognised here and started a turn the sender asked to be message-only.
            run_dispatch_mode = str(run["dispatch_mode"] or "").strip().lower()
            if run_dispatch_mode == "message_only":
                await db.execute(
                    "UPDATE dispatch_runs SET status = 'cancelled', finished_at = ? WHERE id = ?",
                    (_now(), run["id"])
                )
                await _append_dispatch_event(db, run["id"], "skipped", "Dispatch mode is message_only")
                continue
            requested_runtime = run["requested_runtime"] or ""
            if requested_runtime and _normalize_runtime(requested_runtime) != agent_runtime:
                continue

            # Plan 5 (2026-05-25): pass settings so the wrapper-backed
            # channel route (line 1047) matches what _agent_execution_mode
            # returned when the run was created. Without settings here, the
            # helper short-circuits to 'managed', then line 11258 below sees
            # run.execution_mode='channel' != 'managed' and cancels the run.
            execution_mode, reason = _agent_execution_mode(agent, requested_runtime or None, settings=claim_settings)
            if reason or not execution_mode:
                final_status = "failed" if run_dispatch_mode == "require_start" else "cancelled"
                await db.execute(
                    "UPDATE dispatch_runs SET status = ?, error_text = ?, finished_at = ? WHERE id = ?",
                    (final_status, reason or "active dispatch unavailable", _now(), run["id"])
                )
                await _append_dispatch_event(db, run["id"], "skipped", reason or "active dispatch unavailable")
                continue
            if (run["execution_mode"] or execution_mode) != execution_mode:
                final_status = "failed" if run_dispatch_mode == "require_start" else "cancelled"
                reason = (
                    f'Run execution mode "{run["execution_mode"] or "unknown"}" does not match the '
                    f'current capabilities of agent "{req.agentId}" ({execution_mode}).'
                )
                await db.execute(
                    "UPDATE dispatch_runs SET status = ?, error_text = ?, finished_at = ? WHERE id = ?",
                    (final_status, reason, _now(), run["id"])
                )
                await _append_dispatch_event(db, run["id"], "skipped", reason)
                continue

            selected_run = run
            break

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
