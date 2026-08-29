"""Making sure a managed agent has a PTY to be dispatched into.

RELOCATED from `service/api_core/dispatch_start.py` in v0.5.4, byte-identical. It was 159 of that
module's 470 lines, is called by no sibling there, and reads none of its constants — which is what
makes this a relocation rather than a split.

FIVE MODULES IMPORT IT, and that is the argument for its own file rather than against it. Delivery
resolution, the launch loop, spawn settlement, the session-mode gates and the console route all need
"give this agent a console if it has not got one", and a helper with five importers buried in a
module about starting dispatches is findable only by someone who already knows where it is.

`for_session_id` IS A PARAMETER FOR A REASON: a managed RESTART mints a new session, and the PTY has
to be attached to the session being started rather than to whatever the agent's current row happens
to say. That note used to live in the carrier's docstring; it belongs with the function it describes.

DB ACCESS: `db` is passed in, and nothing here opens a connection or commits — this joins its
caller's transaction.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from service.api_core.agent_sessions import ENDED_AGENT_SESSION_STATUS_SQL
from service.api_core.capabilities import _default_console_argv, _environment_supports_terminal
from service.api_core.events import _append_terminal_control, _append_terminal_event
from service.api_core.records import _environment_record_to_dict
from service.api_core.runtime import _normalize_runtime
from service.api_core.serialization import _json_loads_or
from service.api_core.terminal_ownership import _active_terminal_for_agent
from service.api_core.workspace import _workspace_for_environment
from service.clock import now as _now


async def _ensure_managed_pty_for_dispatch(
    db, agent_id: str, *, runtime: str, settings: dict[str, Any], requested_by: str,
    for_session_id: str = "",
):
    """`for_session_id` scopes adoption to ONE session, and a restart is why it exists.

    REPRODUCED LIVE 2026-08-11 (restarttest-claude, first attempt, deterministic). A managed
    restart creates a new spawn and a new session, and the new spawn reaches `running` about two
    seconds BEFORE the old worker's terminal is torn down. `_active_terminal_for_agent` picks the
    agent's most-recently-seen session that has a terminal — which at that instant is still the OLD
    one — so this function said "there is already a PTY" and created nothing. The restart then
    killed that terminal, leaving:

        spawn_requests.status = 'running'   with NO terminal at all, ever
        agent status           = available
        the operator            looking at a session that says stopped

    `ef-manager` sat in exactly that state today after `graph-tech-lead` restarted it, and it took a
    cold-start send to recover. The v0.2.0 dead-terminal finalizer cannot clean it up either,
    because that keys on a terminal being DEAD and here none was ever created.

    Adoption across dispatches WITHIN a session is the whole point of this function and is
    unchanged. What is no longer allowed is adopting a terminal belonging to a DIFFERENT session —
    at a restart that terminal is, by definition, the one being destroyed.
    """
    wanted_session = str(for_session_id or "").strip()
    active = await _active_terminal_for_agent(db, agent_id, settings=settings)
    # `active` is a sqlite3.Row, NOT a dict — it has no `.get()`. The first version of this line
    # called `active.get("session_id")`, which raises AttributeError, and the caller's
    # `except Exception: pass` swallowed it whole. Worse than a plain crash: it only triggered when
    # an active terminal EXISTED, i.e. exactly the restart case this function was being fixed for,
    # and only when the outgoing terminal had not yet flipped to `stopped` — so the first live test
    # passed by luck and the second hung with no worker and no log line.
    if active and (not wanted_session or str(active["session_id"] or "") == wanted_session):
        return active
    normalized_runtime = _normalize_runtime(runtime or "")
    if normalized_runtime not in {"claude-code", "codex", "hermes", "opencode", "pi"}:
        return None

    if wanted_session:
        # Use the caller's session outright. Re-deriving it by `last_seen` would land on the
        # outgoing session for the same two seconds that caused the bug above.
        session = await (await db.execute(
            "SELECT * FROM agent_sessions WHERE id = ? AND agent_id = ?", (wanted_session, agent_id)
        )).fetchone()
    else:
        session = await (await db.execute(
            """
            SELECT *
            FROM agent_sessions
            WHERE agent_id = ?
              AND runtime = ?
              AND status IN ('running', 'recovering')
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (agent_id, normalized_runtime),
        )).fetchone()
    if not session:
        return None
    if normalized_runtime == "pi" and not str(session["session_handle"] or "").strip():
        return None

    env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (session["environment_id"],))).fetchone()
    if not env_row:
        return None
    environment = _environment_record_to_dict(env_row, offline_seconds=settings.get("environment_offline_seconds", 90))
    if str(environment.get("status") or "").lower() != "online":
        return None
    if not _environment_supports_terminal(environment, session["runtime"]):
        return None

    workspace, _workspace_root = _workspace_for_environment(environment, None, session["workspace"] or "")
    terminal_id = f"term_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    bridge_id = str(environment.get("bridgeId") or "").strip()
    # argv is the value; command is its join. Stored together so a bridge can take either, and derived
    # from one source so they cannot describe different launches.
    argv = _default_console_argv(session, workspace)
    command = " ".join(argv)
    now = _now()
    await db.execute(
        """
        INSERT INTO terminal_sessions (
            id, session_id, agent_id, environment_id, bridge_id, runtime, workspace, command, argv,
            output, status, requested_by, created_at, updated_at, stopped_at, error
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            terminal_id,
            session["id"],
            agent_id,
            session["environment_id"],
            bridge_id,
            session["runtime"],
            workspace,
            command,
            json.dumps(argv),
            "",
            "starting",
            requested_by or "dashboard",
            now,
            now,
            None,
            "",
        ),
    )
    await _append_terminal_event(
        db,
        terminal_id,
        "managed_pty_start_requested",
        json.dumps({"requestedBy": requested_by or "dashboard", "sessionId": session["id"], "workspace": workspace, "command": command}),
    )
    await _append_terminal_control(
        db,
        terminal_id=terminal_id,
        environment_id=session["environment_id"],
        bridge_id=bridge_id,
        action="start",
        requested_by=requested_by or "dashboard",
        body=command,
    )
    # Publish the wrapper PTY's terminal_session id into agent.runtime_state.terminalId
    # so the dashboard's chooseSessionConsoleWidget (service/new_dashboard/app.js)
    # can render xterm against it. Without this the row is orphaned from the
    # runtime_state-driven rendering — only ensure_virtual_terminal publishes
    # virtualTerminalId (native RPC adapter path). Operator-reported 2026-05-24:
    # wrapper PTY existed but dashboard couldn't see it.
    agent_runtime_state_row = await (await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (agent_id,))).fetchone()
    if agent_runtime_state_row:
        _agent_rs = _json_loads_or(agent_runtime_state_row["runtime_state"], {})
        if not isinstance(_agent_rs, dict):
            _agent_rs = {}
        _agent_rs["terminalId"] = terminal_id
        await db.execute(
            "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
            (json.dumps(_agent_rs), now, agent_id),
        )

    await db.execute(
        f"""
        UPDATE agent_sessions
        SET owner_mode = 'managed',
            owner_bridge_id = ?,
            terminal_id = ?,
            terminal_status = 'starting',
            terminal_command = ?,
            terminal_workspace = ?,
            -- Spawning a NEW managed PTY for this session IS the "backing (re)started" event:
            -- promote a dead-state denorm back to running, else the row keeps the previous
            -- backing's 'stopped' and the Console label reads "Console stopped" for a live
            -- attached terminal forever (cms-manager, 2026-06-10 — the lazy auto-start-on-send
            -- bound a fresh PTY to a session left 'stopped' by the old backing's death; the
            -- display deriver deliberately never promotes, so the bind moment must).
            status = CASE WHEN status IN {ENDED_AGENT_SESSION_STATUS_SQL}
                          THEN 'running' ELSE status END,
            ended_at = CASE WHEN status IN {ENDED_AGENT_SESSION_STATUS_SQL}
                            THEN NULL ELSE ended_at END,
            last_seen = ?
        WHERE id = ?
        """,
        (bridge_id, terminal_id, command, workspace, now, session["id"]),
    )
    return await _active_terminal_for_agent(db, agent_id, settings=settings)
