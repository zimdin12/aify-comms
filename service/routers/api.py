"""
Claude Code MCP — Message Bus API

HTTP endpoints for inter-agent communication. Replaces the filesystem-based
.messages/ directory, enabling cross-machine agent communication.

Agents (Claude Code instances) register, send messages, share artifacts,
and view a dashboard — all over HTTP.
"""

import json
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

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

# ─── Models ──────────────────────────────────────────────────────────────────

class AgentRegister(BaseModel):
    agentId: str
    role: str
    name: Optional[str] = None

class MessageSend(BaseModel):
    from_agent: str
    to: Optional[str] = None
    toRole: Optional[str] = None
    type: str = "info"
    subject: str
    body: str
    inReplyTo: Optional[str] = None

class ShareText(BaseModel):
    from_agent: str
    name: str
    content: str
    description: Optional[str] = None

class ClearRequest(BaseModel):
    target: str  # inbox, shared, agents, all
    agentId: Optional[str] = None
    olderThanHours: Optional[float] = None

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _read_agents(agents_file: Path) -> dict:
    try:
        return json.loads(agents_file.read_text())
    except Exception:
        return {"agents": {}}

def _write_agents(agents_file: Path, data: dict):
    agents_file.write_text(json.dumps(data, indent=2))

def _read_inbox(inbox_dir: Path, agent_id: str, filter_: str = "unread") -> list:
    d = inbox_dir / agent_id
    d.mkdir(parents=True, exist_ok=True)
    messages = []
    for f in sorted(d.glob("*.json")):
        try:
            msg = json.loads(f.read_text())
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
    for m in messages:
        if m.get("_read"):
            continue
        old = d / m["_file"]
        new = d / m["_file"].replace(".json", ".read.json")
        try:
            old.rename(new)
        except Exception:
            pass

def _deliver(inbox_dir: Path, to_id: str, message: dict):
    d = inbox_dir / to_id
    d.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    uid = uuid.uuid4().hex[:8]
    (d / f"{ts}-{uid}.json").write_text(json.dumps({**message, "timestamp": ts}))

def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

# ─── Root ────────────────────────────────────────────────────────────────────

@router.get("/")
async def root():
    return {
        "service": "claude-code-mcp",
        "version": "2.0.0",
        "endpoints": {
            "agents": "/api/v1/agents",
            "messages": "/api/v1/messages",
            "shared": "/api/v1/shared",
            "dashboard": "/api/v1/dashboard",
            "clear": "/api/v1/clear",
        },
    }

# ─── Agents ──────────────────────────────────────────────────────────────────

@router.get("/agents")
async def list_agents(request: Request):
    agents_file, inbox_dir, _ = _dirs(request)
    registry = _read_agents(agents_file)
    result = {}
    for aid, info in registry["agents"].items():
        unread = len(_read_inbox(inbox_dir, aid, "unread"))
        result[aid] = {**info, "unread": unread}
    return {"agents": result}

@router.post("/agents")
async def register_agent(req: AgentRegister, request: Request):
    agents_file, inbox_dir, _ = _dirs(request)
    registry = _read_agents(agents_file)
    registry["agents"][req.agentId] = {
        "role": req.role,
        "name": req.name or req.agentId,
        "registeredAt": _now(),
        "lastSeen": _now(),
    }
    _write_agents(agents_file, registry)
    (inbox_dir / req.agentId).mkdir(parents=True, exist_ok=True)
    return {"ok": True, "agentId": req.agentId, "role": req.role}

@router.delete("/agents/{agent_id}")
async def unregister_agent(agent_id: str, request: Request):
    agents_file, _, _ = _dirs(request)
    registry = _read_agents(agents_file)
    removed = agent_id in registry["agents"]
    registry["agents"].pop(agent_id, None)
    _write_agents(agents_file, registry)
    return {"ok": removed}

# ─── Messages ────────────────────────────────────────────────────────────────

@router.post("/messages/send")
async def send_message(req: MessageSend, request: Request):
    agents_file, inbox_dir, _ = _dirs(request)

    if not req.to and not req.toRole:
        raise HTTPException(400, "Need 'to' or 'toRole'")

    registry = _read_agents(agents_file)
    if registry["agents"].get(req.from_agent):
        registry["agents"][req.from_agent]["lastSeen"] = _now()
        _write_agents(agents_file, registry)

    msg_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    message = {
        "id": msg_id,
        "from": req.from_agent,
        "type": req.type,
        "subject": req.subject,
        "body": req.body,
    }
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
    agent_id: str,
    request: Request,
    filter: str = Query("unread", pattern="^(unread|read|all)$"),
    fromAgent: Optional[str] = None,
    fromRole: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = Query(20, ge=1, le=200),
):
    agents_file, inbox_dir, _ = _dirs(request)
    registry = _read_agents(agents_file)
    if registry["agents"].get(agent_id):
        registry["agents"][agent_id]["lastSeen"] = _now()
        _write_agents(agents_file, registry)

    messages = _read_inbox(inbox_dir, agent_id, filter)

    if fromAgent:
        messages = [m for m in messages if m.get("from") == fromAgent]
    if fromRole:
        messages = [m for m in messages if registry["agents"].get(m.get("from"), {}).get("role") == fromRole]
    if type:
        messages = [m for m in messages if m.get("type") == type]

    total = len(messages)
    shown = messages[:limit]
    _mark_read(inbox_dir, agent_id, shown)

    clean = []
    for m in shown:
        c = {k: v for k, v in m.items() if not k.startswith("_")}
        c["read"] = m.get("_read", False)
        clean.append(c)

    return {"total": total, "showing": len(clean), "messages": clean}

@router.get("/messages/search")
async def search_messages(
    request: Request,
    query: str = "",
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
                results.append({
                    "type": "message", "read": m.get("_read", False),
                    "id": m.get("id"), "from": m.get("from"),
                    "subject": m.get("subject"),
                    "preview": (m.get("body") or "")[:150],
                })

    if scope in ("shared", "all"):
        for f in shared_dir.iterdir():
            if f.name.endswith(".meta.json"):
                continue
            meta = {}
            mf = shared_dir / f"{f.name}.meta.json"
            if mf.exists():
                try:
                    meta = json.loads(mf.read_text())
                except Exception:
                    pass
            haystack = f"{f.name} {meta.get('description', '')} {meta.get('from', '')}".lower()
            content_match = False
            if f.stat().st_size < 1_000_000:
                try:
                    if q in f.read_text().lower():
                        content_match = True
                except Exception:
                    pass
            if q in haystack or content_match:
                results.append({
                    "type": "artifact", "name": f.name,
                    "from": meta.get("from", "?"),
                    "description": meta.get("description", ""),
                    "size": f.stat().st_size,
                })

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
            try:
                meta = json.loads(mf.read_text())
            except Exception:
                pass
        files.append({
            "name": f.name, "size": f.stat().st_size,
            "from": meta.get("from", "?"),
            "description": meta.get("description", ""),
            "sharedAt": meta.get("sharedAt", ""),
        })
    return {"files": files}

@router.post("/shared")
async def share_artifact(
    request: Request,
    from_agent: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    content: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    _, _, shared_dir = _dirs(request)
    dest = shared_dir / name
    if file:
        data = await file.read()
        dest.write_bytes(data)
        size = len(data)
    elif content:
        dest.write_text(content)
        size = len(content)
    else:
        raise HTTPException(400, "Need 'content' or 'file'")

    meta = {
        "from": from_agent, "name": name, "description": description,
        "sharedAt": _now(), "size": size,
    }
    (shared_dir / f"{name}.meta.json").write_text(json.dumps(meta, indent=2))
    return {"ok": True, "name": name, "size": size}

@router.get("/shared/{name}")
async def get_shared(name: str, request: Request):
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
        try:
            meta = json.loads(mf.read_text())
        except Exception:
            pass
    return {"content": artifact.read_text(), "meta": meta}

@router.delete("/shared/{name}")
async def delete_shared(name: str, request: Request):
    _, _, shared_dir = _dirs(request)
    deleted = False
    for f in [shared_dir / name, shared_dir / f"{name}.meta.json"]:
        if f.exists():
            f.unlink()
            deleted = True
    return {"ok": deleted}

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
            if not p.exists():
                continue
            for f in p.glob("*.json"):
                if cutoff:
                    try:
                        msg = json.loads(f.read_text())
                        if msg.get("timestamp", 0) > cutoff:
                            continue
                    except Exception:
                        pass
                f.unlink()
                cleared["messages"] += 1

    if req.target in ("shared", "all"):
        for f in shared_dir.iterdir():
            if cutoff and f.stat().st_mtime * 1000 > cutoff:
                continue
            f.unlink()
            cleared["files"] += 1

    if req.target in ("agents", "all"):
        registry = _read_agents(agents_file)
        cleared["agents"] = len(registry["agents"])
        _write_agents(agents_file, {"agents": {}})

    return {"ok": True, "cleared": cleared}

# ─── Dashboard ───────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    agents_file, inbox_dir, shared_dir = _dirs(request)
    registry = _read_agents(agents_file)
    agents = list(registry["agents"].items())

    all_msgs = []
    if inbox_dir.exists():
        for d in inbox_dir.iterdir():
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.json")):
                try:
                    msg = json.loads(f.read_text())
                    msg["_to"] = d.name
                    msg["_read"] = f.name.endswith(".read.json")
                    all_msgs.append(msg)
                except Exception:
                    continue
    all_msgs.sort(key=lambda m: m.get("timestamp", 0), reverse=True)

    shared = []
    if shared_dir.exists():
        for f in shared_dir.iterdir():
            if f.name.endswith(".meta.json"):
                continue
            meta = {}
            mf = shared_dir / f"{f.name}.meta.json"
            if mf.exists():
                try:
                    meta = json.loads(mf.read_text())
                except Exception:
                    pass
            shared.append({"name": f.name, **meta, "size": f.stat().st_size})

    e = lambda s: str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    unread = sum(1 for m in all_msgs if not m.get("_read"))
    now = _now()

    agent_rows = "".join(
        f"<tr><td><b>{e(a)}</b></td><td><span class='rb'>{e(i.get('role'))}</span></td>"
        f"<td>{e(i.get('name'))}</td><td>{e(i.get('status','idle'))}</td>"
        f"<td>{sum(1 for m in all_msgs if m['_to']==a and not m['_read'])}/{sum(1 for m in all_msgs if m['_to']==a)}</td>"
        f"<td class='t'>{e(i.get('lastSeen','?'))}</td></tr>"
        for a, i in agents
    )
    msg_rows = "".join(
        f"<tr class='{'mr' if m.get('_read') else 'mu'}'>"
        f"<td class='t'>{time.strftime('%Y-%m-%d %H:%M', time.gmtime((m.get('timestamp',0))/1000))}</td>"
        f"<td>{e(m.get('from'))}</td><td>{e(m.get('_to'))}</td>"
        f"<td><span class='tb t-{e(m.get('type','info'))}'>{e(m.get('type'))}</span></td>"
        f"<td><b>{e(m.get('subject'))}</b></td>"
        f"<td class='mb'>{e((m.get('body',''))[:200])}</td></tr>"
        for m in all_msgs[:100]
    )
    file_rows = "".join(
        f"<tr><td><b>{e(f['name'])}</b></td><td>{e(f.get('from','?'))}</td>"
        f"<td>{f.get('size',0)}B</td><td>{e(f.get('description',''))}</td>"
        f"<td class='t'>{e(f.get('sharedAt',''))}</td></tr>"
        for f in shared
    )

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claude Code MCP</title><meta http-equiv="refresh" content="15">
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:-apple-system,sans-serif;background:#0d1117;color:#c9d1d9;padding:20px}}
h1{{color:#58a6ff;font-size:1.5em}}h2{{color:#58a6ff;margin:20px 0 8px;font-size:1.1em;border-bottom:1px solid #21262d;padding-bottom:6px}}
.sub{{color:#8b949e;margin-bottom:20px;font-size:.85em}}.stats{{display:flex;gap:12px;margin-bottom:18px;flex-wrap:wrap}}
.sc{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 18px;min-width:120px}}
.sc .n{{font-size:1.8em;font-weight:bold;color:#58a6ff}}.sc .l{{color:#8b949e;font-size:.8em}}
table{{width:100%;border-collapse:collapse;background:#161b22;border-radius:8px;overflow:hidden;margin-bottom:16px}}
th{{background:#21262d;color:#8b949e;text-align:left;padding:8px 10px;font-size:.8em;text-transform:uppercase}}
td{{padding:8px 10px;border-top:1px solid #21262d;font-size:.85em;vertical-align:top}}tr:hover{{background:#1c2128}}
.t{{color:#8b949e;font-size:.8em;white-space:nowrap}}.mb{{color:#8b949e;max-width:350px;word-break:break-word}}
.mu{{background:#12201f}}.mu td:first-child::before{{content:"\\25CF ";color:#3fb950}}
.rb{{background:#1f6feb33;color:#58a6ff;padding:2px 7px;border-radius:10px;font-size:.8em}}
.tb{{padding:2px 7px;border-radius:10px;font-size:.75em;font-weight:500}}
.t-request{{background:#da363333;color:#f85149}}.t-response{{background:#3fb95033;color:#3fb950}}
.t-info{{background:#1f6feb33;color:#58a6ff}}.t-error{{background:#da363366;color:#f85149}}
.t-review{{background:#a371f733;color:#a371f7}}.t-approval{{background:#3fb95033;color:#3fb950}}
.em{{color:#484f58;font-style:italic;padding:15px;text-align:center}}
.fb{{margin:6px 0;display:flex;gap:6px}}.fb input,.fb select{{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:5px 8px;border-radius:5px;font-size:.82em}}
.rn{{color:#484f58;font-size:.75em;margin-top:25px;text-align:center}}</style></head><body>
<h1>Claude Code MCP Dashboard</h1><p class="sub">Auto-refreshes every 15s &middot; {now}</p>
<div class="stats"><div class="sc"><div class="n">{len(agents)}</div><div class="l">Agents</div></div>
<div class="sc"><div class="n">{unread}</div><div class="l">Unread</div></div>
<div class="sc"><div class="n">{len(all_msgs)}</div><div class="l">Messages</div></div>
<div class="sc"><div class="n">{len(shared)}</div><div class="l">Files</div></div></div>
<h2>Agents</h2>
{"<table><thead><tr><th>ID</th><th>Role</th><th>Name</th><th>Status</th><th>Msgs</th><th>Last Seen</th></tr></thead><tbody>"+agent_rows+"</tbody></table>" if agents else '<p class="em">No agents.</p>'}
<h2>Messages</h2><div class="fb"><input id="mf" placeholder="Filter..." oninput="F()"><select id="tf" onchange="F()"><option value="">All</option><option>request</option><option>response</option><option>info</option><option>error</option><option>review</option></select></div>
{"<table id='mt'><thead><tr><th>Time</th><th>From</th><th>To</th><th>Type</th><th>Subject</th><th>Body</th></tr></thead><tbody>"+msg_rows+"</tbody></table>" if all_msgs else '<p class="em">No messages.</p>'}
<h2>Shared Files</h2>
{"<table><thead><tr><th>Name</th><th>From</th><th>Size</th><th>Description</th><th>Shared</th></tr></thead><tbody>"+file_rows+"</tbody></table>" if shared else '<p class="em">No files.</p>'}
<p class="rn">Snapshot refreshes every 15s.</p>
<script>function F(){{const t=document.getElementById("mf").value.toLowerCase(),y=document.getElementById("tf").value;document.querySelectorAll("#mt tbody tr").forEach(r=>{{const c=r.textContent.toLowerCase(),b=r.querySelector(".tb");r.style.display=(!t||c.includes(t))&&(!y||b?.textContent===y)?"":"none"}})}}</script>
</body></html>"""
