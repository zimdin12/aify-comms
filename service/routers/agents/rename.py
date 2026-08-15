"""Renaming an agent: the one write that has to repoint every reference to it.

Extracted from `service/routers/agents/identity.py` in v0.5.4. Closure measured before the move —
`api_core` and `service` leaves only, nothing local, nothing borrowed from `agents/shared.py`.

AN AGENT ID IS A FOREIGN KEY IN FOURTEEN PLACES, and a rename is not an UPDATE on one row. It is a
tombstone plus a rewrite of everything pointing at the old id — messages, sessions, dispatch runs,
read receipts — which is why the work lives in `api_core/agent_rename_writes.py` and this handler is
the gate in front of it: validate the new name, refuse if it collides, check the agent is not live
mid-turn, then hand over.

KNOWN GAP, recorded rather than fixed here because it needs an operator ruling and this is a
relocation: `terminal_sessions.agent_id` is the one reference the rewrite does not repoint and no FK
cascade covers, so a renamed agent's terminals stay attached to the tombstoned id. Nothing about
this move changes that, and a test elsewhere pins it as UNRESOLVED so it cannot be forgotten.

Body and route decorator byte-identical.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from service.api_core.agent_rename_writes import _rewrite_agent_references_for_rename
from service.api_core.agent_sessions import _agent_tombstone
from service.api_core.liveness import _agent_liveness
from service.api_core.routing import domain_router
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import AgentRenameRequest

router = domain_router()



@router.post("/agents/{agent_id}/rename")
async def rename_agent(agent_id: str, req: AgentRenameRequest, request: Request):
    validate_name(agent_id, "agent ID")
    new_agent_id = str(req.newAgentId or "").strip()
    validate_name(new_agent_id, "new agent ID")
    if new_agent_id == agent_id:
        return {"ok": True, "agentId": agent_id, "newAgentId": new_agent_id, "changed": False}

    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        agent = await cursor.fetchone()
        if not agent:
            await db.rollback()
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        existing = await (await db.execute("SELECT id FROM agents WHERE id = ?", (new_agent_id,))).fetchone()
        if existing:
            await db.rollback()
            raise HTTPException(409, f'Agent "{new_agent_id}" already exists')
        tombstone = await _agent_tombstone(db, new_agent_id)
        if tombstone:
            await db.rollback()
            raise HTTPException(409, f'Agent "{new_agent_id}" was intentionally removed before; clear that ID before reusing it')

        now = _now()
        await _rewrite_agent_references_for_rename(db, agent_id, new_agent_id, now, req)
        await db.commit()
        # Rename is DB-only: a still-running session is bootstrapped under the OLD id (now
        # tombstoned), so it is orphaned — its heartbeats bounce and it does NOT keep the new id
        # live. Surface that + the recovery in the response so the caller/dashboard doesn't have to
        # rediscover it by hand (2026-07-07: a rename silently orphaned the live session and notified
        # nobody). We report facts + a plain note; the dashboard can format the exact relaunch command.
        session_mode = str(agent["session_mode"] or "resident").strip().lower()
        runtime = str(agent["runtime"] or "").strip()
        # "Live" needs a FRESHNESS predicate, not merely a row that was never superseded.
        #
        # This asked `bridge_instances` for any row with an empty `superseded_by`, which is not the
        # same question. Those rows accumulate BY DESIGN (KNOWN_ISSUES.md, 2026-08-07 retraction) and
        # a bridge that died without a clean supersede keeps `superseded_by = ''` until the sweep's
        # `_reap_stale_orphan_bridges` gets to it. So a rename minutes after a crashed wrapper told
        # the operator "A live session is still running as '<old>' and is now orphaned — relaunch it",
        # sending them to recover a session that had already been dead for hours.
        #
        # `_agent_liveness` is the repo's single liveness predicate and already applies the exact
        # leases the status engine uses, so the note now agrees with the dot the operator is looking
        # at. Advisory text only — nothing here changes state either way — but a wrong instruction is
        # the same class of defect as a wrong status, and this file has spent a week on that class.
        liveness = await _agent_liveness(db, new_agent_id)
        had_live_bridge = bool(
            liveness.get("worker_live")
            or liveness.get("sidecar_live")
            or liveness.get("resident_bridge_fresh")
        )
        note = (
            f"History + session handle preserved under '{new_agent_id}'; old id '{agent_id}' is "
            f"tombstoned (sends to it are now rejected). "
            + (
                (
                    f"A live {session_mode} session is still running as '{agent_id}' and is now orphaned — "
                    f"re-register/relaunch it as '{new_agent_id}' "
                    + ("(dashboard Restart, or delete-session then send to cold-start a fresh worker) "
                       if session_mode == "managed"
                       else f"(relaunch the wrapper with the new id, e.g. --aify-agent {new_agent_id}) ")
                    + "so the live identity matches. "
                )
                if had_live_bridge else ""
            )
            + "Notify teammates to address the new id."
        )
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_renamed", {"oldAgentId": agent_id, "newAgentId": new_agent_id})
        return {
            "ok": True, "agentId": agent_id, "newAgentId": new_agent_id, "changed": True,
            "hadLiveBridge": had_live_bridge, "sessionMode": session_mode, "runtime": runtime,
            "note": note,
        }
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        raise
    finally:
        await db.close()
