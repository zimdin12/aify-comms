"""Channel membership: join, leave, and mark what you have seen.

Extracted from `service/routers/channels.py` in v0.5.4. Closure measured before the move —
`api_core` and `service` leaves only, nothing local to that router.

MARKING A CHANNEL READ IS A MEMBERSHIP FACT, not a message one, which is why it is here rather than
with the send path. A channel keeps one history; "read" is per member, recorded against the
membership row as a position in that history. Storing it per message would multiply every channel
message by its member count for information that is really one number per person.

Bodies and route decorators byte-identical.
"""

from __future__ import annotations

import time
import uuid

from fastapi import HTTPException, Request

from service.api_core.routing import domain_router
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import ChannelJoin

router = domain_router()



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
