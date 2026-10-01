"""Agent identity: registration, listing, lookup, rename, description, favourite, removal.

`register_agent` moved here WHOLE in v0.5.2m, when it was 684 lines and the largest handler in
the product. It is not that any more — v0.5.4 lifted eleven verbatim blocks out of it under the
inline-back proof in `test_register_agent_split_is_inert.py` (retired in v0.6.13). The line count is
deliberately not restated here; measure the file.

v0.5.2m, one surface of the agents package. Built with `domain_router()`;
declares NO tags — the parent applies `tags=["api"]` once when api_v2 includes the package.
"""

from __future__ import annotations

import json
import logging
import time

from fastapi import HTTPException, Request
from service.api_core.definition_records import definition_of, definition_rows, runs_with

from service.api_core.live_process_probes import _agents_with_live_terminal_sessions
from service.api_core.routing import domain_router

logger = logging.getLogger("aify_comms.routers.agents.identity")

# Imported for the ANNOTATIONS. Under postponed evaluation these are strings, so a
# missing one does not fail import -- FastAPI demotes the body to a query parameter and
# the endpoint 422s at request time. The route annotation gate caught 17 of these here.

from service.api_core.message_store import _get_unread_count_map
from service.db_errors import _is_lock_error
from service.api_core.outbound_activity import _get_outbound_activity_map
from service.api_core.agent_sessions import _agent_tombstone
from service.api_core.managed_env import load_session_environment_by_agent
from service.api_core.dispatch_state import _get_dispatch_state_map
from service.api_core.records import _agent_record_to_dict
from service.api_core.settings import _load_settings
from service.api_core.status_refresh import _refresh_expired_agent_live_states
from service.api_core.ws import _get_ws
from service.db import get_db
from service.reconcilers.managed_workers import _repair_unusable_active_runs
from service.reconcilers.status_cache import _live_state_get
import sqlite3
from service.api_core.tuning import LIST_AGENTS_REFRESH_LIMIT
from service.routers.agents.shared import logger
from service.api_core.registration_gates import (
    _enforce_env_reachable_gate,
    _enforce_live_worker_gate,
)
from service.api_core.agent_remove import remove_agent
from service.api_core.definition_guard import defined_on
from service.api_core.definition_requests import queued_for_its_host
from service.api_core.operator_authz import recorded_operator_actor
from service.clock import now as _now

router = domain_router()


@router.get("/agents")
async def list_agents(request: Request):
    db = await get_db()
    try:
        settings = await _load_settings(db)
        # ONE cache for this request, created before the FIRST phase that resolves an environment.
        # `_managed_owning_environment_row` falls back to a lookup keyed on machine_id alone, so a
        # roster of agents sharing a host asks the same question repeatedly.
        #
        # It used to be created between the two phases, which is why it only ever served one of
        # them: measured at 50 agents, the machine lookup ran 17 times -- 16 of those from the
        # live-state refresh below, which runs FIRST and had no cache to consult.
        #
        # Safe to share across both phases because nothing in this request writes `environments`;
        # that is asserted end-to-end by test_the_roster_never_writes_what_it_caches.py, and the
        # ceiling itself by test_the_roster_looks_up_an_environment_once.py.
        environments_by_machine: dict = {}
        # One query for every agent's live-session environment binding, instead of one per agent --
        # and built HERE, above the refresh, for the reason the dict above is: the live-state refresh
        # runs first and resolved the same binding per agent because this map did not exist yet.
        session_environment_by_agent = await load_session_environment_by_agent(db)
        # The cache refresh/repair below is BEST-EFFORT: a SELECT never takes SQLite's write
        # lock (WAL), so when the single writer is briefly contended we serve slightly-stale
        # cached rows instead of 503ing the whole roster — a 503 here broke the dashboard load
        # entirely (the browser surfaces it as "Failed to fetch"). The 60s reconcile sweep
        # persists the refresh on its next pass. (2026-06-18 — read paths must never 503 on a lock.)
        try:
            repaired_active_runs = await _repair_unusable_active_runs(db)
            refreshed_live_states = await _refresh_expired_agent_live_states(
                db, settings=settings, limit=LIST_AGENTS_REFRESH_LIMIT,
                environments_by_machine=environments_by_machine,
                session_environment_by_agent=session_environment_by_agent,
            )
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
        live_terminal_agents = await _agents_with_live_terminal_sessions(db, agent_ids)
        definitions = await definition_rows(db, agent_ids)
        result = {}
        for row in agents:
            aid = row["id"]
            entry = _live_state_get(aid) or {}
            payload = _agent_record_to_dict(row, entry.get("status") or row["status"], unread_map.get(aid, 0), dispatch_map.get(aid), live_reason=entry.get("reason"), outbound=outbound_map.get(aid))
            payload["definition"] = definition_of(row, definitions.get(aid))
            payload["runsWith"] = runs_with(row, definitions.get(aid))
            # Plan 5 Section C: read-path live-worker gate — see
            # _enforce_live_worker_gate for full rationale. (In-memory correction
            # only; the writeback was removed 2026-06-18 to cut read-path writes.)
            payload = await _enforce_live_worker_gate(payload, db, settings, aid, live_terminal_agents=live_terminal_agents)
            payload = await _enforce_env_reachable_gate(
                payload, db, settings, aid, agent_row=row,
                environments_by_machine=environments_by_machine,
                session_environment_by_agent=session_environment_by_agent,
            )
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
        definition_row = (await definition_rows(db, [agent_id])).get(agent_id)
        payload["definition"] = definition_of(row, definition_row)
        payload["runsWith"] = runs_with(row, definition_row)
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
        # A DEFINED AGENT IS REMOVED BY ITS HOST (P0 C5): the removal becomes a request, and the host's
        # `done` runs this same removal behind C4's fence. Asked before any worker is stopped and
        # again inside the deleting transaction, so an agent defined meanwhile is not removed here.
        async def defined_elsewhere(conn):
            owner = await defined_on(conn, agent_id)
            return f"defined on {owner}" if owner else ""

        deleted, why = await remove_agent(db, agent_id, actor="api", reason="delete_agent", refusal=defined_elsewhere)
        if why:
            actor = recorded_operator_actor(None, request, action="removing a defined agent as the operator")
            return await queued_for_its_host(db, agent_id, {"remove": True}, actor, _now())
        ws = await _get_ws(request)
        if ws: await ws.broadcast("agent_removed", {"agentId": agent_id})
        return {"ok": deleted > 0, "agentId": agent_id}
    finally:
        await db.close()
