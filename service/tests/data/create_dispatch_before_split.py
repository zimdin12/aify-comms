"""`create_dispatch` exactly as it was before any extract-method split — the proof's reference.

Committed as a FIXTURE, not recovered from git on demand, and captured with an EXPLICIT utf-8 decode.
Both rules are here because both were learned by breaking them; see
`service/tests/data/register_agent_before_split.py`.

NOT AN IMPORTABLE MODULE — a function lifted out of its module reads names that were in scope there.
"""

async def create_dispatch(req: DispatchRequest, request: Request):
    if not req.to and not req.toRole:
        raise HTTPException(400, "Need 'to' or 'toRole'")
    _reject_sender_truncated_body(req.body)
    if req.mode == "message_only":
        raise HTTPException(400, "Dispatch no longer supports mode='message_only'. Use comms_send for normal live messaging or comms_dispatch without message_only for tracked work.")

    db = await get_db()
    try:
        await _touch_agent(db, req.from_agent)
        resolved_in_reply_to, reply_parent_found = await _resolve_reply_parent_message_id(db, req.inReplyTo)
        warnings = []
        if req.inReplyTo and not reply_parent_found:
            warnings.append(
                f'inReplyTo "{req.inReplyTo}" did not match an existing message; dispatch was sent unthreaded.'
            )
        recipients = await _resolve_recipient_ids(db, to=req.to, to_role=req.toRole, from_agent=req.from_agent)

        if not recipients:
            return {"ok": False, "error": "No recipients found", "recipients": [], "runs": []}

        not_started = []
        launchable_recipients = []
        console_recipients = {}
        recipient_rows = {}
        settings = await _load_settings(db)
        for recipient_id in recipients:
            cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
            row = await cursor.fetchone()
            if row:
                row, _transition = await _auto_return_resident_to_managed_if_possible(db, row, settings=settings)
            if row:
                recipient_rows[recipient_id] = row
                # Plan 2 (2026-05-25) pi flip: reject new dispatches to a pi
                # agent that's currently mid-flip from resident -> managed.
                # _drain_and_flip_pi_resident_agents will migrate it once
                # any active runs drain (~5s). The operator should retry
                # after the flip completes. Without this gate, dispatches
                # queue against a session_mode the runtime no longer
                # supports.
                if _normalize_runtime(row["runtime"] or "") == "pi":
                    _rs = _json_loads_or(row["runtime_state"], {})
                    if _rs.get("pi_resident_pending_flip"):
                        raise HTTPException(
                            409,
                            f'Agent "{recipient_id}" is migrating from resident '
                            f"to managed (pi flip pending). Retry in a few "
                            f"seconds — the drain loop will flip the agent "
                            f"once any active runs complete."
                        )
            execution_mode = None
            reason = None if row else "agent is not registered"
            if row:
                runtime = _normalize_runtime(row["runtime"] or "generic")
                if req.requestedRuntime and _normalize_runtime(req.requestedRuntime) != runtime:
                    reason = f'requested runtime "{req.requestedRuntime}" does not match registered runtime "{runtime}"'
                elif runtime in _NATIVE_MANAGED_RUNTIMES:
                    # Plan 5 (2026-05-25): pass settings so
                    # _agent_execution_mode can detect wrapper-backed managed
                    # runtimes (managed_via_wrapper) and return
                    # execution_mode='channel'. Without settings the helper
                    # short-circuits to 'managed' (line 1065) and the
                    # wrapper-backed dispatch path never fires.
                    execution_mode, reason = _agent_execution_mode(row, req.requestedRuntime, settings=settings)
                    # Plan 5 follow-up (2026-05-26): the PTY-input
                    # (console_recipients) downgrade below MUST only fire
                    # for execution_mode='managed'. When the helper returns
                    # 'channel' for wrapper-backed codex/hermes, leave
                    # the run as channel-mode so the wrapper PTY's child
                    # bridge can pick it up. Falling through to
                    # console_recipients would route the message via raw PTY
                    # keystrokes — the scrambled-text failure mode the
                    # operator explicitly banned.
                    if (
                        not reason
                        and execution_mode == "channel"
                        and _managed_terminal_backing_enabled(settings)
                        and _managed_via_wrapper_for_runtime(settings, runtime)
                    ):
                        console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                        if not console_terminal:
                            console_terminal = await _ensure_managed_pty_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                            )
                        if not console_terminal:
                            reason = f"Managed {runtime} wrapper PTY is unavailable; recover or restart the environment-managed session."
                    if not reason and execution_mode == "managed":
                        if (
                            _managed_terminal_backing_enabled(settings)
                            and _insert_messages_via_console(settings)
                            and runtime not in {"pi", "opencode"}
                        ):
                            console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                            if not console_terminal:
                                console_terminal = await _ensure_managed_pty_for_dispatch(
                                    db,
                                    recipient_id,
                                    runtime=runtime,
                                    settings=settings,
                                    requested_by=req.from_agent,
                                )
                            if console_terminal:
                                console_recipients[recipient_id] = console_terminal
                                execution_mode = None
                            else:
                                reason = await _managed_environment_unavailable_reason(db, row)
                elif runtime in _CHANNEL_MANAGED_RUNTIMES:
                    # Plan 5 (2026-05-25): pass settings (parity with the
                    # NATIVE_MANAGED branch above). _agent_execution_mode
                    # uses settings to gate the wrapper-backed channel route.
                    execution_mode, reason = _agent_execution_mode(row, req.requestedRuntime, settings=settings)
                    if not reason and execution_mode == "channel":
                        reason = await _managed_environment_unavailable_reason(db, row)
                    if not reason and execution_mode == "channel" and _insert_messages_via_console(settings):
                        # PTY-input path — only the opt-in via-console
                        # delivery mode goes through here. Default-false
                        # routing leaves the run launchable and the post-
                        # create _apply_channel_routing_to_claude_runs
                        # flips execution_mode='channel' so claude-channel.js
                        # claims it.
                        console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                        if not console_terminal:
                            console_terminal = await _ensure_managed_pty_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                            )
                        if console_terminal:
                            console_recipients[recipient_id] = console_terminal
                            execution_mode = None
                        else:
                            reason = "Claude claude-aify backing PTY is unavailable; restart the environment bridge or recover the session."
                    elif reason:
                        execution_mode = None
                else:
                    console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                    if not console_terminal:
                        console_terminal = await _ensure_managed_pty_for_dispatch(
                            db,
                            recipient_id,
                            runtime=runtime,
                            settings=settings,
                            requested_by=req.from_agent,
                        )
                    if console_terminal:
                        console_recipients[recipient_id] = console_terminal
                    else:
                        # Plan 5 (2026-05-25): pass settings — see sibling
                        # branches above for rationale.
                        execution_mode, reason = _agent_execution_mode(row, req.requestedRuntime, settings=settings)
                        if not reason and execution_mode:
                            reason = await _managed_environment_unavailable_reason(db, row)
            if reason or not execution_mode:
                if recipient_id not in console_recipients:
                    not_started.append(_dispatch_fix_hint(recipient_id, row, reason or "active dispatch unavailable"))
            else:
                launchable_recipients.append((recipient_id, execution_mode))

        if req.mode == "require_start" and not_started:
            details = "; ".join(f"{item['targetAgentId']}: {item['reason']}" for item in not_started)
            return {
                "ok": False,
                "error": f"Active dispatch unavailable for: {details}",
                "recipients": recipients,
                "runs": [],
                "notStarted": not_started,
            }

        message_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        source_message_ids = {}
        ts = int(time.time() * 1000)
        for recipient_id in recipients:
            recipient_message_id = f"{message_id}-{recipient_id}" if len(recipients) > 1 else message_id
            source_message_ids[recipient_id] = recipient_message_id
            await db.execute(
                "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, in_reply_to, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    recipient_message_id,
                    req.from_agent, recipient_id, "direct", req.type, req.subject, req.body,
                    req.priority, 0 if recipient_id in console_recipients else 1, resolved_in_reply_to, ts
                )
            )
        if resolved_in_reply_to:
            await _link_reply_message_to_dispatch_run(
                db,
                from_agent=req.from_agent,
                resolved_in_reply_to=resolved_in_reply_to,
                reply_message_id=_primary_result_message_id(message_id, recipients),
                reply_type=req.type,
                reply_body=req.body,
            )

        runs = []
        if launchable_recipients:
            require_reply = _dispatch_requires_reply(req.requireReply, default=_message_type_expects_reply(req.type))
            runs = await _create_dispatch_runs(
                db,
                [recipient_id for recipient_id, _ in launchable_recipients],
                from_agent=req.from_agent,
                message_type=req.type,
                subject=req.subject,
                body=req.body,
                priority=req.priority,
                in_reply_to=resolved_in_reply_to,
                dispatch_mode=req.mode,
                execution_mode="managed",
                requested_runtime=req.requestedRuntime,
                message_id=message_id if len(recipients) == 1 else None,
                source_message_ids=source_message_ids,
                steer=req.steer,
                require_reply=require_reply,
            )
            runs = await _finalize_dispatch_runs(db, runs, launchable_recipients, not_started)
            await _apply_channel_routing_to_claude_runs(db, runs, settings)

        console_deliveries = []
        for recipient_id, terminal in console_recipients.items():
            terminal_id = str(terminal["terminal_id"] or "").strip()
            recipient_message_id = source_message_ids.get(recipient_id, message_id)
            terminal_runtime = _normalize_runtime(terminal["runtime"] or "")
            control_id = await _append_terminal_control(
                db,
                terminal_id=terminal_id,
                environment_id=terminal["environment_id"],
                bridge_id=terminal["bridge_id"] or "",
                action="input",
                requested_by=req.from_agent,
                body=_console_dispatch_input_body(
                    req,
                    recipient_id=recipient_id,
                    message_id=recipient_message_id,
                    bracketed_paste=True,
                ),
            )
            submit_control_id = ""
            await _append_terminal_event(
                db,
                terminal_id,
                "terminal_input_requested",
                json.dumps({
                    "requestedBy": req.from_agent,
                    "controlId": control_id,
                    "submitControlId": submit_control_id,
                    "source": "dispatch",
                    "messageId": recipient_message_id,
                }),
            )
            contract_run_id = await _record_terminal_delivery_contract(
                db,
                source_message_id=recipient_message_id,
                from_agent=req.from_agent,
                recipient_id=recipient_id,
                message_type=req.type,
                subject=req.subject,
                body=req.body,
                priority=req.priority,
                in_reply_to=resolved_in_reply_to,
                require_reply=_dispatch_requires_reply(req.requireReply, default=_message_type_expects_reply(req.type)),
                terminal_id=terminal_id,
                control_id=control_id,
                runtime=terminal["runtime"] or "",
            )
            console_deliveries.append({
                "targetAgentId": recipient_id,
                "terminalId": terminal_id,
                "controlId": control_id,
                "contractRunId": contract_run_id,
                "status": "sent_to_console",
            })

        recipient_info = {}
        for recipient_id in recipients:
            info = await _get_recipient_info(db, recipient_id)
            if info:
                recipient_info[recipient_id] = {
                    "status": info["status"],
                    "unread": info["unread"],
                    "runtime": info["runtime"],
                    "machineId": info["machineId"],
                }

        await db.commit()
        # Wake any long-polling claim waiters now that work is committed and visible.
        # A new run was queued and/or a steer control / console delivery appended;
        # over-notifying is harmless (a woken waiter that finds nothing re-sleeps).
        longpoll.notify("dispatch")
        longpoll.notify("control")
        if console_deliveries:
            longpoll.notify("terminal-control")
        ws = await _get_ws(request)
        if ws:
            for recipient_id in recipients:
                await ws.notify_agent(recipient_id, "dispatch_request", {"from": req.from_agent, "subject": req.subject})
            for run in runs:
                if run.get("steered"):
                    continue
                await ws.broadcast("dispatch_queued", {"runId": run["runId"], "targetAgentId": run["targetAgentId"]})
            for delivery in console_deliveries:
                await ws.broadcast("terminal_control_requested", {"terminalId": delivery["terminalId"], "action": "input"})
        for recipient_id in recipients:
            _wake_agent(recipient_id)

        return {
            "ok": True,
            "messageId": message_id,
            "recipients": recipients,
            "recipientStatus": recipient_info,
            "runs": runs,
            "notStarted": not_started,
            "consoleDeliveries": console_deliveries,
            "warnings": warnings,
        }
    finally:
        await db.close()
