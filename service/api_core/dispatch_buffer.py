"""The pending-dispatch buffer: how big it may get, how work is appended to it, and what to say when it
is full.

Created in v0.5.4 to resolve a placement question `api_core/dispatch_text.py` had recorded and refused to
answer badly. `_dispatch_buffer_full_hint` is a pure leaf that would have fitted in dispatch_text, and it
was deliberately left in the carrier because `_DISPATCH_BUFFER_CAP` is a QUEUE LIMIT, not a formatting
concern — making a text module own it would have been filing by convenience. The right answer was a module
for the buffer itself, which is this one.

THE CAP IS A NEUTRAL OWNER HERE, not a follower: `_create_dispatch_runs` still reads it from the control
plane, so no single function owns it, and it lives with the two functions whose whole subject it is.
`_MERGED_DISPATCH_FOOTER` went the other way — its only reader is `_append_pending_dispatch_body` below,
but it is a MARKER and belongs beside `_MERGED_DISPATCH_HEADER` in dispatch_text.py. A delimiter pair split
across two modules is how one half gets edited alone.

WHY A CAP AT ALL, and why the hint matters as much as the limit: when a busy agent's merged buffer fills,
the sender needs to know THAT is why the send did not land, not that the agent was unreachable. A silent
truncation and an offline agent look identical from the outside.

DB ACCESS: none. Both functions are handed text and settings.
"""

from __future__ import annotations

from typing import Any, Optional

from service.api_core.dispatch_text import (
    _MERGED_DISPATCH_FOOTER,
    _MERGED_DISPATCH_HEADER,
    _pending_dispatch_count,
    _render_pending_dispatch_item,
)
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode


_DISPATCH_BUFFER_CAP = 10


def _append_pending_dispatch_body(
    existing_run,
    *,
    from_agent: str,
    message_type: str,
    subject: str,
    body: str,
    priority: str,
    requested_at: str,
    message_id: str = "",
    in_reply_to: str = "",
) -> Optional[tuple[str, int]]:
    """
    Returns (merged_body, item_count) on success, or None if the buffer cap
    is already at _DISPATCH_BUFFER_CAP and the new item cannot be appended.
    """
    existing_body = str(existing_run["body"] or "")
    if existing_body.startswith(_MERGED_DISPATCH_HEADER):
        current_count = _pending_dispatch_count(existing_body)
        if current_count >= _DISPATCH_BUFFER_CAP:
            return None
        count = current_count + 1
        new_item = _render_pending_dispatch_item(
            count,
            from_agent=from_agent,
            message_type=message_type,
            subject=subject,
            body=body,
            priority=priority,
            message_id=message_id,
            in_reply_to=in_reply_to,
            requested_at=requested_at,
        )
        merged_body = existing_body.replace(_MERGED_DISPATCH_FOOTER, f"\n\n{new_item}\n{_MERGED_DISPATCH_FOOTER}")
        return merged_body, count

    first_item = _render_pending_dispatch_item(
        1,
        from_agent=str(existing_run["from_agent"] or ""),
        message_type=str(existing_run["message_type"] or ""),
        subject=str(existing_run["subject"] or ""),
        body=str(existing_run["body"] or ""),
        priority=str(existing_run["priority"] or "normal"),
        message_id=str(existing_run["message_id"] or ""),
        in_reply_to=str(existing_run["in_reply_to"] or ""),
        requested_at=str(existing_run["requested_at"] or ""),
    )
    second_item = _render_pending_dispatch_item(
        2,
        from_agent=from_agent,
        message_type=message_type,
        subject=subject,
        body=body,
        priority=priority,
        message_id=message_id,
        in_reply_to=in_reply_to,
        requested_at=requested_at,
    )
    merged_body = "\n".join([
        _MERGED_DISPATCH_HEADER,
        f"Additional dispatches arrived while another run was active (cap: {_DISPATCH_BUFFER_CAP} items).",
        "Process the buffered items in order. For message-backed items, use comms_inbox(...) if you need the full original text.",
        "",
        first_item,
        "",
        second_item,
        _MERGED_DISPATCH_FOOTER,
    ]).strip()
    return merged_body, 2


def _dispatch_buffer_full_hint(
    recipient_id: str,
    row,
    *,
    from_agent: str,
    current_count: int,
    recipient_status: str,
    has_active_run: bool,
) -> dict[str, Any]:
    runtime = _normalize_runtime((row["runtime"] if row else "") or "generic")
    session_mode = _normalize_session_mode((row["session_mode"] if row else "") or "resident")
    return {
        "targetAgentId": recipient_id,
        "reason": "buffer_full",
        "runtime": runtime,
        "sessionMode": session_mode,
        "bufferCap": _DISPATCH_BUFFER_CAP,
        "bufferedCount": current_count,
        "recipientStatus": recipient_status,
        "hasActiveRun": has_active_run,
        "fromAgent": from_agent,
        "fix": (
            f"Target agent already has {current_count} buffered dispatches from {from_agent} "
            f"(cap: {_DISPATCH_BUFFER_CAP}). Wait for the current run to drain, "
            f"interrupt the active run with comms_run_interrupt, or call "
            f"comms_agent_info to inspect the queue before retrying."
        ),
    }
