"""The `channels` route domain: channel lifecycle, membership, and channel fan-out.

v0.5.2h. Eight handlers, three domain-local helpers, 404 lines.

`send_channel_message` (184 lines) is the interesting one: a channel send FANS OUT to every member,
which is why this domain borrows so much of the dispatch machinery. Those borrows are the honest
shape of that coupling, not a shortcut.

BORROW TABLE with the retirement map, as required for any domain that carries debt:

    _create_dispatch_runs                    retires with: dispatch, messages
    _delete_messages_where                   retires with: messages, and the clear/rotate routes
    _finalize_dispatch_runs                  retires with: dispatch, messages
    _get_recipient_info                      retires with: dispatch, messages
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
from typing import Any, Optional

from fastapi import HTTPException, Query, Request

# Was a borrow shim (the owner lived in the control plane, which a router cannot import at module
# level without a cycle). It moved to api_core/dispatch_runs.py in v0.5.4, then on to
# api_core/send_preflight.py — deciding whether a run is worth creating is not creating one.
from service.api_core.routing import domain_router
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db
# Imported for the ANNOTATIONS, which are strings under postponed evaluation. Leaving one of these
# out does not fail import or compile -- FastAPI silently demotes the body to a query parameter and
# the endpoint 422s at request time. That is the v0.5.2g defect; two gates now catch it, and this
# comment is here so the next person does not "tidy away" an import that looks unused.
from service.models import ChannelCreate
from service.api_core.channel_coldstart import _coldstart_cold_channel_members

logger = logging.getLogger("aify_comms.routers.channels")

router = domain_router()

# THE CHANNEL DOMAIN IS THREE FILES, COMPOSED HERE rather than in `api_v2.py`. The fanout send and
# the membership verbs left in v0.5.4; this module keeps the channel's own lifecycle and history, and
# includes the other two, so `api_v2.py` still sees ONE channels router.
from service.routers.channel_membership import router as _channel_membership_router
from service.routers.channel_send import router as _channel_send_router

router.include_router(_channel_membership_router)
router.include_router(_channel_send_router)






# Was a borrow shim: the owner lived in the control plane, which this module cannot import at
# module level without a cycle. It moved to service/api_core/dispatch_runs.py in v0.5.4.


from service.api_core.message_store import _delete_messages_where  # noqa: E402



# Was a borrow shim: the owner lived in the control plane, which a router cannot import at
# module level without a cycle. It moved to service/api_core/status_refresh.py in v0.5.4, so
# a plain import works.







# Was a borrow shim: the owner lived in the control plane, which a router cannot import at
# module level without a cycle. It moved to service/longpoll.py in v0.5.4 — the module that
# already owned the other waiter registry — so a plain import works.


def _normalize_channel_history_where(channel_name: str) -> tuple[str, tuple[Any, ...]]:
    return "channel = ? AND to_agent IS NULL", (channel_name,)


def _channel_fanout_message_id(canonical_message_id: str, agent_id: str) -> str:
    return f"{canonical_message_id}-{agent_id}"


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
