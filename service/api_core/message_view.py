"""Shaping a message row for the inbox API.

Moved out of `service/routers/dispatch_messages/messages.py` in v0.5.4, byte-identical. A view-model,
not a serialization primitive — `api_core/serialization.py` holds things like `_clip_text` and
`_json_loads_or`, which know nothing about messages, and putting a domain shape in there would have
made that module the place everything drifts into.
"""
from __future__ import annotations

from typing import Any

from service.api_core.serialization import _clip_text

def _serialize_inbox_message(row, *, include_body: bool) -> dict[str, Any]:
    msg = {
        "id": row["id"],
        "from": row["from_agent"],
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
