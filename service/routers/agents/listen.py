"""The long-poll an agent parks on while it waits to be given something to do.

Extracted from `service/routers/agents/config.py` in v0.5.4. Closure measured before the move:
`service` leaves plus the listen-events accessor from `agents/shared.py`.

IT IS A LONG POLL, NOT A QUERY, and that is why it belongs on its own. Every other handler in this
package answers and returns; this one holds the request open, sleeping in a loop until a waiter is
woken or the deadline passes. Its cost is a held connection rather than a query, and its failure
mode is an agent that never wakes rather than a wrong answer.

THE WAITER REGISTRY HAS EXACTLY ONE OWNER, and reaching it through the accessor rather than copying
the dict is the whole point of that shim: two copies would put a waiter in one and the wake in the
other, and the agent would sleep through work that was delivered to it.

Body and route decorator byte-identical.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import Query, Request

from service.api_core.agent_sessions import _mark_agent_present
from service.api_core.message_view import _registered_senders, _serialize_message
from service.api_core.routing import domain_router
from service.api_core.validation import validate_name
from service.clock import now as _now
from service.db import get_db
# Through the module, not by value: `_listen_events` is a process-global (see
# test_process_global_identity.py), and a by-value import would go stale if its owner rebound it.
from service import longpoll

router = domain_router()


def _watch_for_disconnect(request: Request) -> tuple[asyncio.Event, asyncio.Task]:
    """An event set when the caller goes, and the task that sets it.

    `request.is_disconnected()` cannot see it here: the app is wrapped in `BaseHTTPMiddleware`
    subclasses, under which it always answers False, and 0.7.0's guard was tested by calling the handler
    directly, around the middleware (v0.7.1 review, S2). Awaiting `receive()` does reach the
    `http.disconnect`. The request has no body, so nothing else is waiting to read it.
    """
    gone = asyncio.Event()

    async def watch() -> None:
        while True:
            message = await request.receive()
            if message.get("type") == "http.disconnect":
                gone.set()
                return

    return gone, asyncio.create_task(watch())



@router.get("/agents/{agent_id}/listen")
async def listen_for_messages(agent_id: str, request: Request, timeout: int = Query(300, ge=1, le=600),
                              markRead: bool = Query(True)):
    """Long-poll: blocks until agent has unread messages or timeout. Returns the messages.

    `markRead=false` leaves the receipts to the caller (v0.7.4): a caller that disconnects after the
    commit below cannot be helped from here, so the bridge asks for its messages unmarked and marks each
    one read once it holds it. The default keeps the old behaviour for bridges that do not ask.
    """
    validate_name(agent_id, "agent ID")

    # Set status to idle (waiting for work)
    db = await get_db()
    try:
        now = _now()
        await db.execute("UPDATE agents SET status = 'idle', last_seen = ? WHERE id = ?", (now, agent_id))
        await _mark_agent_present(db, agent_id, now)
        await db.commit()
    finally:
        await db.close()

    # Create/get wake-up event for this agent
    if agent_id not in longpoll._listen_events:
        longpoll._listen_events[agent_id] = asyncio.Event()
    event = longpoll._listen_events[agent_id]
    event.clear()

    gone, watcher = _watch_for_disconnect(request)
    try:
        return await _poll_until_work(agent_id, event, gone, timeout, mark_read=markRead)
    finally:
        watcher.cancel()


async def _poll_until_work(agent_id: str, event: asyncio.Event, gone: asyncio.Event, timeout: int,
                          *, mark_read: bool = True) -> dict:
    # Poll for unread messages, waiting on the event
    deadline = time.time() + timeout
    while time.time() < deadline:
        # A caller that has gone must not have its messages marked read: returning them to nobody
        # drops the agent's unread count for messages it never saw (v0.7 scan A13).
        if gone.is_set():
            return {"total": 0, "messages": []}
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM messages m LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ? WHERE m.to_agent = ? AND r.message_id IS NULL",
                (agent_id, agent_id)
            )
            unread = (await cursor.fetchone())[0]
            if unread > 0:
                # Fetch and return the messages (mark as read)
                now = _now()
                mc = await db.execute(
                    "SELECT m.*, NULL AS read_at FROM messages m LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ? WHERE m.to_agent = ? AND r.message_id IS NULL ORDER BY m.timestamp DESC",
                    (agent_id, agent_id)
                )
                rows = await mc.fetchall()
                known_senders = await _registered_senders(db, (row["from_agent"] for row in rows))
                messages = []
                for row in rows:
                    msg = _serialize_message(
                        row, include_body=True,
                        sender_registered=str(row["from_agent"] or "") in known_senders,
                    )
                    # Absent, not null, when the parent is gone: this route never sent the null.
                    msg.pop("parentContext", None)
                    # Parent context for replies
                    if row["in_reply_to"]:
                        pc = await db.execute("SELECT from_agent, subject, body FROM messages WHERE id = ?", (row["in_reply_to"],))
                        parent = await pc.fetchone()
                        if parent:
                            msg["parentContext"] = {"from": parent["from_agent"], "subject": parent["subject"], "preview": (parent["body"] or "")[:100]}
                    messages.append(msg)
                    if mark_read:
                        await db.execute("INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)", (row["id"], agent_id, now))

                # ...and asked again at the last moment. The fetch and the receipts above took awaits, and a
                # caller that went during them must not have them committed (v0.7 review). A caller
                # that goes after the commit cannot be helped from here.
                if gone.is_set():
                    await db.rollback()
                    return {"total": 0, "messages": []}
                # Set status to working
                await db.execute("UPDATE agents SET status = 'working', last_seen = ? WHERE id = ?", (now, agent_id))
                await db.commit()
                return {"total": len(messages), "messages": messages}
        finally:
            await db.close()

        # Wait for a wake-up, the caller leaving, or 2 seconds, whichever comes first.
        woken = asyncio.ensure_future(event.wait())
        left = asyncio.ensure_future(gone.wait())
        done, pending = await asyncio.wait({woken, left}, timeout=2.0, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if woken in done:
            event.clear()

    # Timeout — no messages arrived
    return {"total": 0, "messages": []}
