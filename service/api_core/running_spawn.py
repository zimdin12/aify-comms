"""Everything that happens the moment a spawn reports RUNNING.

Extracted from `update_spawn_request` in `service/routers/spawn_requests.py` in v0.5.4;
`test_update_spawn_request_split_is_inert.py` inlines it back and AST-compares against the pre-split
fixture. The body is at its original 8-space column so the SQL literals are preserved byte-for-byte.

THIS IS THE LARGEST SINGLE EXTRACTION IN THE SERIES and it is one subject, not a grab bag. A bridge
saying "the worker is up" is the moment a spawn REQUEST becomes a live agent, and every write here
exists to finish that conversion: upsert the agent row the spec described, open its session, bind a
terminal if the environment backs one, and deliver the initial message the spawn was created to
carry. Splitting it further would divide one transition across modules without making any part of it
independently meaningful.

IT RUNS INSIDE THE ROUTE'S TRANSACTION AND OWNS NONE OF IT. The commit stays in the caller. These
writes must all land or none of them: an agent row without its session, or a session without the
message that justified the spawn, is worse than a failed spawn because it looks like a working one.

`session_id` IS RETURNED RATHER THAN MUTATED. It is generated here when the request had none, and the
caller writes it back to `spawn_requests` after this returns. After the split it would otherwise be a
HELPER local the caller still reads -- the live-out defect the extract-method gate refuses.
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from service.api_core.managed_pty_for_dispatch import _ensure_managed_pty_for_dispatch
from service.api_core.capabilities import _default_capabilities_for, _managed_via_wrapper_for_runtime
from service.api_core.channel_delivery import (
    _apply_channel_routing_to_claude_runs,
    _insert_messages_via_console,
)
from service.api_core.dispatch_runs import _create_dispatch_runs
from service.api_core.runtime import _normalize_runtime
from service.api_core.runtime_state import _runtime_state_with_handle
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings, _managed_terminal_backing_enabled
from service.longpoll import _wake_agent

#: DELIBERATELY the ROUTER'S logger name, not this module's. The one warning in this block records an
#: eager-PTY failure that must never be silent -- a bare `pass` there once hid an AttributeError for
#: two live restarts while the operator saw an agent with no worker. Renaming the logger would move
#: that line to a channel nobody greps, which is the same outcome by a different route. v0.5.x is a
#: refactor line: the log output is part of what must not change.
logger = logging.getLogger("aify_comms.routers.spawn_requests")


async def _settle_running_spawn(
    db, req, row, spec_row, now, started_at, status_value, session_id, runtime_state
):
        """Convert a spawn request into a live agent, and hand back the session id.

        Guarded inside rather than at the call site, which is what the block looked like before it
        moved -- the extract-method gate splices this body back over its call verbatim, so hoisting
        the condition would break the round trip that proves the move changed nothing. Every argument
        is passed under the caller's own name for the same reason: inline-back does not substitute
        arguments.
        """
        if status_value == "running":
            session_id = session_id or f"sess_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            effective_session_handle = req.sessionHandle or row["session_handle"] or ""
            if effective_session_handle:
                runtime_state = _runtime_state_with_handle(row["runtime"], runtime_state, effective_session_handle)
            spec_metadata = _json_loads_or(spec_row["metadata"], {})
            runtime_config = spec_metadata.get("runtimeConfig") if isinstance(spec_metadata, dict) else {}
            if not isinstance(runtime_config, dict):
                runtime_config = {}
            agent_capabilities = _default_capabilities_for(row["runtime"], "managed", effective_session_handle, runtime_config)
            await db.execute(
                """
                INSERT INTO agents (
                    id, role, name, cwd, model, description, instructions, status, status_note,
                    runtime, machine_id, launch_mode, session_mode, session_handle, managed_by,
                    capabilities, runtime_config, runtime_state, registered_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    role = excluded.role,
                    name = excluded.name,
                    cwd = excluded.cwd,
                    model = excluded.model,
                    instructions = excluded.instructions,
                    status = excluded.status,
                    runtime = excluded.runtime,
                    machine_id = excluded.machine_id,
                    launch_mode = excluded.launch_mode,
                    session_mode = excluded.session_mode,
                    session_handle = excluded.session_handle,
                    managed_by = excluded.managed_by,
                    capabilities = excluded.capabilities,
                    runtime_config = excluded.runtime_config,
                    runtime_state = excluded.runtime_state,
                    last_seen = excluded.last_seen
                """,
                (
                    row["agent_id"],
                    row["role"] or "coder",
                    row["name"] or row["agent_id"],
                    row["workspace"] or "",
                    spec_row["model"] or "",
                    "",
                    spec_row["standing_instructions"] or "",
                    "idle",
                    "",
                    row["runtime"],
                    row["claim_machine_id"] or "",
                    "managed",
                    "managed",
                    effective_session_handle,
                    row["created_by"] or "dashboard",
                    json.dumps(agent_capabilities),
                    json.dumps(runtime_config),
                    json.dumps(runtime_state),
                    now,
                    now,
                ),
            )
            await db.execute(
                """
                INSERT OR REPLACE INTO bridge_instances (
                    id, agent_id, machine_id, runtime, session_mode, registered_at, last_seen, superseded_by, superseded_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    req.bridgeId or row["claimed_by_bridge_id"] or "",
                    row["agent_id"],
                    row["claim_machine_id"] or "",
                    row["runtime"],
                    "managed",
                    now,
                    now,
                    "",
                    None,
                ),
            )
            await _upsert_running_agent_session(
                db, req, row, session_id,
                effective_session_handle, started_at, now,
            )
            # Migrate a live terminal orphaned by this rotation (operator-reported
            # 2026-05-31, sc-architect). A managed respawn's bridge can create the
            # visible-TUI/console terminal a few seconds BEFORE this running
            # transition mints the new session, so the live terminal stays bound to
            # the prior (about-to-be-ended) session and the new running session gets
            # terminal_id=''. The dashboard then shows "Console not started" while
            # the real TUI is alive — and the live terminal row hangs off an ended
            # session, so the FK ON DELETE CASCADE could later drop a running TUI's
            # tracking. Re-point this agent's freshest LIVE, same-bridge terminal
            # onto the new session BEFORE ending the prior sessions.
            #
            # BOUNDED BY THIS SPAWN'S OWN AGE (2026-08-03). "A few seconds BEFORE this
            # transition" was the intent but never a constraint, so the same-bridge match also
            # adopted the PREVIOUS generation's terminal — and on a Restart that is precisely
            # the terminal being killed. Live on ef-manager: the adopted terminal predated its
            # spawn request by 10h16m, the restart's own stop landed one second later, and
            # _close_active_terminal_runs_for_terminal (which keys on the CURRENT session's
            # terminal) then failed the replacement's queued brief. Every dashboard Restart
            # destroyed the brief it was created to deliver, the spawn died on "Initial brief
            # failed", and the reaper killed the leftover sidecar as a headless orphan.
            #
            # A terminal this respawn produced cannot predate the spawn request that ordered
            # it, so that is the bound: same clock (_now() on both inserts), and it still
            # admits the whole legitimate window — bridge claims, creates the terminal, then
            # PATCHes running. COALESCE keeps a row with no created_at migrating as before
            # rather than silently disabling the rescue.
            migrate_bridge_id = req.bridgeId or row["claimed_by_bridge_id"] or ""
            if migrate_bridge_id:
                await _migrate_bridge_id_onto_live_terminal(db, row, session_id, migrate_bridge_id)
            await db.execute(
                """
                UPDATE agent_sessions
                SET status = 'ended',
                    ended_at = COALESCE(NULLIF(ended_at, ''), ?),
                    last_seen = COALESCE(NULLIF(ended_at, ''), NULLIF(last_seen, ''), ?)
                WHERE agent_id = ?
                  AND id != ?
                  AND status IN ('starting', 'running', 'recovering', 'restarting')
                """,
                (now, now, row["agent_id"], session_id),
            )
            if row["status"] != "running" and str(row["initial_message"] or "").strip():
                await _hand_settled_spawn_to_dispatch(db, row)

            # Slices 1/2/4 (architectural): when managed_terminal_backing
            # is enabled, proactively launch the wrapper PTY for this
            # newly-registered managed agent. The wrapper stays alive
            # across dispatches; subsequent sends reuse it via slice 3's
            # console-attach reuse + the existing
            # _active_terminal_for_agent lookup in
            # _ensure_managed_pty_for_dispatch. Operator-visible win: no
            # "console pops up when I send" — the console pre-exists by
            # the time the first dispatch arrives. Best-effort: a
            # wrapper-launch failure here does NOT fail the spawn-request
            # running transition (the dispatch path's lazy spawn is the
            # fallback).
            settings_for_pty = await _load_settings(db)
            _is_claude_managed = _normalize_runtime(row["runtime"]) == "claude-code"
            _eager_flag = bool(settings_for_pty.get("managed_pty_eager_spawn", DEFAULT_SETTINGS["managed_pty_eager_spawn"]))
            # When insert_messages_via_console=false (the default), managed
            # claude needs a wrapper PTY hosting claude-aify so its
            # claude-channel.js child polls /dispatch/claim for this
            # specific agent. Without it, channel dispatches sit queued
            # forever (originally observed in run_1779309370301).
            _claude_needs_wrapper = _is_claude_managed and not _insert_messages_via_console(settings_for_pty)
            # Unified-backing refactor 2026-05-24: when this runtime is
            # wrapper-backed, the wrapper PTY MUST pre-exist by spawn-request
            # running transition — otherwise nothing claims dispatches (the
            # main bridge dispatch loop drops 'managed' from supportedExecutionModes
            # for this runtime, and the wrapper's child bridge doesn't exist
            # until the PTY launches).
            _wrapper_backed = _managed_via_wrapper_for_runtime(settings_for_pty, row["runtime"] or "")
            if _managed_terminal_backing_enabled(settings_for_pty) and (_eager_flag or _claude_needs_wrapper or _wrapper_backed):
                await _ensure_pty_for_settled_spawn(db, row, settings_for_pty)
        return session_id


async def _migrate_bridge_id_onto_live_terminal(db, row, session_id, migrate_bridge_id):
                """Move a spawn's bridge id onto the terminal that is actually serving its session.

                Extracted from `_settle_running_spawn` in v0.5.4. Body at its ORIGINAL COLUMN: it contains
                triple-quoted SQL, and dedenting would rewrite the string contents and make the round trip
                unprovable.
                """
                live_terminal = await (await db.execute(
                    """
                    SELECT id, status, command, workspace, session_id FROM terminal_sessions
                    WHERE agent_id = ?
                      AND bridge_id = ?
                      AND id NOT LIKE 'vterm_%'
                      AND status IN ('starting', 'attached', 'running', 'active', 'idle', 'recovering')
                      AND datetime(COALESCE(NULLIF(created_at, ''), '1970-01-01'))
                          >= datetime(COALESCE(NULLIF(?, ''), '1970-01-01'))
                    ORDER BY datetime(COALESCE(updated_at, created_at, '1970-01-01')) DESC, rowid DESC
                    LIMIT 1
                    """,
                    (row["agent_id"], migrate_bridge_id, row["created_at"]),
                )).fetchone()
                if live_terminal and str(live_terminal["session_id"] or "") != session_id:
                    await db.execute(
                        "UPDATE terminal_sessions SET session_id = ? WHERE id = ?",
                        (session_id, live_terminal["id"]),
                    )
                    await db.execute(
                        """
                        UPDATE agent_sessions
                        SET terminal_id = ?, terminal_status = ?,
                            terminal_command = ?, terminal_workspace = ?,
                            -- Binding a LIVE terminal is the authoritative "backing (re)started"
                            -- event: promote a dead-state denorm back to running, else the row
                            -- keeps the PREVIOUS backing's 'stopped' and the Console label reads
                            -- "Console stopped" for a live attached terminal forever (cms-manager,
                            -- 2026-06-10; the display deriver deliberately never promotes).
                            status = CASE WHEN status IN ('stopped','ended','failed','lost','cancelled','completed')
                                          THEN 'running' ELSE status END,
                            ended_at = CASE WHEN status IN ('stopped','ended','failed','lost','cancelled','completed')
                                            THEN NULL ELSE ended_at END
                        WHERE id = ?
                        """,
                        (
                            live_terminal["id"],
                            live_terminal["status"] or "",
                            live_terminal["command"] or "",
                            live_terminal["workspace"] or "",
                            session_id,
                        ),
                    )


async def _hand_settled_spawn_to_dispatch(db, row):
                """Create the dispatch runs for a spawn that has just become live, and wake it.

                Extracted from `_settle_running_spawn` in v0.5.4. This is the handoff: the spawn stopped being
                a request and became a worker, so the work that was waiting on it becomes real dispatch runs.
                Guarded on the row having only just reached `running`, so a re-run does not double-dispatch.
                """
                settings_for_runs = await _load_settings(db)
                runs = await _create_dispatch_runs(
                    db,
                    [row["agent_id"]],
                    from_agent=row["created_by"] or "dashboard",
                    message_type="request",
                    subject=row["subject"] or f"Spawn {row['agent_id']}",
                    body=row["initial_message"],
                    priority=row["priority"] or "normal",
                    in_reply_to=None,
                    dispatch_mode="start_if_possible",
                    execution_mode=(
                        "channel"
                        if _managed_via_wrapper_for_runtime(settings_for_runs, row["runtime"] or "")
                        else "managed"
                    ),
                    requested_runtime=row["runtime"],
                    message_id=None,
                    require_reply=True,
                )
                # Spawn-time initial-message dispatches for managed claude
                # must honor insert_messages_via_console=false (the channel-
                # route default). Deep-test caught this earlier — without
                # the helper here e2e-test-claude's initial run stayed
                # execution_mode='managed' and claude-channel.js never
                # claimed it.
                await _apply_channel_routing_to_claude_runs(db, runs, settings_for_runs)
                for run in runs:
                    _wake_agent(run["targetAgentId"])


async def _ensure_pty_for_settled_spawn(db, row, settings_for_pty):
                """Give a settled spawn its managed PTY, best-effort.

                Extracted from `_settle_running_spawn` in v0.5.4. Best-effort by design: the spawn is already
                settled and its runs already exist, so a PTY that fails to come up must not undo that — the
                except clause logs and moves on.
                """
                try:
                    await _ensure_managed_pty_for_dispatch(
                        db,
                        row["agent_id"],
                        runtime=row["runtime"],
                        settings=settings_for_pty,
                        requested_by="spawn-request",
                        # Scope adoption to THIS spawn's session. Without it a restart adopts the
                        # outgoing worker's terminal — which is killed two seconds later — and the
                        # agent ends up `running` with no worker at all. Reproduced live.
                        for_session_id=str(row["session_id"] or ""),
                    )
                except Exception as exc:
                    # The dispatch path's lazy spawn is still the fallback — this must never fail a
                    # spawn-request transition. But it must not be SILENT either: a bare `pass` here
                    # hid an AttributeError of mine for two live restarts, during which the operator
                    # saw an agent with no worker and the logs said nothing at all. A best-effort
                    # step that fails invisibly is indistinguishable from one that had nothing to do.
                    logger.warning(
                        "eager managed PTY for %s failed (%s: %s); falling back to lazy spawn on dispatch",
                        row["agent_id"], type(exc).__name__, exc,
                    )


async def _upsert_running_agent_session(
    db, req, row, session_id,
    effective_session_handle, started_at, now,
):
            """Write the agent_sessions row for a spawn that has just become a running worker.

            Extracted from `_settle_running_spawn` in v0.5.4 — fifty lines of one SQL statement sitting
            in the middle of a settlement, which is what made the surrounding phases hard to see.

            The comment above the statement travelled WITH it, because it records why this is an UPSERT
            rather than INSERT OR REPLACE and what happened when it was not. Separated from the SQL it
            explains, that reasoning is one edit away from being lost.

            Body at its ORIGINAL COLUMN: the statement contains triple-quoted SQL, and dedenting would
            rewrite the string contents and make the round trip unprovable.
            """
            # UPSERT, not INSERT OR REPLACE (bughunt 2026-07-03, HIGH): a duplicate/retried
            # 'running' PATCH (routine on the slow 9p/WSL host, where the bridge marks all
            # PATCHes retriable) re-ran this block. INSERT OR REPLACE DELETES the existing
            # row on the reused session_id, and foreign_keys=ON then CASCADE-dropped the
            # live terminal_sessions + terminal_events + pending terminal_controls — the
            # dashboard showed "Console not started" for a live PTY and queued keystrokes/
            # Stop were lost. ON CONFLICT DO UPDATE omits terminal_id/terminal_status so a
            # console bound between PATCHes survives (mirrors the resident path ~15051).
            await db.execute(
                """
                INSERT INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode,
                    owner_mode, owner_bridge_id, terminal_id, terminal_status, terminal_command, terminal_workspace,
                    process_id, session_handle,
                    app_server_url, spawn_spec_id, spawn_request_id, capabilities, telemetry, status,
                    started_at, last_seen, ended_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    runtime = excluded.runtime,
                    workspace = excluded.workspace,
                    mode = excluded.mode,
                    owner_mode = excluded.owner_mode,
                    owner_bridge_id = excluded.owner_bridge_id,
                    process_id = excluded.process_id,
                    session_handle = excluded.session_handle,
                    app_server_url = excluded.app_server_url,
                    capabilities = excluded.capabilities,
                    telemetry = excluded.telemetry,
                    status = 'running',
                    last_seen = excluded.last_seen,
                    ended_at = NULL
                """,
                (
                    session_id,
                    row["agent_id"],
                    row["environment_id"],
                    row["runtime"],
                    row["workspace"] or "",
                    row["mode"] or "managed-warm",
                    "managed",
                    req.bridgeId or row["claimed_by_bridge_id"] or "",
                    "",
                    "",
                    "",
                    "",
                    req.processId or "",
                    effective_session_handle,
                    "",
                    row["spawn_spec_id"],
                    row["id"],
                    json.dumps(req.capabilities or {"persistent": True, "bridgeResume": True}),
                    json.dumps(req.telemetry or {}),
                    "running",
                    started_at or now,
                    now,
                    None,
                ),
            )
