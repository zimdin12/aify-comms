"""Shaping a message row for the inbox API.

Moved out of `service/routers/dispatch_messages/messages.py` in v0.5.4, byte-identical. A view-model,
not a serialization primitive — `api_core/serialization.py` holds things like `_clip_text` and
`_json_loads_or`, which know nothing about messages, and putting a domain shape in there would have
made that module the place everything drifts into.
"""
from __future__ import annotations

from typing import Any

from service.api_core.serialization import _clip_text

def _serialize_inbox_message(row, *, include_body: bool, sender_registered: bool = True) -> dict[str, Any]:
    """One inbox row as the API shows it.

    `sender_registered` is decided by the CALLER, which has the whole page's senders and can answer
    it in one query. It is `True` by default so a caller that does not ask the question does not
    accidentally accuse every sender of being foreign -- a guard that fails open on a warning, which
    is the right direction for this one: a missing badge is a missing hint, a wrong badge is a lie
    about where a message came from.
    """
    msg = {
        "id": row["id"],
        "from": row["from_agent"],
        # WHETHER THE SENDER EXISTS HERE AT ALL. Nothing validates `from_agent` on the way in -- the
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
        msg["body"] = row["body"]
    if row["in_reply_to"]:
        msg["parentContext"] = None
    return msg
