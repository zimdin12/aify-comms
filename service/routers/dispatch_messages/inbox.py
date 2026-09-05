"""The inbox surface: what an agent has been sent, and marking that it has been seen.

Extracted from `service/routers/dispatch_messages/messages.py` in v0.5.4, with a closure measured
before the move: `api_core` and `service` leaves only, nothing local to `messages.py` and nothing
from `shared.py`. What stays behind is `send_message` — the write path — which is a different thing
to be careful about.

READING AN INBOX CHANGES IT, and that is why `set_message_read_state` is here rather than with the
deletions. `get_inbox` settles read receipts as a side effect of being called: the unread count an
agent sees is a consequence of what it has already fetched, so the query and the state it advances
are one subject. Anything that treats them as separate ends up with a count that disagrees with the
list it came from.

`recent_messages` and `search_messages` do NOT settle anything, deliberately — browsing must not
silently mark work as seen, or an agent loses the distinction between "I have read this" and "this
scrolled past me".

Bodies and route decorators are byte-identical to what stood in `messages.py`. The router is built
through `domain_router()`, which rejects a hand-passed `route_class`, so a new surface cannot opt out
of the bounded SQLite write-lock retry.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Query, Request

from service.api_core.inbox_read_receipts import _settle_inbox_read
from service.api_core.message_view import _serialize_inbox_message
from service.api_core.routing import domain_router
from service.api_core.serialization import _clip_text
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db

router = domain_router()



@router.get("/messages/inbox/{agent_id}")
async def get_inbox(
    agent_id: str, request: Request,
    filter: str = Query("unread", pattern="^(unread|read|all)$"),
    fromAgent: Optional[str] = None, fromRole: Optional[str] = None,
    type: Optional[str] = None, limit: int = Query(200, ge=1, le=1000),
    mode: str = Query("full", pattern="^(full|headers)$"),
    messageId: Optional[str] = None,
    peek: Optional[str] = None,
):
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        include_body = mode != "headers"
        # The page and its total are COMPOSED from one source clause. They used to be carved out of
        # each other: the total was the row query with its SELECT string-replaced and everything
        # from `rfind("LIMIT")` chopped off. All four variants below happen to start with one of
        # the two literals that replace looked for, so it worked -- and adding a single column to
        # any of them makes both replaces no-op. The truncation still removes `LIMIT ?` and the
        # matching value, so the parameter count still agrees and the query RUNS: `fetchone()[0]`
        # is then `m.id`, and a message id is returned as `total` with no error raised anywhere.
        # Reproduced in a scratch database before this was changed, rather than argued from
        # reading.
        if messageId:
            select_clause = "SELECT m.*, r.read_at"
            source = """FROM messages m
                      LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                      WHERE m.to_agent = ? AND m.id = ?"""
            params = [agent_id, agent_id, messageId]
        else:
            # Build query
            if filter == "unread":
                select_clause = "SELECT m.*, NULL as read_at"
                source = """FROM messages m
                          LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                          WHERE m.to_agent = ? AND r.message_id IS NULL"""
                params = [agent_id, agent_id]
            elif filter == "read":
                select_clause = "SELECT m.*, r.read_at"
                source = """FROM messages m
                          JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                          WHERE m.to_agent = ?"""
                params = [agent_id, agent_id]
            else:
                select_clause = "SELECT m.*, r.read_at"
                source = """FROM messages m
                          LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                          WHERE m.to_agent = ?"""
                params = [agent_id, agent_id]

        if fromAgent:
            source += " AND m.from_agent = ?"
            params.append(fromAgent)
        if fromRole:
            source += " AND m.from_agent IN (SELECT id FROM agents WHERE role = ?)"
            params.append(fromRole)
        if type:
            source += " AND m.type = ?"
            params.append(type)

        cursor = await db.execute(
            f"{select_clause} {source} ORDER BY m.timestamp DESC LIMIT ?",
            params + [1 if messageId else limit],
        )
        rows = await cursor.fetchall()

        # Count total (without limit)
        c = await db.execute(f"SELECT COUNT(*) {source}", params)
        total = (await c.fetchone())[0]

        messages = []
        for row in rows:
            msg = _serialize_inbox_message(row, include_body=include_body)
            # Include parent message context for replies
            if row["in_reply_to"]:
                pc = await db.execute("SELECT from_agent, subject, body FROM messages WHERE id = ?", (row["in_reply_to"],))
                parent = await pc.fetchone()
                if parent:
                    msg["parentContext"] = {"from": parent["from_agent"], "subject": parent["subject"], "preview": (parent["body"] or "")[:100]}
            messages.append(msg)

        # Mark as read + update status (unless peek)
        await _settle_inbox_read(db, messages, agent_id, peek)

        # HOW MANY ARE UNREAD, GLOBALLY AND NOW. Two words doing work:
        #
        # GLOBALLY: this is the addressed agent's whole unread population, NOT the count matching this
        # query. `total` is query-scoped and stays that way -- a `messageId` view has a total of one,
        # and a `fromAgent` view has a subset -- so reusing it here reported "1 unread" for an agent
        # with hundreds.
        #
        # NOW: computed AFTER `_settle_inbox_read`, which marks the returned messages read. The first
        # version reused `total`, taken before that write, and a reviewer executed the consequence:
        # three unread, `limit=1`, non-peek -> the response said `unreadTotal: 3` while the current
        # answer was 2, because it had just read one of them. Reproduced here before fixing.
        #
        # The optimisation that started this matched the SPELLING of the filter rather than its
        # semantics. `total` is only the same number when the query is unread-scoped AND unfiltered AND
        # nothing settled, so that is exactly the condition -- and `peek` is part of it, not an aside.
        #
        # `bool(peek)` IS THE SAME EXPRESSION `_settle_inbox_read` GATES ON (`if not peek`). Writing a
        # tidier predicate here would be a second implementation of one question, which is how this
        # endpoint got into trouble in the first place. Worth knowing while reading it: that gate takes
        # any non-empty string, so `peek=false` reads as peek -- surprising, load-bearing for callers
        # that pass it, and left alone here rather than changed under cover of an unrelated fix.
        unread_is_already_total = (
            filter == "unread"
            and not messageId
            and not fromAgent
            and not fromRole
            and not type
            and bool(peek)
        )
        if unread_is_already_total:
            unread_total = total
        else:
            unread_cursor = await db.execute(
                "SELECT COUNT(*) FROM messages m "
                "LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ? "
                "WHERE m.to_agent = ? AND r.message_id IS NULL",
                (agent_id, agent_id),
            )
            unread_total = (await unread_cursor.fetchone())[0]
        return {
            "total": total,
            "showing": len(messages),
            "unreadTotal": unread_total,
            "messages": messages,
        }
    finally:
        await db.close()



@router.get("/messages/recent")
async def recent_messages(
    request: Request,
    limit: int = Query(80, ge=1, le=250),
    before: int | None = Query(None, ge=0),
):
    """Recent human-scale message activity without channel fanout duplicates.

    `before` PAGES BACKWARDS INTO HISTORY, and it exists because there was no way to. The dashboard
    holds ONE global window of the newest `limit` rows and filters it per conversation, so an
    operator whose manager sent 137 messages could see the 43 that happened to fall inside the
    newest 80 -- and reported it as messages being deleted. Nothing was deleted; the other 94 were
    never asked for, and no parameter existed to ask.

    INCLUSIVE (`<=`), not exclusive, and the client de-duplicates by id. `timestamp` is
    milliseconds and ids are unique, but two messages CAN share a millisecond -- a channel fanout
    writes several in one loop. An exclusive cursor drops every row tying with the page boundary,
    which is silent message loss of exactly the kind being fixed here; an inclusive one re-sends a
    row the client already holds, which costs nothing. Given the choice between a gap and an
    overlap in a history view, the overlap is the only safe one.

    ORDER BY IS DELIBERATELY UNCHANGED. Making the sort a compound `(timestamp, id)` would give a
    total order and let the cursor be exclusive, but `idx_messages_timestamp` indexes `timestamp`
    alone -- so the planner would fall back to `USE TEMP B-TREE FOR ORDER BY` over the 33k rows this
    predicate matches, on an endpoint every open tab polls every 15s. That is the exact regression
    the `+m.source` comment below exists to prevent, and it is not worth buying an exclusive cursor.
    """
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT m.*, rr.read_at AS read_at
            FROM messages m
            LEFT JOIN read_receipts rr ON rr.message_id = m.id AND rr.agent_id = m.to_agent
            WHERE
              (? IS NULL OR m.timestamp <= ?)
              AND
              -- `+m.source` TELLS SQLite NOT TO INDEX THESE TWO TERMS, and that is the whole
              -- optimisation. Indexed, the planner takes `MULTI-INDEX OR` over
              -- `idx_messages_source` and then `USE TEMP B-TREE FOR ORDER BY`: it materialises and
              -- sorts every matching row to take the newest 81. Measured against the live database
              -- 2026-08-29 -- 33,619 of 34,107 messages match this predicate, so that is a 33,619-row
              -- sort per poll, growing with a table that has no retention. Un-indexed, it walks
              -- `idx_messages_timestamp` in order and stops at 81. Same 81 rows, same order, verified.
              (
                (+m.source = 'direct' AND m.to_agent IS NOT NULL)
                OR (+m.source = 'channel' AND m.to_agent IS NULL)
              )
            ORDER BY m.timestamp DESC
            LIMIT ?
            """,
            (before, before, limit + 1),
        )
        # ONE ROW WIDER THAN THE PAGE, so the response can say whether this is the whole answer -- the
        # same shape as /sessions, /dispatch/runs, /contracts and /terminals.
        rows = await cursor.fetchall()
        truncated = len(rows) > limit
        rows = rows[:limit]
        messages = []
        for row in rows:
            messages.append({
                "id": row["id"],
                "from": row["from_agent"],
                "to": row["to_agent"],
                "channel": row["channel"],
                "source": row["source"],
                "type": row["type"],
                "subject": row["subject"],
                # Full body so the dashboard chat renders complete messages — the bubble
                # reads `m.body` and previously fell back to the 240-char `preview`, so
                # EVERY message was truncated to 240 chars in the conversation view
                # (operator-reported 2026-07-10). `preview` is kept for the light DM-rail
                # one-liner; `body` carries the real content.
                "body": row["body"] or "",
                "preview": _clip_text(row["body"] or "", 240),
                "priority": row["priority"],
                "timestamp": row["timestamp"],
                "inReplyTo": row["in_reply_to"],
                "dispatchRequested": bool(row["dispatch_requested"]) if "dispatch_requested" in row.keys() else False,
                # Recipient-perspective read state (rr.agent_id = to_agent) so the dashboard's
                # unread badges work; channel rows (to_agent NULL) have no receipt → read=False.
                "read": ("read_at" in row.keys()) and (row["read_at"] is not None),
                "readAt": row["read_at"] if "read_at" in row.keys() else None,
            })
        # `total` IS GONE AND THAT IS THE POINT. It was `len(messages)` -- the length of the PAGE,
        # under a name that promises a count of the whole. Measured on the operator's database
        # 2026-08-29: this query's WHERE matches 33,612 rows and the field reported 80. A consumer
        # asking "is there more" got "no", always.
        #
        # NOT REPLACED WITH A REAL COUNT. `SELECT COUNT(*)` over the same WHERE measured 19.6 ms
        # median (7 runs, min 19.2, max 20.8) inside the container, and the dashboard polls this every
        # 15s per open tab. That is a real cost for a number no reader wants: searching aify-comms,
        # aify-wrapper and aify-env found no consumer of this field -- `compact-tool.mjs` reads
        # `.messages` alone, and the dashboard reads neither. I cannot see consumers outside those
        # three repos, so this is a removal made on the evidence available, not on proof of absence.
        #
        # `truncated` answers the question the name was reaching for, exactly, and costs nothing.
        return {"ok": True, "messages": messages, "truncated": truncated, "limit": limit, "before": before}
    finally:
        await db.close()



@router.get("/messages/search")
async def search_messages(
    request: Request, query: str = "",
    agentId: Optional[str] = None,
    scope: str = Query("all", pattern="^(inbox|shared|all)$"),
    limit: int = Query(10, ge=1, le=100),
):
    db = await get_db()
    try:
        q = f"%{query.lower()}%"
        results = []
        # What was ACTUALLY consulted. Returned to the caller because an empty result from this
        # endpoint was being read as "no such message exists" when messages had never been
        # searched at all — see below. A search that cannot say what it searched cannot support an
        # absence claim, and this one was being used to license work on exactly that basis.
        searched: list[str] = []
        skipped: list[str] = []

        if scope in ("inbox", "all"):
            if agentId:
                # BOTH DIRECTIONS. This was `to_agent = ?` only, so an agent could not find
                # messages it had SENT. Reported 2026-08-10 by sc-manager, who searched for a term
                # it had dispatched itself and got nothing: of 101 messages containing "P0-Q", 49
                # were TO it (findable) and 52 were FROM it (invisible). "My own record" plainly
                # includes what I said, not just what I was told.
                cursor = await db.execute(
                    "SELECT * FROM messages WHERE (to_agent = ? OR from_agent = ?) "
                    "AND (LOWER(subject) LIKE ? OR LOWER(body) LIKE ? OR LOWER(from_agent) LIKE ?) "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (agentId, agentId, q, q, q, limit)
                )
                for row in await cursor.fetchall():
                    results.append({
                        "type": "message", "id": row["id"], "from": row["from_agent"],
                        "to": row["to_agent"], "subject": row["subject"],
                        "preview": (row["body"] or "")[:150],
                    })
                searched.append("messages")
            else:
                # NO agentId MEANS MESSAGES WERE NEVER SEARCHED, and the old response gave no sign
                # of it — it just returned artifact hits, or nothing. That silence is what makes
                # this dangerous rather than merely limited: a caller using this to check "was
                # this already ruled?" reads the empty result as "no", and proceeds. It FAILS
                # OPEN. Naming the omission is the fix; the access model is unchanged.
                skipped.append("messages (no agentId supplied — messages were NOT searched)")

        if scope in ("shared", "all"):
            cursor = await db.execute(
                "SELECT * FROM shared_artifacts WHERE LOWER(name) LIKE ? OR LOWER(description) LIKE ? LIMIT ?",
                (q, q, limit)
            )
            for row in await cursor.fetchall():
                results.append({
                    "type": "shared", "name": row["name"], "from": row["from_agent"],
                    "description": row["description"], "size": row["size"],
                })
            searched.append("shared")

        return {
            "results": results[:limit],
            "total": len(results),
            "searched": searched,
            "skipped": skipped,
        }
    finally:
        await db.close()



@router.post("/messages/{message_id}/read")
async def set_message_read_state(message_id: str, request: Request):
    body = await request.json()
    agent_id = str(body.get("agentId") or "").strip()
    read = bool(body.get("read", True))
    if not agent_id:
        raise HTTPException(400, "Need agentId")
    validate_name(agent_id, "agent ID")

    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, to_agent FROM messages WHERE id = ?", (message_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Message '{message_id}' not found")
        if row["to_agent"] != agent_id:
            raise HTTPException(403, f'Message "{message_id}" is not addressed to "{agent_id}"')

        if read:
            await db.execute(
                "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                (message_id, agent_id, _now()),
            )
        else:
            await db.execute(
                "DELETE FROM read_receipts WHERE message_id = ? AND agent_id = ?",
                (message_id, agent_id),
            )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("message_read_state", {"id": message_id, "agentId": agent_id, "read": read})
        return {"ok": True, "id": message_id, "agentId": agent_id, "read": read}
    finally:
        await db.close()
