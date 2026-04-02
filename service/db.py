"""
SQLite database layer for aify-claude v2.
Single database file replaces all JSON file storage.
"""
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
    source TEXT NOT NULL DEFAULT 'direct',
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
CREATE INDEX IF NOT EXISTS idx_read_receipts_msg ON read_receipts(message_id);
"""

async def init_db(db_path: Path = None):
    global _db_path
    if db_path:
        _db_path = db_path
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(_db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(SCHEMA)
        await db.commit()

async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(_db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys=ON")
    return db
