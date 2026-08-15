"""Agent identity: registration, listing, lookup, rename, description, favourite, removal.

`register_agent` moved here WHOLE in v0.5.2m, when it was 684 lines and the largest handler in
the product. It is not that any more — v0.5.4 lifted eleven verbatim blocks out of it under the
inline-back proof in `service/tests/test_register_agent_split_is_inert.py`. The line count is
deliberately not restated here; measure the file.

v0.5.2m, one surface of the agents package. Built with `domain_router()`;
declares NO tags — the parent applies `tags=["api"]` once when api_v2 includes the package.
"""

from __future__ import annotations

import json
import logging
import time

from fastapi import HTTPException, Request

from service.api_core.agent_rename_writes import _rewrite_agent_references_for_rename
from service.api_core.status_events import _apply_status_event
from service.api_core.routing import domain_router

logger = logging.getLogger("aify_comms.routers.agents.identity")

# Imported for the ANNOTATIONS. Under postponed evaluation these are strings, so a
# missing one does not fail import -- FastAPI demotes the body to a query parameter and
# the endpoint 422s at request time. The route annotation gate caught 17 of these here.

from service.api_core.agent_registration_writes import (
    _adopt_console_terminal_on_register,
    _record_registered_session_handle,
    _register_via_adopted_console_terminal,
    _upsert_registered_agent_row,
)
from service.api_core.resident_takeover_writes import (
    _register_via_manual_resident_takeover,
    _stage_manual_resident_takeover,
    _supersede_stale_resident_terminals,
)
from service.api_core.same_mode_bridge_gate import _enforce_same_mode_bridge_gate
from service.api_core.registration_gates import (
    _enforce_driving_mode_switch_gate,
    _enforce_tombstone_registration_gate,
    _enforce_tombstone_resurrection_gate,
)
from service.api_core.message_store import _get_unread_count_map
from service.db_errors import _is_lock_error
from service.api_core.outbound_activity import _get_outbound_activity_map
from service.api_core.agent_sessions import _agent_tombstone
from service.api_core.dispatch_state import _get_dispatch_state_map
from service.api_core.records import _agent_record_to_dict
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import _load_settings
from service.api_core.status_inputs import _compute_live_status_cache
from service.api_core.status_refresh import _refresh_expired_agent_live_states
from service.api_core.ws import _get_ws
from service.db import get_db
from service.reconcilers.managed_workers import _repair_unusable_active_runs
from service.reconcilers.status_cache import _live_state_get
from service.clock import now as _now
from service.terminal_snapshot import render_live_screen as _render_live_terminal_screen
import sqlite3
from service.routers.agents.shared import (
    _borrowed_list_agents_refresh_limit,
    logger,
)
from service.api_core.registration_gates import (
    _enforce_env_reachable_gate,
    _enforce_live_worker_gate,
)
from service.api_core.agent_terminal_ops import (
    _request_stop_agent_terminals,
)
from service.api_core.agent_removal import _remove_agent_record

router = domain_router()


@router.get("/agents")
async def list_agents(request: Request):
    db = await get_db()
    try:
        settings = await _load_settings(db)
        # The cache refresh/repair below is BEST-EFFORT: a SELECT never takes SQLite's write
        # lock (WAL), so when the single writer is briefly contended we serve slightly-stale
        # cached rows instead of 503ing the whole roster — a 503 here broke the dashboard load
        # entirely (the browser surfaces it as "Failed to fetch"). The 60s reconcile sweep
        # persists the refresh on its next pass. (2026-06-18 — read paths must never 503 on a lock.)
        try:
            repaired_active_runs = await _repair_unusable_active_runs(db)
            refreshed_live_states = await _refresh_expired_agent_live_states(db, settings=settings, limit=_borrowed_list_agents_refresh_limit())
            if repaired_active_runs or refreshed_live_states:
                await db.commit()
        except sqlite3.OperationalError as exc:
            if not _is_lock_error(exc):
                raise
            try:
                await db.rollback()
            except Exception:
                pass
        cursor = await db.execute("SELECT * FROM agents")
        agents = await cursor.fetchall()
        agent_ids = [row["id"] for row in agents]
        unread_map = await _get_unread_count_map(db, agent_ids)
        dispatch_map = await _get_dispatch_state_map(db, agent_ids)
        # Roster: cheap half only — see include_runs.
        outbound_map = await _get_outbound_activity_map(db, agent_ids, include_runs=False)
        result = {}
        for row in agents:
            aid = row["id"]
            entry = _live_state_get(aid) or {}
            payload = _agent_record_to_dict(row, entry.get("status") or row["status"], unread_map.get(aid, 0), dispatch_map.get(aid), live_reason=entry.get("reason"), outbound=outbound_map.get(aid))
            # Plan 5 Section C: read-path live-worker gate — see
            # _enforce_live_worker_gate for full rationale. (In-memory correction
            # only; the writeback was removed 2026-06-18 to cut read-path writes.)
            payload = await _enforce_live_worker_gate(payload, db, settings, aid)
            payload = await _enforce_env_reachable_gate(payload, db, settings, aid)
            result[aid] = payload
        return {"agents": result}
    finally:
        await db.close()

@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request):
    db = await get_db()
    try:
        settings = await _load_settings(db)
        # Best-effort cache refresh — serve cached on a write-lock rather than 503 (see list_agents).
        try:
            refreshed_live_states = await _refresh_expired_agent_live_states(db, settings=settings, agent_ids=[agent_id])
            if refreshed_live_states:
                await db.commit()
        except sqlite3.OperationalError as exc:
            if not _is_lock_error(exc):
                raise
            try:
                await db.rollback()
            except Exception:
                pass
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        row = await cursor.fetchone()
        if not row:
            tombstone = await _agent_tombstone(db, agent_id)
            if tombstone:
                raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        unread_map = await _get_unread_count_map(db, [agent_id])
        dispatch_map = await _get_dispatch_state_map(db, [agent_id])
        outbound_map = await _get_outbound_activity_map(db, [agent_id])
        entry = _live_state_get(agent_id) or {}
        payload = _agent_record_to_dict(row, entry.get("status") or row["status"], unread_map.get(agent_id, 0), dispatch_map.get(agent_id), live_reason=entry.get("reason"), outbound=outbound_map.get(agent_id))
        # Plan 5 Section C: read-path live-worker gate (in-memory correction only; the
        # writeback was removed 2026-06-18 to cut read-path writes — see the gate bodies).
        payload = await _enforce_live_worker_gate(payload, db, settings, agent_id)
        payload = await _enforce_env_reachable_gate(payload, db, settings, agent_id)
        return {"ok": True, "agentId": agent_id, "agent": payload}
    finally:
        await db.close()

@router.delete("/agents/{agent_id}")
async def unregister_agent(agent_id: str, request: Request):
    db = await get_db()
    try:
        # fix/hermes-leak P2 (REMOVE): for a MANAGED agent, tear the triad down by
        # signalling the bridge BEFORE the agent record is gone. We cannot use a
        # terminal_control here: deleting the agent cascades agents → agent_sessions
        # → terminal_sessions → terminal_controls, so any control emitted in this
        # request is wiped by the same delete. Instead REMOVE drives the triad reap
        # through the SAME agent-control STOP path (status=stopped + the bridge's
        # managed-hermes terminal stop reaps the triad), committed in its own
        # transaction, THEN tombstones. This makes REMOVE = STOP-then-tombstone, so
        # the surviving stop control (claimed before the tombstone delete) carries
        # the triad-reap. Resident agents are skipped (operator's own session).
        cursor = await db.execute("SELECT session_mode FROM agents WHERE id = ?", (agent_id,))
        agent_row = await cursor.fetchone()
        managed = bool(agent_row) and _normalize_session_mode(agent_row["session_mode"] or "resident") == "managed"
        if managed:
            now = _now()
            await db.execute(
                "UPDATE agents SET status = 'stopped', status_note = ?, launch_mode = 'none', last_seen = ? WHERE id = ?",
                ("Removed from dashboard; tearing down managed session.", now, agent_id),
            )
            await _request_stop_agent_terminals(
                db, agent_id, requested_by="api", now=now, reap_triad=True,
            )
            await db.commit()
        deleted = await _remove_agent_record(
            db,
            agent_id,
            removed_by="api",
            reason="delete_agent",
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("agent_removed", {"agentId": agent_id})
        return {"ok": deleted > 0, "agentId": agent_id}
    finally:
        await db.close()
