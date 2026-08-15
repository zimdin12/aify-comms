"""Changing which underlying runtime session an agent is bound to.

Extracted from `service/routers/agents/session_mode.py` in v0.5.4, which kept the MODE switch. The
two were filed together because both patch an agent's session; they answer different questions. A
mode switch changes how an agent is DRIVEN — resident or managed. This changes WHICH conversation it
is driving, with the mode unchanged.

A HANDLE IS AN IDENTITY CLAIM, and almost all of this handler is refusing bad ones. Two live agents
cannot hold the same session handle: `_refuse_colliding_session_handle` is what stops a second agent
adopting a conversation somebody is already in, and `_session_handle_live_owner` is how it finds out.
When the change cannot be applied immediately it is PARKED rather than dropped — the agent is
mid-turn, and a handle swapped underneath a running turn is how a reply lands in the wrong
conversation.

`_detect_fresh_start_terminal` IS THE OTHER DIRECTION: a genuinely new session, not a rebind, and it
has to be told apart because a fresh start must not inherit the previous terminal.

Body and route decorator are byte-identical to what stood in `session_mode.py`. The router is built
through `domain_router()`, which rejects a hand-passed `route_class`, so a new surface cannot opt out
of the bounded SQLite write-lock retry.
"""

from __future__ import annotations

import json

from fastapi import HTTPException, Request

from service.api_core.agent_sessions import _agent_tombstone, _session_handle_live_owner
from service.api_core.capabilities import _default_capabilities_for
from service.api_core.dispatch_state import _get_dispatch_state_for_agent
from service.api_core.records import _agent_record_to_dict
from service.api_core.routing import domain_router
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.runtime_state import (
    _runtime_state_replacing_handle,
    _runtime_state_with_handle,
)
from service.api_core.serialization import _json_loads_or
from service.api_core.session_handle_change import (
    _detect_fresh_start_terminal,
    _mirror_handle_onto_live_session,
    _park_pending_session_handle_change,
    _refuse_colliding_session_handle,
)
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings
from service.api_core.status_refresh import _compute_agent_status
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db
from service.routers.agents.shared import _sanitize_session_handle

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import AgentSessionHandleUpdate

router = domain_router()



@router.patch("/agents/{agent_id}/session-handle")
async def update_agent_session_handle(agent_id: str, req: AgentSessionHandleUpdate, request: Request):
    validate_name(agent_id, "agent ID")
    # Drop unexpanded shell placeholders ("$HERMES_SESSION_ID", "${VAR}") so a
    # literal is never stored as the resume handle — see _sanitize_session_handle.
    session_handle = _sanitize_session_handle(req.sessionHandle)
    if len(session_handle) > 512:
        raise HTTPException(400, "sessionHandle must be 512 characters or fewer")
    db = await get_db()
    try:
        now = _now()
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            tombstone = await _agent_tombstone(db, agent_id)
            if tombstone:
                raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
            raise HTTPException(404, f"Agent '{agent_id}' not found")

        # ── Sticky session identity + new-id guard (governance, 2026-05-30) ──
        # The bridge heartbeat (session-handle-heartbeat.js, requestedBy=
        # "bridge-heartbeat") continuously reports the runtime's *discovered*
        # session id. We must NOT silently overwrite the persisted handle when
        # that discovered id DRIFTS from what we already pinned — a drift is the
        # observable symptom of a split (agent landed on a fresh id) or a merge
        # (two agents converging on one id). Instead we park the proposed id in
        # `pending_session_id`, flag the agent `session-changed`, and KEEP
        # delivery pointed at the old handle until the operator resolves it.
        #
        # Scope is deliberately narrow so we never break the existing flows:
        #   • First-id auto-accept — no persisted handle yet → accept (current).
        #   • Same id re-reported → no-op (no pending, no churn).
        #   • Clearing (empty handle) → allowed (heal paths clear poisoned ids).
        #   • Deliberate operator re-pin (any other requestedBy, e.g. dashboard
        #     manual set, console attach) → unguarded, as before.
        #   • Re-register (POST /agents) is a separate write site and remains a
        #     full state refresh — it is NOT routed through here.
        requested_by = str(req.requestedBy or "").strip()
        persisted_handle = str(row["session_handle"] or "").strip()

        # ── Cross-agent collision guard (root-cause fix, 2026-05-31) ──
        # A runtime session id must be owned by at most ONE live agent. Never let
        # agent X ADOPT a session id that a DIFFERENT LIVE agent already owns —
        # the resident<->managed invariant. (Incident: graph-tech-lead adopted
        # comms-tech-lead's live resident id 651b895f at 06:07; the kill-prior
        # reaper then turned that collision fatal.) This fires for ANY source
        # (capture, heartbeat, manual set) and covers the first-id case too. Park
        # the colliding id as `pending_session_id` and KEEP this agent's own
        # handle (empty stays empty → the agent launches fresh and captures its
        # OWN id, which won't collide). A stale/dead owner is NOT a collision
        # (the id is free to reassign) — _session_handle_live_owner gates on
        # heartbeat freshness.
        if session_handle and session_handle != persisted_handle:
            _settings_g = await _load_settings(db)
            _owner = await _session_handle_live_owner(
                db, session_handle, exclude_agent_id=agent_id,
                lease_seconds=_settings_g.get("resident_lease_seconds", 150),
            )
            if _owner:
                return await _refuse_colliding_session_handle(
                    db, request, agent_id, session_handle,
                    persisted_handle, now, _owner,
                )

        # Auto-confirm (2026-06-04): when ON (default), a SAFE self-change — the
        # cross-agent collision guard above already returned for a live-owned id —
        # is adopted immediately (fall through to the bind path below) instead of
        # parked. This breaks the managed-claude session-changed → stale-console-
        # owner → recycle loop. When OFF, park as `pending_session_id` and wait for
        # a manual Confirm (the original sticky-identity governance behavior).
        _auto_confirm_sid = bool(
            (await _load_settings(db)).get(
                "auto_confirm_session_id", DEFAULT_SETTINGS["auto_confirm_session_id"]
            )
        )
        _fresh_start_terminal = await _detect_fresh_start_terminal(
            db, agent_id, _auto_confirm_sid, requested_by, session_handle, persisted_handle
        )
        if (
            requested_by == "bridge-heartbeat"
            and session_handle
            and persisted_handle
            and session_handle != persisted_handle
            and (not _auto_confirm_sid or _fresh_start_terminal)
        ):
            return await _park_pending_session_handle_change(
                db, request, agent_id, session_handle,
                persisted_handle, now,
            )

        runtime = _normalize_runtime(row["runtime"] or "generic")
        session_mode = _normalize_session_mode(row["session_mode"] or "resident")
        runtime_config = _json_loads_or(row["runtime_config"], {})
        runtime_state = _runtime_state_replacing_handle(runtime, row["runtime_state"], session_handle)
        capabilities = _default_capabilities_for(runtime, session_mode, session_handle, runtime_config)
        registered_handle = _runtime_state_with_handle(runtime, {}, session_handle)

        # G3 (2026-06-03): advisory (non-blocking) warning when the handle being
        # bound is already owned by a DIFFERENT live agent. The strict cross-agent
        # collision guard above already HARD-BLOCKS the `handle != persisted` live
        # case; this warning covers the remaining binds (e.g. re-pinning the same
        # handle another live agent already shares) so the operator sees that two
        # live agents are pointing at one native session id.
        handle_share_warning = ""
        if session_handle:
            _settings_g3 = await _load_settings(db)
            _owner_g3 = await _session_handle_live_owner(
                db, session_handle, exclude_agent_id=agent_id,
                lease_seconds=_settings_g3.get("resident_lease_seconds", 150),
            )
            if _owner_g3:
                handle_share_warning = (
                    f"session id '{session_handle}' is also owned by live agent "
                    f"'{_owner_g3['agentId']}' ({_owner_g3['sessionMode']}); two live agents "
                    "should not share one native session."
                )
        await db.execute(
            """
            UPDATE agents
            SET session_handle = ?,
                pending_session_id = '',
                runtime_state = ?,
                capabilities = ?,
                status_note = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (
                session_handle,
                json.dumps(runtime_state),
                json.dumps(capabilities),
                f"Session handle set by {req.requestedBy or 'operator'}." if session_handle else f"Session handle cleared by {req.requestedBy or 'operator'}.",
                now,
                agent_id,
            ),
        )
        await _mirror_handle_onto_live_session(
            db, agent_id, runtime, session_handle, registered_handle, now
        )
        await db.commit()

        updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        settings = await _load_settings(db)
        status = await _compute_agent_status(updated, db)
        dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_session_handle_updated", {"agentId": agent_id, "sessionHandle": session_handle})
        handle_response = {
            "ok": True,
            "agentId": agent_id,
            "sessionHandle": session_handle,
            "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
        }
        if handle_share_warning:
            handle_response["warning"] = handle_share_warning
        return handle_response
    finally:
        await db.close()
