"""
SQLite database layer for aify-comms v2.
Single database file replaces all JSON file storage.
"""
import json
import time
import aiosqlite
from datetime import datetime, timezone
from pathlib import Path

SQLITE_BUSY_TIMEOUT_MS = 5000

# Claim probes (BEGIN IMMEDIATE "is there work for me?") are idempotent and retry-safe: under
# write contention they should fail fast and report "nothing claimed this round" rather than camp
# on the write lock for the full 5s. Without this, a long-poll's final attempt near the wait
# deadline could block ~5s on the lock and push the whole request past the bridge's ~28s HTTP
# timeout (observed on a busy host). A short claim timeout caps that overshoot to well under a
# second AND reduces overall write-lock contention. (2026-07-01)
SQLITE_CLAIM_BUSY_TIMEOUT_MS = 1200


async def _apply_connection_pragmas(db: aiosqlite.Connection, busy_timeout_ms: int = SQLITE_BUSY_TIMEOUT_MS) -> None:
    # One round-trip: get_db() runs this on every per-request connection, so on
    # the high-frequency terminal-output path three separate execute() calls
    # were three extra round-trips per chunk.
    await db.executescript(
        f"PRAGMA busy_timeout={busy_timeout_ms};"
        "PRAGMA synchronous=NORMAL;"
        "PRAGMA foreign_keys=ON;"
        # Cap the -wal file SQLite leaves behind after a checkpoint truncates it.
        # Under continuous dashboard polling the WAL grew to ~83MB (checkpoint
        # starvation); this makes every checkpoint that DOES advance reclaim the
        # file back to <=16MB instead of leaving it bloated. Pairs with the
        # explicit TRUNCATE checkpoint in the reconcile loop. (2026-06-18)
        "PRAGMA journal_size_limit=16777216;"
    )

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
    pending_session_id TEXT DEFAULT '',
    driver_state TEXT DEFAULT 'idle',
    managed_by TEXT DEFAULT '',
    capabilities TEXT DEFAULT '[]',
    runtime_config TEXT DEFAULT '{}',
    runtime_state TEXT DEFAULT '{}',
    favorited INTEGER NOT NULL DEFAULT 0,
    registered_at TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_live_state (
    agent_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'offline',
    reason TEXT DEFAULT '',
    environment_id TEXT DEFAULT '',
    session_id TEXT DEFAULT '',
    terminal_id TEXT DEFAULT '',
    active_run_id TEXT DEFAULT '',
    refresh_after TEXT DEFAULT '',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_agent_live_state_refresh_after ON agent_live_state(refresh_after, updated_at);

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
    client_nonce TEXT DEFAULT '',
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
    queue_if_busy INTEGER NOT NULL DEFAULT 0,
    steer_if_busy INTEGER NOT NULL DEFAULT 0,
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
CREATE INDEX IF NOT EXISTS idx_dispatch_runs_status_requested ON dispatch_runs(status, requested_at DESC);
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
    session_handle TEXT DEFAULT '',
    terminal_id TEXT DEFAULT '',
    bridge_kind TEXT DEFAULT '',
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
    owner_mode TEXT DEFAULT 'managed',
    owner_bridge_id TEXT DEFAULT '',
    terminal_id TEXT DEFAULT '',
    terminal_status TEXT DEFAULT '',
    terminal_command TEXT DEFAULT '',
    terminal_workspace TEXT DEFAULT '',
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

CREATE TABLE IF NOT EXISTS terminal_sessions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    bridge_id TEXT DEFAULT '',
    runtime TEXT NOT NULL,
    workspace TEXT DEFAULT '',
    command TEXT DEFAULT '',
    output TEXT DEFAULT '',
    output_seq INTEGER DEFAULT 0,
    status TEXT DEFAULT 'starting',
    requested_by TEXT DEFAULT '',
    process_id TEXT DEFAULT '',
    cols INTEGER DEFAULT 0,
    rows INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    stopped_at TEXT,
    error TEXT DEFAULT '',
    FOREIGN KEY (session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (environment_id) REFERENCES environments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS terminal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    terminal_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    body TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (terminal_id) REFERENCES terminal_sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS terminal_controls (
    id TEXT PRIMARY KEY,
    terminal_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    bridge_id TEXT DEFAULT '',
    action TEXT NOT NULL,
    body TEXT DEFAULT '',
    cols INTEGER DEFAULT 0,
    rows INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    requested_by TEXT DEFAULT '',
    requested_at TEXT NOT NULL,
    claimed_at TEXT,
    handled_at TEXT,
    error TEXT DEFAULT '',
    FOREIGN KEY (terminal_id) REFERENCES terminal_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (environment_id) REFERENCES environments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_terminal_sessions_session ON terminal_sessions(session_id, updated_at DESC);
-- Perf (audit 2026-06-28): agent_id is the join key on the hottest liveness path (status derive,
-- session display, /agents + /sessions worker gates) which previously full-scanned this table.
CREATE INDEX IF NOT EXISTS idx_terminal_sessions_agent ON terminal_sessions(agent_id, status);
CREATE INDEX IF NOT EXISTS idx_terminal_events_terminal ON terminal_events(terminal_id, id);
CREATE INDEX IF NOT EXISTS idx_terminal_controls_env_status ON terminal_controls(environment_id, bridge_id, status, requested_at, id);

CREATE TABLE IF NOT EXISTS agent_turn_state (
    agent_id TEXT PRIMARY KEY,
    turn_busy INTEGER NOT NULL DEFAULT 0,
    turn_run_id TEXT DEFAULT '',
    turn_bridge_id TEXT DEFAULT '',
    turn_runtime TEXT DEFAULT '',
    turn_updated_at TEXT NOT NULL,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
);

-- Console-working lease (2026-06-05): the managed-claude PTY spinner footer
-- ("esc to interrupt" / "<glyph> <verb> for <time>") refreshes working_at while
-- claude is generating. A short TTL lease OR'd into derived `working` — additive,
-- never clears turn_busy, self-expires when the spinner stops. Closes the
-- "online while thinking" under-report the per-completed-message transcript can't see.
CREATE TABLE IF NOT EXISTS agent_console_signal (
    agent_id TEXT PRIMARY KEY,
    working_at TEXT NOT NULL,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
);

-- Status engine v2 (2026-06-04): one row per agent holding the event-driven
-- turn sub-state that feeds status_engine.derive(). Placed after agent_turn_state
-- so the FK target `agents` already exists. Liveness / worker_present /
-- env_reachable are NOT stored here; they are gathered live at derive() time.
CREATE TABLE IF NOT EXISTS agent_status_state (
    agent_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'offline',
    in_turn INTEGER NOT NULL DEFAULT 0,
    awaiting_input INTEGER NOT NULL DEFAULT 0,
    turn_run_id TEXT NOT NULL DEFAULT '',
    last_event TEXT NOT NULL DEFAULT '',
    last_event_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
);

-- WS5 Task 5.1 (2026-06-02): explicit delivery-loop claimer lease. A managed
-- sidecar-delivery loop (hermes-managed-host.js / claude-channel.js) POSTs
-- `claimer-acquire` when it becomes a live claimer (gateway ok + heartbeat +
-- first successful /dispatch/claim) and `claimer-release` on terminal teardown.
-- The lease is a POSITIVE deliverability signal that resolves the lazy-claim
-- ambiguity at SEND time: state='released' (or never-recorded) disambiguates a
-- genuinely-deaf target from a healthy claimer that simply has not polled yet.
-- One lease row per agent (the loop is the single lifecycle owner).
CREATE TABLE IF NOT EXISTS claimer_leases (
    agent_id TEXT PRIMARY KEY,
    bridge_id TEXT DEFAULT '',
    state TEXT NOT NULL DEFAULT 'released',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
);
"""

AGENT_MIGRATIONS = {
    "runtime": "ALTER TABLE agents ADD COLUMN runtime TEXT DEFAULT 'generic'",
    "machine_id": "ALTER TABLE agents ADD COLUMN machine_id TEXT DEFAULT ''",
    "launch_mode": "ALTER TABLE agents ADD COLUMN launch_mode TEXT DEFAULT 'detached'",
    "session_mode": "ALTER TABLE agents ADD COLUMN session_mode TEXT DEFAULT 'resident'",
    "session_handle": "ALTER TABLE agents ADD COLUMN session_handle TEXT DEFAULT ''",
    # Sticky session identity (governance, 2026-05-30): when an agent reports an
    # in-session session-id that DIFFERS from its persisted session_handle, we do
    # NOT overwrite the live handle. Instead the proposed id is parked here and
    # the agent enters a visible `session-changed` state until the operator
    # confirms (re-pin) or keeps (resume the persisted id). Empty = no pending.
    "pending_session_id": "ALTER TABLE agents ADD COLUMN pending_session_id TEXT DEFAULT ''",
    # Mode/driver FSM (governance, 2026-05-30): tracks whether a driver is
    # currently attached to this agent's session. `idle` = no active driver;
    # `driving` = a sidecar (managed) or operator TUI/CLI (resident) is driving
    # the session. Combined with `session_mode`, this enforces the one-driver
    # invariant: a second driver attaching in the OTHER mode is rejected (the
    # mutual-exclusion collision guard). Same-mode supersession stays allowed.
    "driver_state": "ALTER TABLE agents ADD COLUMN driver_state TEXT DEFAULT 'idle'",
    "managed_by": "ALTER TABLE agents ADD COLUMN managed_by TEXT DEFAULT ''",
    "capabilities": "ALTER TABLE agents ADD COLUMN capabilities TEXT DEFAULT '[]'",
    "runtime_config": "ALTER TABLE agents ADD COLUMN runtime_config TEXT DEFAULT '{}'",
    "runtime_state": "ALTER TABLE agents ADD COLUMN runtime_state TEXT DEFAULT '{}'",
    "description": "ALTER TABLE agents ADD COLUMN description TEXT DEFAULT ''",
    "status_note": "ALTER TABLE agents ADD COLUMN status_note TEXT DEFAULT ''",
    # Operator-set "favorite" flag for chat ordering (dashboard puts
    # favorited agents on top). Per-deployment marker; not synced
    # across remote dashboards.
    "favorited": "ALTER TABLE agents ADD COLUMN favorited INTEGER NOT NULL DEFAULT 0",
}

DISPATCH_RUN_MIGRATIONS = {
    "execution_mode": "ALTER TABLE dispatch_runs ADD COLUMN execution_mode TEXT DEFAULT 'managed'",
    "claim_bridge_id": "ALTER TABLE dispatch_runs ADD COLUMN claim_bridge_id TEXT DEFAULT ''",
    "require_reply": "ALTER TABLE dispatch_runs ADD COLUMN require_reply INTEGER NOT NULL DEFAULT 0",
    "queue_if_busy": "ALTER TABLE dispatch_runs ADD COLUMN queue_if_busy INTEGER NOT NULL DEFAULT 0",
    "steer_if_busy": "ALTER TABLE dispatch_runs ADD COLUMN steer_if_busy INTEGER NOT NULL DEFAULT 0",
}

MESSAGE_MIGRATIONS = {
    "dispatch_requested": "ALTER TABLE messages ADD COLUMN dispatch_requested INTEGER DEFAULT 0",
    "client_nonce": "ALTER TABLE messages ADD COLUMN client_nonce TEXT DEFAULT ''",
}

DISPATCH_CONTROL_MIGRATIONS = {
    "source_message_id": "ALTER TABLE dispatch_controls ADD COLUMN source_message_id TEXT DEFAULT ''",
}

ENVIRONMENT_MIGRATIONS = {
    "bridge_version": "ALTER TABLE environments ADD COLUMN bridge_version TEXT DEFAULT ''",
}

AGENT_SESSION_MIGRATIONS = {
    "owner_mode": "ALTER TABLE agent_sessions ADD COLUMN owner_mode TEXT DEFAULT 'managed'",
    "owner_bridge_id": "ALTER TABLE agent_sessions ADD COLUMN owner_bridge_id TEXT DEFAULT ''",
    "terminal_id": "ALTER TABLE agent_sessions ADD COLUMN terminal_id TEXT DEFAULT ''",
    "terminal_status": "ALTER TABLE agent_sessions ADD COLUMN terminal_status TEXT DEFAULT ''",
    "terminal_command": "ALTER TABLE agent_sessions ADD COLUMN terminal_command TEXT DEFAULT ''",
    "terminal_workspace": "ALTER TABLE agent_sessions ADD COLUMN terminal_workspace TEXT DEFAULT ''",
}

TERMINAL_SESSION_MIGRATIONS = {
    "output": "ALTER TABLE terminal_sessions ADD COLUMN output TEXT DEFAULT ''",
    "output_seq": "ALTER TABLE terminal_sessions ADD COLUMN output_seq INTEGER DEFAULT 0",
    # PTY root pid (2026-06-02): persisted so Dashboard Stop/Restart can
    # kill-by-pid when the bridge that originally spawned the PTY has
    # restarted/died and no longer holds it in its in-memory terminals Map
    # (orphaned-console reap). Reported by the bridge on terminal attach.
    "process_id": "ALTER TABLE terminal_sessions ADD COLUMN process_id TEXT DEFAULT ''",
    # A3 real-cols (2026-07-02): the LAST APPLIED PTY dims, persisted from a
    # completed resize terminal_control so GET /terminals renders snapshots at
    # the true width instead of inferring it.
    "cols": "ALTER TABLE terminal_sessions ADD COLUMN cols INTEGER DEFAULT 0",
    "rows": "ALTER TABLE terminal_sessions ADD COLUMN rows INTEGER DEFAULT 0",
}

# Plan 4 task 12 (2026-05-25): `ready` records that a worker process completed
# its initial handshake and can accept dispatches. It is an internal readiness
# bit; public idle-live status is `online`, not `ready`.
AGENT_TURN_STATE_MIGRATIONS = {
    "ready": "ALTER TABLE agent_turn_state ADD COLUMN ready INTEGER NOT NULL DEFAULT 0",
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
    # Atomic idempotency backstop (#240): a PARTIAL UNIQUE index on
    # (from_agent, client_nonce, to_agent) is the DB-level guarantee that a retried
    # send with the same nonce cannot double-insert — the upfront SELECT is only a
    # fast path and races under concurrent retries (the client aborts+retries while
    # the first request is still mid-handler). Scoped WHERE client_nonce != '' so the
    # empty-nonce default (all legacy + nonce-less sends) is exempt, and keyed on
    # to_agent too so a legit MULTI-recipient send (one row per recipient, same nonce)
    # is not rejected. Created here — after the column migration above — so the column
    # exists on both fresh and pre-existing DBs.
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_client_nonce "
        "ON messages(from_agent, client_nonce, to_agent) WHERE client_nonce != ''"
    )


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


async def _migrate_agent_sessions_table(db: aiosqlite.Connection):
    cursor = await db.execute("PRAGMA table_info(agent_sessions)")
    existing = {row[1] for row in await cursor.fetchall()}
    for column, statement in AGENT_SESSION_MIGRATIONS.items():
        if column not in existing:
            await db.execute(statement)


BRIDGE_INSTANCE_MIGRATIONS = {
    "session_handle": "ALTER TABLE bridge_instances ADD COLUMN session_handle TEXT DEFAULT ''",
    "terminal_id": "ALTER TABLE bridge_instances ADD COLUMN terminal_id TEXT DEFAULT ''",
    "bridge_kind": "ALTER TABLE bridge_instances ADD COLUMN bridge_kind TEXT DEFAULT ''",
}


CONSOLE_SIGNAL_MIGRATIONS = {
    "subagents_at": "ALTER TABLE agent_console_signal ADD COLUMN subagents_at TEXT DEFAULT ''",
}


async def _migrate_console_signal_table(db: aiosqlite.Connection):
    cursor = await db.execute("PRAGMA table_info(agent_console_signal)")
    existing = {row[1] for row in await cursor.fetchall()}
    for column, statement in CONSOLE_SIGNAL_MIGRATIONS.items():
        if column not in existing:
            await db.execute(statement)


async def _migrate_bridge_instances_table(db: aiosqlite.Connection):
    cursor = await db.execute("PRAGMA table_info(bridge_instances)")
    existing = {row[1] for row in await cursor.fetchall()}
    for column, statement in BRIDGE_INSTANCE_MIGRATIONS.items():
        if column not in existing:
            await db.execute(statement)


async def _migrate_terminal_sessions_table(db: aiosqlite.Connection):
    cursor = await db.execute("PRAGMA table_info(terminal_sessions)")
    existing = {row[1] for row in await cursor.fetchall()}
    for column, statement in TERMINAL_SESSION_MIGRATIONS.items():
        if column not in existing:
            await db.execute(statement)


async def _migrate_agent_turn_state_table(db: aiosqlite.Connection):
    cursor = await db.execute("PRAGMA table_info(agent_turn_state)")
    existing = {row[1] for row in await cursor.fetchall()}
    for column, statement in AGENT_TURN_STATE_MIGRATIONS.items():
        if column not in existing:
            await db.execute(statement)


# Runtimes the bridge can drive through a native managed integration.
# Kept in sync with mcp/stdio/runtimes.js defaultCapabilitiesForRuntime and
# service/routers/api_v2.py _NATIVE_MANAGED_RUNTIMES.
_NATIVE_MANAGED_RUNTIMES = ("codex", "pi", "opencode", "hermes")


async def _backfill_native_managed_capability(db: aiosqlite.Connection):
    """Durable fix for the native-dispatch deadlock.

    The bridge derives its claim executionModes from the server's stored
    agent capabilities (server.js: supportedExecutionModes(state.info)).
    Managed codex/pi/opencode agents registered before the bridge advertised
    `native-managed-run` carry stale capabilities, so the post-upgrade bridge
    refuses to claim their `managed` dispatch runs and they queue forever.

    Backfill `native-managed-run` (right after `managed-run`) for any managed
    codex/pi/opencode/hermes agent missing it. Idempotent, runtime-scoped,
    matches defaultCapabilitiesForRuntime intent. claude-code is untouched
    because managed Claude is channel/wrapper-backed only. Belt-and-braces
    with the bridge self-heal.
    """
    cursor = await db.execute(
        "SELECT id, runtime, capabilities FROM agents WHERE session_mode = 'managed'"
    )
    for agent_id, runtime, capabilities in await cursor.fetchall():
        if str(runtime or "").strip().lower() not in _NATIVE_MANAGED_RUNTIMES:
            continue
        try:
            caps = json.loads(capabilities or "[]")
        except (ValueError, TypeError):
            caps = []
        if not isinstance(caps, list) or "native-managed-run" in caps:
            continue
        new_caps = []
        for cap in caps:
            new_caps.append(cap)
            if cap == "managed-run":
                new_caps.append("native-managed-run")
        if "native-managed-run" not in new_caps:
            new_caps.insert(0, "native-managed-run")
        await db.execute(
            "UPDATE agents SET capabilities = ? WHERE id = ?",
            (json.dumps(new_caps), agent_id),
        )


async def _reconcile_terminal_controls(db: aiosqlite.Connection):
    # SAME format every other writer uses (api_v2._now()). isoformat() adds sub-second
    # precision, so `...:00.123456Z` sorts BEFORE `...:00Z` in any lexical comparison — and this
    # repo has already been bitten six times by exactly that (bughunt-round2-2026-07-03). Safe
    # today because the only comparison is datetime(handled_at), but one future `handled_at >= ?`
    # would be a silent bug. One shape, everywhere (C2).
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # A queued `stop` is EXEMPT from the liveness sweep. This rule is implemented TWICE — here and
    # in api_v2._reconcile_ended_terminal_controls — with the same predicate and the same error
    # text, so BOTH must carry the exemption or neither does: an earlier fix landed in api_v2 only
    # and changed nothing, because this copy still cancelled the stop.
    #
    # Why the exemption: stop_agent_worker marks the terminal 'stopping' (correct — the host has not
    # acknowledged) and queues the stop control in the same transaction. 'stopping' is not in the
    # active set below, and this sweep runs on a timer while the bridge polls every ~3s, so whenever
    # the sweep won the race it cancelled the very stop meant to kill the process. The PTY then
    # survived a "successful" Stop worker, and 900s later the stuck-stopping reaper wrote 'stopped'
    # over it — a row asserting a death that never happened. The pre-existing VIRTUAL path has the
    # identical exposure via 'stopped', which is why the fix is not "add 'stopping' to the set".
    # Killing a process is idempotent and stays desirable on a dead-looking row; server.js keeps an
    # orphan-pid fallback for the case where no bridge owns the PTY in memory any more.
    #
    # Accumulation is still bounded: the env-currency sweep immediately below fails controls whose
    # environment/bridge is no longer current, stop included, so a control for a dead environment
    # does not pile up forever.
    await db.execute(
        """
        UPDATE terminal_controls
        SET status = 'failed',
            handled_at = COALESCE(handled_at, ?),
            error = CASE WHEN COALESCE(error, '') = ''
                         THEN 'terminal is not active'
                         ELSE error END
        WHERE status IN ('pending', 'claimed')
          AND LOWER(COALESCE(action, '')) != 'stop'
          AND terminal_id IN (
              SELECT id FROM terminal_sessions
              WHERE status NOT IN ('starting', 'attached', 'running', 'active', 'idle')
          )
        """,
        (now,),
    )
    # A pending `stop` whose owning bridge restarted is RE-TARGETED at the environment's current
    # bridge instead of being cancelled. This closes the composed defect a reviewer identified on
    # `9747dda`, and the root cause is that it made existing machinery unreachable:
    #
    #   server.js carries an orphan-pid fallback for precisely "the owning bridge restarted/died and
    #   orphaned a still-live console" — it kills the persisted PTY root BY PID when a stop arrives
    #   at a bridge that never owned the terminal in memory. That fallback could never run, because
    #   this sweep failed the control the moment `bridge_id` stopped matching a current online
    #   environment. So the code written for bridge restart was dead in the exact scenario it names.
    #
    # Consequence when it fired: the PTY survived, and because stop_agent_worker writes the session
    # 'ended', Start was then free to spawn a SECOND worker for the same agent — the instance-leak
    # class this repo has been bitten by before. Re-targeting is the root fix; a Start gate would
    # only hide the duplicate, and a too-strict Start gate is what made the whole ef- team
    # unstartable in v0.1.
    #
    # Safe because a bridge on that environment is machine-local, so it can reap a local orphan, and
    # server.js still guards the pid (`orphanPidReapAllowed` refuses when the cmdline positively
    # names a DIFFERENT agent, and pidIsSelfProtected blocks bridge/shell/init).
    #
    # STOP-ONLY on purpose: replaying a queued keystroke at a different bridge would inject it into
    # whatever that bridge now owns. Only an idempotent kill may be re-pointed.
    #
    # The CLAIM MUST BE RELEASED TOO (review finding on `530ee71` — re-pointing alone was a no-op for
    # the commonest case). A bridge only ever claims PENDING work: api_v2.py:12675 is
    # `SET status='claimed' ... WHERE id = ? AND status = 'pending'`. So a stop the dying bridge had
    # already claimed kept `status='claimed'`, got re-pointed at the new bridge, and the new bridge
    # never looked at it — stranded forever, which is precisely the state most likely to exist when a
    # bridge dies mid-stop. A claim held by a bridge that no longer exists is not a claim; drop it and
    # clear `claimed_at` so the replacement can take the work.
    #
    # Releasing is stop-only for the same reason re-targeting is: re-queueing a keystroke the previous
    # bridge may already have delivered would double-type it.
    await db.execute(
        """
        UPDATE terminal_controls
        SET bridge_id = (
                SELECT COALESCE(environments.bridge_id, '')
                FROM environments
                WHERE environments.id = terminal_controls.environment_id
                  AND environments.status = 'online'
                LIMIT 1
            ),
            status = 'pending',
            claimed_at = NULL
        WHERE status IN ('pending', 'claimed')
          AND LOWER(COALESCE(action, '')) = 'stop'
          AND EXISTS (
              SELECT 1 FROM environments
              WHERE environments.id = terminal_controls.environment_id
                AND environments.status = 'online'
                AND COALESCE(environments.bridge_id, '') != COALESCE(terminal_controls.bridge_id, '')
                AND COALESCE(environments.bridge_id, '') != ''
          )
        """
    )
    # Everything still unreachable is failed, stop included — that is the bound on accumulation. A
    # stop whose environment has no ONLINE bridge at all cannot be delivered by anyone, so leaving it
    # pending forever would just grow the table. The re-target above has already rescued the cases
    # that a live bridge could still act on.
    await db.execute(
        """
        UPDATE terminal_controls
        SET status = 'failed',
            handled_at = COALESCE(handled_at, ?),
            error = CASE WHEN COALESCE(error, '') = ''
                         THEN 'environment bridge is no longer current'
                         ELSE error END
        WHERE status IN ('pending', 'claimed')
          AND NOT EXISTS (
              SELECT 1 FROM environments
              WHERE environments.id = terminal_controls.environment_id
                AND COALESCE(environments.bridge_id, '') = COALESCE(terminal_controls.bridge_id, '')
                AND environments.status = 'online'
          )
        """,
        (now,),
    )
    await db.execute(
        """
        UPDATE environment_controls
        SET status = 'failed',
            handled_at = COALESCE(handled_at, ?),
            error = CASE WHEN COALESCE(error, '') = ''
                         THEN 'environment bridge is no longer current'
                         ELSE error END
        WHERE status IN ('pending', 'claimed')
          AND NOT EXISTS (
              SELECT 1 FROM environments
              WHERE environments.id = environment_controls.environment_id
                AND COALESCE(environments.bridge_id, '') = COALESCE(environment_controls.bridge_id, '')
                AND environments.status = 'online'
          )
        """,
        (now,),
    )
    await db.execute(
        """
        UPDATE terminal_controls AS stale
        SET status = 'failed',
            handled_at = COALESCE(handled_at, ?),
            error = CASE WHEN COALESCE(error, '') = ''
                         THEN 'superseded by newer pending resize'
                         ELSE error END
        WHERE stale.action = 'resize'
          AND stale.status = 'pending'
          AND EXISTS (
              SELECT 1 FROM terminal_controls newer
              WHERE newer.terminal_id = stale.terminal_id
                AND newer.action = 'resize'
                AND newer.status = 'pending'
                AND (
                    newer.requested_at > stale.requested_at
                    OR (newer.requested_at = stale.requested_at AND newer.id > stale.id)
                )
          )
        """,
        (now,),
    )
    await db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_terminal_controls_pending_resize
        ON terminal_controls(terminal_id)
        WHERE action = 'resize' AND status = 'pending'
        """
    )
    columns = [
        row[2]
        for row in await (await db.execute(
            "PRAGMA index_info(idx_terminal_controls_env_status)"
        )).fetchall()
    ]
    if columns != ["environment_id", "bridge_id", "status", "requested_at", "id"]:
        await db.execute("DROP INDEX IF EXISTS idx_terminal_controls_env_status")
        await db.execute(
            """
            CREATE INDEX idx_terminal_controls_env_status
            ON terminal_controls(environment_id, bridge_id, status, requested_at, id)
            """
        )


async def init_db(db_path: Path = None):
    global _db_path
    if db_path:
        _db_path = db_path
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(_db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await _apply_connection_pragmas(db)
        await db.executescript(SCHEMA)
        await _migrate_agents_table(db)
        await _migrate_dispatch_runs_table(db)
        await _migrate_messages_table(db)
        await _migrate_dispatch_controls_table(db)
        await _migrate_environments_table(db)
        await _migrate_agent_sessions_table(db)
        await _migrate_terminal_sessions_table(db)
        await _migrate_bridge_instances_table(db)
        await _migrate_console_signal_table(db)
        await _migrate_agent_turn_state_table(db)
        await _backfill_native_managed_capability(db)
        await _reconcile_terminal_controls(db)
        await db.commit()

async def get_db(busy_timeout_ms: int = SQLITE_BUSY_TIMEOUT_MS) -> aiosqlite.Connection:
    db = await aiosqlite.connect(_db_path)
    db.row_factory = aiosqlite.Row
    try:
        await _apply_connection_pragmas(db, busy_timeout_ms)
    except BaseException:
        await db.close()
        raise
    return db
