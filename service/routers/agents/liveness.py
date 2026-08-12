"""Liveness and turn signals: heartbeat, turn start/end, ready, status events, leases.

v0.5.2m, one surface of the agents package. Built with `domain_router()`;
declares NO tags — the parent applies `tags=["api"]` once when api_v2 includes the package.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import BaseModel

from fastapi import HTTPException, Query, Request

from service.api_core.routing import domain_router

logger = logging.getLogger("aify_comms.routers.agents.liveness")

# Imported for the ANNOTATIONS. Under postponed evaluation these are strings, so a
# missing one does not fail import -- FastAPI demotes the body to a query parameter and
# the endpoint 422s at request time. The route annotation gate caught 17 of these here.
from service.models import AgentReadyUpdate

from service.routers.agents.shared import DEFAULT_SETTINGS, LIVE_SESSION_STATUSES, _SESSION_MODES, _active_terminal_for_agent, _adopt_live_resident_driver, _agent_liveness, _agent_record_to_dict, _agent_session_to_dict, _agent_tombstone, _append_dispatch_control, _append_dispatch_event, _append_terminal_control, _append_terminal_event, _apply_status_event, _auto_return_resident_to_managed_if_possible, _borrowed_console_tail_max_bytes, _borrowed_console_tail_max_lines, _borrowed_list_agents_refresh_limit, _borrowed_listen_events, _borrowed_live_session_statuses, _borrowed_manual_statuses, _borrowed_reap_triad_body_sentinel, _borrowed_runtime_config_live_keys, _borrowed_shell_placeholder_handle_re, _borrowed_terminal_end_statuses, _borrowed_windows_drive_cwd_re, _borrowed_wsl_drive_cwd_re, _broadcast_agent_status, _broadcast_engine_status, _clear_status_state_in_turn, _coldstart_refusal_message, _coldstart_spawn_request_for_dispatch, _compute_agent_status, _compute_live_status_cache, _default_capabilities_for, _enforce_env_reachable_gate, _enforce_live_worker_gate, _ensure_managed_pty_for_dispatch, _environment_effective_status, _environment_record_to_dict, _fail_active_runs_for_superseded_bridges, _fresh_same_mode_bridge_conflict, _get_blocking_active_run, _get_dispatch_state_for_agent, _get_dispatch_state_map, _get_outbound_activity_map, _get_unread_count_map, _get_ws, _has_codex_live_app_server, _has_live_terminal_session, _has_pending_or_booting_spawn_request, _invalidate_agent_live_state, _is_lock_error, _iso_to_epoch, _json_loads_or, _live_state_get, _load_settings, _machine_family, _managed_owning_environment_row, _managed_via_wrapper_for_runtime, _merge_runtime_policy_for_wrapper_reregister, _normalize_machine_id, _normalize_runtime, _normalize_session_mode, _now, _record_bridge_registration, _record_channel_sidecar_heartbeat, _record_claimer_lease, _refresh_expired_agent_live_states, _remove_agent_record, _render_live_terminal_screen, _render_terminal_snapshot, _repair_unusable_active_runs, _request_stop_agent_terminals, _resolve_live_console_terminal, _resume_command_for, _row_status_note, _runtime_capability_for_environment, _runtime_handle_from_state, _runtime_state_replacing_handle, _runtime_state_with_handle, _sanitize_session_handle, _session_capabilities_replacing_handle, _session_handle_live_owner, _stop_virtual_terminals_for_superseded_bridges, _synth_terminal_should_be_created, _terminal_failure_line, _terminal_failure_tail, _terminal_session_to_dict, _timestamp_sort_key, _touch_current_agent_session, _upsert_resident_agent_session, _validate_registration_cwd, _workspace_for_environment, apply_event, derive, engine_status, get_db, logger, re, sqlite3, validate_name

# Domain-local MODEL: defined in api_v2 rather than models.py, and its only user is the handler
# below. It moves with the handler instead of becoming a cross-module import.
class AgentStatusEventRequest(BaseModel):
    kind: str
    runId: str | None = None
    bridgeId: str | None = None
    detail: str | None = None


router = domain_router()


@router.patch("/agents/{agent_id}/ready")
async def update_agent_ready(agent_id: str, req: AgentReadyUpdate, request: Request):
    """Plan 4 task 12 (2026-05-25): bridge POSTs here when an adapter
    controller's start() has completed initial handshake. This stores an
    internal readiness bit; public idle-live status remains `online`.

    Upsert preserves any existing turn_busy/turn_run_id state — clearing
    ready does NOT also clear turn_busy and vice versa.
    """
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            tombstone = await _agent_tombstone(db, agent_id)
            if tombstone:
                raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        now = _now()
        ready_int = 1 if req.ready else 0
        # Upsert agent_turn_state: insert with ready, or update only ready
        # (and updated_at) on conflict — turn_busy and run/bridge/runtime
        # fields are owned by the dispatch path, not by this endpoint.
        await db.execute(
            """
            INSERT INTO agent_turn_state
                (agent_id, turn_busy, turn_run_id, turn_bridge_id,
                 turn_runtime, turn_updated_at, ready)
            VALUES (?, 0, '', '', '', ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                ready = excluded.ready,
                turn_updated_at = excluded.turn_updated_at
            """,
            (agent_id, now, ready_int),
        )
        await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast(
                "agent_ready",
                {"agentId": agent_id, "ready": bool(req.ready)},
            )
        return {"ok": True, "agentId": agent_id, "ready": bool(req.ready)}
    finally:
        await db.close()


@router.get("/agents/{agent_id}/last-read")
async def agent_last_read(agent_id: str, request: Request):
    """Get the last message this agent read — useful for checking if they've seen your message."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT m.*, r.read_at FROM read_receipts r JOIN messages m ON m.id = r.message_id WHERE r.agent_id = ? ORDER BY r.read_at DESC LIMIT 1",
            (agent_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return {"agentId": agent_id, "lastRead": None}
        return {"agentId": agent_id, "lastRead": {
            "messageId": row["id"], "from": row["from_agent"], "subject": row["subject"],
            "type": row["type"], "readAt": row["read_at"], "timestamp": row["timestamp"],
        }}
    finally:
        await db.close()


@router.post("/agents/{agent_id}/heartbeat")
async def agent_heartbeat(agent_id: str, request: Request):
    """Lightweight heartbeat — bridge poll loop calls this to signal liveness."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    bridge_id = str(body.get("bridgeId", "") or "").strip()
    terminal_id = str(body.get("terminalId", "") or "").strip()
    bridge_kind = str(body.get("bridgeKind", "") or "").strip().lower()
    now = _now()
    db = await get_db()
    try:
        tombstone = await _agent_tombstone(db, agent_id)
        if tombstone:
            raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
        # Mode FSM release signal (Task 4.1, 2026-05-30). Symmetric with the
        # claim path: a DISPLACED managed sidecar (bridgeKind="channel-sidecar")
        # pulsing turn_busy via heartbeat is told to RELEASE once the agent has
        # been switched to resident, so it stops driving even between claims.
        # driver_state guard (2026-05-31, sc-manager): see the claim-path comment.
        # A live resident driver (driver_state='driving') keeps its own delivery
        # sidecar; only a displaced managed driver (not 'driving') is released.
        if bridge_kind == "channel-sidecar":
            mode_row = await (await db.execute(
                "SELECT session_mode, driver_state FROM agents WHERE id = ?",
                (agent_id,),
            )).fetchone()
            if (
                mode_row
                and _normalize_session_mode(mode_row["session_mode"] or "resident") != "managed"
                and str((mode_row["driver_state"] if "driver_state" in mode_row.keys() else "") or "").strip().lower() != "driving"
            ):
                # Live resident bridge ⇒ this is the resident's OWN delivery sidecar,
                # not a displaced managed driver — adopt driving instead of releasing
                # (see _adopt_live_resident_driver).
                if await _adopt_live_resident_driver(db, agent_id):
                    await db.commit()
                else:
                    return {"ok": True, "release": True}
        if bridge_id:
            bridge_row = await (await db.execute(
                "SELECT superseded_by FROM bridge_instances WHERE id = ? AND agent_id = ?",
                (bridge_id, agent_id),
            )).fetchone()
            if bridge_row and str(bridge_row["superseded_by"] or "").strip():
                return {
                    "ok": False,
                    "ignored": True,
                    "reason": "bridge_superseded",
                    "supersededBy": str(bridge_row["superseded_by"] or "").strip(),
                }
        await db.execute(
            "UPDATE agents SET last_seen = ?, status = CASE WHEN status = 'stopped' THEN status ELSE 'active' END WHERE id = ?",
            (now, agent_id),
        )
        if bridge_id:
            if terminal_id:
                await db.execute(
                    "UPDATE bridge_instances SET last_seen = ?, terminal_id = ? WHERE id = ? AND agent_id = ?",
                    (now, terminal_id, bridge_id, agent_id),
                )
            else:
                await db.execute(
                    "UPDATE bridge_instances SET last_seen = ? WHERE id = ? AND agent_id = ?",
                    (now, bridge_id, agent_id),
                )
        # Unconditional liveness beat (Workstream A, 2026-06-01). A long-lived
        # bridge posts {bridgeId, bridgeKind, liveness:true} on a fixed interval
        # regardless of turn activity, so last_seen is a true "alive now" signal.
        # Unlike the plain UPDATE above (which no-ops when the bridge has no row
        # yet — e.g. an idle channel-sidecar that never claimed), this UPSERTS the
        # row, refreshing its current agent identity as well as last_seen +
        # bridge_kind. It never clears superseded_by and never touches turn
        # state. (A superseded existing row is already short-circuited by the
        # guard above.)
        if body.get("liveness") and bridge_id:
            arow = await (await db.execute(
                "SELECT machine_id, runtime, session_mode FROM agents WHERE id = ?", (agent_id,),
            )).fetchone()
            arow_machine = (arow["machine_id"] if arow else "") or ""
            arow_runtime = (arow["runtime"] if arow else "") or "generic"
            if bridge_kind == "channel-sidecar":
                await _record_channel_sidecar_heartbeat(
                    db,
                    bridge_id=bridge_id,
                    agent_id=agent_id,
                    machine_id=arow_machine,
                    runtime=arow_runtime,
                    session_mode=(arow["session_mode"] if arow else "") or "managed",
                    now=now,
                )
            else:
                # FIX SET B3 (2026-06-03): the 30s liveness beat from the host-side
                # bridge (server.js) posts bridgeKind="resident", but the SAME agent
                # may have a wrapper-child / channel-sidecar bridge row that registered
                # the authoritative managed kind. A plain COALESCE(NULLIF(?,''),...)
                # let that generic "resident" beat DEMOTE a 'managed-wrapper-child'
                # (or 'channel-sidecar') back to 'resident' — after which
                # _has_live_managed_wrapper_child / _has_live_channel_sidecar stop
                # matching and the managed agent loses its claimer (the lc-coder /
                # codex-managed strand). Guard: an incoming '' or 'resident' can NEVER
                # overwrite an existing 'managed-wrapper-child' or 'channel-sidecar';
                # any other incoming kind still COALESCE-wins as before.
                updated = await db.execute(
                    "UPDATE bridge_instances SET last_seen = ?, "
                    "bridge_kind = CASE "
                    "WHEN COALESCE(bridge_kind, '') IN ('managed-wrapper-child', 'channel-sidecar') "
                    "AND COALESCE(?, '') IN ('', 'resident') THEN bridge_kind "
                    "ELSE COALESCE(NULLIF(?, ''), bridge_kind) END "
                    "WHERE id = ? AND agent_id = ?",
                    (now, bridge_kind, bridge_kind, bridge_id, agent_id),
                )
                if not getattr(updated, "rowcount", 0):
                    await db.execute(
                        """
                        INSERT OR IGNORE INTO bridge_instances (
                            id, agent_id, machine_id, runtime, session_mode,
                            session_handle, terminal_id, bridge_kind,
                            registered_at, last_seen, superseded_by, superseded_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (bridge_id, agent_id,
                         _normalize_machine_id(arow_machine),
                         arow_runtime,
                         "managed", "", "", bridge_kind or "resident",
                         now, now, "", None),
                    )
                    await db.execute(
                        "UPDATE bridge_instances SET last_seen = ? WHERE id = ? AND agent_id = ?",
                        (now, bridge_id, agent_id),
                    )
        # Liveness recovery (audit 2026-06-28): a plain liveness beat (no turnBusy) doesn't flip
        # turn state, but it DOES prove the bridge is alive again. If the agent was cached
        # `offline`, drop that entry so the next read recomputes to available/online instead of
        # serving offline for the full ~180s horizon (the documented "recovery on any real event
        # is immediate" contract was violated — invalidation only ran on the turnBusy path).
        # Surgical: only the offline-cached case, so normal online agents keep their warm cache.
        if body.get("liveness"):
            _cached_live = _live_state_get(agent_id)
            if _cached_live and _cached_live.get("status") == "offline":
                await _invalidate_agent_live_state(db, agent_id)

        # Authoritative turn-busy signal (contract with the bridge). Missing
        # "turnBusy" → liveness only (old-bridge safe). turnBusy=true: latest
        # bridge wins. turnBusy=false: only the owning bridge+run may clear,
        # so a stale false from a superseded bridge/run cannot wipe a newer
        # active turn.
        turn_flip = False  # WS-1: did this heartbeat actually change turn_busy (working⇄ready)?
        if "turnBusy" in body:
            turn_busy = bool(body.get("turnBusy"))
            turn_run_id = str(body.get("turnRunId", "") or "").strip()
            turn_runtime = str(body.get("turnRuntime", "") or "").strip()
            _prev_row = await (await db.execute(
                "SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?", (agent_id,))).fetchone()
            _prev_busy = bool(_prev_row and _prev_row["turn_busy"])
            if turn_busy:
                await db.execute(
                    """
                    INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
                    VALUES (?, 1, ?, ?, ?, ?)
                    ON CONFLICT(agent_id) DO UPDATE SET
                        turn_busy = 1,
                        turn_run_id = excluded.turn_run_id,
                        turn_bridge_id = excluded.turn_bridge_id,
                        turn_runtime = excluded.turn_runtime,
                        turn_updated_at = excluded.turn_updated_at
                    """,
                    (agent_id, turn_run_id, bridge_id, turn_runtime, now),
                )
                turn_flip = not _prev_busy  # to-working transition
                # status v2 (Fix A, 2026-06-05): the /heartbeat turnBusy field is the
                # DOMINANT turn signal for MANAGED runtimes (hermes/codex/pi/opencode)
                # and claude channel-woken turns — the dispatch lifecycle pulses it,
                # but it only ever wrote agent_turn_state (OLD engine) and never fed
                # agent_status_state, so the `new` engine showed online/idle mid-turn.
                # Feed turn_start here too. Flag-agnostic at the write layer (only the
                # `new` read path consumes agent_status_state, so it is a no-op for
                # `old`); idempotent with any resident turn-start hook (turn_start just
                # sets in_turn=1). Mirrors the /turn-start endpoint's same pattern.
                await _apply_status_event(db, agent_id, {"kind": "turn_start", "runId": turn_run_id})
            else:
                cur = await (await db.execute(
                    "SELECT turn_bridge_id, turn_run_id FROM agent_turn_state WHERE agent_id = ?",
                    (agent_id,),
                )).fetchone()
                if cur:
                    stored_bridge = str(cur["turn_bridge_id"] or "").strip()
                    stored_run = str(cur["turn_run_id"] or "").strip()
                    if stored_bridge == bridge_id and (not stored_run or stored_run == turn_run_id):
                        await db.execute(
                            "UPDATE agent_turn_state SET turn_busy = 0, turn_updated_at = ? WHERE agent_id = ?",
                            (now, agent_id),
                        )
                        # status v2 (Fix A): clear in_turn ONLY inside the SAME
                        # ownership guard that gates the turn_busy=0 write, so a
                        # stale/superseded bridge or a non-owning run can never wipe
                        # a live turn's in_turn. Mirrors exactly the guard the
                        # turn_busy=0 write uses — never clears where the old code
                        # would not clear turn_busy.
                        await _apply_status_event(db, agent_id, {"kind": "turn_end", "runId": ""})
                        turn_flip = _prev_busy  # to-ready transition (only when we actually cleared)
            # A turn_busy flip changes derived status (working ⇄ idle). Invalidate
            # the live-state cache so the next read recomputes immediately, instead
            # of lagging up to the 60s reconcile sweep. Symmetric with the dedicated
            # /turn-start and /turn-end endpoints, which already invalidate.
            await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        # WS-1 (2026-06-17): the /heartbeat turnBusy field is the DOMINANT turn signal for
        # managed runtimes, but it only invalidated the cache — the dashboard still waited its
        # ~60s poll to see the flip. Push it immediately, but ONLY on an actual working⇄ready
        # flip (not every 3s liveness/refresh beat), flag-gated to keep `old` unchanged.
        if turn_flip:
            settings = await _load_settings(db)
            await _broadcast_engine_status(await _get_ws(request), db, agent_id, settings=settings)
        return {"ok": True}
    finally:
        await db.close()


@router.post("/agents/{agent_id}/claimer-lease")
async def post_claimer_lease(agent_id: str, request: Request):
    """WS5 Task 5.1 (2026-06-02): record a delivery-loop claimer lease.

    The managed sidecar-delivery loop (hermes-managed-host.js) POSTs
    {action: "acquire"} the moment it becomes a live claimer (gateway ok +
    heartbeat + first successful /dispatch/claim — the same point it writes the
    loop-ready marker) and {action: "release"} in its terminal teardown path.

    The lease is the positive deliverability signal that lets the send path tell
    a genuinely-deaf target (released/stale lease) apart from a healthy claimer
    that simply has not polled yet (no lease ever ⇒ fall back to lazy delivery).
    Best-effort/no-throw on the bridge side; tombstoned agents 410 so a removed
    agent's loop stops re-acquiring.
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    action = str(body.get("action", "") or "").strip().lower()
    bridge_id = str(body.get("bridgeId", "") or "").strip()
    if action not in {"acquire", "release"}:
        raise HTTPException(400, "action must be 'acquire' or 'release'")
    now = _now()
    db = await get_db()
    try:
        tombstone = await _agent_tombstone(db, agent_id)
        if tombstone:
            raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
        agent_row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent_row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        state = await _record_claimer_lease(db, agent_id, action=action, bridge_id=bridge_id, now=now)
        # A lease flip changes deliverability/derived status — invalidate the
        # live-state cache so the next read recomputes immediately.
        await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        return {"ok": True, "state": state}
    finally:
        await db.close()


@router.post("/agents/{agent_id}/status-event")
async def post_status_event(agent_id: str, req: AgentStatusEventRequest, request: Request):
    db = await get_db()
    try:
        row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        await _apply_status_event(db, agent_id, req.model_dump())
        await _invalidate_agent_live_state(db, agent_id)  # pops the in-memory live-status cache
        # The invalidate is an in-memory dict pop now (2026-06-18) — immediate, not tied to a
        # commit. The commit below persists _apply_status_event's turn-state write.
        await db.commit()
        # Push the transition immediately so the dashboard updates the instant a turn
        # starts/ends (proof-based engine is the only path).
        settings = await _load_settings(db)
        ws = await _get_ws(request)
        await _broadcast_engine_status(ws, db, agent_id, settings=settings)
        return {"ok": True, "agentId": agent_id, "kind": req.kind}
    finally:
        await db.close()


@router.post("/agents/{agent_id}/console-working")
async def agent_console_working(agent_id: str, request: Request):
    """Spinner-gated working lease from the managed-claude console PTY.

    The host bridge POSTs this while the claude TUI working footer
    ("esc to interrupt" / "<glyph> <verb> for <time>") is visible. It stamps a
    short TTL lease that is OR'd into derived `working` — additive, never clears
    turn_busy, self-expires when the spinner stops. This closes the
    "online while thinking" under-report the per-completed-message transcript
    cannot see. Idempotent best-effort.
    """
    now = _now()
    db = await get_db()
    try:
        agent_row = await (await db.execute(
            "SELECT id FROM agents WHERE id = ?", (agent_id,)
        )).fetchone()
        if not agent_row:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        try:
            body = await request.json()
        except Exception:
            body = {}
        subagents = bool(isinstance(body, dict) and body.get("subagents"))
        await db.execute(
            "INSERT INTO agent_console_signal (agent_id, working_at, subagents_at) VALUES (?, ?, ?) "
            "ON CONFLICT(agent_id) DO UPDATE SET working_at = excluded.working_at, "
            "subagents_at = CASE WHEN ? THEN excluded.working_at ELSE '' END",
            (agent_id, now, now if subagents else "", 1 if subagents else 0),
        )
        # Invalidate the in-memory live-status cache (a dict pop now, 2026-06-18 — immediate,
        # not tied to the commit) so the next read recomputes the spinner-driven to-working.
        await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        # Push the working lease immediately so the spinner-driven to-working shows without
        # the ~60s poll wait.
        settings = await _load_settings(db)
        await _broadcast_engine_status(await _get_ws(request), db, agent_id, settings=settings)
    finally:
        await db.close()
    return {"ok": True}


@router.post("/agents/{agent_id}/turn-start")
async def agent_turn_start(agent_id: str, request: Request):
    """Harness-level turn-START signal — symmetric counterpart to /turn-end.

    Called by per-runtime UserPromptSubmit hooks (claude-aify's
    UserPromptSubmit hook installed via install.sh) when the operator
    types a prompt directly into the resident CLI without going through
    aify-comms's dispatch path. Without this, channel-route dispatches
    correctly flip the agent to "working" but direct CLI typing leaves
    the status at "online" while the assistant is actually mid-turn —
    operator-asked 2026-05-22 to make the two surfaces symmetric.

    Idempotent: refreshes turn_updated_at on every call so the 120s
    server-side staleness window keeps resetting while the assistant
    works.
    """
    db = await get_db()
    try:
        tombstone = await _agent_tombstone(db, agent_id)
        if tombstone:
            raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
        agent_row = await (await db.execute(
            "SELECT id, runtime FROM agents WHERE id = ?", (agent_id,)
        )).fetchone()
        if not agent_row:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        now = _now()
        runtime = _normalize_runtime(agent_row["runtime"] or "claude-code")
        # If a managed dispatch is already in flight (turn_run_id set,
        # fresh, set by a real bridge), DON'T clobber the dispatch
        # context with our user-prompt-submit attribution. Just refresh
        # turn_updated_at so the existing run linkage keeps the
        # dashboard's "working on subject X" display intact.
        await db.execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 1, '', 'user-prompt-submit', ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                turn_busy = 1,
                turn_bridge_id = CASE
                    WHEN turn_busy = 1 AND COALESCE(turn_run_id, '') != ''
                         AND COALESCE(turn_bridge_id, '') NOT IN ('', 'user-prompt-submit')
                    THEN turn_bridge_id
                    ELSE 'user-prompt-submit'
                END,
                turn_runtime = excluded.turn_runtime,
                turn_updated_at = excluded.turn_updated_at
            """,
            (agent_id, runtime, now),
        )
        await db.execute(
            "UPDATE agents SET last_seen = ? WHERE id = ?",
            (now, agent_id),
        )
        # status v2: feed the event-driven engine from the SAME turn signal so the
        # `new` engine reflects working without a separate post. Flag-agnostic — only
        # the `new` read path reads agent_status_state, so this is a no-op for `old`.
        await _apply_status_event(db, agent_id, {"kind": "turn_start", "runId": ""})
        await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        # Push the transition immediately so the dashboard reflects to-working within a
        # second instead of waiting out its ~60s poll.
        settings = await _load_settings(db)
        await _broadcast_engine_status(await _get_ws(request), db, agent_id, settings=settings)
        return {"ok": True, "agentId": agent_id}
    finally:
        await db.close()


@router.post("/agents/{agent_id}/turn-end")
async def agent_turn_end(agent_id: str, request: Request):
    """Harness-level turn-end signal.

    Called by per-runtime Stop hooks (claude-aify's Stop hook, hermes's
    post_tool_call hook variant, etc.) when the agent has finished its
    current turn at the HARNESS level — i.e., the assistant turn is
    actually over, not just "the agent sent a message." Authoritative
    clear of turn_busy regardless of which bridge originally set it,
    because the harness itself is the source of truth about when its
    own turns end. This is the architectural complement to the
    per-runtime native turn-end signals (codex turn/completed, pi
    agent_end, hermes process exit) that already exist for managed
    runs but were missing for resident claude under claude-channel.js.

    Idempotent: calling when turn_busy is already 0 is a no-op (still
    refreshes turn_updated_at for liveness tracking).
    """
    db = await get_db()
    try:
        tombstone = await _agent_tombstone(db, agent_id)
        if tombstone:
            raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
        agent_row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent_row:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        # WS-4a (2026-06-17): a turn-end carrying a bridgeId comes from a bridge-side turn
        # DETECTOR (the harness Stop hook posts no body, so it stays authoritative). If that
        # bridge has been SUPERSEDED by a newer one for this agent, ignore the clear — a stale
        # detector from a replaced bridge must not false-clear the live successor's turn (the
        # F5 working→idle flap on bridge restart mid-turn). The heartbeat turnBusy=false path
        # already has this guard; this brings the dedicated endpoint in line for detector posts.
        try:
            _body = await request.json()
        except Exception:
            _body = {}
        _posting_bridge = str((_body or {}).get("bridgeId") or "").strip()
        if _posting_bridge:
            _sup = await (await db.execute(
                "SELECT superseded_by FROM bridge_instances WHERE id = ? AND agent_id = ?",
                (_posting_bridge, agent_id),
            )).fetchone()
            if _sup and str((_sup["superseded_by"] if "superseded_by" in _sup.keys() else "") or "").strip():
                return {"ok": True, "agentId": agent_id, "ignored": "superseded_bridge"}
        # No-op fast path (2026-07-19): a KEEP-CLEARED detector re-assert fires every ~45s for the
        # WHOLE idle life of every agent. When there is genuinely nothing to clear — turn_busy already 0
        # AND the engine's in_turn already 0 — the full write+commit+broadcast is pure waste (the
        # periodic-write anti-pattern the _LIVE_STATE_CACHE redesign removed). Skip it. A real stray
        # (either bit set) still takes the full clear below, preserving KEEP-CLEARED's healing purpose.
        # last_seen refresh is safe to skip here: the unconditional liveness beat owns liveness.
        _tb = await (await db.execute(
            "SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?", (agent_id,)
        )).fetchone()
        _st = await (await db.execute(
            "SELECT in_turn FROM agent_status_state WHERE agent_id = ?", (agent_id,)
        )).fetchone()
        _turn_busy = int((_tb["turn_busy"] if _tb and "turn_busy" in _tb.keys() else 0) or 0)
        _in_turn = int((_st["in_turn"] if _st and "in_turn" in _st.keys() else 0) or 0)
        if _turn_busy == 0 and _in_turn == 0:
            return {"ok": True, "agentId": agent_id, "noop": "already-cleared"}
        now = _now()
        await db.execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 0, '', '', '', ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                turn_busy = 0,
                turn_run_id = '',
                turn_bridge_id = '',
                turn_runtime = '',
                turn_updated_at = excluded.turn_updated_at
            """,
            (agent_id, now),
        )
        await db.execute(
            "UPDATE agents SET last_seen = ? WHERE id = ?",
            (now, agent_id),
        )
        # status v2: feed the event-driven engine (clears in_turn). Flag-agnostic —
        # only the `new` read path reads agent_status_state, so it's a no-op for `old`.
        await _apply_status_event(db, agent_id, {"kind": "turn_end", "runId": ""})
        await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        # Push the to-ready transition immediately — this is the hop the operator most needs
        # ("send queued work after the agent goes ready"); waiting the ~60s poll looked stuck.
        settings = await _load_settings(db)
        await _broadcast_engine_status(await _get_ws(request), db, agent_id, settings=settings)
        return {"ok": True, "agentId": agent_id}
    finally:
        await db.close()
