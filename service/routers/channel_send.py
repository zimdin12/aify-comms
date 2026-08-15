"""Fanning a channel message out to its members, and deciding who actually gets woken.

Extracted from `service/routers/channels.py` in v0.5.4 as a PRIVATE CLUSTER: the handler plus
`_has_recent_direct_delivery_for_channel_fanout`, which nothing else in the tree calls. Everything
else it reaches is an `api_core` or `service` leaf.

A CHANNEL SEND IS N DELIVERIES, NOT ONE, and almost all of this file's length is the difference.
Each member is preflighted separately — offline, stopped and no-wake members fail the send for
themselves without storing — and the reply has to name who was skipped, or a caller reads "sent to
#dev" as "the team has it" when half of them never will.

THE DUPLICATE-SUPPRESSION HELPER IS THE SUBTLE PART. A member who was just sent the same content
directly must not also be woken by the fanout: the run would deliver a second copy of something the
agent is already holding, and the agent cannot tell the two apart. That is what
`_has_recent_direct_delivery_for_channel_fanout` prevents, and it is why it travels with this
handler rather than staying behind as a shared helper it never was.

Body and route decorator byte-identical to what stood in `channels.py`. The router is built through
`domain_router()`, which rejects a hand-passed `route_class`, so a new surface cannot opt out of the
bounded SQLite write-lock retry.
"""

from __future__ import annotations

import time
import uuid

from fastapi import HTTPException, Request

from service.api_core.agent_sessions import _touch_agent
from service.api_core.channel_coldstart import _coldstart_cold_channel_members
from service.api_core.dispatch_run_state import _finalize_dispatch_runs
from service.api_core.dispatch_runs import _create_dispatch_runs
from service.api_core.routing import domain_router
from service.api_core.send_preflight import _preflight_live_send_recipients
from service.api_core.status_refresh import _get_recipient_info
from service.api_core.validation import _reject_sender_truncated_body, validate_name
from service.api_core.ws import _get_ws
from service.db import get_db
from service.longpoll import _wake_agent
from service.ntfy import notify_operator

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import ChannelMessage

router = domain_router()

# TRAVELLED WITH ITS ONLY READER. It sat at module scope in `channels.py` under the comment
# "Domain-local after the handlers moved: nothing outside references it" — which was true there
# and is true here, one file smaller. The undefined-name sweep is what noticed it had been left
# behind: a closure scan that walks imports and function definitions does not see a bare
# module-level assignment, and the moved code referenced a name that no longer existed.
_CHANNEL_FANOUT_DEDUP_WINDOW_MS = 30_000


async def _has_recent_direct_delivery_for_channel_fanout(
    db,
    *,
    from_agent: str,
    recipient_id: str,
    message_type: str,
    body: str,
    timestamp_ms: int,
) -> bool:
    lower_bound = int(timestamp_ms) - _CHANNEL_FANOUT_DEDUP_WINDOW_MS
    upper_bound = int(timestamp_ms) + _CHANNEL_FANOUT_DEDUP_WINDOW_MS
    cursor = await db.execute(
        """
        SELECT 1
        FROM messages
        WHERE from_agent = ?
          AND to_agent = ?
          AND source = 'direct'
          AND type = ?
          AND body = ?
          AND timestamp BETWEEN ? AND ?
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (from_agent, recipient_id, message_type, body, lower_bound, upper_bound),
    )
    return await cursor.fetchone() is not None



@router.post("/channels/{name}/send")
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
            await _coldstart_cold_channel_members(db, req, launchable_recipients)

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
