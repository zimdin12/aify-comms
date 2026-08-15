"""The pre-split `_replay_undelivered_channel_messages_on_env_recovery`, frozen.

Not imported by anything. It is the ONE true original that
`test_replay_undelivered_channel_messages_split_is_inert.py` inlines every extraction back against.

Captured from `git show HEAD:service/reconcilers/dispatch_queue.py` at the commit before the
extraction, decoded as utf-8 rather than through the locale codec.
"""


async def _replay_undelivered_channel_messages_on_env_recovery(
    db, *, horizon_hours: Optional[int] = None, limit: int = 200
) -> list[dict[str, str]]:
    """Task #238: replay a channel post to a member whose managed environment was
    OFFLINE at send time, once that environment recovers.

    ``send_channel_message`` drops any member whose managed environment is
    effectively offline from ``dispatch_recipients`` (via
    ``_preflight_live_send_recipients`` → ``_managed_environment_unavailable_reason``):
    the canonical message + the member's inbox copy are stored with
    ``dispatch_requested=1`` but NO ``dispatch_run`` is created. The env-recovery
    heartbeat only invalidates the status cache — nothing turns that stored
    message into a wake. So a cold team that recovers stays silent (the
    "sc-manager's broadcasts left targets available, no answers" class, #191).

    This reconciler closes the gap: for each stored-but-un-dispatched channel
    inbox message whose member's env is now AVAILABLE, it creates the queued
    dispatch run the send would have made. The existing queued-run backstop
    (``_reap_undeliverable_queued_runs``, later in the same sweep) then claims or
    cold-start-rescues it, so no separate coldstart is needed here.

    Idempotent + double-dispatch-safe: every channel run records the member's
    fanout inbox id in ``dispatch_runs.message_id`` (see
    ``_dispatch_message_id_for_recipient``), so a member who already has a run
    (launchable at send, even if still queued/unread) is excluded by the
    ``NOT EXISTS`` guard, and a member we replay is excluded on the next pass by
    the same guard. Already-read messages are skipped too. A horizon bounds how
    far back we look so an env down for days doesn't resurrect stale roll-calls.
    """
    settings = await _load_settings(db)
    if horizon_hours is None:
        horizon_hours = int(
            settings.get("channel_offline_replay_horizon_hours", 24) or 24
        )
    horizon_hours = max(1, int(horizon_hours))
    cutoff_param = f"-{horizon_hours} hours"
    cursor = await db.execute(
        """
        SELECT m.id, m.from_agent, m.to_agent, m.channel, m.type, m.subject, m.body, m.priority
        FROM messages m
        LEFT JOIN read_receipts rr ON rr.message_id = m.id AND rr.agent_id = m.to_agent
        WHERE m.source = 'channel'
          AND m.to_agent IS NOT NULL AND m.to_agent != '' AND m.to_agent != 'dashboard'
          AND m.dispatch_requested = 1
          -- `messages.timestamp` is epoch MILLISECONDS, not ISO. `datetime(1786402075333)` returns
          -- NULL, so this comparison was NULL — never true — and this reconciler could not match a
          -- single row it exists to replay. Measured on the live DB: 0 candidates under the old
          -- predicate, 115 under this one.
          --
          -- Same class as the `finished_at` guard that excluded its own target rows for two months,
          -- and the sixth lexical/epoch timestamp bug recorded in this repo. Other code already
          -- knew the shape and did it correctly (`datetime(timestamp / 1000, 'unixepoch')`), which
          -- is what makes this a copy that drifted rather than a misunderstanding.
          AND datetime(m.timestamp / 1000, 'unixepoch') >= datetime('now', ?)
          AND rr.message_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM dispatch_runs dr WHERE dr.message_id = m.id)
        ORDER BY m.timestamp ASC
        LIMIT ?
        """,
        (cutoff_param, max(1, int(limit or 200))),
    )
    rows = await cursor.fetchall()
    replayed: list[dict[str, str]] = []
    for row in rows:
        message_id = str(row["id"] or "").strip()
        member = str(row["to_agent"] or "").strip()
        if not message_id or not member:
            continue
        agent_row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (member,))).fetchone()
        if agent_row is None:
            # Tombstoned member — its stored messages are drained by agent-delete.
            continue
        if _normalize_session_mode(agent_row["session_mode"] or "resident") != "managed":
            # The offline-env drop is a managed-binding concern; resident delivery
            # is owned by the channel-sidecar, not this reconciler.
            continue
        if await _managed_environment_unavailable_reason(db, agent_row):
            # Env still not available — leave the message stored, retry next pass.
            continue
        # Env recovered → create the queued run the send would have made. Mirror the
        # channel-send call exactly, keyed on this member's fanout inbox id so the run
        # carries message_id = m.id (the idempotency/double-dispatch guard above).
        runs = await _create_dispatch_runs(
            db,
            [member],
            from_agent=str(row["from_agent"] or ""),
            message_type=str(row["type"] or "message"),
            subject=str(row["subject"] or ""),
            body=str(row["body"] or ""),
            priority=str(row["priority"] or "normal"),
            in_reply_to=None,
            dispatch_mode="start_if_possible",
            execution_mode="managed",
            requested_runtime=None,
            message_id=message_id,
            source_message_ids={member: message_id},
            steer=False,
            require_reply=False,
            # #238: never merge a replay into a pre-existing queued run — the merge keeps
            # the OTHER run's message_id, so this replayed message's fanout id would never
            # be recorded on a run and the sweep would re-replay it forever. Insert a
            # dedicated run keyed on this message_id so the watermark records it.
            allow_merge=False,
        )
        await _finalize_dispatch_runs(db, runs, [(member, "managed")], [])
        await _invalidate_agent_live_state(db, member)
        replayed.append({"messageId": message_id, "agentId": member})
    return replayed
