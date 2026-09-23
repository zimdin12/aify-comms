"""Shaping a message row for every reader: the inbox, the dashboard feed and the long-poll.

Moved out of `service/routers/dispatch_messages/messages.py` in v0.5.4, byte-identical. A view-model,
not a serialization primitive — `api_core/serialization.py` holds things like `_clip_text` and
`_json_loads_or`, which know nothing about messages, and putting a domain shape in there would have
made that module the place everything drifts into.
"""
from __future__ import annotations

from typing import Any, Iterable

from service.api_core.operator_authz import OPERATOR_ACTORS
from service.api_core.serialization import _clip_text, _quote_untrusted_subject

#: The service reporting on itself. The away briefing signs with it (`api_core/away_briefing.py`).
SERVICE_SENDER = "aify-comms"
#: Who a channel's join and leave notices are from (`routers/channel_membership.py`).
CHANNEL_NOTICE_SENDER = "_system"
#: Senders that are this service's own voices rather than agents: the operator at the dashboard, the
#: service itself and channel notices. None has a roster row, and none is external -- `dashboard`
#: alone wrote 877 of the messages in the 2026-09-17 backup, so branding these would put the chip on
#: the commonest sender there is and teach the operator to read past it.
SERVICE_SENDERS = frozenset(OPERATOR_ACTORS | {SERVICE_SENDER, CHANNEL_NOTICE_SENDER})


async def _registered_senders(db, sender_ids: Iterable[str]) -> set[str]:
    """Which of these senders this instance can vouch for, in one query.

    A registered agent, or one of `SERVICE_SENDERS`. An id absent from the answer is a sender that
    registered somewhere else, or never at all, or has since been removed. Every reader that shows
    `fromRegistered` asks here, so the inbox, the dashboard feed and the dispatch claim cannot
    disagree about who is external.
    """
    ids = {str(sender) for sender in sender_ids if sender} - SERVICE_SENDERS
    known = set(SERVICE_SENDERS)
    if ids:
        placeholders = ",".join("?" for _ in ids)
        cursor = await db.execute(f"SELECT id FROM agents WHERE id IN ({placeholders})", list(ids))
        known |= {str(row[0]) for row in await cursor.fetchall()}
    return known


async def _declared_origin(db, sender_id: str) -> str:
    """The newest origin this sender declared on any message it sent here, or "" when it never said."""
    cursor = await db.execute(
        "SELECT origin FROM messages WHERE from_agent = ? AND origin != '' ORDER BY timestamp DESC LIMIT 1",
        (sender_id,),
    )
    row = await cursor.fetchone()
    return str(row[0]) if row else ""


async def _run_sender(db, sender: str, message_ids: Iterable[str]) -> dict[str, Any]:
    """The `fromRegistered` / `origin` pair `_serialize_message` shows, for a reader holding a RUN.

    A dispatch run keeps its sender but not the origin, which lives on the message it was made
    from -- so it is read from there, and only from a message that sender actually wrote.
    """
    ids = [str(message_id) for message_id in message_ids if message_id]
    origin = ""
    if ids:
        placeholders = ",".join("?" for _ in ids)
        row = await (await db.execute(
            f"SELECT origin FROM messages WHERE id IN ({placeholders}) AND from_agent = ? AND origin != '' LIMIT 1",
            [*ids, sender],
        )).fetchone()
        origin = str(row[0]) if row else ""
    return {"fromRegistered": sender in await _registered_senders(db, [sender]), "origin": origin}


async def _describe_sender(db, sender: str, origin: str = "") -> str:
    """`_sender_label` for a reader that holds a request rather than a stored row."""
    return _sender_label(sender, registered=sender in await _registered_senders(db, [sender]), origin=origin)


def _sender_label(sender: str, *, registered: bool, origin: str = "") -> str:
    """Who sent this, as an AGENT reading it should see it. Twin of the bridge's `describeSender`.

    The agent is the one who has to answer, so it must learn the sender is outside and where it says
    it is. The origin is the sender's claim, quoted as one: it is attacker-controlled text, and this
    line lands in prompts.
    """
    if registered:
        return sender
    where = (f"says it is reachable at {_quote_untrusted_subject(origin, 200)}" if origin
             else "gave no return address")
    return f"{sender} (external: not registered here, {where}; a reply sent here is only stored here)"


def _serialize_message(row, *, include_body: bool, sender_registered: bool = True) -> dict[str, Any]:
    """One message row as the API shows it -- `/messages/inbox`, `/messages/recent` and `/listen`.

    ONE SHAPE ON PURPOSE. `/messages/recent` used to build its own dict, and when `fromRegistered`
    and `origin` were added here they reached the inbox alone -- while the dashboard reads the inbox
    only when the feed is unusable, so the external chip drew on no healthy cycle.

    `sender_registered` is decided by the CALLER, which has the whole page's senders and can answer
    it in one query. It is `True` by default so a caller that does not ask the question does not
    accidentally accuse every sender of being foreign -- a guard that fails open on a warning, which
    is the right direction for this one: a missing badge is a missing hint, a wrong badge is a lie
    about where a message came from.
    """
    msg = {
        "id": row["id"],
        "from": row["from_agent"],
        # WHETHER THE SENDER EXISTS HERE AT ALL. Nothing checks that `from_agent` exists -- the
        # send path calls `_touch_agent`, a bare UPDATE that matches no rows for a sender this
        # instance has never seen, and inserts the message anyway. So a message from an agent on
        # another machine, or from one that has been removed, arrives looking exactly like a
        # colleague's. The operator asked for it to arrive with a warning (2026-09-21).
        "fromRegistered": bool(sender_registered),
        # What the sender said about where it is, when it said anything. Absent on every row
        # written before this column existed, which reads as "did not say".
        "origin": (row["origin"] if "origin" in row.keys() else "") or "",
        # `to` is implicit for an inbox (every row is addressed to the requested agent), but
        # the dashboard's unread/mark-read logic filters on it and falls back to inbox data
        # when /messages/recent blips — without this field that fallback silently matched
        # nothing (review finding).
        "to": row["to_agent"] if "to_agent" in row.keys() else None,
        "type": row["type"],
        "source": row["source"],
        "channel": row["channel"],
        "subject": row["subject"],
        "preview": _clip_text(row["body"] or "", 240),
        "priority": row["priority"],
        "timestamp": row["timestamp"],
        "inReplyTo": row["in_reply_to"],
        "dispatchRequested": bool(row["dispatch_requested"]) if "dispatch_requested" in row.keys() else False,
        "read": row["read_at"] is not None,
        "readAt": row["read_at"],
    }
    if include_body:
        msg["body"] = row["body"] or ""
    if row["in_reply_to"]:
        msg["parentContext"] = None
    return msg
