"""The agent console surface and virtual-terminal ensure.

v0.5.2m, one surface of the agents package. Built with `domain_router()`;
declares NO tags — the parent applies `tags=["api"]` once when api_v2 includes the package.
"""

from __future__ import annotations

from service.api_core.virtual_rpc import VIRTUAL_RPC_COMMANDS_BY_RUNTIME
from service.api_core.terminal_text import _ANSI_RE
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException, Query, Request

from service.api_core.routing import domain_router

logger = logging.getLogger("aify_comms.routers.agents.console")

# Imported for the ANNOTATIONS. Under postponed evaluation these are strings, so a
# missing one does not fail import -- FastAPI demotes the body to a query parameter and
# the endpoint 422s at request time. The route annotation gate caught 17 of these here.
from service.models import AgentConsoleInputRequest, VirtualTerminalEnsureRequest

from service.routers.agents.shared import (
    DEFAULT_SETTINGS,
    LIVE_SESSION_STATUSES,
    _SESSION_MODES,
    _adopt_live_resident_driver,
    _agent_liveness,
    _agent_record_to_dict,
    _agent_session_to_dict,
    _agent_tombstone,
    _append_dispatch_control,
    _append_dispatch_event,
    _append_terminal_control,
    _append_terminal_event,
    _apply_status_event,
    _auto_return_resident_to_managed_if_possible,
    _borrowed_console_tail_max_bytes,
    _borrowed_console_tail_max_lines,
    _borrowed_list_agents_refresh_limit,
    _borrowed_listen_events,
    _borrowed_live_session_statuses,
    _borrowed_manual_statuses,
    _borrowed_reap_triad_body_sentinel,
    _borrowed_runtime_config_live_keys,
    _borrowed_shell_placeholder_handle_re,
    _borrowed_terminal_end_statuses,
    _borrowed_windows_drive_cwd_re,
    _borrowed_wsl_drive_cwd_re,
    _broadcast_agent_status,
    _broadcast_engine_status,
    _clear_status_state_in_turn,
    _coldstart_refusal_message,
    _compute_agent_status,
    _compute_live_status_cache,
    _default_capabilities_for,
    _enforce_env_reachable_gate,
    _enforce_live_worker_gate,
    _environment_effective_status,
    _environment_record_to_dict,
    _fail_active_runs_for_superseded_bridges,
    _fresh_same_mode_bridge_conflict,
    _get_blocking_active_run,
    _get_dispatch_state_for_agent,
    _get_dispatch_state_map,
    _get_outbound_activity_map,
    _get_unread_count_map,
    _get_ws,
    _has_codex_live_app_server,
    _has_live_terminal_session,
    _has_pending_or_booting_spawn_request,
    _invalidate_agent_live_state,
    _is_lock_error,
    _iso_to_epoch,
    _json_loads_or,
    _live_state_get,
    _load_settings,
    _machine_family,
    _managed_owning_environment_row,
    _managed_via_wrapper_for_runtime,
    _merge_runtime_policy_for_wrapper_reregister,
    _normalize_machine_id,
    _normalize_runtime,
    _normalize_session_mode,
    _now,
    _record_bridge_registration,
    _record_channel_sidecar_heartbeat,
    _record_claimer_lease,
    _refresh_expired_agent_live_states,
    _remove_agent_record,
    _render_live_terminal_screen,
    _render_terminal_snapshot,
    _repair_unusable_active_runs,
    _request_stop_agent_terminals,
    _resolve_live_console_terminal,
    _resume_command_for,
    _row_status_note,
    _runtime_capability_for_environment,
    _runtime_handle_from_state,
    _runtime_state_replacing_handle,
    _runtime_state_with_handle,
    _sanitize_session_handle,
    _session_capabilities_replacing_handle,
    _session_handle_live_owner,
    _stop_virtual_terminals_for_superseded_bridges,
    _synth_terminal_should_be_created,
    _terminal_failure_line,
    _terminal_failure_tail,
    _terminal_session_to_dict,
    _timestamp_sort_key,
    _touch_current_agent_session,
    _upsert_resident_agent_session,
    _validate_registration_cwd,
    apply_event,
    derive,
    engine_status,
    get_db,
    logger,
    re,
    sqlite3,
    validate_name,
)
from service.terminal_write_queue import TERMINAL_OUTPUT_WRITES
from service.api_core.workspace import _workspace_for_environment
from service.api_core.terminal_ownership import _active_terminal_for_agent
from service.api_core.dispatch_start import (
    _coldstart_spawn_request_for_dispatch,
    _ensure_managed_pty_for_dispatch,
)

router = domain_router()


@router.post("/agents/{agent_id}/virtual-terminal/ensure")
async def ensure_virtual_terminal(agent_id: str, req: VirtualTerminalEnsureRequest, request: Request):
    """Bridge-driven creation of a synthesized terminal_session row.

    Managed pi runs use a persistent `omp --mode rpc` child whose AgentSessionEvent
    stream is synthesized by the bridge into a human-readable terminal_output
    feed. There is no real PTY — the bridge owns the lifecycle. This endpoint is
    idempotent: a second call for the same agent on the same bridge returns the
    existing virtual terminal row. See docs/plans/pi-persistent-rpc.md.
    """
    db = await get_db()
    try:
        agent = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        bridge_id = str(req.bridgeId or "").strip()
        if not bridge_id:
            raise HTTPException(400, "bridgeId is required")
        runtime = _normalize_runtime(req.runtime or agent["runtime"] or "pi")
        virtual_command = VIRTUAL_RPC_COMMANDS_BY_RUNTIME.get(runtime)
        if not virtual_command:
            raise HTTPException(
                409,
                f'Virtual terminal is available for runtimes {sorted(VIRTUAL_RPC_COMMANDS_BY_RUNTIME)} only (got runtime="{runtime}")',
            )

        env_row = await (await db.execute(
            "SELECT * FROM environments WHERE bridge_id = ? ORDER BY last_seen DESC LIMIT 1",
            (bridge_id,),
        )).fetchone()
        if not env_row:
            raise HTTPException(404, f'No environment registered for bridgeId "{bridge_id}"')
        environment_id = env_row["id"]

        session_row = await (await db.execute(
            """
            SELECT *
            FROM agent_sessions
            WHERE agent_id = ?
              AND environment_id = ?
              AND status IN ('running', 'recovering', 'starting', 'managed-warm')
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (agent_id, environment_id),
        )).fetchone()
        if not session_row:
            raise HTTPException(
                409,
                f'No active agent_session for "{agent_id}" on environment "{environment_id}". '
                f'The bridge should dispatch at least once before requesting a virtual terminal.',
            )
        session_id = session_row["id"]

        # Agent-scoped lookup: one virtual terminal per agent across all of
        # its agent_sessions. If a prior session created the row and is now
        # stale, re-anchor the terminal's session_id (and the new session's
        # terminal_id pointer) to the requesting session so the
        # CASCADE-on-delete FK keeps the row alive once the original
        # session row is eventually cleaned up.
        existing = await (await db.execute(
            """
            SELECT *
            FROM terminal_sessions
            WHERE agent_id = ?
              AND command = ?
              AND status NOT IN ('stopped', 'failed')
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (agent_id, virtual_command),
        )).fetchone()
        if existing:
            existing_session_id = existing["session_id"]
            if existing_session_id != session_id:
                rebind_now = _now()
                await db.execute(
                    """
                    UPDATE terminal_sessions
                    SET session_id = ?,
                        bridge_id = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (session_id, bridge_id, rebind_now, existing["id"]),
                )
                # Detach the prior session from the terminal but keep its
                # historical record otherwise intact.
                await db.execute(
                    """
                    UPDATE agent_sessions
                    SET terminal_id = '',
                        terminal_status = '',
                        terminal_command = ''
                    WHERE id = ? AND terminal_id = ?
                    """,
                    (existing_session_id, existing["id"]),
                )
                # Point the new active session at the terminal.
                await db.execute(
                    """
                    UPDATE agent_sessions
                    SET terminal_id = ?,
                        terminal_status = 'running',
                        terminal_command = ?,
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (existing["id"], virtual_command, rebind_now, session_id),
                )
                await _append_terminal_event(
                    db,
                    existing["id"],
                    "virtual_pi_rpc_reanchored",
                    json.dumps({
                        "fromSessionId": existing_session_id,
                        "toSessionId": session_id,
                        "bridgeId": bridge_id,
                    }),
                )
                await db.commit()
                existing = await (await db.execute(
                    "SELECT * FROM terminal_sessions WHERE id = ?",
                    (existing["id"],),
                )).fetchone()
                session_row = await (await db.execute(
                    "SELECT * FROM agent_sessions WHERE id = ?",
                    (session_id,),
                )).fetchone()
            return {
                "ok": True,
                "terminal": _terminal_session_to_dict(existing),
                "session": _agent_session_to_dict(session_row),
                "reused": True,
            }

        # Plan 4 (2026-05-25) synth-terminal deprecation: when this runtime
        # routes through a *-aify wrapper PTY, the wrapper IS the terminal —
        # don't create a synth row in parallel. Reuse of a pre-existing synth
        # row (handled above) is still allowed for backwards compatibility
        # and for the hard-failure fallback path that may seed one explicitly.
        settings_for_synth_gate = await _load_settings(db)
        if not _synth_terminal_should_be_created(runtime, settings_for_synth_gate):
            raise HTTPException(
                409,
                f'Synth terminal creation skipped for wrapper-backed runtime "{runtime}" '
                f'(Plan 4 deprecation — the wrapper PTY is the terminal).',
            )

        workspace = str(req.workspace or session_row["workspace"] or "").strip()
        terminal_id = f"vterm_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        now = _now()
        requested_by = str(req.requestedBy or "bridge-rpc").strip() or "bridge-rpc"
        await db.execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime, workspace, command,
                output, status, requested_by, created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                terminal_id,
                session_id,
                agent_id,
                environment_id,
                bridge_id,
                runtime,
                workspace,
                virtual_command,
                "",
                "running",
                requested_by,
                now,
                now,
                None,
                "",
            ),
        )
        await _append_terminal_event(
            db,
            terminal_id,
            f"virtual_{runtime}_rpc_attached",
            json.dumps({
                "requestedBy": requested_by,
                "sessionId": session_id,
                "bridgeId": bridge_id,
                "sessionHandle": req.sessionHandle or "",
            }),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET terminal_id = ?,
                terminal_status = 'running',
                terminal_command = ?,
                terminal_workspace = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (terminal_id, virtual_command, workspace, now, session_id),
        )
        next_runtime_state = _json_loads_or(agent["runtime_state"], {}) or {}
        next_runtime_state["virtualTerminal"] = True
        next_runtime_state["virtualTerminalId"] = terminal_id
        await db.execute(
            """
            UPDATE agents
            SET runtime_state = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (json.dumps(next_runtime_state), now, agent_id),
        )
        # The agent now has a live worker (virtualTerminalId + terminal_status
        # running). Invalidate the live-status cache so it recomputes to online
        # immediately instead of lying `available` until the 60s sweep.
        await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        updated_session = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast(
                "terminal_started",
                {
                    "terminalId": terminal_id,
                    "sessionId": session_id,
                    "agentId": agent_id,
                    "virtual": True,
                },
            )
        return {
            "ok": True,
            "terminal": _terminal_session_to_dict(terminal),
            "session": _agent_session_to_dict(updated_session),
            "reused": False,
        }
    finally:
        await db.close()


@router.get("/agents/{agent_id}/console")
async def get_agent_console(agent_id: str, lines: int = 40):
    """Read the tail of an agent's console output, live or LAST RECORDED (read-only).

    Side-effect-free: never starts a worker. Resolves the agent's live console
    terminal via runtime_state; if none, falls back to the most recent terminal the
    agent had and returns its RECORDED tail with live=false, historical=true.

    Why the fallback exists (v0.2 WS-1). Until v0.2 this returned only "no live
    console", which made a DEAD worker's output — the case that matters most —
    unreachable by the agent that needed it. On 2026-08-07 the cause of a failed
    managed hermes launch sat in `terminal_sessions.output` for 2.5 hours while the
    requesting agent was told "No online environment can host managed hermes", and
    the operator had to relay the real error to a human. The bytes were never
    missing; nothing would serve them.
    """
    db = await get_db()
    try:
        terminal = await _resolve_live_console_terminal(db, agent_id)
        if not terminal:
            agent_row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))).fetchone()
            if not agent_row:
                raise HTTPException(404, f"Agent '{agent_id}' not found")
            # Most recent terminal this agent had, whatever its state. Ordered by
            # created_at (not updated_at) so a late touch on an old row cannot
            # outrank the genuinely newest attempt.
            past = await (await db.execute(
                """
                SELECT id, status, output, error, stopped_at, updated_at, command
                FROM terminal_sessions
                WHERE agent_id = ?
                ORDER BY datetime(COALESCE(NULLIF(created_at, ''), '1970-01-01')) DESC, rowid DESC
                LIMIT 1
                """,
                (agent_id,),
            )).fetchone()
            recorded = str((past["output"] if past is not None else "") or "")
            if past is not None and (recorded.strip() or str(past["error"] or "").strip()):
                tail_lines = max(1, min(int(lines or 40), _borrowed_console_tail_max_lines()))
                output = _terminal_failure_tail(recorded, max_lines=tail_lines)
                cause = _terminal_failure_line(recorded) or str(past["error"] or "").strip()
                died_at = str(past["stopped_at"] or past["updated_at"] or "")
                status = str(past["status"] or "").strip().lower() or "unknown"
                return {
                    "ok": True,
                    "live": False,
                    "historical": True,
                    "terminalId": str(past["id"] or ""),
                    "status": status,
                    "stoppedAt": died_at,
                    "command": str(past["command"] or ""),
                    "failureLine": cause,
                    "lines": len(output.splitlines()) if output else 0,
                    "output": output,
                    "message": (
                        f"{agent_id} has NO live console. This is the last recorded output of "
                        f"terminal {past['id']} ({status}"
                        + (f" at {died_at}" if died_at else "")
                        + "), not a running session."
                    ),
                }
            return {
                "ok": True,
                "live": False,
                "historical": False,
                "message": f"{agent_id} has no live console (it lazy-starts on a message).",
            }
        # Drain any buffered output for this terminal so the tail is current,
        # then re-read the row to pick up the flushed bytes.
        await TERMINAL_OUTPUT_WRITES.flush_terminal(terminal["id"])
        terminal = await (
            await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal["id"],))
        ).fetchone()
        tail_lines = max(1, min(int(lines or 40), _borrowed_console_tail_max_lines()))
        keys = terminal.keys()
        full_output = (terminal["output"] if "output" in keys else "") or ""
        screen_output = full_output
        terminal_id = str(terminal["id"] or "")
        if terminal_id and not terminal_id.startswith("vterm_"):
            try:
                live = _render_live_terminal_screen(terminal_id)
                if live:
                    screen_output = live[0]
                elif "\x1b" in full_output:
                    screen_output = await asyncio.to_thread(
                        _render_terminal_snapshot,
                        full_output,
                        int(terminal["cols"] or 0) if "cols" in keys else 100,
                        int(terminal["rows"] or 0) if "rows" in keys else 40,
                    )
            except Exception:
                screen_output = full_output
        clean = _ANSI_RE.sub("", screen_output)
        clean = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", clean)
        screen_lines = clean.splitlines()
        while screen_lines and not screen_lines[-1].strip():
            screen_lines.pop()
        selected = screen_lines[-tail_lines:]
        output = "\n".join(selected)
        if len(output.encode("utf-8", "ignore")) > _borrowed_console_tail_max_bytes():
            output = output.encode("utf-8", "ignore")[-_borrowed_console_tail_max_bytes():].decode("utf-8", "ignore")
        return {
            "ok": True,
            "live": True,
            "historical": False,
            "terminalId": terminal["id"],
            "status": terminal["status"] or "",
            "lines": len(selected),
            "output": output,
        }
    finally:
        await db.close()


@router.post("/agents/{agent_id}/console/input")
async def post_agent_console_input(agent_id: str, req: AgentConsoleInputRequest, request: Request):
    """Send input (keystrokes/text) into an agent's live console. Audited.

    SAFETY: the caller (`from`) must be a registered agent; the input is
    recorded against that caller in both the terminal control's requested_by
    and an `agent_console_input` audit event. Callers can only target the
    agent's own resolved console terminal — never an arbitrary terminal id.
    Managed agents only (v1). This explicit recovery/control path is intentionally
    independent of `insert_messages_via_console`, which gates only legacy automatic
    message delivery through a PTY. Disabling that legacy path must never disable
    deliberate console control.
    """
    db = await get_db()
    try:
        agent_row = await (await db.execute("SELECT id, runtime, session_mode FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent_row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        caller = str(req.from_ or "").strip()
        if not caller:
            raise HTTPException(400, "console input requires a `from` caller (the requesting agent id)")
        caller_row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (caller,))).fetchone()
        if not caller_row:
            raise HTTPException(403, f"caller '{caller}' is not a registered agent")

        settings = await _load_settings(db)
        terminal = await _resolve_live_console_terminal(db, agent_id)
        if not terminal:
            # Best-effort lazy-autostart the SAME way dispatch does so the
            # visible-TUI requirement is preserved. If nothing can be started
            # (no live session / offline env), return the clear message.
            started = await _ensure_managed_pty_for_dispatch(
                db,
                agent_id,
                runtime=str(agent_row["runtime"] or ""),
                settings=settings,
                requested_by=caller,
            )
            await db.commit()
            if not started:
                return {
                    "ok": False,
                    "live": False,
                    "message": f"{agent_id} has no live console; send a message to start it first.",
                }
            # Re-resolve via runtime_state (autostart publishes the pointer).
            terminal = await _resolve_live_console_terminal(db, agent_id)
            if not terminal:
                # The freshly-started terminal row exists but its runtime_state
                # pointer may not be the consoleTerminal shape yet (e.g. the
                # `starting` row from _ensure_managed_pty_for_dispatch). Use it
                # directly — it is agent-scoped (the helper only returns this
                # agent's own session terminal).
                started_id = started["terminal_id"] if "terminal_id" in started.keys() else started["id"]
                terminal = await (
                    await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (started_id,))
                ).fetchone()
            if not terminal:
                return {
                    "ok": False,
                    "live": False,
                    "message": f"{agent_id} has no live console; send a message to start it first.",
                }

        text = str(req.text or "")
        body = text + ("\r" if (req.enter is None or req.enter) else "")
        control_id = await _append_terminal_control(
            db,
            terminal_id=terminal["id"],
            environment_id=terminal["environment_id"],
            bridge_id=terminal["bridge_id"] or "",
            action="input",
            requested_by=caller,
            body=body,
        )
        await _append_terminal_event(
            db,
            terminal["id"],
            "agent_console_input",
            json.dumps({"from": caller, "controlId": control_id, "bytes": len(body)}),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_control_requested", {"terminalId": terminal["id"], "action": "input"})
        # HONESTY (C8, 2026-07-26). `ok` here means the control was QUEUED — nothing more. It was
        # being read as "the input landed and the runtime acted on it", and that is not a claim this
        # endpoint can make: an operator's sc-manager issued two text writes and three bare-Enter
        # retries against a stuck managed-claude draft, ALL of which reached status='completed' with
        # handled_at set, while the draft never submitted. A completed control proves only that the
        # bridge wrote the bytes to the PTY; whether the TUI consumed them as a keypress is
        # unobservable from here. The tool lied to an AGENT, which then burned ~15 minutes of
        # critical path retrying a lever that could not work.
        #
        # Deliberately NOT verified here. Two reasons, and the second is why there is no byte-diff
        # affordance either:
        #   * confirming would mean waiting for the bridge's poll cycle inside this handler, and the
        #     service is single-worker by hard constraint (_LIVE_STATE_CACHE) — a blocking wait would
        #     stall every other request;
        #   * a tail diff CANNOT prove submission (review finding on `35cc646`). A managed claude
        #     repaints its spinner and footer continuously, so the console output changes constantly
        #     whether or not the keystroke was consumed. An earlier cut of this returned
        #     `consoleBytesBefore` for the caller to diff; that was a misleading affordance dressed
        #     as evidence, so it is gone rather than documented.
        # What is left is the honest shape: say it is queued, say submission is unknown, and point at
        # the only thing that actually settles it — a human or agent READING the console and judging
        # whether the draft is still sitting at the prompt.
        return {
            "ok": True,
            "live": True,
            "terminalId": terminal["id"],
            "controlId": control_id,
            "queued": True,
            # Tri-state on purpose: not False (no failure was observed) and not True (no submit was
            # observed). Unknown is the only defensible value, and it is not knowable from here.
            "submitted": None,
            "note": (
                "QUEUED, not confirmed. A completed control proves only that the bytes were "
                "written to the PTY — not that the runtime acted on them. Nothing this endpoint "
                "returns can confirm a submit; read the console with comms_console_tail and judge "
                "whether the draft is still at the prompt."
            ),
        }
    finally:
        await db.close()
