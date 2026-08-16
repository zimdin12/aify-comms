"""Taking messages back: unsend one, clear a conversation, sweep unread rows nobody can read.

Extracted from `service/routers/dispatch_messages/messages.py` in v0.5.4. Closure measured before
the move — `api_core` and `service` leaves only, nothing local, nothing from `shared.py`.

DELETING A MESSAGE IS NOT ENOUGH ON ITS OWN, and that is the thread joining these three. A message
may already have QUEUED DISPATCH RUNS pointing at it, so removing the row without cancelling them
strands work that will later try to deliver something that no longer exists —
`_cancel_queued_dispatch_runs_for_message_ids` is why unsend and conversation-clear are not simple
DELETEs.

`cleanup_orphan_unread_messages` exists for the residue: unread rows whose message is already gone,
which inflate an agent's unread count with mail it can never open. It is the sweep, not the fix; the
cancellation above is the fix.

Bodies and route decorators byte-identical.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from service.api_core.dispatch_run_state import _cancel_queued_dispatch_runs_for_message_ids
from service.api_core.message_store import _delete_messages_by_ids, _delete_messages_where
from service.api_core.routing import domain_router
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.db import get_db

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import ConversationClearRequest

router = domain_router()



@router.delete("/messages/{message_id}")
async def unsend_message(message_id: str, request: Request):
    """Delete a message by ID. Also removes associated read receipts."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Message '{message_id}' not found")
        message_ids = [message_id]
        if (row["source"] or "") == "channel" and not (row["to_agent"] or ""):
            fanout_cursor = await db.execute(
                "SELECT id FROM messages WHERE id LIKE ? AND channel = ? AND source = 'channel'",
                (f"{message_id}-%", row["channel"] or ""),
            )
            message_ids.extend([fanout["id"] for fanout in await fanout_cursor.fetchall()])
        cancelled_dispatch_run_ids = await _cancel_queued_dispatch_runs_for_message_ids(db, message_ids)
        deleted = await _delete_messages_by_ids(db, message_ids)
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("message_deleted", {"id": message_id, "deleted": deleted})
            for run_id in cancelled_dispatch_run_ids:
                await ws.broadcast("dispatch_updated", {"runId": run_id, "status": "cancelled"})
        return {
            "ok": True,
            "id": message_id,
            "deleted": deleted,
            "cancelledDispatchRuns": len(cancelled_dispatch_run_ids),
            "cancelledDispatchRunIds": cancelled_dispatch_run_ids,
        }
    finally:
        await db.close()



@router.post("/messages/conversation/clear")
async def clear_direct_conversation(req: ConversationClearRequest, request: Request):
    agent_id = str(req.agentId or "").strip()
    peer_id = str(req.peerId or "").strip()
    if not agent_id or not peer_id:
        raise HTTPException(400, "Need agentId and peerId")
    validate_name(agent_id, "agent ID")
    validate_name(peer_id, "peer agent ID")

    db = await get_db()
    try:
        deleted = await _delete_messages_where(
            db,
            """
            source = 'direct'
            AND channel IS NULL
            AND (
                (from_agent = ? AND to_agent = ?)
                OR (from_agent = ? AND to_agent = ?)
            )
            """,
            (agent_id, peer_id, peer_id, agent_id),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("conversation_cleared", {"agentId": agent_id, "peerId": peer_id, "deleted": deleted})
        return {"ok": True, "agentId": agent_id, "peerId": peer_id, "deleted": deleted}
    finally:
        await db.close()



#: The clause that decides which messages this endpoint DELETES, named so a test can run the real
#: one rather than a copy of it. It was a string literal inside the handler, reachable only by
#: calling the route — and this is the one route in the service that deletes rows nobody asked it
#: about, so "a mock would agree with whatever I believed" applies with force.
#:
#: THREE CONDITIONS, EACH LOAD-BEARING, and the tests are written one per condition:
#:   * `m.to_agent IS NOT NULL` — a CHANNEL BROADCAST row has no recipient (`channel_send.py` inserts
#:     one row with no `to_agent` plus one fan-out row per member WITH it). Drop this and every
#:     unread broadcast in the database matches, because `a.id IS NULL` is trivially true for them.
#:   * `a.id IS NULL` — the agent is GONE. Removal really does `DELETE FROM agents` (plus a
#:     tombstone), so this is what "orphan" means here.
#:   * `r.message_id IS NULL` — nobody read it. A message the operator already read is history, not
#:     an orphan.
_ORPHAN_UNREAD_WHERE = """
            id IN (
                SELECT m.id
                FROM messages m
                LEFT JOIN agents a ON a.id = m.to_agent
                LEFT JOIN read_receipts r ON r.message_id = m.id AND r.agent_id = m.to_agent
                WHERE m.to_agent IS NOT NULL AND a.id IS NULL AND r.message_id IS NULL
            )
            """


@router.post("/messages/cleanup/orphan-unread")
async def cleanup_orphan_unread_messages(request: Request):
    """Delete unread inbox messages addressed to removed agents."""
    db = await get_db()
    try:
        deleted = await _delete_messages_where(db, _ORPHAN_UNREAD_WHERE)
        await db.commit()
        ws = await _get_ws(request)
        if ws and deleted:
            await ws.broadcast("messages_cleaned", {"kind": "orphan_unread", "deleted": deleted})
        return {"ok": True, "deleted": deleted}
    finally:
        await db.close()
