"""
SQLite database layer for aify-comms v2.
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
    description TEXT DEFAULT '',
    instructions TEXT DEFAULT '',
    status TEXT DEFAULT 'idle',
    status_note TEXT DEFAULT '',
    runtime TEXT DEFAULT 'generic',
    machine_id TEXT DEFAULT '',
    launch_mode TEXT DEFAULT 'detached',
    session_mode TEXT DEFAULT 'resident',
    session_handle TEXT DEFAULT '',
    managed_by TEXT DEFAULT '',
    capabilities TEXT DEFAULT '[]',
    runtime_config TEXT DEFAULT '{}',
    runtime_state TEXT DEFAULT '{}',
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
    dispatch_requested INTEGER DEFAULT 0,
    in_reply_to TEXT,
    timestamp INTEGER NOT NULL,
    FOREIGN KEY (in_reply_to) REFERENCES messages(id) ON DELETE SET NULL
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

CREATE TABLE IF NOT EXISTS dispatch_runs (
    id TEXT PRIMARY KEY,
    message_id TEXT,
    from_agent TEXT NOT NULL,
    target_agent TEXT NOT NULL,
    dispatch_mode TEXT NOT NULL DEFAULT 'start_if_possible',
    execution_mode TEXT NOT NULL DEFAULT 'managed',
    requested_runtime TEXT DEFAULT '',
    runtime TEXT DEFAULT '',
    message_type TEXT NOT NULL DEFAULT 'request',
    subject TEXT DEFAULT '',
    body TEXT DEFAULT '',
    priority TEXT DEFAULT 'normal',
    in_reply_to TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    claim_machine_id TEXT DEFAULT '',
    claim_bridge_id TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    error_text TEXT DEFAULT '',
    result_message_id TEXT DEFAULT '',
    require_reply INTEGER NOT NULL DEFAULT 0,
    external_thread_id TEXT DEFAULT '',
    external_turn_id TEXT DEFAULT '',
    requested_at TEXT NOT NULL,
    claimed_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE SET NULL,
    FOREIGN KEY (in_reply_to) REFERENCES messages(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS dispatch_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    body TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES dispatch_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS dispatch_controls (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    from_agent TEXT DEFAULT '',
    source_message_id TEXT DEFAULT '',
    action TEXT NOT NULL,
    body TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    response_text TEXT DEFAULT '',
    claim_machine_id TEXT DEFAULT '',
    requested_at TEXT NOT NULL,
    claimed_at TEXT,
    handled_at TEXT,
    FOREIGN KEY (run_id) REFERENCES dispatch_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_to ON messages(to_agent, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_messages_from ON messages(from_agent, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_messages_reply ON messages(in_reply_to);
CREATE INDEX IF NOT EXISTS idx_read_receipts_agent ON read_receipts(agent_id);
CREATE INDEX IF NOT EXISTS idx_read_receipts_msg ON read_receipts(message_id);
CREATE INDEX IF NOT EXISTS idx_dispatch_runs_target_status ON dispatch_runs(target_agent, status, requested_at);
CREATE INDEX IF NOT EXISTS idx_dispatch_runs_from ON dispatch_runs(from_agent, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_dispatch_events_run ON dispatch_events(run_id, id);
CREATE INDEX IF NOT EXISTS idx_dispatch_controls_run_status ON dispatch_controls(run_id, status, requested_at);

CREATE TABLE IF NOT EXISTS bridge_instances (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    machine_id TEXT DEFAULT '',
    runtime TEXT DEFAULT 'generic',
    session_mode TEXT DEFAULT 'resident',
    registered_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    superseded_by TEXT DEFAULT '',
    superseded_at TEXT,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_bridge_instances_agent_machine ON bridge_instances(agent_id, machine_id, last_seen DESC);

CREATE TABLE IF NOT EXISTS agent_tombstones (
    agent_id TEXT PRIMARY KEY,
    removed_at TEXT NOT NULL,
    removed_by TEXT DEFAULT '',
    bridge_id TEXT DEFAULT '',
    reason TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS environments (
    id TEXT PRIMARY KEY,
    label TEXT DEFAULT '',
    machine_id TEXT DEFAULT '',
    os TEXT DEFAULT '',
    kind TEXT DEFAULT '',
    bridge_id TEXT DEFAULT '',
    bridge_version TEXT DEFAULT '',
    cwd_roots TEXT DEFAULT '[]',
    runtimes TEXT DEFAULT '[]',
    status TEXT DEFAULT 'online',
    metadata TEXT DEFAULT '{}',
    registered_at TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_environments_status_seen ON environments(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS environment_controls (
    id TEXT PRIMARY KEY,
    environment_id TEXT NOT NULL,
    bridge_id TEXT DEFAULT '',
    machine_id TEXT DEFAULT '',
    action TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    requested_by TEXT DEFAULT '',
    requested_at TEXT NOT NULL,
    claimed_at TEXT,
    handled_at TEXT,
    error TEXT DEFAULT '',
    FOREIGN KEY (environment_id) REFERENCES environments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_environment_controls_env_status ON environment_controls(environment_id, status, requested_at);

CREATE TABLE IF NOT EXISTS spawn_specs (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    runtime TEXT NOT NULL,
    workspace TEXT DEFAULT '',
    model TEXT DEFAULT '',
    profile TEXT DEFAULT '',
    mode TEXT DEFAULT 'managed-warm',
    system_prompt TEXT DEFAULT '',
    standing_instructions TEXT DEFAULT '',
    env_vars TEXT DEFAULT '{}',
    channel_ids TEXT DEFAULT '[]',
    budget_policy TEXT DEFAULT '{}',
    context_policy TEXT DEFAULT '{}',
    restart_policy TEXT DEFAULT '{}',
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (environment_id) REFERENCES environments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS spawn_requests (
    id TEXT PRIMARY KEY,
    spawn_spec_id TEXT NOT NULL,
    created_by TEXT DEFAULT '',
    environment_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    role TEXT DEFAULT 'coder',
    name TEXT DEFAULT '',
    runtime TEXT NOT NULL,
    workspace TEXT DEFAULT '',
    workspace_root TEXT DEFAULT '',
    initial_message TEXT DEFAULT '',
    priority TEXT DEFAULT 'normal',
    subject TEXT DEFAULT '',
    mode TEXT DEFAULT 'managed-warm',
    resume_policy TEXT DEFAULT 'native_first',
    status TEXT DEFAULT 'queued',
    claimed_by_bridge_id TEXT DEFAULT '',
    claim_machine_id TEXT DEFAULT '',
    process_id TEXT DEFAULT '',
    session_handle TEXT DEFAULT '',
    session_id TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    claimed_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY (spawn_spec_id) REFERENCES spawn_specs(id) ON DELETE CASCADE,
    FOREIGN KEY (environment_id) REFERENCES environments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_spawn_requests_env_status ON spawn_requests(environment_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_spawn_requests_agent_created ON spawn_requests(agent_id, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    runtime TEXT NOT NULL,
    workspace TEXT DEFAULT '',
    mode TEXT DEFAULT 'managed-warm',
    process_id TEXT DEFAULT '',
    session_handle TEXT DEFAULT '',
    app_server_url TEXT DEFAULT '',
    spawn_spec_id TEXT DEFAULT '',
    spawn_request_id TEXT DEFAULT '',
    capabilities TEXT DEFAULT '{}',
    telemetry TEXT DEFAULT '{}',
    status TEXT DEFAULT 'starting',
    started_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    ended_at TEXT,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE,
    FOREIGN KEY (environment_id) REFERENCES environments(id) ON DELETE CASCADE,
    FOREIGN KEY (spawn_spec_id) REFERENCES spawn_specs(id) ON DELETE SET NULL,
    FOREIGN KEY (spawn_request_id) REFERENCES spawn_requests(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_sessions_agent_seen ON agent_sessions(agent_id, last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_env_status ON agent_sessions(environment_id, status, last_seen DESC);
"""

AGENT_MIGRATIONS = {
    "runtime": "ALTER TABLE agents ADD COLUMN runtime TEXT DEFAULT 'generic'",
    "machine_id": "ALTER TABLE agents ADD COLUMN machine_id TEXT DEFAULT ''",
    "launch_mode": "ALTER TABLE agents ADD COLUMN launch_mode TEXT DEFAULT 'detached'",
    "session_mode": "ALTER TABLE agents ADD COLUMN session_mode TEXT DEFAULT 'resident'",
    "session_handle": "ALTER TABLE agents ADD COLUMN session_handle TEXT DEFAULT ''",
    "managed_by": "ALTER TABLE agents ADD COLUMN managed_by TEXT DEFAULT ''",
    "capabilities": "ALTER TABLE agents ADD COLUMN capabilities TEXT DEFAULT '[]'",
    "runtime_config": "ALTER TABLE agents ADD COLUMN runtime_config TEXT DEFAULT '{}'",
    "runtime_state": "ALTER TABLE agents ADD COLUMN runtime_state TEXT DEFAULT '{}'",
    "description": "ALTER TABLE agents ADD COLUMN description TEXT DEFAULT ''",
    "status_note": "ALTER TABLE agents ADD COLUMN status_note TEXT DEFAULT ''",
}

DISPATCH_RUN_MIGRATIONS = {
    "execution_mode": "ALTER TABLE dispatch_runs ADD COLUMN execution_mode TEXT DEFAULT 'managed'",
    "claim_bridge_id": "ALTER TABLE dispatch_runs ADD COLUMN claim_bridge_id TEXT DEFAULT ''",
    "require_reply": "ALTER TABLE dispatch_runs ADD COLUMN require_reply INTEGER NOT NULL DEFAULT 0",
}

MESSAGE_MIGRATIONS = {
    "dispatch_requested": "ALTER TABLE messages ADD COLUMN dispatch_requested INTEGER DEFAULT 0",
}

DISPATCH_CONTROL_MIGRATIONS = {
    "source_message_id": "ALTER TABLE dispatch_controls ADD COLUMN source_message_id TEXT DEFAULT ''",
}

ENVIRONMENT_MIGRATIONS = {
    "bridge_version": "ALTER TABLE environments ADD COLUMN bridge_version TEXT DEFAULT ''",
}


async def _migrate_agents_table(db: aiosqlite.Connection):
    cursor = await db.execute("PRAGMA table_info(agents)")
    existing = {row[1] for row in await cursor.fetchall()}
    for column, statement in AGENT_MIGRATIONS.items():
        if column not in existing:
            await db.execute(statement)


async def _migrate_dispatch_runs_table(db: aiosqlite.Connection):
    cursor = await db.execute("PRAGMA table_info(dispatch_runs)")
    existing = {row[1] for row in await cursor.fetchall()}
    for column, statement in DISPATCH_RUN_MIGRATIONS.items():
        if column not in existing:
            await db.execute(statement)


async def _migrate_messages_table(db: aiosqlite.Connection):
    cursor = await db.execute("PRAGMA table_info(messages)")
    existing = {row[1] for row in await cursor.fetchall()}
    for column, statement in MESSAGE_MIGRATIONS.items():
        if column not in existing:
            await db.execute(statement)


async def _migrate_dispatch_controls_table(db: aiosqlite.Connection):
    cursor = await db.execute("PRAGMA table_info(dispatch_controls)")
    existing = {row[1] for row in await cursor.fetchall()}
    for column, statement in DISPATCH_CONTROL_MIGRATIONS.items():
        if column not in existing:
            await db.execute(statement)


async def _migrate_environments_table(db: aiosqlite.Connection):
    cursor = await db.execute("PRAGMA table_info(environments)")
    existing = {row[1] for row in await cursor.fetchall()}
    for column, statement in ENVIRONMENT_MIGRATIONS.items():
        if column not in existing:
            await db.execute(statement)


async def init_db(db_path: Path = None):
    global _db_path
    if db_path:
        _db_path = db_path
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(_db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(SCHEMA)
        await _migrate_agents_table(db)
        await _migrate_dispatch_runs_table(db)
        await _migrate_messages_table(db)
        await _migrate_dispatch_controls_table(db)
        await _migrate_environments_table(db)
        await db.commit()

async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(_db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys=ON")
    return db
