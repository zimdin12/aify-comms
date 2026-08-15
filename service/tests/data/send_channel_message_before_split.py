async def send_channel_message(name: str, req: ChannelMessage, request: Request):
    validate_name(name, "channel name")
    _reject_sender_truncated_body(req.body)
    db = await get_db()
    try:
        await _touch_agent(db, req.from_agent)

        # Verify membership
        cursor = await db.execute("SELECT 1 FROM channel_members WHERE channel_name = ? AND agent_id = ?", (name, req.from_agent))
        if not await cursor.fetchone():
            raise HTTPException(403, f"Agent '{req.from_agent}' is not a member of #{name}. Join first.")

        msg_id = f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"
        ts = int(time.time() * 1000)
        subject = f"#{name}: {req.body[:80]}"
        should_trigger = False if req.silent else req.trigger is not False

        mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (name,))
        members = [r["agent_id"] for r in await mem_c.fetchall()]
        recipients = []
        inbox_message_ids = {}
        suppressed_duplicates = []
        for member in members:
            if member == req.from_agent:
                continue
            if await _has_recent_direct_delivery_for_channel_fanout(
                db,
                from_agent=req.from_agent,
                recipient_id=member,
                message_type=req.type,
                body=req.body,
                timestamp_ms=ts,
            ):
                suppressed_duplicates.append(member)
                continue
            recipient_msg_id = f"{msg_id}-{member}"
            recipients.append(member)
            inbox_message_ids[member] = recipient_msg_id

        launchable_recipients = []
        not_started = []
        dispatch_recipients = [recipient_id for recipient_id in recipients if recipient_id != "dashboard"]
        # Channel fan-out is a SHARED surface: a single offline/non-startable member must not
        # silence the post for everyone (audit 2026-06-28 — the old code returned ok:False and
        # stored NOTHING when any member couldn't start live work). Always store the canonical
        # message + every member's inbox copy below; the preflight here only narrows WHICH live
        # members get woken now, and unreachable ones are surfaced in `notStarted` (they still
        # have the message waiting in their inbox). Mirrors the direct-send "stored even if not
        # live-woken" semantics.
        not_started = []
        if should_trigger and recipients:
            prefer_steer = (req.steer is not False) and not bool(req.queueIfBusy)
            allow_queue_busy = bool(req.queueIfBusy) or prefer_steer
            launchable_recipients, not_started = await _preflight_live_send_recipients(
                db,
                dispatch_recipients,
                allow_steer=prefer_steer,
                allow_queue_busy=allow_queue_busy,
            )
            # Only wake the members who can actually start; the rest are stored-only.
            dispatch_recipients = launchable_recipients

        # Channel message (canonical)
        await db.execute(
            "INSERT INTO messages (id, from_agent, channel, source, type, subject, body, priority, dispatch_requested, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (msg_id, req.from_agent, name, "channel", req.type, subject, req.body, req.priority or "normal", 1 if should_trigger else 0, ts)
        )

        # Deliver to each member's inbox (except sender)
        for member in members:
            if member != req.from_agent:
                recipient_msg_id = inbox_message_ids.get(member)
                if not recipient_msg_id:
                    continue
                await db.execute(
                    "INSERT INTO messages (id, from_agent, to_agent, channel, source, type, subject, body, priority, dispatch_requested, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        recipient_msg_id, req.from_agent, member, name, "channel", req.type, subject,
                        req.body, req.priority or "normal", 1 if should_trigger and member != "dashboard" else 0, ts
                    )
                )

        dispatch_runs = []
        if should_trigger and dispatch_recipients:
            dispatch_runs = await _create_dispatch_runs(
                db,
                [recipient_id for recipient_id, _ in launchable_recipients],
                from_agent=req.from_agent,
                message_type=req.type,
                subject=subject,
                body=req.body,
                priority=req.priority or "normal",
                in_reply_to=None,
                dispatch_mode="start_if_possible",
                execution_mode="managed",
                requested_runtime=None,
                message_id=inbox_message_ids.get(recipients[0]) if len(recipients) == 1 else None,
                source_message_ids=inbox_message_ids,
                steer=prefer_steer,
                queue_if_busy=bool(req.queueIfBusy),
                require_reply=False,
            )
            dispatch_runs = await _finalize_dispatch_runs(db, dispatch_runs, launchable_recipients, not_started)
            # Send-time coldstart for COLD managed members (2026-07-02). Channel posts
            # previously created queued runs and relied entirely on the 180s queued-run
            # backstop to spawn workers (and before the backstop's coldstart-rescue existed,
            # those runs just FAILED — the "sc-manager's broadcasts left targets available,
            # no answers" incident, #191). Mirror the direct-send path: spawn a managed-warm
            # worker NOW for each launchable member with no live wrapper child, so a channel
            # roll-call wakes a cold team in seconds, not minutes. The helper is idempotent
            # (pending/booting spawn_request short-circuits; unresolvable env returns False,
            # leaving the run queued for the backstop rescue as before).
            coldstart_settings = await _load_settings(db)
            for recipient_id, _exec_mode in launchable_recipients:
                agent_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
                agent_row = await agent_cursor.fetchone()
                if not agent_row:
                    continue
                if _normalize_session_mode(agent_row["session_mode"] or "resident") != "managed":
                    continue
                member_runtime = _normalize_runtime(agent_row["runtime"] or "")
                # Wrapper-child rows only exist for the channel-claim runtimes; for
                # pi/opencode (native RPC controllers inside the env bridge) the gate
                # below is permanently False, so coldstarting on it would duplicate-spawn
                # a LIVE worker on every channel post. Those runtimes spawn on claim,
                # same as the direct-send path.
                if member_runtime not in _CHANNEL_CLAIM_RUNTIMES:
                    continue
                if await _has_live_managed_wrapper_child(db, recipient_id):
                    continue
                await _coldstart_spawn_request_for_dispatch(
                    db,
                    recipient_id,
                    runtime=member_runtime,
                    settings=coldstart_settings,
                    requested_by=req.from_agent,
                )

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
        # v0.4 C1/C7 — post-commit, and outside the websocket gate for the same reason as the direct
        # send. `members` was already loaded above, so the operator's membership is answered from
        # authoritative data with no extra query: this is the asymmetry the agreement table records
        # as allowed, where the browser has to fail closed and the server does not.
        notify_operator(
            "channel_message",
            {"channel": name, "from": req.from_agent, "body": req.body[:200]},
            channel_joined=("dashboard" in members),
        )
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("channel_message", {"channel": name, "from": req.from_agent, "body": req.body[:200]})
            for recipient_id in recipients:
                await ws.notify_agent(recipient_id, "new_message", {"from": req.from_agent, "subject": subject, "channel": name})
            for run in dispatch_runs:
                if run.get("steered"):
                    continue
                await ws.broadcast("dispatch_queued", {"runId": run["runId"], "targetAgentId": run["targetAgentId"]})
        # Wake up any listening members
        for member in members:
            if member != req.from_agent:
                _wake_agent(member)
        return {
            "ok": True,
            "messageId": msg_id,
            "members": members,
            "recipients": recipients,
            "suppressedDuplicates": suppressed_duplicates,
            "recipientStatus": recipient_info,
            "dispatchRuns": dispatch_runs,
            "notStarted": not_started,
        }
    finally:
        await db.close()
