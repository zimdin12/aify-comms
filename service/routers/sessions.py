"""The `sessions` route domain: listing, deletion, console start, and session control.

v0.5.2i. Four handlers, three domain-local helpers, 732 lines — and the FIRST BORROW RETIREMENT of
the series, which is the part worth noting.

`_spawn_request_to_dict` and `_spawn_spec_to_dict` did NOT come here. Measurement said they were
"local to sessions", but only because a borrow shim does not count as a user: their real callers were
`list_spawn_requests` (borrowing them) and `control_session` (moving now). Their natural owner is the
spawn-requests domain, so that is where they went — which retires two of the borrows
`service/routers/spawn_requests.py` was carrying, exactly as its retirement map predicted.

v0.5.4 moved them once more, to `service/api_core/spawn_requests_io.py`. The DOMAIN judgement above
still holds and is not being revisited; what changed is the LAYER. This module was importing them
from another router, and one route domain reaching into another is an edge neither of them should
have. Both now import from a leaf.

That is the measurement needing judgement rather than obedience: "no users outside this domain" is a
necessary condition for locality, not a sufficient one, because shims are invisible to it.

`start_session_console` and `control_session` both moved WHOLE into this module. `control_session`
still is whole; `start_session_console` is not — v0.5.4 lifted its terminal-capability refusal into
`service/api_core/console_capability_gate.py`, proved by `test_start_session_console_split_is_inert.py`.

BORROW TABLE with retirement map:

    _coldstart_spawn_request_for_dispatch    retires with: agents, messages
    _agent_session_to_dict                   retires with: agents
    _environment_record_to_dict              retires with: agents
    _default_console_command                 retires with: agents
    _terminal_session_to_dict                retires with: agents, terminals
    _append_dispatch_control                 retires with: agents, dispatch
    _workspace_for_environment               retires with: agents

Nearly all of it retires with `agents`, which is the last domain.

Built with `domain_router()`, and declares NO tags: the parent applies `tags=["api"]` on include.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from fastapi import HTTPException, Query, Request

from service.db_errors import _is_lock_error
from service.api_core.routing import domain_router
from service.api_core.records import _agent_session_to_dict
from service.api_core.ws import _get_ws
import sqlite3
from service.reconcilers.sessions import (
    _compute_session_display_status,
    _repair_current_session_freshness,
    _repair_superseded_recovering_sessions,
)
from service.reconcilers.terminal_consistency import _repair_terminal_session_consistency
from service.terminal_snapshot import drop_live_screen as _drop_live_terminal_screen
from service.db import get_db
# Imported for ANNOTATIONS as well as calls. Under postponed evaluation a missing model does not
# fail import -- FastAPI demotes the body to a query param and the route 422s at request time.
from service.api_core.tuning import _SESSION_DELETE_ALLOWED_STATUSES

logger = logging.getLogger("aify_comms.routers.sessions")

router = domain_router()

# THE SESSION DOMAIN IS THREE FILES, COMPOSED HERE rather than in `api_v2.py`. Starting a console and
# controlling a live session left in v0.5.4; this module keeps the list and the delete, and includes
# the other two, so `api_v2.py` still sees ONE sessions router.
#
# Not converted to a package, for the same reason `terminals.py` was not: ten provenance comments
# across `api_core/`, `reconcilers/` and the split fixtures say a helper "moved out of
# service/routers/sessions.py" — statements about what HAPPENED. A package rename makes every one of
# them false, and rewriting history in comments to satisfy a path gate is the wrong trade.
from service.routers.session_console import router as _session_console_router
from service.routers.session_control import router as _session_control_router

router.include_router(_session_console_router)
router.include_router(_session_control_router)


# Domain-local: nothing outside this module referenced them once the handlers moved.
# CLEAN history: finished AND nothing left to act on — the only rows GET /sessions hides by
# default. Deliberately NOT the same set as _borrowed_session_delete_allowed_statuses() above, and the
# difference is load-bearing (regression 2026-07-26, caught in review): "safe to eventually
# delete" is not "not worth showing".
#   * `stopped` is a session the OPERATOR stopped — Restart / Reset / Compact are precisely the
#     actions you take on it. `comms_restart` even falls back to `sessions[0]` on purpose so a
#     non-live session can be restarted; hiding `stopped` made it answer "no session to restart".
#   * `failed` / `lost` are crashed workers the operator may still restart or inspect.
# Hiding those three broke real consumers (comms_restart, comms_compact, the drawer's
# Restart/Reset/Compact buttons). Only a cleanly-finished session is pure history.
SESSION_CLEAN_HISTORY_STATUSES = {"ended", "completed", "cancelled"}
_TERMINAL_DELETE_ALLOWED_STATUSES = {"stopped", "failed", "lost", "ended", "completed", "cancelled"}




def _borrowed_session_delete_allowed_statuses():
    """BORROWED constant: one owner, never a copy — a forked status set is finding N7."""

    return _SESSION_DELETE_ALLOWED_STATUSES

















async def _agent_session_dict_live(db, row, *, agent_row=None) -> dict[str, Any]:
    """Build the session dict (identical shape/keys to _agent_session_to_dict) but
    with the `status` value DERIVED from live truth via
    _compute_session_display_status. The stored row is never mutated; only the
    served `status` becomes derived so GET /sessions matches GET /agents."""
    data = _agent_session_to_dict(row)
    try:
        data["status"] = await _compute_session_display_status(db, row, agent_row=agent_row)
    except Exception:
        # Defensive: never fail a session list because the deriver hit an edge —
        # fall back to the stored status (the prior behavior).
        pass
    return data


# _repair_current_session_freshness moved to service/reconcilers/sessions.py in v0.5.4 - session
# reconciliation belongs to the reconciler; the CALL SITE is unchanged.


# _repair_superseded_recovering_sessions moved to service/reconcilers/sessions.py in v0.5.4 - session
# reconciliation belongs to the reconciler; the CALL SITE is unchanged.


@router.get("/sessions")
async def list_sessions(
    request: Request,
    agentId: Optional[str] = None,
    environmentId: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    includeEnded: bool = Query(
        False,
        description="Include TERMINAL (ended/stopped/failed/lost/cancelled/completed) sessions. "
                    "Off by default: this endpoint lists CURRENT sessions.",
    ),
):
    """List sessions. CURRENT ones by default — this is not a history feed.

    Why the default changed (2026-07-26): the query filtered only on agentId/environmentId, so it
    returned every historical row and the dashboard's Sessions rail became an accidental history
    dump — one entry per worker process ever started. mc-senior-dev showed 79, all of them the SAME
    native conversation (`session_handle` 20260715_001441_960b8f) resumed by 75 successive boots,
    which is exactly why it read as "duplicates". Session HISTORY already has a home: the
    spawn-requests list under Environments.

    Two latent bugs came with it, which is why this is a correctness fix and not just tidying:
      * the dashboard picks an agent's session with `state.sessions.find(...)` — the FIRST match.
        Ordering is `last_seen DESC`, and a dead row can carry a newer `last_seen` than the live
        one, so the console/drawer could bind to an ENDED session.
      * the dashboard requests `limit=80`. With 449 rows, one agent's dead history could push
        another agent's LIVE session out of the window entirely, making it invisible.

    `includeEnded=true` preserves the old behaviour for anything that genuinely wants history.
    """
    db = await get_db()
    try:
        # Best-effort consistency repairs — serve cached on a write-lock rather than 503 (see list_agents).
        # These stay on the read path (NOT moved to reconcile): they correct the console/terminal
        # binding shown in THIS response (e.g. a just-stopped terminal must immediately read as
        # unbound), so a 60s reconcile lag would surface a dead terminal as still-attached. The
        # functions no-op when nothing needs repair, so steady-state polls do not write.
        try:
            await _repair_superseded_recovering_sessions(db)
            await _repair_current_session_freshness(db)
            await _repair_terminal_session_consistency(db)
        except sqlite3.OperationalError as exc:
            if not _is_lock_error(exc):
                raise
            try:
                await db.rollback()
            except Exception:
                pass
        where = []
        params: list[Any] = []
        if agentId:
            where.append("agent_id = ?")
            params.append(agentId)
        if environmentId:
            where.append("environment_id = ?")
            params.append(environmentId)
        if not includeEnded:
            # Filter on the STORED status only. A row stored live but derived dead below stays in
            # the response on purpose — the operator should see it (and the reconcilers heal it);
            # what must not appear is a row that is finished AND has nothing left to act on.
            hidden = sorted(SESSION_CLEAN_HISTORY_STATUSES)
            where.append(
                f"LOWER(COALESCE(status,'')) NOT IN ({','.join('?' for _ in hidden)})"
            )
            params.extend(hidden)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        cursor = await db.execute(
            f"SELECT * FROM agent_sessions {where_sql} ORDER BY last_seen DESC LIMIT ?",
            (*params, limit),
        )
        rows = await cursor.fetchall()
        # Phase 3 (2026-06-03): DERIVE the served session status from live truth
        # (terminal_sessions / bridge_instances) so GET /sessions matches GET
        # /agents — the stored agent_sessions.status is a cache, never the display
        # source. Cache the agent row per agent so a multi-session list resolves
        # each agent's liveness once.
        agent_cache: dict[str, Any] = {}
        sessions: list[dict[str, Any]] = []
        for row in rows:
            aid = str(row["agent_id"] or "")
            if aid and aid not in agent_cache:
                agent_cache[aid] = await (
                    await db.execute("SELECT * FROM agents WHERE id = ?", (aid,))
                ).fetchone()
            sessions.append(await _agent_session_dict_live(db, row, agent_row=agent_cache.get(aid)))
        return {"ok": True, "sessions": sessions}
    finally:
        await db.close()


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))
        session = await cursor.fetchone()
        if not session:
            raise HTTPException(404, f'Session "{session_id}" not found')

        status = str(session["status"] or "").strip().lower()
        if status not in _borrowed_session_delete_allowed_statuses():
            raise HTTPException(
                409,
                f'Session "{session_id}" is {status or "active"}; stop or finish it before deleting the session record.',
            )

        terminal_rows = await (await db.execute("SELECT * FROM terminal_sessions WHERE session_id = ?", (session_id,))).fetchall()
        stale_active_terminal_ids = [
            terminal["id"]
            for terminal in terminal_rows
            if str(terminal["status"] or "").strip().lower() not in _TERMINAL_DELETE_ALLOWED_STATUSES
        ]

        for terminal in terminal_rows:
            # Release the live screen with the terminal it belongs to. (A merely STOPPED console
            # keeps its screen on purpose — you can still read the last thing it showed, e.g. an
            # agent that stopped sitting at a dialog. The registry is bounded regardless.)
            try:
                _drop_live_terminal_screen(str(terminal["id"]))
            except Exception:
                pass
            await db.execute("DELETE FROM terminal_controls WHERE terminal_id = ?", (terminal["id"],))
            await db.execute("DELETE FROM terminal_events WHERE terminal_id = ?", (terminal["id"],))
        await db.execute("DELETE FROM terminal_sessions WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM agent_sessions WHERE id = ?", (session_id,))
        await db.commit()

        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("session_deleted", {"sessionId": session_id, "agentId": session["agent_id"]})
        return {
            "ok": True,
            "deleted": True,
            "sessionId": session_id,
            "agentId": session["agent_id"],
            "staleActiveTerminalsDeleted": stale_active_terminal_ids,
        }
    finally:
        await db.close()
