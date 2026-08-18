"""
SQLite database layer for aify-comms v2.
Single database file replaces all JSON file storage.
"""
import json
import time
import aiosqlite
from pathlib import Path

from service.reconcilers.terminal_controls import _reconcile_terminal_controls
# SCHEMA moved to service/schema.py in v0.5.4 — 431 lines of DDL is data, and this module opens
# connections. Imported rather than re-exported: `init_db` below is its only reader.
from service.schema import SCHEMA

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
    "handoff_message_id": "ALTER TABLE dispatch_runs ADD COLUMN handoff_message_id TEXT DEFAULT ''",
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
#
# A DELIBERATE THIRD COPY, and it cannot become an import. The owner is
# `service/api_core/runtime.py`, but this module sits BELOW api_core — three api_core modules import
# `service.db` and it imports none of them — so reaching up for the constant would invert the layering
# and close a package-level cycle. The other copy is JS (`mcp/stdio/dispatch-execution.js`) and cannot
# import a Python name at all.
#
# What keeps the three honest is `service/tests/test_native_managed_runtimes_parity.py`, which compares
# all three by CONTENT (the type differs — a tuple here, a set there — because both are only ever
# membership-tested). The previous note here said "kept in sync with ... service/routers/api_v2.py",
# naming a file that has declared nothing since v0.5.4: a manual-sync instruction pointing at a
# declaration that no longer exists, which is worse than no note, since it reads as governance.
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


# _reconcile_terminal_controls and its two environment helpers moved to
# service/reconcilers/terminal_controls.py in v0.5.4 — a reconciler belongs in the reconcilers
# package, not in the connection layer. init_db still CALLS it; the import is at the top.
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
