"""`send_message` exactly as it was before any extract-method split — the proof's reference.

Committed as a FIXTURE rather than recovered from git on demand: a proof that needs `.git` cannot run
from a clean clone, which is how the v0.5 route gates shipped broken once.

CAPTURED WITH AN EXPLICIT utf-8 DECODE. `subprocess.run(..., text=True)` decodes using the Windows locale
and mangles every multi-byte character in the comments; that produced a round-trip failure pointing at an
untouched block when the register_agent fixture was made that way. Decode the bytes.

NOT AN IMPORTABLE MODULE. A function lifted out of its module reads names that were in scope THERE.
`scripts/undefined_name_sweep.py` skips `service/tests/data/` for that reason. The test reads it as text.
"""

async def send_message(req: MessageSend, request: Request):
    if not req.to and not req.toRole:
        raise HTTPException(400, "Need 'to' or 'toRole'")
    _reject_sender_truncated_body(req.body)
    db = await get_db()
    try:
        await _touch_agent(db, req.from_agent)
        # NOTE: do NOT clear turn_busy here based on the agent sending a
        # message. The agent might send a reply and then keep working
        # (more tool calls, more analysis, more messages) — clearing on
        # response would flip status to "active" while real work is
        # still happening. Turn-end is a harness-level signal: each
        # runtime delivers its own (codex turn/completed, pi agent_end,
        # hermes process exit, opencode SDK turn-complete). Resident
        # claude under claude-channel.js needs its Stop hook to call
        # the bridge — see install.sh's claude wrapper installation.
        # Idempotency (#240): a bridge send that hit a transient socket error may have
        # actually landed server-side. /messages/send is otherwise non-idempotent (a fresh
        # msg_id per call), so a retry would DOUBLE-send — which is why the bridge excluded
        # it from its retry list and instead DROPPED the send, stranding owed replies. With
        # an optional clientNonce, a retry of the same logical send collapses to the original
        # message: look it up by (from_agent, client_nonce) and short-circuit with the SAME
        # messageId so the bridge can retry safely. Scoped per sender; absent nonce = today's
        # behavior (old bridges omit it, so no dedup — fully backward compatible).
        client_nonce = str(req.clientNonce or "").strip()
        if client_nonce:
            prior = await (await db.execute(
                "SELECT id FROM messages WHERE from_agent = ? AND client_nonce = ? ORDER BY timestamp ASC LIMIT 1",
                (req.from_agent, client_nonce),
            )).fetchone()
            if prior is not None:
                return {
                    "ok": True,
                    "messageId": prior["id"],
                    "replayed": True,
                    "recipients": [],
                    "recipientStatus": {},
                    "dispatchRuns": [],
                    "notStarted": [],
                    "consoleDeliveries": [],
                    "warnings": [],
                }
        msg_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        ts = int(time.time() * 1000)
        resolved_in_reply_to, reply_parent_found = await _resolve_reply_parent_message_id(db, req.inReplyTo)
        warnings = []
        if req.inReplyTo and not reply_parent_found:
            warnings.append(
                f'inReplyTo "{req.inReplyTo}" did not match an existing message; message was sent unthreaded.'
            )

        # ASYMMETRY: replies bypass the live-wake hard-gate by design.
        # A reply must ALWAYS be persisted + threaded (and close its
        # require_reply run) even when the recipient can't be live-woken —
        # the recipient simply sees it in their inbox. Hard-rejecting a
        # reply because the recipient's bridge is stale dropped legitimate
        # replies (broke managed-hermes self-reply when the original
        # sender's resident bridge was stale) and left the require_reply
        # run open forever. The live-wake hard-gate below stays in force
        # only for NEW dispatches (requests/etc.), never for replies.
        # A reply is identified by a resolved inReplyTo OR type=="response".
        is_reply = bool(resolved_in_reply_to) or str(req.type or "").strip().lower() == "response"

        recipients = await _resolve_recipient_ids(db, to=req.to, to_role=req.toRole, from_agent=req.from_agent)

        if not recipients:
            return {"ok": False, "error": "No recipients found", "recipients": []}

        launchable_recipients = []
        not_started = []
        console_recipients = {}
        dispatch_recipients = [r for r in recipients if r != "dashboard"]
        if req.trigger:
            prefer_steer = (req.steer is not False) and not bool(req.queueIfBusy)
            allow_queue_busy = bool(req.queueIfBusy) or prefer_steer or str(req.type or "").strip().lower() == "response"
            launchable_recipients, not_started = await _preflight_live_send_recipients(
                db,
                dispatch_recipients,
                allow_steer=prefer_steer,
                allow_queue_busy=allow_queue_busy,
            )
            # ASYMMETRY: do NOT hard-reject a reply here. Replies fall through
            # to persist + thread regardless of recipient live-startability
            # (see is_reply note above). Only NEW dispatches hard-gate.
            if not_started and not is_reply:
                recipient_info = {}
                for r in recipients:
                    info = await _get_recipient_info(db, r)
                    if info:
                        recipient_info[r] = {
                            "status": info["status"],
                            "unread": info["unread"],
                            "runtime": info["runtime"],
                            "machineId": info["machineId"],
                        }
                await db.commit()
                return {
                    "ok": False,
                    "error": "Message was not sent because one or more recipients cannot start live work now.",
                    "recipients": recipients,
                    "recipientStatus": recipient_info,
                    "dispatchRuns": [],
                    "notStarted": not_started,
                    "consoleDeliveries": [],
                    "warnings": warnings,
                }
            settings = await _load_settings(db)
            channel_backing_failed = set()
            for recipient_id, _execution_mode in launchable_recipients:
                row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))).fetchone()
                if row:
                    row, _transition = await _auto_return_resident_to_managed_if_possible(db, row, settings=settings)
                if not row:
                    continue
                runtime = _normalize_runtime(row["runtime"] or "generic")
                # Queue only waits behind real active/queued work. For an idle
                # terminal-backed target, it should still use the normal live
                # delivery path instead of creating an orphan dispatch queue.
                if bool(req.queueIfBusy):
                    dispatch_state = await _get_dispatch_state_for_agent(db, recipient_id)
                    # Three signals of "currently busy":
                    # 1. hasActiveRun: tracked dispatch_run in claimed/running
                    # 2. queuedRuns > 0: prior queue already pending
                    # 3. raw turn_busy=1: the agent is mid-turn even if
                    #    no tracked dispatch_run is in flight. Operator-
                    #    reported 2026-05-22: queue button sent immediately
                    #    because require_reply=0 info messages auto-complete
                    #    their dispatch_run on delivery → hasActiveRun goes
                    #    false → queue fires the next message immediately
                    #    while the assistant is still working. turn_busy
                    #    is the harness-level signal that survives the
                    #    auto-completion.
                    # Raw signal, bounded ONLY by the anti-strand ceiling that also bounds the
                    # claim gate — otherwise an abandoned turn_busy=1 makes every later send to
                    # this agent queue behind a turn that already ended (and the claim gate then
                    # never releases it). See _turn_busy_holds_delivery.
                    try:
                        is_turn_busy = await _turn_busy_holds_delivery(db, recipient_id)
                    except Exception:
                        is_turn_busy = False
                    if (
                        dispatch_state.get("hasActiveRun")
                        or int(dispatch_state.get("queuedRuns") or 0) > 0
                        or is_turn_busy
                    ):
                        continue
                execution_mode = str(_execution_mode or "").strip().lower()
                # Native-managed runtimes (codex/pi/opencode/hermes) — only
                # route through PTY-input when the operator opted into
                # the legacy via-console delivery mode AND managed-
                # terminal-backing is enabled. Default
                # (insert_messages_via_console=false) falls through and
                # the dispatch is claimed by the runtime's native RPC
                # adapter (createCodexController, createPiController,
                # opencode SDK) on its /dispatch/claim poll.
                if runtime in _NATIVE_MANAGED_RUNTIMES:
                    # Wrapper-backed managed (operator-stated 2026-05-25): if
                    # the runtime is in managed_via_wrapper, the wrapper PTY
                    # MUST exist to claim — auto-spawn here so an available
                    # agent gets its console started on first message arrival
                    # (mirror of the operator's "send → console auto-starts
                    # → status flips" model).
                    if (
                        execution_mode == "channel"
                        and _managed_terminal_backing_enabled(settings)
                        and _managed_via_wrapper_for_runtime(settings, runtime)
                    ):
                        console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                        # FIX SET B2 (2026-06-03): for a wrapper-backed runtime a
                        # leftover RESIDENT-mode terminal_session must NOT short-
                        # circuit the managed coldstart. _active_terminal_for_agent /
                        # _ensure_managed_pty_for_dispatch would re-attach a PTY to
                        # that stale resident row (a resident `--resume`, NOT a
                        # managed-warm worker), so no `managed-wrapper-child` bridge
                        # ever registers and the 'channel' run is rejected
                        # `managed_wrapper_child_required` → queued forever (the
                        # lc-coder strand). Only a LIVE managed-wrapper-child proves a
                        # managed worker is actually backing this agent; absent it,
                        # drop the leftover terminal so the coldstart branch below
                        # fires and a managed-warm worker is spawned.
                        if console_terminal and not await _has_live_managed_wrapper_child(db, recipient_id):
                            console_terminal = None
                        if not console_terminal:
                            console_terminal = await _ensure_managed_pty_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                            )
                            # The PTY re-attach above can still resolve a leftover
                            # resident row; re-gate on the live wrapper-child so a
                            # non-managed terminal never suppresses the coldstart.
                            if console_terminal and not await _has_live_managed_wrapper_child(db, recipient_id):
                                console_terminal = None
                        if not console_terminal:
                            # Phase 2 lazy-autostart: no live wrapper PTY to
                            # back this agent (it was only registered, never
                            # run — the operator's `available` sc-coder case).
                            # Instead of rejecting, cold-start a spawn_request
                            # (auto-binding an online env when none is bound)
                            # so a bridge spawns the wrapper and claims this
                            # dispatch on its next poll. Only reject when no
                            # online environment can host the runtime.
                            # N8: collect WHY so a refusal reports its real cause, not the
                            # environment sentence that fired for all five of them.
                            _cs_reasons: list[str] = []
                            coldstarted = await _coldstart_spawn_request_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                                warnings=_cs_reasons,
                            )
                            if not coldstarted and not await _has_claimable_spawn_request(db, recipient_id):
                                not_started.append(
                                    _dispatch_fix_hint(
                                        recipient_id,
                                        row,
                                        _coldstart_refusal_message(_cs_reasons, runtime),
                                    )
                                )
                                channel_backing_failed.add(recipient_id)
                        # Do NOT add to console_recipients (that's the legacy
                        # PTY-input delivery path). Wrapper child bridge claims
                        # via /dispatch/claim once its in-process MCP boots.
                        # Just let the dispatch sit queued; it'll get picked up
                        # within a polling cycle (3s) once the wrapper is up.
                        continue
                    if (
                        execution_mode == "managed"
                        and _managed_terminal_backing_enabled(settings)
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
                            continue
                    continue
                # Managed Claude PTY-input branch — only fires when the
                # operator has opted into the legacy via-console delivery
                # mode (insert_messages_via_console=true). Default-false
                # routing flows through the channel branch below: the run
                # is left launchable with execution_mode='channel' (see
                # _apply_channel_routing_to_claude_runs after
                # _create_dispatch_runs) so claude-channel.js inside the
                # wrapper-hosted claude-aify claims it and emits the
                # message as a channel wake-up event.
                if (
                    runtime in _CHANNEL_MANAGED_RUNTIMES
                    and _execution_mode == "channel"
                    and _insert_messages_via_console(settings)
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
                    else:
                        not_started.append(
                            _dispatch_fix_hint(
                                recipient_id,
                                row,
                                "Claude claude-aify backing PTY is unavailable; restart the environment bridge or recover the session.",
                            )
                        )
                        channel_backing_failed.add(recipient_id)
                    continue
                if runtime in _CHANNEL_MANAGED_RUNTIMES:
                    # Channel-mode managed Claude (insert_messages_via_console=false)
                    # needs a wrapper PTY running so claude-aify's
                    # claude-channel.js child actually polls
                    # /dispatch/claim for this agent and picks up the
                    # channel-routed dispatch. Without it, the run sits
                    # queued forever (originally observed in
                    # run_1779309370301). We don't inject input — the
                    # PTY is the host for the subscriber, not the
                    # delivery channel. Existing terminal is reused
                    # (slice-3 reuse semantics); only spawned if absent.
                    if (
                        not _insert_messages_via_console(settings)
                        and _managed_terminal_backing_enabled(settings)
                        and _execution_mode == "channel"
                    ):
                        existing = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                        # B2 parity (2026-06-12): a leftover non-managed terminal row must not
                        # suppress the cold start — only a LIVE managed-wrapper-child proves a
                        # worker actually backs this agent (same strand class as lc-coder).
                        if existing and not await _has_live_managed_wrapper_child(db, recipient_id):
                            existing = None
                        if not existing:
                            started = None
                            try:
                                started = await _ensure_managed_pty_for_dispatch(
                                    db,
                                    recipient_id,
                                    runtime=runtime,
                                    settings=settings,
                                    requested_by=req.from_agent,
                                )
                            except Exception:
                                started = None
                            if not started:
                                # ROOT-CAUSE-G PARITY (2026-06-12, graph-tech-lead strand):
                                # _ensure_managed_pty_for_dispatch returns None when the agent
                                # has no usable session row to launch into — exactly the state
                                # after an env-bridge restart retires every session. The native
                                # runtimes fall back to a cold-start spawn_request here; managed
                                # claude never did, so the channel run sat queued with a claimer
                                # that could never exist until the 180s backstop FAILED it.
                                coldstarted = False
                                # N8: declared OUTSIDE the try so a reason recorded before an
                                # exception is still reportable.
                                _cs_reasons_b: list[str] = []
                                try:
                                    coldstarted = await _coldstart_spawn_request_for_dispatch(
                                        db,
                                        recipient_id,
                                        runtime=runtime,
                                        settings=settings,
                                        requested_by=req.from_agent,
                                        warnings=_cs_reasons_b,
                                    )
                                except Exception as _cs_err:
                                    coldstarted = False
                                    _cs_reasons_b.append(
                                        f"{COLDSTART_REFUSED_PREFIX}cold-start raised: {_cs_err}")
                                if not coldstarted and not await _has_claimable_spawn_request(db, recipient_id):
                                    not_started.append(
                                        _dispatch_fix_hint(
                                            recipient_id,
                                            row,
                                            _coldstart_refusal_message(_cs_reasons_b, runtime),
                                        )
                                    )
                                    channel_backing_failed.add(recipient_id)
                    # Final safety (2026-07-04): a channel-managed claude dispatch must
                    # never strand until the 180s queued-run backstop. If — after the
                    # terminal reuse / PTY-ensure above — there is STILL no live
                    # managed-wrapper-child to run claude-channel.js AND no claimable
                    # spawn request, cold-start one now so a bridge spawns the wrapper
                    # and claims this run on its next poll (the aicm-lc-manager
                    # 'queued, never spawned' strand). Idempotent: a live claimer or a
                    # pending spawn short-circuits it, so no duplicate workers.
                    if recipient_id not in channel_backing_failed and (
                        not await _has_live_managed_wrapper_child(db, recipient_id)
                        and not await _has_claimable_spawn_request(db, recipient_id)
                    ):
                        try:
                            await _coldstart_spawn_request_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                            )
                        except Exception:
                            pass
                    continue
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
            launchable_recipients = [
                (recipient_id, execution_mode)
                for recipient_id, execution_mode in launchable_recipients
                if recipient_id not in console_recipients and recipient_id not in channel_backing_failed
            ]
            # ASYMMETRY: replies are never hard-rejected — see is_reply note
            # above. Fall through to persist + thread the reply.
            if not_started and not is_reply:
                recipient_info = {}
                for r in recipients:
                    info = await _get_recipient_info(db, r)
                    if info:
                        recipient_info[r] = {
                            "status": info["status"],
                            "unread": info["unread"],
                            "runtime": info["runtime"],
                            "machineId": info["machineId"],
                        }
                await db.commit()
                return {
                    "ok": False,
                    "error": "Message was not sent because one or more recipients cannot start live work now.",
                    "recipients": recipients,
                    "recipientStatus": recipient_info,
                    "dispatchRuns": [],
                    "notStarted": not_started,
                    "consoleDeliveries": [],
                    "warnings": warnings,
                }

        linked_result_message_id = _primary_result_message_id(msg_id, recipients)

        inserted_rows = 0
        for r in recipients:
            recipient_message_id = f"{msg_id}-{r}" if len(recipients) > 1 else msg_id
            dispatch_requested = 1 if req.trigger and r != "dashboard" else 0
            # INSERT OR IGNORE is the ATOMIC half of idempotency (#240): the upfront SELECT
            # is only a fast path and races under concurrent retries; the partial UNIQUE
            # index on (from_agent, client_nonce, to_agent) rejects a duplicate here, and
            # rowcount tells us whether THIS request actually wrote the row. (Empty nonce =
            # not in the index, so nonce-less sends always insert, exactly as before.)
            cursor = await db.execute(
                "INSERT OR IGNORE INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, in_reply_to, client_nonce, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (recipient_message_id,
                 req.from_agent, r, "direct", req.type, req.subject, req.body, req.priority, dispatch_requested, resolved_in_reply_to, client_nonce, ts)
            )
            inserted_rows += cursor.rowcount or 0

        # Lost the concurrent race (#240): a nonce was supplied but every row was ignored as
        # a duplicate → another (racing) request already committed this exact send. Return
        # its ORIGINAL messageId with ok:true and create NO dispatch runs (the winner made
        # them), so a retry that overlapped the first in-flight request never double-sends.
        if client_nonce and inserted_rows == 0:
            prior = await (await db.execute(
                "SELECT id FROM messages WHERE from_agent = ? AND client_nonce = ? ORDER BY timestamp ASC LIMIT 1",
                (req.from_agent, client_nonce),
            )).fetchone()
            return {
                "ok": True,
                "messageId": prior["id"] if prior is not None else msg_id,
                "replayed": True,
                "recipients": [],
                "recipientStatus": {},
                "dispatchRuns": [],
                "notStarted": [],
                "consoleDeliveries": [],
                "warnings": [],
            }

        if resolved_in_reply_to:
            await _link_reply_message_to_dispatch_run(
                db,
                from_agent=req.from_agent,
                resolved_in_reply_to=resolved_in_reply_to,
                reply_message_id=linked_result_message_id,
                reply_type=req.type,
                reply_body=req.body,
            )
        else:
            for r in recipients:
                recipient_message_id = f"{msg_id}-{r}" if len(recipients) > 1 else msg_id
                await _link_unthreaded_reply_to_recent_dispatch_run(
                    db,
                    from_agent=req.from_agent,
                    to_agent=r,
                    reply_message_id=recipient_message_id,
                    reply_type=req.type,
                    reply_subject=req.subject,
                    reply_body=req.body,
                    reply_timestamp_ms=ts,
                )

        dispatch_runs = []
        if req.trigger:
            require_reply = _dispatch_requires_reply(req.requireReply, default=_message_type_expects_reply(req.type))
            source_message_ids = {
                recipient_id: (f"{msg_id}-{recipient_id}" if len(recipients) > 1 else msg_id)
                for recipient_id in recipients
            }
            dispatch_runs = await _create_dispatch_runs(
                db,
                [recipient_id for recipient_id, _ in launchable_recipients],
                from_agent=req.from_agent,
                message_type=req.type,
                subject=req.subject,
                body=req.body,
                priority=req.priority,
                in_reply_to=resolved_in_reply_to,
                dispatch_mode="start_if_possible",
                execution_mode="managed",
                requested_runtime=None,
                message_id=msg_id if len(recipients) == 1 else None,
                source_message_ids=source_message_ids,
                steer=prefer_steer,
                queue_if_busy=bool(req.queueIfBusy),
                require_reply=require_reply,
            )
            dispatch_runs = await _finalize_dispatch_runs(db, dispatch_runs, launchable_recipients, not_started)
            await _apply_channel_routing_to_claude_runs(db, dispatch_runs, settings)

        console_deliveries = []
        if req.trigger:
            source_message_ids = {
                recipient_id: (f"{msg_id}-{recipient_id}" if len(recipients) > 1 else msg_id)
                for recipient_id in recipients
            }
            for recipient_id, terminal in console_recipients.items():
                terminal_id = str(terminal["terminal_id"] or "").strip()
                recipient_message_id = source_message_ids.get(recipient_id, msg_id)
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
                        "source": "message_send",
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

        # Gather recipient status info for sender context
        recipient_info = {}
        for r in recipients:
            info = await _get_recipient_info(db, r)
            if info:
                recipient_info[r] = {
                    "status": info["status"],
                    "unread": info["unread"],
                    "runtime": info["runtime"],
                    "machineId": info["machineId"],
                }

        await db.commit()
        # v0.4 C1 — AFTER commit, so a phone can never buzz for a message that rolled back, and the
        # enqueue can never roll one back. Deliberately OUTSIDE the `if ws:` below: the entire point
        # of the mobile alert is to reach the operator when no dashboard is connected, so gating it
        # on a live websocket would silence it exactly when it is the only channel left.
        # Sync, non-raising, network-free — see service/ntfy.py.
        notify_operator(
            "message_sent",
            {"id": msg_id, "from": req.from_agent, "to": recipients, "subject": req.subject},
        )
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("message_sent", {"id": msg_id, "from": req.from_agent, "to": recipients, "subject": req.subject})
            for r in recipients:
                await ws.notify_agent(r, "new_message", {"from": req.from_agent, "subject": req.subject})
            for run in dispatch_runs:
                if run.get("steered"):
                    continue
                await ws.broadcast("dispatch_queued", {"runId": run["runId"], "targetAgentId": run["targetAgentId"]})
            for delivery in console_deliveries:
                await ws.broadcast("terminal_control_requested", {"terminalId": delivery["terminalId"], "action": "input"})
        # Wake up any listening agents
        for r in recipients:
            _wake_agent(r)
        return {
            "ok": True,
            "messageId": msg_id,
            "recipients": recipients,
            "recipientStatus": recipient_info,
            "dispatchRuns": dispatch_runs,
            "notStarted": not_started,
            "consoleDeliveries": console_deliveries,
            "warnings": warnings,
        }
    finally:
        await db.close()
