"""The database schema, as one DDL script. Data, not logic.

RELOCATED from `service/db.py` in v0.5.4, byte-identical. It was 431 of that file's 752 lines — more
than half a module whose job is opening connections given over to a string nobody reads but
`init_db`. Every `CREATE TABLE IF NOT EXISTS` in the product lives here and nowhere else.

WHY IT IS ONE SCRIPT AND NOT PER-TABLE MODULES: `executescript` runs it in one go at startup, the
order inside matters where foreign keys do, and splitting it would turn one readable artefact into a
question about which file a table is in. The file is long because the schema is; that is the honest
shape.

CHANGING A TABLE DOES NOT GO HERE ALONE. `IF NOT EXISTS` means an edit to a CREATE only affects
databases that do not exist yet — every deployed database keeps its old columns. A column added here
must ALSO be added to the matching `*_MIGRATIONS` map in `service/db.py`, or it will be present on a
fresh install and missing on every upgrade, which is the worst of both.
"""
from __future__ import annotations

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
    -- The sender was TOLD the run ended; NOT the same fact as result_message_id, which means
    -- the obligated answer arrived. Conflating them let a system-authored failure notice close
    -- a require_reply contract the target never answered (external review H2, 2026-08-18).
    handoff_message_id TEXT DEFAULT '',
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
    -- Who settled it. Mandatory at the endpoint; empty only for rows predating the column.
    handled_by TEXT DEFAULT '',
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
    argv TEXT DEFAULT '',
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
