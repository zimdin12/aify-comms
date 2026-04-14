"""
aify-comms — Legacy Message Bus API

HTTP endpoints for inter-agent communication. Replaces the filesystem-based
.messages/ directory, enabling cross-machine agent communication.

Agents register, send messages, share artifacts,
and view a dashboard — all over HTTP.
"""

import datetime
import json
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$')

def validate_name(name: str, label: str = "name") -> None:
    """Reject names that could escape filesystem boundaries."""
    if not SAFE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: must be 1-128 alphanumeric chars, dots, hyphens, underscores.")

router = APIRouter(tags=["api"])

# ─── Paths (resolved from app config at runtime) ────────────────────────────

def _data_dir(request: Request) -> Path:
    """Get data directory from app config, fallback to /data."""
    try:
        config = request.app.state.config
        return Path(config.data_dir)
    except Exception:
        return Path("/data")

def _dirs(request: Request):
    d = _data_dir(request)
    agents = d / "agents.json"
    inbox = d / "inbox"
    shared = d / "shared"
    for p in [inbox, shared]:
        p.mkdir(parents=True, exist_ok=True)
    return agents, inbox, shared

# ─── Settings ────────────────────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    "retention_days": 90,
    "max_messages_per_agent": 1000,
    "max_shared_size_mb": 500,
    "stale_agent_hours": 24,
    "dashboard_refresh_seconds": 15,
    "rotation_enabled": True,
}

def _settings_file(request: Request) -> Path:
    return _data_dir(request) / "settings.json"

def _read_settings(request: Request) -> dict:
    f = _settings_file(request)
    try:
        saved = json.loads(f.read_text(encoding="utf-8"))
        return {**DEFAULT_SETTINGS, **saved}
    except Exception:
        return dict(DEFAULT_SETTINGS)

def _write_settings(request: Request, settings: dict):
    _settings_file(request).write_text(encoding="utf-8", data=json.dumps(settings, indent=2))

# ─── Models ──────────────────────────────────────────────────────────────────

class AgentRegister(BaseModel):
    agentId: str
    role: str
    name: Optional[str] = None
    cwd: Optional[str] = None
    model: Optional[str] = None
    instructions: Optional[str] = None
    status: Optional[str] = None

class AgentStatusUpdate(BaseModel):
    status: str  # idle, working, reviewing, testing, researching, blocked, completed

class MessageSend(BaseModel):
    from_agent: str
    to: Optional[str] = None
    toRole: Optional[str] = None
    type: str = "info"
    subject: str
    body: str
    priority: str = "normal"  # normal, high, urgent
    inReplyTo: Optional[str] = None
    trigger: bool = False  # If true, spawn a Claude Code instance to handle this message

class ClearRequest(BaseModel):
    target: str  # inbox, shared, agents, all, channels
    agentId: Optional[str] = None
    olderThanHours: Optional[float] = None

class ChannelCreate(BaseModel):
    name: str
    description: Optional[str] = None
    createdBy: str

class ChannelMessage(BaseModel):
    from_agent: str
    channel: str
    body: str
    type: str = "info"
    priority: str = "normal"
    trigger: bool = True
    silent: bool = False

class ChannelJoin(BaseModel):
    agentId: str

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _read_agents(agents_file: Path) -> dict:
    try:
        return json.loads(agents_file.read_text(encoding="utf-8"))
    except Exception:
        return {"agents": {}}

def _write_agents(agents_file: Path, data: dict):
    agents_file.write_text(encoding="utf-8", data=json.dumps(data, indent=2))

def _read_inbox(inbox_dir: Path, agent_id: str, filter_: str = "unread") -> list:
    d = inbox_dir / agent_id
    d.mkdir(parents=True, exist_ok=True)
    messages = []
    for f in sorted(d.glob("*.json"), reverse=True):
        try:
            msg = json.loads(f.read_text(encoding="utf-8"))
            msg["_file"] = f.name
            msg["_read"] = f.name.endswith(".read.json")
            if filter_ == "unread" and msg["_read"]:
                continue
            if filter_ == "read" and not msg["_read"]:
                continue
            messages.append(msg)
        except Exception:
            continue
    return messages

def _mark_read(inbox_dir: Path, agent_id: str, messages: list):
    d = inbox_dir / agent_id
    now = _now()
    for m in messages:
        if m.get("_read"):
            continue
        old = d / m["_file"]
        new = d / m["_file"].replace(".json", ".read.json")
        try:
            content = json.loads(old.read_text(encoding="utf-8"))
            content["readAt"] = now
            old.write_text(encoding="utf-8", data=json.dumps(content))
            old.rename(new)
        except Exception:
            pass

# Manual statuses that auto-status should not override
_MANUAL_STATUSES = {"blocked", "completed"}

def _touch_agent(agents_file: Path, registry: dict, agent_id: str):
    """Update lastSeen and auto-set status to 'active' (unless manually set to blocked/completed)."""
    agent = registry["agents"].get(agent_id)
    if not agent:
        return
    agent["lastSeen"] = _now()
    if agent.get("status") not in _MANUAL_STATUSES:
        agent["status"] = "active"
    _write_agents(agents_file, registry)

def _deliver(inbox_dir: Path, to_id: str, message: dict):
    d = inbox_dir / to_id
    d.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    uid = uuid.uuid4().hex[:8]
    (d / f"{ts}-{uid}.json").write_text(encoding="utf-8", data=json.dumps({**message, "timestamp": ts}))

def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

# ─── Root ────────────────────────────────────────────────────────────────────

@router.get("/")
async def root():
    return {
        "service": "aify-comms",
        "version": "3.6.6",
        "endpoints": {
            "agents": "/api/v1/agents",
            "messages": "/api/v1/messages",
            "shared": "/api/v1/shared",
            "settings": "/api/v1/settings",
            "dashboard": "/api/v1/dashboard",
            "stats": "/api/v1/stats",
            "rotate": "/api/v1/rotate",
            "clear": "/api/v1/clear",
        },
    }

# ─── Agents ──────────────────────────────────────────────────────────────────

@router.get("/agents")
async def list_agents(request: Request):
    agents_file, inbox_dir, _ = _dirs(request)
    registry = _read_agents(agents_file)
    idle_cutoff = time.time() - 300  # 5 minutes
    result = {}
    for aid, info in registry["agents"].items():
        unread = len(_read_inbox(inbox_dir, aid, "unread"))
        # Auto-idle: if not manually set and inactive for 5+ min
        status = info.get("status", "idle")
        if status not in _MANUAL_STATUSES and status != "stale":
            try:
                dt = datetime.datetime.fromisoformat(info.get("lastSeen", "").replace("Z", "+00:00"))
                if dt.timestamp() < idle_cutoff:
                    status = "idle"
            except Exception:
                pass
        result[aid] = {**info, "status": status, "unread": unread}
    return {"agents": result}

@router.post("/agents")
async def register_agent(req: AgentRegister, request: Request):
    validate_name(req.agentId, "agent ID")
    agents_file, inbox_dir, _ = _dirs(request)
    registry = _read_agents(agents_file)
    registry["agents"][req.agentId] = {
        "role": req.role,
        "name": req.name or req.agentId,
        "cwd": req.cwd or "",
        "model": req.model or "",
        "instructions": req.instructions or "",
        "status": req.status or "idle",
        "registeredAt": _now(),
        "lastSeen": _now(),
    }
    _write_agents(agents_file, registry)
    (inbox_dir / req.agentId).mkdir(parents=True, exist_ok=True)
    return {"ok": True, "agentId": req.agentId, "role": req.role, "status": registry["agents"][req.agentId]["status"]}

@router.delete("/agents/{agent_id}")
async def unregister_agent(agent_id: str, request: Request):
    agents_file, _, _ = _dirs(request)
    registry = _read_agents(agents_file)
    removed = agent_id in registry["agents"]
    registry["agents"].pop(agent_id, None)
    _write_agents(agents_file, registry)
    return {"ok": removed}

@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: str, req: AgentStatusUpdate, request: Request):
    """Update an agent's status. Valid statuses: idle, working, reviewing, testing, researching, blocked, completed."""
    agents_file, _, _ = _dirs(request)
    registry = _read_agents(agents_file)
    if agent_id not in registry["agents"]:
        raise HTTPException(404, f"Agent '{agent_id}' not found")
    registry["agents"][agent_id]["status"] = req.status
    registry["agents"][agent_id]["lastSeen"] = _now()
    _write_agents(agents_file, registry)
    return {"ok": True, "agentId": agent_id, "status": req.status}

# ─── Messages ────────────────────────────────────────────────────────────────

@router.post("/messages/send")
async def send_message(req: MessageSend, request: Request):
    agents_file, inbox_dir, _ = _dirs(request)
    if not req.to and not req.toRole:
        raise HTTPException(400, "Need 'to' or 'toRole'")
    registry = _read_agents(agents_file)
    _touch_agent(agents_file, registry, req.from_agent)
    msg_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    message = {"id": msg_id, "from": req.from_agent, "type": req.type, "subject": req.subject, "body": req.body, "priority": req.priority}
    if req.inReplyTo:
        message["inReplyTo"] = req.inReplyTo
    recipients = []
    if req.to:
        recipients.append(req.to)
    if req.toRole:
        for aid, info in registry["agents"].items():
            if info["role"] == req.toRole and aid != req.from_agent:
                recipients.append(aid)
    if not recipients:
        return {"ok": False, "error": "No recipients found", "recipients": []}
    for r in recipients:
        _deliver(inbox_dir, r, message)
    return {"ok": True, "messageId": msg_id, "recipients": recipients}

@router.get("/messages/inbox/{agent_id}")
async def get_inbox(
    agent_id: str, request: Request,
    filter: str = Query("unread", pattern="^(unread|read|all)$"),
    fromAgent: Optional[str] = None, fromRole: Optional[str] = None,
    type: Optional[str] = None, limit: int = Query(200, ge=1, le=1000),
):
    validate_name(agent_id, "agent ID")
    agents_file, inbox_dir, _ = _dirs(request)
    registry = _read_agents(agents_file)
    messages = _read_inbox(inbox_dir, agent_id, filter)
    if fromAgent:
        messages = [m for m in messages if m.get("from") == fromAgent]
    if fromRole:
        messages = [m for m in messages if registry["agents"].get(m.get("from"), {}).get("role") == fromRole]
    if type:
        messages = [m for m in messages if m.get("type") == type]
    total = len(messages)
    shown = messages[:limit]
    # Only mark as read when explicitly requested (not during dashboard/peek views)
    if not request.query_params.get("peek"):
        _mark_read(inbox_dir, agent_id, shown)
    clean = []
    for m in shown:
        c = {k: v for k, v in m.items() if not k.startswith("_")}
        c["read"] = m.get("_read", False)
        clean.append(c)
    return {"total": total, "showing": len(clean), "messages": clean}

@router.get("/messages/search")
async def search_messages(
    request: Request, query: str = "",
    agentId: Optional[str] = None,
    scope: str = Query("all", pattern="^(inbox|shared|all)$"),
    limit: int = Query(10, ge=1, le=100),
):
    _, inbox_dir, shared_dir = _dirs(request)
    q = query.lower()
    results = []
    if agentId and scope in ("inbox", "all"):
        for m in _read_inbox(inbox_dir, agentId, "all"):
            haystack = f"{m.get('subject', '')} {m.get('body', '')} {m.get('from', '')}".lower()
            if q in haystack:
                results.append({"type": "message", "read": m.get("_read", False), "id": m.get("id"),
                    "from": m.get("from"), "subject": m.get("subject"), "preview": (m.get("body") or "")[:150]})
    if scope in ("shared", "all"):
        for f in shared_dir.iterdir():
            if f.name.endswith(".meta.json"):
                continue
            meta = {}
            mf = shared_dir / f"{f.name}.meta.json"
            if mf.exists():
                try: meta = json.loads(mf.read_text(encoding="utf-8"))
                except Exception: pass
            haystack = f"{f.name} {meta.get('description', '')} {meta.get('from', '')}".lower()
            content_match = False
            if f.stat().st_size < 1_000_000:
                try:
                    if q in f.read_text(encoding="utf-8").lower(): content_match = True
                except Exception: pass
            if q in haystack or content_match:
                results.append({"type": "artifact", "name": f.name, "from": meta.get("from", "?"),
                    "description": meta.get("description", ""), "size": f.stat().st_size})
    return {"total": len(results), "results": results[:limit]}

# ─── Shared artifacts ────────────────────────────────────────────────────────

@router.get("/shared")
async def list_shared(request: Request):
    _, _, shared_dir = _dirs(request)
    files = []
    for f in sorted(shared_dir.iterdir()):
        if f.name.endswith(".meta.json"):
            continue
        meta = {}
        mf = shared_dir / f"{f.name}.meta.json"
        if mf.exists():
            try: meta = json.loads(mf.read_text(encoding="utf-8"))
            except Exception: pass
        files.append({"name": f.name, "size": f.stat().st_size, "from": meta.get("from", "?"),
            "description": meta.get("description", ""), "sharedAt": meta.get("sharedAt", "")})
    return {"files": files}

@router.post("/shared")
async def share_artifact(
    request: Request, from_agent: str = Form(...), name: str = Form(...),
    description: str = Form(""), content: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    validate_name(name, "artifact name")
    _, _, shared_dir = _dirs(request)
    dest = shared_dir / name
    if file:
        data = await file.read(); dest.write_bytes(data); size = len(data)
    elif content:
        dest.write_text(encoding="utf-8", data=content); size = len(content)
    else:
        raise HTTPException(400, "Need 'content' or 'file'")
    meta = {"from": from_agent, "name": name, "description": description, "sharedAt": _now(), "size": size}
    (shared_dir / f"{name}.meta.json").write_text(encoding="utf-8", data=json.dumps(meta, indent=2))
    return {"ok": True, "name": name, "size": size}

@router.get("/shared/{name}")
async def get_shared(name: str, request: Request):
    validate_name(name, "artifact name")
    _, _, shared_dir = _dirs(request)
    artifact = shared_dir / name
    if not artifact.exists():
        raise HTTPException(404, f"'{name}' not found")
    binary_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".tar", ".gz"}
    if artifact.suffix.lower() in binary_exts:
        return FileResponse(artifact)
    meta = {}
    mf = shared_dir / f"{name}.meta.json"
    if mf.exists():
        try: meta = json.loads(mf.read_text(encoding="utf-8"))
        except Exception: pass
    return {"content": artifact.read_text(encoding="utf-8"), "meta": meta}

@router.delete("/shared/{name}")
async def delete_shared(name: str, request: Request):
    validate_name(name, "artifact name")
    _, _, shared_dir = _dirs(request)
    deleted = False
    for f in [shared_dir / name, shared_dir / f"{name}.meta.json"]:
        if f.exists(): f.unlink(); deleted = True
    return {"ok": deleted}

# ─── Channels (group chat) ───────────────────────────────────────────────────

def _channels_dir(request: Request) -> Path:
    d = _data_dir(request) / "channels"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _read_channel(channels_dir: Path, name: str) -> dict:
    f = channels_dir / f"{name}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None

def _write_channel(channels_dir: Path, name: str, data: dict):
    (channels_dir / f"{name}.json").write_text(encoding="utf-8", data=json.dumps(data, indent=2))

@router.get("/channels")
async def list_channels(request: Request):
    cdir = _channels_dir(request)
    channels = []
    for f in sorted(cdir.glob("*.json")):
        try:
            ch = json.loads(f.read_text(encoding="utf-8"))
            channels.append({
                "name": ch["name"],
                "description": ch.get("description", ""),
                "members": ch.get("members", []),
                "messageCount": len(ch.get("messages", [])),
                "createdBy": ch.get("createdBy", "?"),
                "createdAt": ch.get("createdAt", ""),
            })
        except Exception:
            continue
    return {"channels": channels}

@router.post("/channels")
async def create_channel(req: ChannelCreate, request: Request):
    validate_name(req.name, "channel name")
    cdir = _channels_dir(request)
    if _read_channel(cdir, req.name):
        raise HTTPException(409, f"Channel '{req.name}' already exists")
    ch = {
        "name": req.name,
        "description": req.description or "",
        "createdBy": req.createdBy,
        "createdAt": _now(),
        "members": [req.createdBy],
        "messages": [],
    }
    _write_channel(cdir, req.name, ch)
    return {"ok": True, "channel": req.name}

@router.get("/channels/{name}")
async def get_channel(name: str, request: Request, limit: int = Query(50, ge=1, le=500), offset: int = 0):
    validate_name(name, "channel name")
    cdir = _channels_dir(request)
    ch = _read_channel(cdir, name)
    if not ch:
        raise HTTPException(404, f"Channel '{name}' not found")
    msgs = ch.get("messages", [])
    total = len(msgs)
    # Return newest messages (from end), with pagination
    sliced = msgs[max(0, total - offset - limit):total - offset] if offset else msgs[-limit:]
    return {
        "name": ch["name"],
        "description": ch.get("description", ""),
        "members": ch.get("members", []),
        "totalMessages": total,
        "messages": sliced,
    }

@router.delete("/channels/{name}")
async def delete_channel(name: str, request: Request):
    cdir = _channels_dir(request)
    f = cdir / f"{name}.json"
    if f.exists():
        f.unlink()
        return {"ok": True}
    raise HTTPException(404, f"Channel '{name}' not found")

@router.post("/channels/{name}/join")
async def join_channel(name: str, req: ChannelJoin, request: Request):
    validate_name(name, "channel name")
    cdir = _channels_dir(request)
    ch = _read_channel(cdir, name)
    if not ch:
        raise HTTPException(404, f"Channel '{name}' not found")
    if req.agentId not in ch["members"]:
        ch["members"].append(req.agentId)
        # System message
        ch["messages"].append({
            "id": f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}",
            "from": "_system",
            "type": "info",
            "body": f"{req.agentId} joined the channel",
            "timestamp": int(time.time() * 1000),
        })
        _write_channel(cdir, name, ch)
    return {"ok": True, "members": ch["members"]}

@router.post("/channels/{name}/leave")
async def leave_channel(name: str, req: ChannelJoin, request: Request):
    validate_name(name, "channel name")
    cdir = _channels_dir(request)
    ch = _read_channel(cdir, name)
    if not ch:
        raise HTTPException(404, f"Channel '{name}' not found")
    if req.agentId in ch["members"]:
        ch["members"].remove(req.agentId)
        ch["messages"].append({
            "id": f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}",
            "from": "_system",
            "type": "info",
            "body": f"{req.agentId} left the channel",
            "timestamp": int(time.time() * 1000),
        })
        _write_channel(cdir, name, ch)
    return {"ok": True, "members": ch["members"]}

@router.post("/channels/{name}/send")
async def send_channel_message(name: str, req: ChannelMessage, request: Request):
    validate_name(name, "channel name")
    agents_file, _, _ = _dirs(request)
    registry = _read_agents(agents_file)
    _touch_agent(agents_file, registry, req.from_agent)
    cdir = _channels_dir(request)
    ch = _read_channel(cdir, name)
    if not ch:
        raise HTTPException(404, f"Channel '{name}' not found")
    if req.from_agent not in ch["members"]:
        raise HTTPException(403, f"Agent '{req.from_agent}' is not a member of #{name}. Join first.")
    msg_id = f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"
    msg = {
        "id": msg_id,
        "from": req.from_agent,
        "type": req.type,
        "body": req.body,
        "timestamp": int(time.time() * 1000),
    }
    ch["messages"].append(msg)
    _write_channel(cdir, name, ch)
    # Deliver to each member's inbox (except sender) so notifications work
    _, inbox_dir, _ = _dirs(request)
    inbox_msg = {
        "id": msg_id,
        "from": req.from_agent,
        "type": req.type,
        "source": "channel",
        "channel": name,
        "subject": f"#{name}: {req.body[:80]}",
        "body": req.body,
    }
    for member in ch["members"]:
        if member != req.from_agent:
            _deliver(inbox_dir, member, inbox_msg)
    return {"ok": True, "messageId": msg_id, "members": ch["members"]}

# ─── Clear ───────────────────────────────────────────────────────────────────

@router.post("/clear")
async def clear_data(req: ClearRequest, request: Request):
    agents_file, inbox_dir, shared_dir = _dirs(request)
    cutoff = None
    if req.olderThanHours:
        cutoff = time.time() * 1000 - req.olderThanHours * 3600_000
    cleared = {"messages": 0, "files": 0, "agents": 0}
    if req.target in ("inbox", "all"):
        dirs = [req.agentId] if req.agentId else [d.name for d in inbox_dir.iterdir() if d.is_dir()]
        for d in dirs:
            p = inbox_dir / d
            if not p.exists(): continue
            for f in p.glob("*.json"):
                if cutoff:
                    try:
                        msg = json.loads(f.read_text(encoding="utf-8"))
                        if msg.get("timestamp", 0) > cutoff: continue
                    except Exception: pass
                f.unlink(); cleared["messages"] += 1
    if req.target in ("shared", "all"):
        for f in shared_dir.iterdir():
            if cutoff and f.stat().st_mtime * 1000 > cutoff: continue
            f.unlink(); cleared["files"] += 1
    if req.target in ("agents", "all"):
        registry = _read_agents(agents_file)
        cleared["agents"] = len(registry["agents"])
        _write_agents(agents_file, {"agents": {}})
    return {"ok": True, "cleared": cleared}

# ─── Settings API ────────────────────────────────────────────────────────────

class SettingsUpdate(BaseModel):
    retention_days: Optional[int] = None
    max_messages_per_agent: Optional[int] = None
    max_shared_size_mb: Optional[int] = None
    stale_agent_hours: Optional[int] = None
    dashboard_refresh_seconds: Optional[int] = None
    rotation_enabled: Optional[bool] = None

@router.get("/settings")
async def get_settings(request: Request):
    return _read_settings(request)

@router.put("/settings")
async def update_settings(req: SettingsUpdate, request: Request):
    settings = _read_settings(request)
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    settings.update(updates)
    _write_settings(request, settings)
    return {"ok": True, "settings": settings}

# ─── Rotation ────────────────────────────────────────────────────────────────

@router.post("/rotate")
async def rotate_messages(request: Request):
    """Delete expired messages, trim large inboxes, cap shared files, mark stale agents."""
    settings = _read_settings(request)
    agents_file, inbox_dir, shared_dir = _dirs(request)
    if not settings.get("rotation_enabled", True):
        return {"ok": False, "reason": "Rotation disabled in settings"}
    retention_ms = settings["retention_days"] * 86400 * 1000
    max_per_agent = settings["max_messages_per_agent"]
    max_shared_mb = settings["max_shared_size_mb"]
    cutoff = time.time() * 1000 - retention_ms
    stats = {"expired_messages": 0, "trimmed_messages": 0, "expired_files": 0, "stale_agents": 0}

    if inbox_dir.exists():
        for agent_dir in inbox_dir.iterdir():
            if not agent_dir.is_dir(): continue
            for f in sorted(agent_dir.glob("*.json")):
                try:
                    msg = json.loads(f.read_text(encoding="utf-8"))
                    if msg.get("timestamp", 0) < cutoff:
                        f.unlink(); stats["expired_messages"] += 1
                except Exception: pass
            remaining = sorted(agent_dir.glob("*.json"))
            if len(remaining) > max_per_agent:
                for f in remaining[:len(remaining) - max_per_agent]:
                    f.unlink(); stats["trimmed_messages"] += 1

    if shared_dir.exists():
        total_size = 0
        files_with_time = []
        for f in shared_dir.iterdir():
            if f.name.endswith(".meta.json"): continue
            total_size += f.stat().st_size
            files_with_time.append((f.stat().st_mtime * 1000, f))
        for mtime, f in files_with_time:
            if mtime < cutoff:
                f.unlink()
                meta = shared_dir / f"{f.name}.meta.json"
                if meta.exists(): meta.unlink()
                stats["expired_files"] += 1
        if total_size > max_shared_mb * 1024 * 1024:
            files_with_time.sort()
            for mtime, f in files_with_time:
                if total_size <= max_shared_mb * 1024 * 1024: break
                if f.exists():
                    total_size -= f.stat().st_size; f.unlink()
                    meta = shared_dir / f"{f.name}.meta.json"
                    if meta.exists(): meta.unlink()
                    stats["expired_files"] += 1

    import datetime
    stale_cutoff = time.time() - settings.get("stale_agent_hours", 24) * 3600
    registry = _read_agents(agents_file)
    for aid, info in registry["agents"].items():
        try:
            dt = datetime.datetime.fromisoformat(info.get("lastSeen", "").replace("Z", "+00:00"))
            if dt.timestamp() < stale_cutoff and info.get("status") != "stale":
                info["status"] = "stale"; stats["stale_agents"] += 1
        except Exception: pass
    _write_agents(agents_file, registry)
    return {"ok": True, "stats": stats}

# ─── Stats ───────────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(request: Request):
    agents_file, inbox_dir, shared_dir = _dirs(request)
    registry = _read_agents(agents_file)
    settings = _read_settings(request)
    total_messages = unread_messages = messages_today = 0
    messages_by_type = {}
    messages_by_agent = {}
    today_start = int(time.mktime(time.gmtime()[:3] + (0, 0, 0, 0, 0, 0))) * 1000
    if inbox_dir.exists():
        for agent_dir in inbox_dir.iterdir():
            if not agent_dir.is_dir(): continue
            agent_total = 0
            for f in agent_dir.glob("*.json"):
                total_messages += 1; agent_total += 1
                if not f.name.endswith(".read.json"): unread_messages += 1
                try:
                    msg = json.loads(f.read_text(encoding="utf-8"))
                    mtype = msg.get("type", "info")
                    messages_by_type[mtype] = messages_by_type.get(mtype, 0) + 1
                    if msg.get("timestamp", 0) >= today_start: messages_today += 1
                except Exception: pass
            messages_by_agent[agent_dir.name] = agent_total
    shared_count = shared_size = 0
    if shared_dir.exists():
        for f in shared_dir.iterdir():
            if not f.name.endswith(".meta.json"):
                shared_count += 1; shared_size += f.stat().st_size
    return {
        "agents": len(registry.get("agents", {})),
        "total_messages": total_messages, "unread_messages": unread_messages,
        "messages_today": messages_today, "messages_by_type": messages_by_type,
        "messages_by_agent": messages_by_agent, "shared_files": shared_count,
        "shared_size_bytes": shared_size, "shared_size_mb": round(shared_size / 1024 / 1024, 2),
        "settings": settings,
    }

# ─── Dashboard ───────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Serve the SPA dashboard. Data fetched client-side via API calls."""
    html_path = Path(__file__).parent.parent / "dashboard.html"
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )
