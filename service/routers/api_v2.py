"""
aify-claude v2 API — SQLite backend.
Drop-in replacement for api.py with identical endpoint signatures.
"""
import json
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse

from service.db import get_db
from service.models import (
    AgentRegister, AgentStatusUpdate, MessageSend, ClearRequest,
    ChannelCreate, ChannelMessage, ChannelJoin,
)

SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$')

def validate_name(name: str, label: str = "name") -> None:
    if not SAFE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: must be 1-128 alphanumeric chars, dots, hyphens, underscores.")

router = APIRouter(tags=["api"])

def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def _shared_dir(request: Request) -> Path:
    try:
        d = Path(request.app.state.config.data_dir) / "shared_files"
    except Exception:
        d = Path("/data/shared_files")
    d.mkdir(parents=True, exist_ok=True)
    return d

_MANUAL_STATUSES = {"blocked", "completed"}

DEFAULT_SETTINGS = {
    "retention_days": 90,
    "max_messages_per_agent": 1000,
    "max_shared_size_mb": 500,
    "stale_agent_hours": 24,
    "dashboard_refresh_seconds": 15,
    "rotation_enabled": True,
    "idle_minutes": 5,
    "offline_minutes": 30,
}

async def _get_ws(request: Request):
    try:
        return request.app.state.ws_manager
    except Exception:
        return None

async def _touch_agent(db, agent_id: str):
    await db.execute(
        "UPDATE agents SET last_seen = ?, status = CASE WHEN status IN ('blocked','completed') THEN status ELSE 'active' END WHERE id = ?",
        (_now(), agent_id)
    )

# ─── Root ────────────────────────────────────────────────────────────────────

@router.get("/")
async def root():
    return {
        "service": "aify-claude",
        "version": "2.1.0",
        "storage": "sqlite",
        "endpoints": {
            "agents": "/api/v1/agents",
            "messages": "/api/v1/messages",
            "shared": "/api/v1/shared",
            "channels": "/api/v1/channels",
            "settings": "/api/v1/settings",
            "dashboard": "/api/v1/dashboard",
            "stats": "/api/v1/stats",
        },
    }

# ─── Agents ──────────────────────────────────────────────────────────────────

@router.get("/agents")
async def list_agents(request: Request):
    db = await get_db()
    try:
        # Get configurable thresholds
        settings = {**DEFAULT_SETTINGS}
        sc = await db.execute("SELECT key, value FROM settings")
        for row in await sc.fetchall():
            try: settings[row["key"]] = json.loads(row["value"])
            except Exception: pass
        idle_minutes = settings.get("idle_minutes", 5)
        offline_minutes = settings.get("offline_minutes", 30)

        cursor = await db.execute("SELECT * FROM agents")
        agents = await cursor.fetchall()
        result = {}
        for a in agents:
            aid = a["id"]
            c = await db.execute(
                "SELECT COUNT(*) FROM messages m LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ? WHERE m.to_agent = ? AND r.message_id IS NULL",
                (aid, aid)
            )
            unread = (await c.fetchone())[0]

            # Compute display status from stored status + last_seen
            status = a["status"]
            if status not in _MANUAL_STATUSES and status != "stale":
                try:
                    from datetime import datetime, timezone, timedelta
                    last = datetime.fromisoformat(a["last_seen"].replace("Z", "+00:00"))
                    age = datetime.now(timezone.utc) - last
                    if age > timedelta(minutes=offline_minutes):
                        status = "offline"
                    elif age > timedelta(minutes=idle_minutes) and status not in ("working",):
                        status = "idle"
                except Exception:
                    pass

            result[aid] = {
                "role": a["role"], "name": a["name"], "cwd": a["cwd"],
                "model": a["model"], "instructions": a["instructions"],
                "status": status, "registeredAt": a["registered_at"],
                "lastSeen": a["last_seen"], "unread": unread,
            }
        return {"agents": result}
    finally:
        await db.close()


@router.post("/agents")
async def register_agent(req: AgentRegister, request: Request):
    validate_name(req.agentId, "agent ID")
    db = await get_db()
    try:
        now = _now()
        await db.execute(
            "INSERT OR REPLACE INTO agents (id, role, name, cwd, model, instructions, status, registered_at, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
            (req.agentId, req.role, req.name or req.agentId, req.cwd or "", req.model or "",
             req.instructions or "", req.status or "idle", now, now)
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("agent_registered", {"agentId": req.agentId, "role": req.role})
        return {"ok": True, "agentId": req.agentId, "role": req.role, "status": req.status or "idle"}
    finally:
        await db.close()


@router.delete("/agents/{agent_id}")
async def unregister_agent(agent_id: str, request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("agent_removed", {"agentId": agent_id})
        return {"ok": cursor.rowcount > 0}
    finally:
        await db.close()


@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: str, req: AgentStatusUpdate, request: Request):
    db = await get_db()
    try:
        note = getattr(req, 'note', None) or ''
        status_val = f"{req.status}: {note}" if note else req.status
        cursor = await db.execute(
            "UPDATE agents SET status = ?, last_seen = ? WHERE id = ?",
            (status_val, _now(), agent_id)
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        ws = await _get_ws(request)
        if ws: await ws.broadcast("agent_status", {"agentId": agent_id, "status": req.status})
        return {"ok": True, "agentId": agent_id, "status": req.status}
    finally:
        await db.close()


# ─── Messages ────────────────────────────────────────────────────────────────

@router.post("/messages/send")
async def send_message(req: MessageSend, request: Request):
    if not req.to and not req.toRole:
        raise HTTPException(400, "Need 'to' or 'toRole'")
    db = await get_db()
    try:
        await _touch_agent(db, req.from_agent)
        msg_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        ts = int(time.time() * 1000)

        # Find recipients
        recipients = []
        if req.to:
            recipients.append(req.to)
        if req.toRole:
            cursor = await db.execute("SELECT id FROM agents WHERE role = ? AND id != ?", (req.toRole, req.from_agent))
            for row in await cursor.fetchall():
                recipients.append(row["id"])

        if not recipients:
            return {"ok": False, "error": "No recipients found", "recipients": []}

        for r in recipients:
            await db.execute(
                "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, in_reply_to, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"{msg_id}-{r}" if len(recipients) > 1 else msg_id,
                 req.from_agent, r, "direct", req.type, req.subject, req.body, req.priority, req.inReplyTo, ts)
            )

        # Gather recipient status info for sender context
        recipient_info = {}
        for r in recipients:
            c = await db.execute("SELECT status, last_seen FROM agents WHERE id = ?", (r,))
            row = await c.fetchone()
            uc = await db.execute(
                "SELECT COUNT(*) FROM messages m LEFT JOIN read_receipts rr ON m.id = rr.message_id AND rr.agent_id = ? WHERE m.to_agent = ? AND rr.message_id IS NULL",
                (r, r)
            )
            unread = (await uc.fetchone())[0]
            if row:
                status = row["status"]
                # Auto-idle check
                if status not in _MANUAL_STATUSES and status != "stale":
                    try:
                        from datetime import datetime, timezone, timedelta
                        last = datetime.fromisoformat(row["last_seen"].replace("Z", "+00:00"))
                        if datetime.now(timezone.utc) - last > timedelta(minutes=5):
                            status = "idle"
                    except Exception:
                        pass
                recipient_info[r] = {"status": status, "unread": unread}

        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("message_sent", {"id": msg_id, "from": req.from_agent, "to": recipients, "subject": req.subject})
            for r in recipients:
                await ws.notify_agent(r, "new_message", {"from": req.from_agent, "subject": req.subject})
        return {"ok": True, "messageId": msg_id, "recipients": recipients, "recipientStatus": recipient_info}
    finally:
        await db.close()


@router.get("/messages/inbox/{agent_id}")
async def get_inbox(
    agent_id: str, request: Request,
    filter: str = Query("unread", pattern="^(unread|read|all)$"),
    fromAgent: Optional[str] = None, fromRole: Optional[str] = None,
    type: Optional[str] = None, limit: int = Query(200, ge=1, le=1000),
    peek: Optional[str] = None,
):
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        # Build query
        if filter == "unread":
            base = """SELECT m.*, NULL as read_at FROM messages m
                      LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                      WHERE m.to_agent = ? AND r.message_id IS NULL"""
            params = [agent_id, agent_id]
        elif filter == "read":
            base = """SELECT m.*, r.read_at FROM messages m
                      JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                      WHERE m.to_agent = ?"""
            params = [agent_id, agent_id]
        else:
            base = """SELECT m.*, r.read_at FROM messages m
                      LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                      WHERE m.to_agent = ?"""
            params = [agent_id, agent_id]

        if fromAgent:
            base += " AND m.from_agent = ?"
            params.append(fromAgent)
        if fromRole:
            base += " AND m.from_agent IN (SELECT id FROM agents WHERE role = ?)"
            params.append(fromRole)
        if type:
            base += " AND m.type = ?"
            params.append(type)

        base += " ORDER BY m.timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = await db.execute(base, params)
        rows = await cursor.fetchall()

        # Count total (without limit)
        count_q = base.replace("SELECT m.*, NULL as read_at", "SELECT COUNT(*)").replace("SELECT m.*, r.read_at", "SELECT COUNT(*)")
        count_q = count_q[:count_q.rfind("LIMIT")]
        c = await db.execute(count_q, params[:-1])
        total = (await c.fetchone())[0]

        messages = []
        for row in rows:
            msg = {
                "id": row["id"], "from": row["from_agent"], "type": row["type"],
                "source": row["source"], "channel": row["channel"],
                "subject": row["subject"], "body": row["body"],
                "priority": row["priority"], "timestamp": row["timestamp"],
                "inReplyTo": row["in_reply_to"],
                "read": row["read_at"] is not None,
                "readAt": row["read_at"],
            }
            # Include parent message context for replies
            if row["in_reply_to"]:
                pc = await db.execute("SELECT from_agent, subject, body FROM messages WHERE id = ?", (row["in_reply_to"],))
                parent = await pc.fetchone()
                if parent:
                    msg["parentContext"] = {"from": parent["from_agent"], "subject": parent["subject"], "preview": (parent["body"] or "")[:100]}
            messages.append(msg)

        # Mark as read + update status (unless peek)
        if not peek:
            now = _now()
            unread_found = 0
            for msg in messages:
                if not msg["read"]:
                    unread_found += 1
                    await db.execute(
                        "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                        (msg["id"], agent_id, now)
                    )
            # Smart status: got messages = working, no messages = idle
            new_status = "working" if unread_found > 0 else "idle"
            await db.execute(
                "UPDATE agents SET last_seen = ?, status = CASE WHEN status IN ('blocked','completed') THEN status ELSE ? END WHERE id = ?",
                (now, new_status, agent_id)
            )
            await db.commit()

        return {"total": total, "showing": len(messages), "messages": messages}
    finally:
        await db.close()


@router.get("/messages/search")
async def search_messages(
    request: Request, query: str = "",
    agentId: Optional[str] = None,
    scope: str = Query("all", pattern="^(inbox|shared|all)$"),
    limit: int = Query(10, ge=1, le=100),
):
    db = await get_db()
    try:
        q = f"%{query.lower()}%"
        results = []

        if agentId and scope in ("inbox", "all"):
            cursor = await db.execute(
                "SELECT * FROM messages WHERE to_agent = ? AND (LOWER(subject) LIKE ? OR LOWER(body) LIKE ? OR LOWER(from_agent) LIKE ?) ORDER BY timestamp DESC LIMIT ?",
                (agentId, q, q, q, limit)
            )
            for row in await cursor.fetchall():
                results.append({
                    "type": "message", "id": row["id"], "from": row["from_agent"],
                    "subject": row["subject"], "preview": (row["body"] or "")[:150],
                })

        if scope in ("shared", "all"):
            cursor = await db.execute(
                "SELECT * FROM shared_artifacts WHERE LOWER(name) LIKE ? OR LOWER(description) LIKE ? LIMIT ?",
                (q, q, limit)
            )
            for row in await cursor.fetchall():
                results.append({
                    "type": "shared", "name": row["name"], "from": row["from_agent"],
                    "description": row["description"], "size": row["size"],
                })

        return {"results": results[:limit], "total": len(results)}
    finally:
        await db.close()


# ─── Agent Info ──────────────────────────────────────────────────────────────

@router.get("/agents/{agent_id}/last-read")
async def agent_last_read(agent_id: str, request: Request):
    """Get the last message this agent read — useful for checking if they've seen your message."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT m.*, r.read_at FROM read_receipts r JOIN messages m ON m.id = r.message_id WHERE r.agent_id = ? ORDER BY r.read_at DESC LIMIT 1",
            (agent_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return {"agentId": agent_id, "lastRead": None}
        return {"agentId": agent_id, "lastRead": {
            "messageId": row["id"], "from": row["from_agent"], "subject": row["subject"],
            "type": row["type"], "readAt": row["read_at"], "timestamp": row["timestamp"],
        }}
    finally:
        await db.close()


@router.post("/agents/{agent_id}/heartbeat")
async def agent_heartbeat(agent_id: str, request: Request):
    """Lightweight heartbeat — called by notification hook to signal agent is alive."""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE agents SET last_seen = ?, status = CASE WHEN status IN ('blocked','completed') THEN status ELSE 'working' END WHERE id = ?",
            (_now(), agent_id)
        )
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@router.delete("/messages/{message_id}")
async def unsend_message(message_id: str, request: Request):
    """Delete a message by ID. Also removes associated read receipts."""
    db = await get_db()
    try:
        await db.execute("DELETE FROM read_receipts WHERE message_id = ?", (message_id,))
        cursor = await db.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"Message '{message_id}' not found")
        ws = await _get_ws(request)
        if ws: await ws.broadcast("message_deleted", {"id": message_id})
        return {"ok": True, "id": message_id}
    finally:
        await db.close()


# ─── Shared Artifacts ────────────────────────────────────────────────────────

@router.get("/shared")
async def list_shared(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM shared_artifacts ORDER BY shared_at DESC")
        files = []
        for row in await cursor.fetchall():
            files.append({
                "name": row["name"], "from": row["from_agent"],
                "description": row["description"], "size": row["size"],
                "sharedAt": row["shared_at"],
            })
        return {"files": files}
    finally:
        await db.close()


@router.post("/shared")
async def share_artifact(
    request: Request,
    from_agent: str = Form(...), name: str = Form(...),
    description: str = Form(""), content: str = Form(None),
    file: UploadFile = File(None),
):
    validate_name(name, "artifact name")
    db = await get_db()
    try:
        now = _now()
        if file:
            shared_dir = _shared_dir(request)
            file_path = shared_dir / name
            data = await file.read()
            file_path.write_bytes(data)
            await db.execute(
                "INSERT OR REPLACE INTO shared_artifacts (name, from_agent, description, file_path, size, is_binary, shared_at) VALUES (?,?,?,?,?,?,?)",
                (name, from_agent, description, str(file_path), len(data), 1, now)
            )
        else:
            text = content or ""
            await db.execute(
                "INSERT OR REPLACE INTO shared_artifacts (name, from_agent, description, content, size, is_binary, shared_at) VALUES (?,?,?,?,?,?,?)",
                (name, from_agent, description, text, len(text), 0, now)
            )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("file_shared", {"name": name, "from": from_agent})
        return {"ok": True, "name": name}
    finally:
        await db.close()


@router.get("/shared/{name}")
async def read_shared(name: str, request: Request):
    validate_name(name, "artifact name")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM shared_artifacts WHERE name = ?", (name,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Artifact '{name}' not found")
        meta = {"from": row["from_agent"], "description": row["description"], "size": row["size"], "sharedAt": row["shared_at"]}
        if row["is_binary"] and row["file_path"]:
            from fastapi.responses import FileResponse
            return FileResponse(row["file_path"], filename=name)
        return {"content": row["content"], "meta": meta}
    finally:
        await db.close()


@router.delete("/shared/{name}")
async def delete_shared(name: str, request: Request):
    validate_name(name, "artifact name")
    db = await get_db()
    try:
        # Delete file if binary
        cursor = await db.execute("SELECT file_path FROM shared_artifacts WHERE name = ? AND is_binary = 1", (name,))
        row = await cursor.fetchone()
        if row and row["file_path"]:
            p = Path(row["file_path"])
            if p.exists(): p.unlink()
        await db.execute("DELETE FROM shared_artifacts WHERE name = ?", (name,))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


# ─── Channels ────────────────────────────────────────────────────────────────

@router.get("/channels")
async def list_channels(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM channels")
        channels = []
        for ch in await cursor.fetchall():
            mc = await db.execute("SELECT COUNT(*) FROM channel_members WHERE channel_name = ?", (ch["name"],))
            member_count = (await mc.fetchone())[0]
            msg_c = await db.execute("SELECT COUNT(*) FROM messages WHERE channel = ?", (ch["name"],))
            msg_count = (await msg_c.fetchone())[0]
            channels.append({
                "name": ch["name"], "description": ch["description"],
                "createdBy": ch["created_by"], "createdAt": ch["created_at"],
                "members": [], "memberCount": member_count, "messageCount": msg_count,
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
async def get_channel(name: str, request: Request, limit: int = Query(50, ge=1, le=500), offset: int = 0):
    validate_name(name, "channel name")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM channels WHERE name = ?", (name,))
        ch = await cursor.fetchone()
        if not ch:
            raise HTTPException(404, f"Channel '{name}' not found")

        mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (name,))
        members = [r["agent_id"] for r in await mem_c.fetchall()]

        total_c = await db.execute("SELECT COUNT(*) FROM messages WHERE channel = ?", (name,))
        total = (await total_c.fetchone())[0]

        # Paginate newest first
        msg_c = await db.execute(
            "SELECT * FROM messages WHERE channel = ? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (name, limit, offset)
        )
        messages = []
        for row in await msg_c.fetchall():
            messages.append({
                "id": row["id"], "from": row["from_agent"], "type": row["type"],
                "body": row["body"], "timestamp": row["timestamp"],
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
        await db.execute("DELETE FROM messages WHERE channel = ?", (name,))
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
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM channels WHERE name = ?", (name,))
        if not await cursor.fetchone():
            raise HTTPException(404, f"Channel '{name}' not found")
        now = _now()
        await db.execute(
            "INSERT OR IGNORE INTO channel_members (channel_name, agent_id, joined_at) VALUES (?,?,?)",
            (name, req.agentId, now)
        )
        # System message
        await db.execute(
            "INSERT INTO messages (id, from_agent, channel, source, type, subject, body, timestamp) VALUES (?,?,?,?,?,?,?,?)",
            (f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}", "_system", name, "channel", "info", f"#{name}", f"{req.agentId} joined the channel", int(time.time()*1000))
        )
        await db.commit()
        mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (name,))
        members = [r["agent_id"] for r in await mem_c.fetchall()]
        return {"ok": True, "members": members}
    finally:
        await db.close()


@router.post("/channels/{name}/leave")
async def leave_channel(name: str, req: ChannelJoin, request: Request):
    validate_name(name, "channel name")
    db = await get_db()
    try:
        await db.execute("DELETE FROM channel_members WHERE channel_name = ? AND agent_id = ?", (name, req.agentId))
        await db.execute(
            "INSERT INTO messages (id, from_agent, channel, source, type, subject, body, timestamp) VALUES (?,?,?,?,?,?,?,?)",
            (f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}", "_system", name, "channel", "info", f"#{name}", f"{req.agentId} left the channel", int(time.time()*1000))
        )
        await db.commit()
        mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (name,))
        members = [r["agent_id"] for r in await mem_c.fetchall()]
        return {"ok": True, "members": members}
    finally:
        await db.close()


@router.post("/channels/{name}/send")
async def send_channel_message(name: str, req: ChannelMessage, request: Request):
    validate_name(name, "channel name")
    db = await get_db()
    try:
        await _touch_agent(db, req.from_agent)

        # Verify membership
        cursor = await db.execute("SELECT 1 FROM channel_members WHERE channel_name = ? AND agent_id = ?", (name, req.from_agent))
        if not await cursor.fetchone():
            raise HTTPException(403, f"Agent '{req.from_agent}' is not a member of #{name}. Join first.")

        msg_id = f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"
        ts = int(time.time() * 1000)

        # Channel message (canonical)
        await db.execute(
            "INSERT INTO messages (id, from_agent, channel, source, type, subject, body, timestamp) VALUES (?,?,?,?,?,?,?,?)",
            (msg_id, req.from_agent, name, "channel", req.type, f"#{name}: {req.body[:80]}", req.body, ts)
        )

        # Deliver to each member's inbox (except sender)
        mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (name,))
        members = [r["agent_id"] for r in await mem_c.fetchall()]
        for member in members:
            if member != req.from_agent:
                await db.execute(
                    "INSERT INTO messages (id, from_agent, to_agent, channel, source, type, subject, body, priority, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (f"{msg_id}-{member}", req.from_agent, member, name, "channel", req.type, f"#{name}: {req.body[:80]}", req.body, "normal", ts)
                )

        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("channel_message", {"channel": name, "from": req.from_agent, "body": req.body[:200]})
        return {"ok": True, "messageId": msg_id, "members": members}
    finally:
        await db.close()


# ─── Settings ────────────────────────────────────────────────────────────────

@router.get("/settings")
async def get_settings(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT key, value FROM settings")
        saved = {}
        for row in await cursor.fetchall():
            try:
                saved[row["key"]] = json.loads(row["value"])
            except Exception:
                saved[row["key"]] = row["value"]
        return {**DEFAULT_SETTINGS, **saved}
    finally:
        await db.close()


@router.put("/settings")
async def update_settings(request: Request):
    body = await request.json()
    db = await get_db()
    try:
        for key, value in body.items():
            if key in DEFAULT_SETTINGS:
                await db.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                    (key, json.dumps(value))
                )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("settings_updated")
        return await get_settings(request)
    finally:
        await db.close()


# ─── Stats ───────────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(request: Request):
    db = await get_db()
    try:
        agents_c = await db.execute("SELECT COUNT(*) FROM agents")
        agents = (await agents_c.fetchone())[0]

        total_c = await db.execute("SELECT COUNT(*) FROM messages WHERE source = 'direct'")
        total = (await total_c.fetchone())[0]

        # Unread across all agents
        unread_c = await db.execute(
            "SELECT COUNT(*) FROM messages m LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = m.to_agent WHERE m.to_agent IS NOT NULL AND r.message_id IS NULL"
        )
        unread = (await unread_c.fetchone())[0]

        # Today
        today_start = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")) * 1000)
        today_c = await db.execute("SELECT COUNT(*) FROM messages WHERE timestamp >= ?", (today_start,))
        today = (await today_c.fetchone())[0]

        # By type
        type_c = await db.execute("SELECT type, COUNT(*) as cnt FROM messages WHERE source = 'direct' GROUP BY type")
        by_type = {row["type"]: row["cnt"] for row in await type_c.fetchall()}

        # By agent
        agent_c = await db.execute("SELECT to_agent, COUNT(*) as cnt FROM messages WHERE to_agent IS NOT NULL GROUP BY to_agent")
        by_agent = {row["to_agent"]: row["cnt"] for row in await agent_c.fetchall()}

        # Shared
        shared_c = await db.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(size),0) as total_size FROM shared_artifacts")
        shared_row = await shared_c.fetchone()

        return {
            "agents": agents,
            "total_messages": total,
            "unread_messages": unread,
            "messages_today": today,
            "messages_by_type": by_type,
            "messages_by_agent": by_agent,
            "shared_files": shared_row["cnt"],
            "shared_size_bytes": shared_row["total_size"],
            "shared_size_mb": round(shared_row["total_size"] / 1048576, 2),
        }
    finally:
        await db.close()


# ─── Clear ───────────────────────────────────────────────────────────────────

@router.post("/clear")
async def clear_data(req: ClearRequest, request: Request):
    db = await get_db()
    try:
        cutoff = None
        if req.olderThanHours:
            cutoff = int((time.time() - req.olderThanHours * 3600) * 1000)

        if req.target in ("inbox", "all"):
            if req.agentId:
                if cutoff:
                    await db.execute("DELETE FROM messages WHERE to_agent = ? AND timestamp < ?", (req.agentId, cutoff))
                else:
                    await db.execute("DELETE FROM messages WHERE to_agent = ?", (req.agentId,))
            else:
                if cutoff:
                    await db.execute("DELETE FROM messages WHERE timestamp < ?", (cutoff,))
                else:
                    await db.execute("DELETE FROM messages")

        if req.target in ("shared", "all"):
            # Delete binary files from disk
            cursor = await db.execute("SELECT file_path FROM shared_artifacts WHERE is_binary = 1")
            for row in await cursor.fetchall():
                if row["file_path"]:
                    p = Path(row["file_path"])
                    if p.exists(): p.unlink()
            await db.execute("DELETE FROM shared_artifacts")

        if req.target in ("agents", "all"):
            await db.execute("DELETE FROM agents")

        if req.target in ("channels", "all"):
            await db.execute("DELETE FROM channel_members")
            await db.execute("DELETE FROM channels")

        if req.target == "all":
            await db.execute("DELETE FROM read_receipts")

        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("data_cleared", {"target": req.target})
        return {"ok": True}
    finally:
        await db.close()


# ─── Rotate ──────────────────────────────────────────────────────────────────

@router.post("/rotate")
async def rotate(request: Request):
    settings = await get_settings(request)
    if not settings.get("rotation_enabled", True):
        return {"ok": False, "reason": "Rotation disabled"}

    db = await get_db()
    try:
        stats = {"expired_messages": 0, "trimmed_messages": 0, "expired_files": 0, "stale_agents": 0}

        # Expire old messages
        retention_ms = int(settings["retention_days"] * 86400 * 1000)
        cutoff = int(time.time() * 1000) - retention_ms
        cursor = await db.execute("DELETE FROM messages WHERE timestamp < ?", (cutoff,))
        stats["expired_messages"] = cursor.rowcount

        # Trim per-agent inboxes
        max_msgs = settings["max_messages_per_agent"]
        agents_c = await db.execute("SELECT id FROM agents")
        for agent in await agents_c.fetchall():
            aid = agent["id"]
            c = await db.execute("SELECT COUNT(*) FROM messages WHERE to_agent = ?", (aid,))
            count = (await c.fetchone())[0]
            if count > max_msgs:
                trim = count - max_msgs
                await db.execute(
                    "DELETE FROM messages WHERE id IN (SELECT id FROM messages WHERE to_agent = ? ORDER BY timestamp ASC LIMIT ?)",
                    (aid, trim)
                )
                stats["trimmed_messages"] += trim

        # Mark stale agents
        stale_hours = settings["stale_agent_hours"]
        stale_cutoff = _now()  # We compare in SQL
        cursor = await db.execute(
            "UPDATE agents SET status = 'stale' WHERE status != 'stale' AND last_seen < datetime('now', ? || ' hours')",
            (f"-{stale_hours}",)
        )
        stats["stale_agents"] = cursor.rowcount

        # Clean orphaned read receipts
        await db.execute("DELETE FROM read_receipts WHERE message_id NOT IN (SELECT id FROM messages)")

        await db.commit()
        return {"ok": True, "stats": stats}
    finally:
        await db.close()


# ─── Dashboard ───────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    html_path = Path(__file__).parent.parent / "dashboard.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))
