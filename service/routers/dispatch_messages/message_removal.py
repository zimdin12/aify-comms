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
from service.api_core.operator_authz import authorize_operator, operator_key_from

router = domain_router()



#: Identities allowed to unsend a message they did not write. The operator's own surfaces — nothing
#: agent-facing — because an unsend is destructive and reaches other agents' inboxes.
# RETIRED: the actor vocabulary moved to service/api_core/operator_authz.py (OPERATOR_ACTORS),
# because naming an operator no longer grants anything on its own — see that module.


@router.delete("/messages/{message_id}")
async def unsend_message(message_id: str, request: Request, requestedBy: str = ""):
    """Delete a message by ID, on behalf of its SENDER or the operator.

    H4 (external review 2026-08-18): this took an id and nothing else. No acting agent, no ownership
    check, and `comms_unsend` exposes it to every agent — so agent B could delete an A->C message by
    id, and a channel row triggered a `LIKE '{id}-%'` fan-out delete of every recipient copy. Message
    ids are not secret: they appear in inbox listings and in dispatch text.

    Ruled by comms-senior-dev: sender-plus-operator, actor MANDATORY and service-enforced, absence
    FAILS CLOSED. An optional actor would be theatre — an attacker simply omits it.

    R5-H1 (2026-08-18): the operator exemption used to be granted on the ACTOR STRING alone, so any
    caller could pass requestedBy="operator" and delete any message — and the audit trail then named
    the operator as the one who did it. Operator privilege is now VERIFIED against a configured key
    (`service/api_core/operator_authz.py`); the string records who acted and grants nothing.

    HONEST LIMIT, stated because the fix should not be read as more than it is: an ordinary agent
    actor is still self-asserted. Every agent shares one API key, so the service cannot
    cryptographically distinguish agents from each other, and one can still name another as the
    sender. What the actor check stops is the accident and the casual cross-delete; what the operator
    key stops is the universal one. Per-agent credentials remain a separate, larger question.
    """
    actor = str(requestedBy or "").strip()
    if not actor:
        raise HTTPException(
            400,
            "unsend requires `requestedBy` (the agent unsending its own message, or an operator "
            "surface). Refused rather than defaulted: a missing actor used to mean 'anyone may "
            "delete anything'.",
        )
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Message '{message_id}' not found")
        author = str(row["from_agent"] or "").strip()
        may_override = authorize_operator(actor, request, operator_key_from(request), action="unsend")
        if not may_override and actor != author:
            # 403, not 404: the row exists and the caller may well know it does. Pretending it is
            # absent would send an agent hunting for a message it can see in its own inbox.
            raise HTTPException(
                403,
                f"'{actor}' cannot unsend a message written by '{author or '(unknown)'}'. "
                f"Only the sender or an operator surface may take a message back.",
            )
        message_ids = [message_id]
        if (row["source"] or "") == "channel" and not (row["to_agent"] or ""):
            # The CANONICAL channel post (no to_agent) owns its per-recipient copies, and removing it
            # must remove them — a post half-deleted is worse than either outcome. The ruling's
            # constraint is that authorization happens on THIS row first, which it now has: the
            # sender check above ran against the canonical post before we got here, so the `LIKE`
            # below is scoped by a row we are already permitted to delete rather than being the
            # authority itself.
            fanout_cursor = await db.execute(
                "SELECT id FROM messages WHERE id LIKE ? AND channel = ? AND source = 'channel'",
                (f"{message_id}-%", row["channel"] or ""),
            )
            message_ids.extend([fanout["id"] for fanout in await fanout_cursor.fetchall()])
        elif (row["source"] or "") == "channel" and (row["to_agent"] or ""):
            # A RECIPIENT COPY was named. Resolve back to the canonical post and authorize on that,
            # rather than letting one recipient's copy be deleted out from under a channel — the
            # copies are not independently ownable, and the id shape (`<canonical>-<recipient>`) is
            # the only link between them.
            canonical_id = message_id.rsplit("-", 1)[0] if "-" in message_id else ""
            canonical = None
            if canonical_id:
                canonical = await (await db.execute(
                    "SELECT * FROM messages WHERE id = ? AND source = 'channel'", (canonical_id,)
                )).fetchone()
            if canonical is None:
                raise HTTPException(
                    409,
                    f"'{message_id}' is a per-recipient channel copy whose canonical post could not "
                    f"be resolved; unsend the canonical post instead.",
                )
            canonical_author = str(canonical["from_agent"] or "").strip()
            # THE SECOND EXEMPTION ON THIS ENDPOINT, and it had to be found separately. Retiring the
            # constant above left this one referencing a name that no longer existed — it would have
            # raised NameError on the channel-recipient-copy path, and before that it was the same
            # R5-H1 bypass a second time. A guard behind a guard: fixing the first one is not fixing
            # the endpoint.
            if not may_override and actor != canonical_author:
                raise HTTPException(
                    403,
                    f"'{actor}' cannot unsend a channel post written by "
                    f"'{canonical_author or '(unknown)'}'.",
                )
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
