"""The text a dispatch reports, and the error classification behind it. PURE — no DB, no router.

Layer-0 slice of the v0.5.4 decomposition: subjects and state labels the dashboard renders, the
cold-start refusal message, and the provider-rate-limit predicate that decides which notice a failed
run carries.

`COLDSTART_REFUSED_PREFIX` moved here WITH `_coldstart_refusal_message`. Under the v0.5.3 rule a
constant the carrier still read stayed in the carrier behind an accessor — but that rule was for the
router era, when the carrier was the legitimate owner. In the layer phase a leaf must not import the
control plane at all, so the constant follows its function and the control plane becomes one reader
among several. `_coldstart_refusal` still reads it and imports it from here.

`_dispatch_buffer_full_hint` was deliberately LEFT BEHIND even though it is a pure leaf and would
have fitted. Its `_DISPATCH_BUFFER_CAP` is also read by `_append_pending_dispatch_body` and
`_create_dispatch_runs` — dispatch-core functions, not text — so pulling the cap in here would make a
formatting module the owner of a queue limit. It moves when the dispatch core does.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from service.api_core.serialization import _clip_text, _quote_untrusted_subject


COLDSTART_REFUSED_PREFIX = "coldstart-refused: "


def _format_dispatch_state(active_row, queued_count: int) -> dict[str, Any]:
    active = None
    if active_row:
        active = {
            "runId": active_row["id"],
            "status": active_row["status"],
            "subject": active_row["subject"],
            "from": active_row["from_agent"],
            "dispatchMode": active_row["dispatch_mode"] or "",
            "executionMode": active_row["execution_mode"] or "managed",
            "runtime": active_row["runtime"] or "",
            "claimBridgeId": active_row["claim_bridge_id"] or "",
            "requestedAt": active_row["requested_at"] or "",
            "startedAt": active_row["started_at"] or active_row["claimed_at"] or "",
        }
    return {
        "hasActiveRun": bool(active),
        "activeRun": active,
        "queuedRuns": max(int(queued_count or 0), 0),
    }


def _coldstart_refusal_message(warnings: Optional[list[str]], runtime: str) -> str:
    """Render the REAL reason cold-start refused, falling back to the generic sentence.

    See COLDSTART_REFUSED_PREFIX. Falls back only when no reason was recorded, so a path that
    somehow refuses without one degrades to the old wording rather than to silence.
    """
    for entry in reversed(list(warnings or [])):
        text = str(entry or "")
        if text.startswith(COLDSTART_REFUSED_PREFIX):
            return f"Cannot start managed {runtime} for this agent: {text[len(COLDSTART_REFUSED_PREFIX):]}"
    return (
        f"Cannot start managed {runtime} for this agent; no reason was recorded. "
        f"Check the environment bridge advertises {runtime}, and whether a spawn is already in flight."
    )


def _render_pending_dispatch_item(
    index: int,
    *,
    from_agent: str,
    message_type: str,
    subject: str,
    body: str,
    priority: str,
    message_id: str = "",
    in_reply_to: str = "",
    requested_at: str = "",
) -> str:
    lines = [
        f"=== ITEM {index} ===",
        f"From: {from_agent or 'unknown'}",
        f"Type: {message_type or 'request'}",
        # QUOTED: this is somebody else's subject line, not an instruction to the reader.
        f"Subject: {_quote_untrusted_subject(subject, 240)}",
        f"Priority: {priority or 'normal'}",
    ]
    if requested_at:
        lines.append(f"At: {requested_at}")
    if message_id:
        lines.append(f"MessageId: {message_id}")
        lines.append("Full details are in the inbox. Read them there if you need the complete context.")
        preview = _clip_text(body or "", 240)
        if preview:
            lines.extend(["Body preview:", preview])
    else:
        if in_reply_to:
            lines.append(f"InReplyTo: {in_reply_to}")
        lines.extend(["Body:", str(body or "").strip()])
    return "\n".join(lines).strip()


def _build_pending_dispatch_subject(count: int, latest_subject: str) -> str:
    # QUOTED for the same reason as the item renderer: `Restart lc-coder` as a bare subject line
    # reads as a command to whoever receives this summary, and at least one agent acted on exactly
    # that. A quoted string is plainly a quotation.
    latest = _quote_untrusted_subject(latest_subject, 80)
    if count <= 1:
        return latest
    return f"Pending updates ({count}); latest: {latest}"


def _auto_handoff_subject_for_run(row) -> str:
    subject = str((row["subject"] if row else "") or (row["id"] if row else "") or "dispatch result").strip()
    status = str((row["status"] if row else "") or "").strip().lower()
    if status == "failed":
        return f"[FAILED] {subject}"
    if status == "cancelled":
        return f"[CANCELLED] {subject}"
    return f"Re: {subject}"


def _is_provider_rate_limit_error(text: str) -> bool:
    """A model-provider rate / usage / capacity limit (NOT an aify-comms bug).

    These surface as run failures with provider error text; the sender deserves a clear,
    actionable note ("retry shortly") rather than a raw API error string.
    """
    t = str(text or "").lower()
    if any(
        s in t
        for s in (
            "temporarily limiting requests",
            "rate limit",
            "rate-limit",
            "ratelimit",
            "hit your limit",
            "usage limit",
            "too many requests",
            "overloaded",
            "quota exceeded",
            "usage quota",
        )
    ):
        return True
    # Bare HTTP status codes only count as a throttle when they appear as a standalone token
    # (word-bounded) — so "code 429"/"429 Too Many Requests" match but "exited with code 4290"
    # or a token count like "529 tokens" do not.
    return bool(re.search(r"\b(429|529)\b", t))
