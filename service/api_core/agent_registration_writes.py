"""Everything `register_agent` writes, and nothing else.

Split out of `service/api_core/agent_sessions.py` in v0.5.4, byte-identical. That module had been the
landing site for six extractions in this series and reached 832 lines — it was becoming the next
grab-bag, which is moving debt rather than reducing it.

THE LINE IS DRAWN BY CALLER, not by taste. Every function here has exactly ONE caller and it is the
same one: `register_agent`. Everything left in `agent_sessions.py` is either shared across the routers
(`_touch_agent`, `_agent_tombstone`, `_touch_current_agent_session` and the rest have four to eleven
callers each) or belongs to session CONTROL rather than registration.

None of these five calls another, and none needs anything that stayed behind — measured before the
split, which is what made it a clean cut rather than a rearrangement.
"""
from __future__ import annotations

import json

from service.api_core.bridge_registration import _record_bridge_registration
from service.api_core.runtime import _normalize_launch_mode, _normalize_session_mode
from service.api_core.runtime_state import _runtime_state_with_handle
from service.api_core.serialization import _json_loads_or
from service.api_core.ws import _get_ws
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state

async def _record_registered_session_handle(db, req, normalized_runtime, runtime_config, session_handle, now) -> None:
    """Pin the freshly-registered session handle onto the agent's CURRENT session row.

    v0.5.4, extracted verbatim out of the 622-line `register_agent`. It belongs here because "which
    agent_sessions row is current, and who owns a handle" is this module's stated subject; the route was
    only its caller.

    WHY THE UPDATE TARGETS A SUBQUERY: an agent can have several session rows for one runtime (a
    process-per-boot history), so this pins the handle to the MOST RECENTLY SEEN row rather than to all of
    them. Writing every row would make a stale boot look resumable.

    THE `CASE WHEN ... = '{}'` GUARDS ARE DELIBERATE: capabilities and telemetry are only seeded when the
    row has none. A registration must not clobber richer values a live session already reported.

    THE SQL LITERAL IS INDENTED ONE LEVEL DEEPER THAN ITS SURROUNDING CODE and must not be tidied. The
    interior lines of a triple-quoted string are DATA: dedenting them on the way out of the route would
    change the constant's value while leaving the code correct. `tokenize` identified them and only the
    code lines moved. This exact mistake failed the inline-back proof once already.

    WRITES: left UNCOMMITTED for the caller's transaction, per the DB-leaf rule.
    """
    if session_handle:
        app_server_url = ""
        if isinstance(runtime_config, dict):
            app_server_url = str(runtime_config.get("appServerUrl") or "").strip()
        session_runtime_state = _runtime_state_with_handle(normalized_runtime, {}, session_handle)
        await db.execute(
            """
                UPDATE agent_sessions
                SET session_handle = ?,
                    app_server_url = CASE WHEN ? != '' THEN ? ELSE app_server_url END,
                    last_seen = ?,
                    capabilities = CASE
                        WHEN COALESCE(NULLIF(capabilities, ''), '{}') = '{}' THEN ?
                        ELSE capabilities
                    END,
                    telemetry = CASE
                        WHEN COALESCE(NULLIF(telemetry, ''), '{}') = '{}' THEN ?
                        ELSE telemetry
                    END
                WHERE id = (
                    SELECT id
                    FROM agent_sessions
                    WHERE agent_id = ?
                      AND runtime = ?
                    ORDER BY last_seen DESC
                    LIMIT 1
                )
                """,
            (
                session_handle,
                app_server_url,
                app_server_url,
                now,
                json.dumps({"persistent": True, "nativeResume": True, "bridgeResume": True, "cliAttach": True}),
                json.dumps({"registeredHandle": session_runtime_state}),
                req.agentId,
                normalized_runtime,
            ),
        )

async def _adopt_console_terminal_on_register(db, req, console_terminal, terminal_id: str,
                                              normalized_runtime: str, session_handle: str,
                                              resolved_cwd: str, next_state, existing_capabilities,
                                              existing_runtime_config, now: str) -> None:
            """Write a live console PTY into the agent and its session rows, as one act.

            Extracted from `register_agent` in v0.5.4;
            `test_register_agent_split_is_inert.py` inlines it back and AST-compares against the
            pre-split fixture. Body left at its original 12-space column so the two multi-line SQL
            literals inside are preserved byte-for-byte — the gate compares ASTs and refuses a
            re-indent that rewrites a query string.

            TWO UPDATES, ONE MEANING, which is why they are extracted together rather than separately:
            a console PTY attaching IS the authoritative "backing (re)started" event, so the agent row
            learns about the terminal and the session row is promoted out of a dead denorm state in the
            same breath. Splitting them would invite a later change to one without the other, and a
            session left in a dead state with a live PTY reads to the dashboard as a console that
            cannot be typed into.

            Everything it needs is passed in; it calls nothing.
            """
            await db.execute(
                """
                UPDATE agents
                SET role = ?,
                    name = ?,
                    cwd = ?,
                    runtime = ?,
                    machine_id = ?,
                    session_handle = CASE WHEN ? != '' THEN ? ELSE session_handle END,
                    capabilities = ?,
                    runtime_config = ?,
                    runtime_state = ?,
                    status = CASE WHEN status = 'stopped' THEN status ELSE 'active' END,
                    status_note = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    req.role,
                    req.name or req.agentId,
                    resolved_cwd,
                    normalized_runtime,
                    req.machineId or "",
                    session_handle,
                    session_handle,
                    existing_capabilities,
                    existing_runtime_config,
                    json.dumps(next_state),
                    "Dashboard Console PTY attached.",
                    now,
                    req.agentId,
                ),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET owner_mode = 'console',
                    owner_bridge_id = ?,
                    terminal_id = ?,
                    terminal_status = ?,
                    session_handle = CASE WHEN ? != '' THEN ? ELSE session_handle END,
                    -- A live console PTY attaching IS the authoritative "backing (re)started"
                    -- event: promote a dead-state denorm back to running, else the session row
                    -- stays 'stopped' from the PREVIOUS backing's death and the Console label
                    -- reads "Console stopped" for a live attached terminal forever (cms-manager,
                    -- 2026-06-10 — the display deriver deliberately never promotes, so the bind
                    -- moment must). Operator disable is enforced on agents.status, not here.
                    status = CASE WHEN status IN ('cli-takeover','stopped','ended','failed','lost','cancelled','completed')
                                  THEN 'running' ELSE status END,
                    ended_at = CASE WHEN status IN ('cli-takeover','stopped','ended','failed','lost','cancelled','completed')
                                    THEN NULL ELSE ended_at END,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    console_terminal["bridge_id"] or "",
                    terminal_id,
                    console_terminal["status"] or "attached",
                    session_handle,
                    session_handle,
                    now,
                    console_terminal["session_id"],
                ),
            )

async def _upsert_registered_agent_row(db, req, row, normalized_runtime: str, normalized_session_mode: str,
                                       session_handle: str, resolved_cwd: str, description_value,
                                       model_value, capabilities, runtime_config, existing_state,
                                       bridge_id: str, now: str) -> None:
        """Write the agent row a registration produces — INSERT, or UPDATE if it already exists.

        Extracted from `register_agent` in v0.5.4; `test_register_agent_split_is_inert.py` inlines it
        back and AST-compares against the pre-split fixture. Body left at its original 8-space column so
        the one large SQL literal inside is preserved byte-for-byte.

        THE `ON CONFLICT` HALF IS THE WHOLE POINT. Registration is idempotent by design — an agent
        re-registers on every boot — so this is a single statement rather than a read-then-branch, which
        would race two boots of the same agent against each other. Which columns the conflict path
        updates is therefore a behavioural decision per column, not a formality: anything listed here is
        reset by a re-registration, and anything omitted survives one.

        It calls nothing; every value is a parameter.
        """
        await db.execute(
            """
            INSERT INTO agents (
                id, role, name, cwd, model, description, instructions, status, status_note, runtime, machine_id,
                launch_mode, session_mode, session_handle, managed_by, capabilities,
                runtime_config, runtime_state, driver_state, registered_at, last_seen
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                role = excluded.role,
                name = excluded.name,
                cwd = excluded.cwd,
                model = excluded.model,
                description = excluded.description,
                instructions = excluded.instructions,
                status = excluded.status,
                status_note = excluded.status_note,
                runtime = excluded.runtime,
                machine_id = excluded.machine_id,
                launch_mode = excluded.launch_mode,
                session_mode = excluded.session_mode,
                session_handle = excluded.session_handle,
                managed_by = excluded.managed_by,
                capabilities = excluded.capabilities,
                runtime_config = excluded.runtime_config,
                runtime_state = excluded.runtime_state,
                driver_state = excluded.driver_state,
                last_seen = excluded.last_seen
            """,
            (
                req.agentId, req.role, req.name or req.agentId, resolved_cwd, model_value,
                # Folded for the same reason as `launch_mode` two lines down: `agents.status`
                # is compared against lowercase literals by readers that do not all fold.
                description_value, req.instructions or "", str(req.status or "idle").strip().lower(),
                (row["status_note"] if row and "status_note" in row.keys() else "") or "",
                normalized_runtime,
                # NORMALISED, like `runtime` and `session_mode` beside it. Stored verbatim, a
                # `launchMode` of "None" made every `== "none"` reader miss the STOP marker.
                req.machineId or "", _normalize_launch_mode(req.launchMode),
                normalized_session_mode, session_handle, req.managedBy or "",
                json.dumps(capabilities or []), json.dumps(runtime_config),
                existing_state,
                # One-driver FSM: an attaching process carrying a bridge_id is a
                # live driver for this session -> mark driving. A metadata-only
                # (re)register without a bridge keeps the prior driver_state.
                ("driving" if bridge_id else (str((row["driver_state"] if row and "driver_state" in row.keys() else "") or "idle"))),
                row["registered_at"] if row and row["registered_at"] else now, now
            )
        )


async def _register_via_adopted_console_terminal(
    db, req, request, row, console_terminal, terminal_id,
    bridge_id, normalized_runtime, session_handle, resolved_cwd, capabilities, runtime_config, now,
):
    """The register path where an existing console terminal is ADOPTED instead of a new session.

    Extracted from `register_agent` in v0.5.4, byte-identical apart from the dedent. It is an
    early-exit branch: it ends in the response the handler returns, so the caller is
    `return await ...` rather than a bare call. That shape was REFUSED by
    `service/tests/extract_method.py` until the call-site-shape rule landed, which is why 51
    lines sat in the handler with no way to prove moving them was inert.
    """
    existing_mode = _normalize_session_mode((row["session_mode"] if row else "") or "managed")
    existing_state = _json_loads_or((row["runtime_state"] if row else "") or "{}", {})
    existing_capabilities = (row["capabilities"] if row and "capabilities" in row.keys() else "") or json.dumps(capabilities or [])
    existing_runtime_config = (row["runtime_config"] if row and "runtime_config" in row.keys() else "") or json.dumps(runtime_config)
    next_state = _runtime_state_with_handle(normalized_runtime, existing_state, session_handle)
    next_state["consoleTerminal"] = {
        "terminalId": terminal_id,
        "bridgeId": bridge_id,
        "sessionHandle": session_handle,
        "at": now,
    }
    await _adopt_console_terminal_on_register(
        db, req, console_terminal, terminal_id, normalized_runtime, session_handle,
        resolved_cwd, next_state, existing_capabilities, existing_runtime_config, now,
    )
    if bridge_id:
        await _record_bridge_registration(
            db,
            bridge_id=bridge_id,
            agent_id=req.agentId,
            machine_id=req.machineId or "",
            runtime=normalized_runtime,
            session_mode="managed",
            session_handle=session_handle,
            terminal_id=terminal_id,
            now=now,
        )
    await _invalidate_agent_live_state(db, req.agentId)
    await db.commit()
    ws = await _get_ws(request)
    if ws:
        await ws.broadcast("agent_registered", {
            "agentId": req.agentId,
            "role": req.role,
            "runtime": normalized_runtime,
            "machineId": req.machineId or "",
            "sessionMode": existing_mode,
            "ownershipTransition": "console_terminal_attached",
        })
    return {
        "ok": True,
        "agentId": req.agentId,
        "role": req.role,
        "status": str(req.status or "idle").strip().lower(),
        "runtime": normalized_runtime,
        "machineId": req.machineId or "",
        "bridgeId": bridge_id,
        "sessionMode": existing_mode,
        "ownershipTransition": "console_terminal_attached",
    }
