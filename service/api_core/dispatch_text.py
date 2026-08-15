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
have fitted, because its `_DISPATCH_BUFFER_CAP` is a QUEUE LIMIT and making a formatting module own
one would have been filing by convenience. That reasoning held: in v0.5.4 the cap and both its
functions went to `api_core/dispatch_buffer.py` instead, and this module kept only the markers.
`_MERGED_DISPATCH_FOOTER` arrived here at the same time, beside the header it pairs with — the two
are one vocabulary and splitting a delimiter pair across modules is how one of them gets edited alone.
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
        # NEUTRALISED first: quoting stops it reading as a command but not from forging the buffer
        # markers this module parses — see `_neutralise_buffer_markers`.
        f"Subject: {_quote_untrusted_subject(_neutralise_buffer_markers(subject), 240)}",
        f"Priority: {priority or 'normal'}",
    ]
    if requested_at:
        lines.append(f"At: {requested_at}")
    if message_id:
        lines.append(f"MessageId: {message_id}")
        lines.append("Full details are in the inbox. Read them there if you need the complete context.")
        preview = _clip_text(_neutralise_buffer_markers(body), 240)
        if preview:
            lines.extend(["Body preview:", preview])
    else:
        if in_reply_to:
            lines.append(f"InReplyTo: {in_reply_to}")
        lines.extend(["Body:", _neutralise_buffer_markers(body).strip()])
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


# v0.5.4: `_MERGED_DISPATCH_HEADER` and `_pending_dispatch_count` arrived from the control plane.
#
# The constant is here as a NEUTRAL owner, not as a follower. Two functions read it and only one of them
# moved: `_append_pending_dispatch_body` (69 lines, and it also reads _DISPATCH_BUFFER_CAP and
# _MERGED_DISPATCH_FOOTER) stays in the carrier for now, so it imports this. A constant with a reader on
# each side of a boundary belongs to neither of them — it belongs where the vocabulary lives, which for
# the marker that identifies a merged dispatch body is the module that parses dispatch body text.

_MERGED_DISPATCH_HEADER = "[AIFY PENDING DISPATCHES]"


def _pending_dispatch_count(body: str) -> int:
    text = str(body or "")
    if text.startswith(_MERGED_DISPATCH_HEADER):
        return len(re.findall(r"^=== ITEM \d+ ===$", text, flags=re.MULTILINE))
    return 1 if text.strip() else 0


_MERGED_DISPATCH_FOOTER = "[/AIFY PENDING DISPATCHES]"

_ITEM_MARKER_RE = re.compile(r"^=== ITEM \d+ ===$", re.MULTILINE)


def _neutralise_buffer_markers(text: str) -> str:
    """Stop untrusted text from impersonating the merged buffer's own structure.

    A message body and subject are written by one agent and rendered INTO the markers this module
    parses. Until 2026-08-16 they went in raw, and both markers could be forged from a message:

      * `_pending_dispatch_count` counts `^=== ITEM n ===$` lines anywhere in the text, so a body
        carrying nine of them takes a two-item buffer to the ten-item cap. Every subsequent send to
        that agent is then refused as `buffer_full` — one agent consuming another's capacity — and
        the next item is numbered from the inflated count.
      * `_append_pending_dispatch_body` spliced the next item in with `str.replace(FOOTER, ...)`,
        which rewrites EVERY occurrence. A body containing the footer meant the next dispatch was
        inserted twice: the buffer ends up with two `=== ITEM 3 ===` blocks and two footers, so the
        agent reads the same instruction twice.

    Both were verified by calling the real functions before this fix existed, not reasoned about.

    Substituting brackets is the same move `dispatchContent` on the bridge makes when it turns ``` into
    ''' in a dispatch body: the text stays readable and obviously quoted, and it is structurally inert.
    This is the body-side counterpart to `_quote_untrusted_subject`, which already exists because a
    foreign subject read as an instruction to whoever saw it.
    """
    neutralised = str(text or "")
    neutralised = neutralised.replace(_MERGED_DISPATCH_FOOTER, "(/AIFY PENDING DISPATCHES)")
    neutralised = neutralised.replace(_MERGED_DISPATCH_HEADER, "(AIFY PENDING DISPATCHES)")
    return _ITEM_MARKER_RE.sub(lambda m: m.group(0).replace("===", "---"), neutralised)


# v0.5.4: moved out of the control plane in the dispatch-run-state slice, but NOT into that module. It
# composes message TEXT, which is this module's subject, and its only dependency —
# `_is_provider_rate_limit_error` — is defined here. Subject and import graph agreed for once.
def _auto_handoff_body_for_run(row) -> str:
    status = str((row["status"] if row else "") or "").strip().lower()
    from_agent = str((row["from_agent"] if row else "") or "").strip()
    if status == "failed":
        detail = str((row["error_text"] if row else "") or (row["summary"] if row else "") or "Run failed.").strip()
        if _is_provider_rate_limit_error(detail):
            # Sender-facing notice (2026-06-07): a provider rate/usage limit is transient and
            # NOT the sender's fault — say so plainly so they retry instead of assuming the
            # recipient ignored them. Flows through the existing auto-handoff delivery.
            who = str((row["target_agent"] if row else "") or "").strip() or "The agent"
            note = (
                f"⚠️ {who} couldn't respond — its model provider is rate-limiting / at a usage "
                "limit right now (a provider-side throttle, not your request). Please retry shortly."
            )
            return f"{note}\n\n{detail}"
        if from_agent == "dashboard":
            return f"The run failed before the agent sent a chat reply.\n\n{detail}"
        intro = "Auto-mirrored dispatch failure because no explicit reply message was recorded for the run."
    elif status == "cancelled":
        detail = str((row["summary"] if row else "") or "Run cancelled.").strip()
        if from_agent == "dashboard":
            return f"The run was cancelled before the agent sent a chat reply.\n\n{detail}"
        intro = "Auto-mirrored dispatch cancellation because no explicit reply message was recorded for the run."
    else:
        detail = str((row["summary"] if row else "") or "Run completed.").strip()
        return detail
    return f"{intro}\n\n{detail}"
