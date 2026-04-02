# aify-claude v2: SQLite + WebSocket Rewrite

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace JSON file storage with SQLite, add WebSocket for real-time updates, add threading, message attachments, and export/import system (including one-time migration from v1 JSON to v2 SQLite).

**Architecture:** Single SQLite database replaces all JSON files (agents, messages, channels, shared artifacts, settings). WebSocket replaces dashboard polling. All existing API endpoints preserved with same signatures — MCP server and clients require zero changes. A migration script reads the v1 Docker volume data and populates the v2 database.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite (async SQLite), WebSocket (fastapi.websockets), existing Node.js MCP server (unchanged).

**Development safety:** The running v1 container uses a baked-in image copy of the code. Source files at `C:\Docker\aify-claude\` are NOT mounted (only `config/` is read-only mounted). All development happens on source files without affecting the running system. Deploy by rebuilding when ready.

---

## File Structure

### New files
| File | Responsibility |
|------|---------------|
| `service/db.py` | SQLite schema, connection pool, migrations |
| `service/models.py` | Pydantic models (extracted from api.py inline classes) |
| `service/ws.py` | WebSocket manager — broadcast, per-agent connections, presence |
| `service/routers/api_v2.py` | All API endpoints rewritten for SQLite |
| `service/export_v1.py` | Read v1 JSON files from Docker volume, export to JSON bundle |
| `service/import_v2.py` | Import JSON bundle into v2 SQLite database |
| `tests/test_db.py` | SQLite schema and query tests |
| `tests/test_api_v2.py` | API endpoint tests against SQLite |
| `tests/test_migration.py` | v1-to-v2 migration tests |
| `tests/test_ws.py` | WebSocket connection and broadcast tests |
| `tests/conftest.py` | Shared fixtures (test db, test client) |

### Modified files
| File | Changes |
|------|---------|
| `service/main.py` | Swap router from api to api_v2, add WebSocket route, init db on startup |
| `service/dashboard.html` | Replace polling with WebSocket, add channel send from dashboard, add search page, threading UI |
| `service/routers/api.py` | Keep as-is for reference, not imported |
| `requirements.txt` | Add aiosqlite, pytest, httpx (test client) |

### Unchanged files
| File | Why |
|------|-----|
| `mcp/stdio/server.js` | Talks to HTTP API — endpoints stay same, zero changes needed |
| `mcp/stdio/notify-check.js` | Queries inbox API — works unchanged |
| `docker-compose.yml` | Same volume mount, same port |
| `Dockerfile` | Same build, just picks up new Python files |

---

## Task 1: SQLite Schema and Connection

**Files:**
- Create: `service/db.py`
- Create: `service/requirements.txt` (update)
- Create: `tests/test_db.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Add aiosqlite to requirements**

```
# Add to service/requirements.txt:
aiosqlite>=0.20.0
pytest>=8.0
pytest-asyncio>=0.23
httpx>=0.27
```

- [ ] **Step 2: Write failing test for schema creation**

```python
# tests/test_db.py
import pytest
import aiosqlite
from service.db import init_db, get_db

@pytest.mark.asyncio
async def test_schema_creates_all_tables(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in await cursor.fetchall()]
    assert "agents" in tables
    assert "messages" in tables
    assert "read_receipts" in tables
    assert "channels" in tables
    assert "channel_members" in tables
    assert "shared_artifacts" in tables
    assert "settings" in tables
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd service && python -m pytest ../tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.db'`

- [ ] **Step 4: Implement db.py with schema**

```python
# service/db.py
import aiosqlite
from pathlib import Path

_db_path: Path = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    name TEXT NOT NULL,
    cwd TEXT DEFAULT '',
    model TEXT DEFAULT '',
    instructions TEXT DEFAULT '',
    status TEXT DEFAULT 'idle',
    registered_at TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    from_agent TEXT NOT NULL,
    to_agent TEXT,
    channel TEXT,
    source TEXT NOT NULL DEFAULT 'direct',  -- 'direct' or 'channel'
    type TEXT NOT NULL DEFAULT 'info',
    subject TEXT DEFAULT '',
    body TEXT DEFAULT '',
    priority TEXT DEFAULT 'normal',
    in_reply_to TEXT,
    timestamp INTEGER NOT NULL,
    FOREIGN KEY (in_reply_to) REFERENCES messages(id)
);

CREATE TABLE IF NOT EXISTS read_receipts (
    message_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    read_at TEXT NOT NULL,
    PRIMARY KEY (message_id, agent_id),
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS channels (
    name TEXT PRIMARY KEY,
    description TEXT DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_members (
    channel_name TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    joined_at TEXT NOT NULL,
    PRIMARY KEY (channel_name, agent_id),
    FOREIGN KEY (channel_name) REFERENCES channels(name) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shared_artifacts (
    name TEXT PRIMARY KEY,
    from_agent TEXT NOT NULL,
    description TEXT DEFAULT '',
    content TEXT,
    file_path TEXT,
    size INTEGER DEFAULT 0,
    is_binary INTEGER DEFAULT 0,
    shared_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_to ON messages(to_agent, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_messages_from ON messages(from_agent, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_messages_reply ON messages(in_reply_to);
CREATE INDEX IF NOT EXISTS idx_read_receipts_agent ON read_receipts(agent_id);
"""

async def init_db(db_path: Path = None):
    global _db_path
    if db_path:
        _db_path = db_path
    async with aiosqlite.connect(_db_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()

async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(_db_path)
    db.row_factory = aiosqlite.Row
    return db
```

- [ ] **Step 5: Create test conftest**

```python
# tests/conftest.py
import pytest
import asyncio
from pathlib import Path

@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / "test.db"
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd service && python -m pytest ../tests/test_db.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add service/db.py tests/test_db.py tests/conftest.py service/requirements.txt
git commit -m "feat: SQLite schema with agents, messages, read_receipts, channels, shared_artifacts"
```

---

## Task 2: Pydantic Models

**Files:**
- Create: `service/models.py`

- [ ] **Step 1: Extract models from api.py into models.py**

```python
# service/models.py
from typing import Optional
from pydantic import BaseModel

class AgentRegister(BaseModel):
    agentId: str
    role: str
    name: Optional[str] = None
    cwd: Optional[str] = None
    model: Optional[str] = None
    instructions: Optional[str] = None
    status: Optional[str] = None

class AgentStatusUpdate(BaseModel):
    status: str

class MessageSend(BaseModel):
    from_agent: str
    to: Optional[str] = None
    toRole: Optional[str] = None
    type: str = "info"
    subject: str
    body: str
    priority: str = "normal"
    inReplyTo: Optional[str] = None
    trigger: bool = False

class ClearRequest(BaseModel):
    target: str
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

class ChannelJoin(BaseModel):
    agentId: str

class SettingsUpdate(BaseModel):
    retention_days: Optional[int] = None
    max_messages_per_agent: Optional[int] = None
    max_shared_size_mb: Optional[int] = None
    stale_agent_hours: Optional[int] = None
    dashboard_refresh_seconds: Optional[int] = None
    rotation_enabled: Optional[bool] = None
```

- [ ] **Step 2: Commit**

```bash
git add service/models.py
git commit -m "refactor: extract Pydantic models to service/models.py"
```

---

## Task 3: Agent Endpoints (SQLite)

**Files:**
- Create: `service/routers/api_v2.py`
- Create: `tests/test_api_v2.py`

- [ ] **Step 1: Write failing test for agent registration**

```python
# tests/test_api_v2.py
import pytest
from httpx import AsyncClient, ASGITransport
from service.main import app
from service.db import init_db

@pytest.fixture(autouse=True)
async def setup_db(tmp_path):
    await init_db(tmp_path / "test.db")

@pytest.mark.asyncio
async def test_register_agent():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v2/agents", json={
            "agentId": "coder", "role": "developer"
        })
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["agentId"] == "coder"

@pytest.mark.asyncio
async def test_list_agents():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/v2/agents", json={"agentId": "a1", "role": "coder"})
        r = await client.get("/api/v2/agents")
        agents = r.json()["agents"]
        assert "a1" in agents
        assert agents["a1"]["role"] == "coder"

@pytest.mark.asyncio
async def test_agent_auto_idle():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/v2/agents", json={"agentId": "old", "role": "coder"})
        # Manually set lastSeen to 10 min ago
        from service.db import get_db
        db = await get_db()
        await db.execute("UPDATE agents SET last_seen = datetime('now', '-10 minutes') WHERE id = 'old'")
        await db.commit()
        await db.close()
        r = await client.get("/api/v2/agents")
        assert r.json()["agents"]["old"]["status"] == "idle"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_v2.py -v`
Expected: FAIL

- [ ] **Step 3: Implement agent endpoints in api_v2.py**

Implement: `POST /agents`, `GET /agents`, `PATCH /agents/{id}`, `DELETE /agents/{id}` using `service.db.get_db()`. Same response format as v1. Auto-idle logic in `GET /agents` using SQL: `CASE WHEN last_seen < datetime('now', '-5 minutes') AND status NOT IN ('blocked','completed') THEN 'idle' ELSE status END`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_api_v2.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add service/routers/api_v2.py tests/test_api_v2.py
git commit -m "feat: agent endpoints on SQLite with auto-idle"
```

---

## Task 4: Message Endpoints (SQLite)

**Files:**
- Modify: `service/routers/api_v2.py`
- Modify: `tests/test_api_v2.py`

- [ ] **Step 1: Write failing tests for send + inbox + read receipts**

Test: send a message, check inbox shows it unread, check again shows it read, verify read_receipts table has readAt.

Test: send with priority="urgent", verify it's stored and returned.

Test: send with inReplyTo, verify threading works.

Test: inbox returns newest first.

Test: inbox with peek=true does not mark as read.

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement message endpoints**

`POST /messages/send` — INSERT into messages table. For toRole, query agents table for matching role. Touch sender (UPDATE last_seen, status='active'). Return messageId + recipients.

`GET /messages/inbox/{agent_id}` — SELECT messages WHERE to_agent=id, LEFT JOIN read_receipts. Filter by read/unread using read_receipts presence. Support fromAgent, fromRole, type, limit params. If not peek, INSERT into read_receipts for each returned message.

`GET /messages/search` — SELECT with LIKE on subject, body, from_agent. Support scope (inbox/shared/all).

Key query for unread:
```sql
SELECT m.* FROM messages m
LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
WHERE m.to_agent = ? AND r.message_id IS NULL
ORDER BY m.timestamp DESC LIMIT ?
```

- [ ] **Step 4: Run tests, verify pass**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: message endpoints with read receipts and threading"
```

---

## Task 5: Channel Endpoints (SQLite)

**Files:**
- Modify: `service/routers/api_v2.py`
- Modify: `tests/test_api_v2.py`

- [ ] **Step 1: Write failing tests**

Test: create channel, join, send message, read messages.
Test: channel send delivers to each member's inbox (INSERT into messages with source='channel').
Test: channel read with pagination (limit/offset).
Test: leave channel.

- [ ] **Step 2: Run tests, verify fail**

- [ ] **Step 3: Implement channel endpoints**

`POST /channels` — INSERT into channels + channel_members.
`GET /channels` — SELECT with member count and message count subqueries.
`GET /channels/{name}` — SELECT messages WHERE channel=name, with limit/offset pagination.
`POST /channels/{name}/join` — INSERT into channel_members.
`POST /channels/{name}/send` — INSERT into messages (channel message) + INSERT per-member inbox copies with source='channel'.
`DELETE /channels/{name}` — CASCADE deletes members and messages.

- [ ] **Step 4: Run tests, verify pass**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: channel endpoints on SQLite with inbox delivery"
```

---

## Task 6: Shared Artifacts, Settings, Stats, Clear, Rotate

**Files:**
- Modify: `service/routers/api_v2.py`
- Modify: `tests/test_api_v2.py`

- [ ] **Step 1: Write failing tests**

Test: share text artifact, read it back, list files, delete.
Test: share binary file via upload.
Test: settings get/put round-trip.
Test: stats returns correct counts.
Test: clear with target='all' empties everything.
Test: rotate deletes messages older than retention_days.

- [ ] **Step 2: Implement all remaining endpoints**

Shared: Store text content in `content` column, binary files on disk with `file_path` column.
Settings: Key-value store in settings table, merge with defaults on read.
Stats: Single query with COUNT/SUM aggregations.
Clear: DELETE with optional WHERE timestamp < cutoff.
Rotate: DELETE expired messages, DELETE excess per-agent messages (keep newest N), DELETE oldest shared until under size limit, UPDATE stale agents.

- [ ] **Step 3: Run tests, verify pass**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: shared artifacts, settings, stats, clear, rotate on SQLite"
```

---

## Task 7: WebSocket Manager

**Files:**
- Create: `service/ws.py`
- Create: `tests/test_ws.py`

- [ ] **Step 1: Write failing test for WebSocket broadcast**

```python
# tests/test_ws.py
import pytest
from service.ws import ConnectionManager

@pytest.mark.asyncio
async def test_manager_tracks_connections():
    manager = ConnectionManager()
    assert manager.active_count() == 0

@pytest.mark.asyncio
async def test_manager_agent_presence():
    manager = ConnectionManager()
    # Simulate agent connect
    manager.register_agent("coder")
    assert "coder" in manager.online_agents()
    manager.unregister_agent("coder")
    assert "coder" not in manager.online_agents()
```

- [ ] **Step 2: Implement WebSocket manager**

```python
# service/ws.py
import json
import asyncio
from fastapi import WebSocket

class ConnectionManager:
    def __init__(self):
        self._connections: list[WebSocket] = []
        self._agents: dict[str, WebSocket] = {}  # agent_id -> ws

    async def connect(self, ws: WebSocket, agent_id: str = None):
        await ws.accept()
        self._connections.append(ws)
        if agent_id:
            self._agents[agent_id] = ws

    def disconnect(self, ws: WebSocket):
        self._connections.remove(ws)
        self._agents = {k: v for k, v in self._agents.items() if v != ws}

    def register_agent(self, agent_id: str, ws: WebSocket = None):
        if ws:
            self._agents[agent_id] = ws

    def unregister_agent(self, agent_id: str):
        self._agents.pop(agent_id, None)

    def online_agents(self) -> set:
        return set(self._agents.keys())

    def active_count(self) -> int:
        return len(self._connections)

    async def broadcast(self, event: str, data: dict):
        """Send to all dashboard connections."""
        msg = json.dumps({"event": event, "data": data})
        dead = []
        for ws in self._connections:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def notify_agent(self, agent_id: str, event: str, data: dict):
        """Send to a specific agent's WebSocket if connected."""
        ws = self._agents.get(agent_id)
        if ws:
            try:
                await ws.send_text(json.dumps({"event": event, "data": data}))
            except Exception:
                self.unregister_agent(agent_id)
```

- [ ] **Step 3: Run tests, verify pass**

- [ ] **Step 4: Commit**

```bash
git add service/ws.py tests/test_ws.py
git commit -m "feat: WebSocket connection manager with agent presence"
```

---

## Task 8: Wire WebSocket into Main App

**Files:**
- Modify: `service/main.py`
- Modify: `service/routers/api_v2.py`

- [ ] **Step 1: Add WebSocket route to main.py**

Add `/ws` endpoint that accepts WebSocket connections. Optional `?agent_id=` query param for agent presence tracking.

- [ ] **Step 2: Add broadcast calls to api_v2.py endpoints**

After every state change (message sent, agent registered, channel message, etc.), call `manager.broadcast("event_type", data)`. Events:
- `agent_registered`, `agent_removed`, `agent_status_changed`
- `message_sent`, `message_read`
- `channel_created`, `channel_message`
- `file_shared`, `file_deleted`

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: wire WebSocket into app with broadcast on state changes"
```

---

## Task 9: v1 Export Script

**Files:**
- Create: `service/export_v1.py`
- Create: `tests/test_migration.py`

- [ ] **Step 1: Write failing test for v1 export**

Create a mock v1 data directory with agents.json, inbox files, channel JSONs, shared artifacts. Run export. Verify output JSON bundle contains all data.

- [ ] **Step 2: Implement export_v1.py**

```python
# service/export_v1.py
"""
Export v1 JSON file data to a single JSON bundle for import into v2 SQLite.

Usage:
    python -m service.export_v1 /data /path/to/export.json

Reads:
    /data/agents.json
    /data/inbox/{agent_id}/*.json
    /data/channels/{name}.json
    /data/shared/* + *.meta.json
    /data/settings.json
"""
import json
import sys
from pathlib import Path

def export_v1(data_dir: Path) -> dict:
    bundle = {"version": "v1", "agents": {}, "messages": [], "channels": [], "shared": [], "settings": {}}

    # Agents
    agents_file = data_dir / "agents.json"
    if agents_file.exists():
        bundle["agents"] = json.loads(agents_file.read_text(encoding="utf-8")).get("agents", {})

    # Messages (all inboxes)
    inbox_dir = data_dir / "inbox"
    if inbox_dir.exists():
        for agent_dir in inbox_dir.iterdir():
            if not agent_dir.is_dir():
                continue
            agent_id = agent_dir.name
            for f in sorted(agent_dir.glob("*.json")):
                try:
                    msg = json.loads(f.read_text(encoding="utf-8"))
                    msg["_to"] = agent_id
                    msg["_read"] = f.name.endswith(".read.json")
                    bundle["messages"].append(msg)
                except Exception:
                    continue

    # Channels
    channels_dir = data_dir / "channels"
    if channels_dir.exists():
        for f in channels_dir.glob("*.json"):
            try:
                ch = json.loads(f.read_text(encoding="utf-8"))
                bundle["channels"].append(ch)
            except Exception:
                continue

    # Shared artifacts
    shared_dir = data_dir / "shared"
    if shared_dir.exists():
        for f in shared_dir.iterdir():
            if f.name.endswith(".meta.json"):
                continue
            meta_file = shared_dir / f"{f.name}.meta.json"
            meta = {}
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            try:
                content = f.read_text(encoding="utf-8")
                is_binary = False
            except UnicodeDecodeError:
                content = None
                is_binary = True
            bundle["shared"].append({
                **meta,
                "name": f.name,
                "content": content,
                "is_binary": is_binary,
                "size": f.stat().st_size,
            })

    # Settings
    settings_file = data_dir / "settings.json"
    if settings_file.exists():
        try:
            bundle["settings"] = json.loads(settings_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    return bundle

if __name__ == "__main__":
    data_dir = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("v1-export.json")
    bundle = export_v1(data_dir)
    out_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Exported: {len(bundle['agents'])} agents, {len(bundle['messages'])} messages, "
          f"{len(bundle['channels'])} channels, {len(bundle['shared'])} artifacts")
```

- [ ] **Step 3: Run tests, verify pass**

- [ ] **Step 4: Commit**

```bash
git add service/export_v1.py tests/test_migration.py
git commit -m "feat: v1 JSON export script"
```

---

## Task 10: v2 Import Script

**Files:**
- Create: `service/import_v2.py`
- Modify: `tests/test_migration.py`

- [ ] **Step 1: Write failing test for v2 import**

Export mock v1 data to bundle, import into fresh SQLite db. Verify all agents, messages (with read state), channels (with members), shared artifacts exist in the database.

- [ ] **Step 2: Implement import_v2.py**

```python
# service/import_v2.py
"""
Import v1 export bundle into v2 SQLite database.

Usage:
    python -m service.import_v2 /path/to/export.json /path/to/aify.db

Handles:
    - Agents with all fields
    - Messages with read state -> read_receipts
    - Channels with members
    - Shared artifacts (text content; binary files need manual copy)
    - Settings
"""
import json
import sys
import asyncio
from pathlib import Path
from service.db import init_db, get_db

async def import_v2(bundle: dict, db_path: Path):
    await init_db(db_path)
    db = await get_db()

    # Agents
    for agent_id, info in bundle.get("agents", {}).items():
        await db.execute(
            "INSERT OR REPLACE INTO agents (id, role, name, cwd, model, instructions, status, registered_at, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
            (agent_id, info.get("role",""), info.get("name", agent_id), info.get("cwd",""),
             info.get("model",""), info.get("instructions",""), info.get("status","idle"),
             info.get("registeredAt",""), info.get("lastSeen",""))
        )

    # Messages + read receipts
    seen_ids = set()
    for msg in bundle.get("messages", []):
        msg_id = msg.get("id", "")
        if msg_id in seen_ids:
            # Duplicate (channel message delivered to multiple inboxes) — just add read receipt
            if msg.get("_read"):
                await db.execute(
                    "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                    (msg_id, msg.get("_to",""), msg.get("readAt", ""))
                )
            continue
        seen_ids.add(msg_id)

        source = msg.get("source", "direct")
        channel = msg.get("channel", "")
        await db.execute(
            "INSERT OR IGNORE INTO messages (id, from_agent, to_agent, channel, source, type, subject, body, priority, in_reply_to, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (msg_id, msg.get("from",""), msg.get("_to",""), channel, source,
             msg.get("type","info"), msg.get("subject",""), msg.get("body",""),
             msg.get("priority","normal"), msg.get("inReplyTo"), msg.get("timestamp",0))
        )
        if msg.get("_read"):
            await db.execute(
                "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                (msg_id, msg.get("_to",""), msg.get("readAt", ""))
            )

    # Channels
    for ch in bundle.get("channels", []):
        await db.execute(
            "INSERT OR IGNORE INTO channels (name, description, created_by, created_at) VALUES (?,?,?,?)",
            (ch["name"], ch.get("description",""), ch.get("createdBy",""), ch.get("createdAt",""))
        )
        for member in ch.get("members", []):
            await db.execute(
                "INSERT OR IGNORE INTO channel_members (channel_name, agent_id, joined_at) VALUES (?,?,?)",
                (ch["name"], member, ch.get("createdAt",""))
            )
        # Import channel messages that weren't in any inbox
        for msg in ch.get("messages", []):
            if msg.get("from") == "_system":
                continue
            msg_id = msg.get("id", "")
            if msg_id not in seen_ids:
                seen_ids.add(msg_id)
                await db.execute(
                    "INSERT OR IGNORE INTO messages (id, from_agent, channel, source, type, subject, body, priority, timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
                    (msg_id, msg.get("from",""), ch["name"], "channel",
                     msg.get("type","info"), f"#{ch['name']}", msg.get("body",""),
                     msg.get("priority","normal"), msg.get("timestamp",0))
                )

    # Shared artifacts
    for art in bundle.get("shared", []):
        if art.get("is_binary"):
            continue  # Binary files need manual copy
        await db.execute(
            "INSERT OR IGNORE INTO shared_artifacts (name, from_agent, description, content, size, is_binary, shared_at) VALUES (?,?,?,?,?,?,?)",
            (art["name"], art.get("from",""), art.get("description",""),
             art.get("content",""), art.get("size",0), 0, art.get("sharedAt",""))
        )

    # Settings
    for key, value in bundle.get("settings", {}).items():
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
            (key, json.dumps(value))
        )

    await db.commit()
    await db.close()

if __name__ == "__main__":
    bundle_path = Path(sys.argv[1])
    db_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("aify.db")
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    asyncio.run(import_v2(bundle, db_path))
    print(f"Imported into {db_path}")
```

- [ ] **Step 3: Write round-trip test**

Export mock v1 -> import to v2 SQLite -> verify all data present with correct read states.

- [ ] **Step 4: Run tests, verify pass**

- [ ] **Step 5: Commit**

```bash
git add service/import_v2.py tests/test_migration.py
git commit -m "feat: v2 SQLite import with read receipt migration"
```

---

## Task 11: Dashboard WebSocket + Channel Send + Search

**Files:**
- Modify: `service/dashboard.html`

- [ ] **Step 1: Replace polling with WebSocket**

Connect to `ws://host/ws` on page load. Listen for events and update UI reactively instead of setInterval polling. Reconnect on disconnect with exponential backoff.

```javascript
let ws;
function connectWS() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (e) => {
    const {event, data} = JSON.parse(e.data);
    if (event === 'refresh') refreshDashboard();
    // Granular updates for specific events
  };
  ws.onclose = () => setTimeout(connectWS, 3000);
}
connectWS();
```

- [ ] **Step 2: Add channel send form to Channels page**

Add a send form per channel (input + send button) so Steven can post to channels from the dashboard.

- [ ] **Step 3: Add Search page to sidebar**

New sidebar link "Search". Input field + results area. Calls `/api/v2/messages/search?query=...` and shows matching messages.

- [ ] **Step 4: Add thread view**

When clicking a message with inReplyTo or replies, show the thread inline — all messages linked by inReplyTo chain.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: dashboard WebSocket, channel send, search page, thread view"
```

---

## Task 12: Wire Main App to v2

**Files:**
- Modify: `service/main.py`

- [ ] **Step 1: Swap router**

Replace `from service.routers.api import router` with `from service.routers.api_v2 import router`. Init database on startup with `await init_db(Path(config.data_dir) / "aify.db")`. Add WebSocket route. Keep v1 api.py in the codebase for reference but don't import it.

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: wire v2 SQLite router into main app"
```

---

## Task 13: Migration Workflow (v1 -> v2)

**Files:**
- Create: `scripts/migrate-v1-to-v2.sh`

- [ ] **Step 1: Write migration script**

```bash
#!/bin/bash
# Migrate aify-claude v1 (JSON files) to v2 (SQLite)
# Run AFTER stopping v1 container, BEFORE starting v2.
set -e

echo "=== aify-claude v1 -> v2 migration ==="

# 1. Export v1 data from Docker volume
echo "Step 1: Exporting v1 data..."
docker run --rm -v service-data:/data -v "$(pwd):/out" python:3.12-slim \
  python -c "
import json, sys; sys.path.insert(0, '/out')
from service.export_v1 import export_v1
from pathlib import Path
bundle = export_v1(Path('/data'))
Path('/out/v1-export.json').write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding='utf-8')
print(f'Exported: {len(bundle[\"agents\"])} agents, {len(bundle[\"messages\"])} messages')
"

# 2. Import into v2 SQLite
echo "Step 2: Importing into SQLite..."
python -m service.import_v2 v1-export.json data/aify.db

# 3. Rebuild and start v2
echo "Step 3: Rebuilding container..."
docker compose up -d --build

echo "=== Migration complete ==="
echo "Verify: curl http://localhost:8800/health"
echo "Dashboard: http://localhost:8800"
```

- [ ] **Step 2: Test migration end-to-end**

Run against the actual v1 Docker volume data. Verify all agents, messages, channels appear in v2 dashboard.

- [ ] **Step 3: Commit**

```bash
git add scripts/migrate-v1-to-v2.sh
git commit -m "feat: v1 to v2 migration script"
```

---

## Task 14: Final Integration Test

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short
```

- [ ] **Step 2: Build Docker image**

```bash
docker compose build
```

- [ ] **Step 3: Test with fresh database**

```bash
docker compose up -d
curl http://localhost:8800/health
# Register an agent, send a message, check inbox, create channel
```

- [ ] **Step 4: Test migration path**

```bash
bash scripts/migrate-v1-to-v2.sh
# Verify all v1 data appears in v2 dashboard
```

- [ ] **Step 5: Tag release**

```bash
git tag v2.0.0
git commit -m "release: aify-claude v2.0.0 — SQLite + WebSocket"
```
