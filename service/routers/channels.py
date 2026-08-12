"""The `channels` route domain: channel lifecycle, membership, and channel fan-out.

v0.5.2h. Eight handlers, three domain-local helpers, 404 lines.

`send_channel_message` (184 lines) is the interesting one: a channel send FANS OUT to every member,
which is why this domain borrows so much of the dispatch machinery. Those borrows are the honest
shape of that coupling, not a shortcut.

BORROW TABLE with the retirement map, as required for any domain that carries debt:

    _coldstart_spawn_request_for_dispatch    retires with: agents, messages, sessions
    _create_dispatch_runs                    retires with: dispatch, messages
    _delete_messages_where                   retires with: messages, and the clear/rotate routes
    _finalize_dispatch_runs                  retires with: dispatch, messages
    _get_recipient_info                      retires with: dispatch, messages
    _has_live_managed_wrapper_child          retires with: messages
    _preflight_live_send_recipients          retires with: messages
    _reject_sender_truncated_body            retires with: dispatch, messages
    _touch_agent                             retires with: dispatch, messages
    _wake_agent                              retires with: dispatch, messages

Read that as: nearly all of it retires with `messages` and `dispatch`. Channels genuinely sits on top
of the send path, so it borrows rather than forking it — `_create_dispatch_runs` and
`_preflight_live_send_recipients` in particular are dispatch orchestration the reviewer ruled should
not be pulled into a shared core.

The three helpers that moved had no users outside this domain.

Built with `domain_router()`, and declares NO tags: the parent applies `tags=["api"]` on include.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional

from fastapi import HTTPException, Query, Request

from service.api_core.routing import domain_router
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.serialization import _clip_text, _iso_from_ms, _json_loads_or
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.api_core.liveness import _has_live_managed_wrapper_child
from service.api_core.agent_sessions import _touch_agent
from service.clock import now as _now
from service.db import get_db
from service.ntfy import notify_operator
# Imported for the ANNOTATIONS, which are strings under postponed evaluation. Leaving one of these
# out does not fail import or compile -- FastAPI silently demotes the body to a query parameter and
# the endpoint 422s at request time. That is the v0.5.2g defect; two gates now catch it, and this
# comment is here so the next person does not "tidy away" an import that looks unused.
from service.models import ChannelCreate, ChannelJoin, ChannelMessage
from service.api_core.channel_delivery import _CHANNEL_CLAIM_RUNTIMES

logger = logging.getLogger("aify_comms.routers.channels")

router = domain_router()

# Domain-local after the handlers moved: nothing outside references it.
_CHANNEL_FANOUT_DEDUP_WINDOW_MS = 30_000




async def _coldstart_spawn_request_for_dispatch(*a, **k):
    from service.control_plane import _coldstart_spawn_request_for_dispatch as _impl

    return await _impl(*a, **k)


async def _create_dispatch_runs(*a, **k):
    from service.control_plane import _create_dispatch_runs as _impl

    return await _impl(*a, **k)


async def _delete_messages_where(*a, **k):
    from service.control_plane import _delete_messages_where as _impl

    return await _impl(*a, **k)


async def _finalize_dispatch_runs(*a, **k):
    from service.control_plane import _finalize_dispatch_runs as _impl

    return await _impl(*a, **k)


async def _get_recipient_info(*a, **k):
    from service.control_plane import _get_recipient_info as _impl

    return await _impl(*a, **k)




async def _preflight_live_send_recipients(*a, **k):
    from service.control_plane import _preflight_live_send_recipients as _impl

    return await _impl(*a, **k)


def _reject_sender_truncated_body(*a, **k):
    from service.control_plane import _reject_sender_truncated_body as _impl

    return _impl(*a, **k)




def _wake_agent(*a, **k):
    from service.control_plane import _wake_agent as _impl

    return _impl(*a, **k)


def _normalize_channel_history_where(channel_name: str) -> tuple[str, tuple[Any, ...]]:
    return "channel = ? AND to_agent IS NULL", (channel_name,)


def _channel_fanout_message_id(canonical_message_id: str, agent_id: str) -> str:
    return f"{canonical_message_id}-{agent_id}"


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


@router.get("/channels")
async def list_channels(request: Request, agentId: Optional[str] = None):
    viewer_id = str(agentId or "").strip()
    if viewer_id:
        validate_name(viewer_id, "agent ID")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM channels")
        channels = []
        for ch in await cursor.fetchall():
            mc = await db.execute("SELECT COUNT(*) FROM channel_members WHERE channel_name = ?", (ch["name"],))
            member_count = (await mc.fetchone())[0]
            history_where, history_params = _normalize_channel_history_where(ch["name"])
            msg_c = await db.execute(f"SELECT COUNT(*) FROM messages WHERE {history_where}", history_params)
            msg_count = (await msg_c.fetchone())[0]
            last_c = await db.execute(f"SELECT MAX(timestamp) FROM messages WHERE {history_where}", history_params)
            last_message_at = (await last_c.fetchone())[0]
            unread_count = 0
            if viewer_id:
                unread_c = await db.execute(
                    """
                    SELECT COUNT(*)
                    FROM messages m
                    LEFT JOIN read_receipts r ON r.message_id = m.id AND r.agent_id = ?
                    WHERE m.channel = ? AND m.to_agent = ? AND m.source = 'channel' AND r.message_id IS NULL
                    """,
                    (viewer_id, ch["name"], viewer_id),
                )
                unread_count = (await unread_c.fetchone())[0]
            channels.append({
                "name": ch["name"], "description": ch["description"],
                "createdBy": ch["created_by"], "createdAt": ch["created_at"],
                "members": [], "memberCount": member_count, "messageCount": msg_count,
                "unreadCount": unread_count, "lastMessageAt": last_message_at,
            })
            # Fetch member list
            mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (ch["name"],))
            channels[-1]["members"] = [r["agent_id"] for r in await mem_c.fetchall()]
        return {"channels": channels}
    finally:
        await db.close()


@router.post("/channels")
async def create_channel(req: ChannelCreate, request: Request):
    validate_name(req.name, "channel name")
    db = await get_db()
    try:
        now = _now()
        try:
            await db.execute(
                "INSERT INTO channels (name, description, created_by, created_at) VALUES (?,?,?,?)",
                (req.name, req.description or "", req.createdBy, now)
            )
        except Exception:
            raise HTTPException(409, f"Channel '{req.name}' already exists")
        await db.execute(
            "INSERT INTO channel_members (channel_name, agent_id, joined_at) VALUES (?,?,?)",
            (req.name, req.createdBy, now)
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("channel_created", {"name": req.name})
        return {"ok": True, "channel": req.name}
    finally:
        await db.close()


@router.get("/channels/{name}")
async def get_channel(
    name: str,
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = 0,
    agentId: Optional[str] = None,
):
    validate_name(name, "channel name")
    viewer_id = str(agentId or "").strip()
    if viewer_id:
        validate_name(viewer_id, "agent ID")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM channels WHERE name = ?", (name,))
        ch = await cursor.fetchone()
        if not ch:
            raise HTTPException(404, f"Channel '{name}' not found")

        mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (name,))
        members = [r["agent_id"] for r in await mem_c.fetchall()]

        history_where, history_params = _normalize_channel_history_where(name)
        total_c = await db.execute(f"SELECT COUNT(*) FROM messages WHERE {history_where}", history_params)
        total = (await total_c.fetchone())[0]

        # Paginate newest first
        msg_c = await db.execute(
            f"SELECT * FROM messages WHERE {history_where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            history_params + (limit, offset)
        )
        messages = []
        for row in await msg_c.fetchall():
            read = True
            fanout_id = ""
            if viewer_id and row["from_agent"] != viewer_id and row["from_agent"] != "_system":
                fanout_id = _channel_fanout_message_id(row["id"], viewer_id)
                read_cursor = await db.execute(
                    "SELECT 1 FROM read_receipts WHERE message_id = ? AND agent_id = ?",
                    (fanout_id, viewer_id),
                )
                read = bool(await read_cursor.fetchone())
            messages.append({
                "id": row["id"], "from": row["from_agent"], "type": row["type"],
                "body": row["body"], "priority": row["priority"], "timestamp": row["timestamp"],
                "dispatchRequested": bool(row["dispatch_requested"]) if "dispatch_requested" in row.keys() else False,
                "read": read,
                "fanoutMessageId": fanout_id,
            })
        # Reverse so oldest is first in the returned slice (chat order)
        messages.reverse()

        return {
            "name": ch["name"], "description": ch["description"],
            "members": members, "totalMessages": total, "messages": messages,
        }
    finally:
        await db.close()


@router.delete("/channels/{name}")
async def delete_channel(name: str, request: Request):
    db = await get_db()
    try:
        await db.execute("DELETE FROM channel_members WHERE channel_name = ?", (name,))
        await _delete_messages_where(db, "channel = ?", (name,))
        cursor = await db.execute("DELETE FROM channels WHERE name = ?", (name,))
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"Channel '{name}' not found")
        return {"ok": True}
    finally:
        await db.close()


@router.post("/channels/{name}/join")
async def join_channel(name: str, req: ChannelJoin, request: Request):
    validate_name(name, "channel name")
    validate_name(req.agentId, "agent ID")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM channels WHERE name = ?", (name,))
        if not await cursor.fetchone():
            raise HTTPException(404, f"Channel '{name}' not found")
        now = _now()
        insert_cursor = await db.execute(
            "INSERT OR IGNORE INTO channel_members (channel_name, agent_id, joined_at) VALUES (?,?,?)",
            (name, req.agentId, now)
        )
        changed = insert_cursor.rowcount > 0
        if changed:
            await db.execute(
                "INSERT INTO messages (id, from_agent, channel, source, type, subject, body, timestamp) VALUES (?,?,?,?,?,?,?,?)",
                (f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}", "_system", name, "channel", "info", f"#{name}", f"{req.agentId} joined the channel", int(time.time()*1000))
            )
        await db.commit()
        mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (name,))
        members = [r["agent_id"] for r in await mem_c.fetchall()]
        ws = await _get_ws(request)
        if ws and changed:
            await ws.broadcast("channel_membership", {"channel": name, "agentId": req.agentId, "action": "join", "members": members})
        return {"ok": True, "members": members, "changed": changed}
    finally:
        await db.close()


@router.post("/channels/{name}/leave")
async def leave_channel(name: str, req: ChannelJoin, request: Request):
    validate_name(name, "channel name")
    validate_name(req.agentId, "agent ID")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM channels WHERE name = ?", (name,))
        if not await cursor.fetchone():
            raise HTTPException(404, f"Channel '{name}' not found")
        delete_cursor = await db.execute("DELETE FROM channel_members WHERE channel_name = ? AND agent_id = ?", (name, req.agentId))
        changed = delete_cursor.rowcount > 0
        if changed:
            await db.execute(
                "INSERT INTO messages (id, from_agent, channel, source, type, subject, body, timestamp) VALUES (?,?,?,?,?,?,?,?)",
                (f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}", "_system", name, "channel", "info", f"#{name}", f"{req.agentId} left the channel", int(time.time()*1000))
            )
        await db.commit()
        mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (name,))
        members = [r["agent_id"] for r in await mem_c.fetchall()]
        ws = await _get_ws(request)
        if ws and changed:
            await ws.broadcast("channel_membership", {"channel": name, "agentId": req.agentId, "action": "leave", "members": members})
        return {"ok": True, "members": members, "changed": changed}
    finally:
        await db.close()


@router.post("/channels/{name}/read")
async def mark_channel_read(name: str, request: Request):
    validate_name(name, "channel name")
    body = await request.json()
    agent_id = str(body.get("agentId") or "").strip()
    if not agent_id:
        raise HTTPException(400, "Need agentId")
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        member_cursor = await db.execute(
            "SELECT 1 FROM channel_members WHERE channel_name = ? AND agent_id = ?",
            (name, agent_id),
        )
        if not await member_cursor.fetchone():
            raise HTTPException(403, f'Agent "{agent_id}" is not a member of #{name}')
        now = _now()
        cursor = await db.execute(
            """
            SELECT id
            FROM messages
            WHERE channel = ? AND to_agent = ? AND source = 'channel'
            """,
            (name, agent_id),
        )
        rows = await cursor.fetchall()
        for row in rows:
            await db.execute(
                "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                (row["id"], agent_id, now),
            )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("channel_read", {"channel": name, "agentId": agent_id, "count": len(rows)})
        return {"ok": True, "channel": name, "agentId": agent_id, "read": len(rows)}
    finally:
        await db.close()


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
