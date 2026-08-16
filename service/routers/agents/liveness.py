"""Liveness and turn signals: heartbeat, turn start/end, ready, status events, leases.

v0.5.2m, one surface of the agents package. Built with `domain_router()`;
declares NO tags — the parent applies `tags=["api"]` once when api_v2 includes the package.
"""

from __future__ import annotations

import json
import logging
import time

from pydantic import BaseModel

from fastapi import HTTPException, Request

from service.api_core.bridge_liveness_beat import _upsert_bridge_liveness_beat
from service.api_core.turn_busy_signal import _apply_turn_busy_signal
from service.api_core.status_events import _apply_status_event
from service.api_core.status_broadcast import _broadcast_engine_status
from service.api_core.routing import domain_router

logger = logging.getLogger("aify_comms.routers.agents.liveness")

# Imported for the ANNOTATIONS. Under postponed evaluation these are strings, so a
# missing one does not fail import -- FastAPI demotes the body to a query parameter and
# the endpoint 422s at request time. The route annotation gate caught 17 of these here.
from service.models import AgentReadyUpdate

from service.api_core.agent_sessions import _agent_tombstone
from service.api_core.runtime import _normalize_session_mode
from service.api_core.settings import _load_settings
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.db import get_db
from service.reconcilers.status_cache import _live_state_get
from service.clock import now as _now
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
import sqlite3
from service.routers.agents.shared import (
    _record_claimer_lease,
    logger,
)
from service.api_core.agent_sessions import _adopt_live_resident_driver

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
        await _upsert_bridge_liveness_beat(db, agent_id, bridge_id, bridge_kind, body, now)
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
        turn_flip = await _apply_turn_busy_signal(db, agent_id, bridge_id, body, now, turn_flip)
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
