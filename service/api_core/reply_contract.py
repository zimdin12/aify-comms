"""Reply-contract rules: what counts as an answer, and what the reminder says. PURE — no DB.

Layer-0 slice of the v0.5.4 decomposition. A dispatch with `require_reply=1` is owed an answer, and
these five decide the terms: which message types satisfy the contract, when a contract counts as
operator-closed, how the overdue query is shaped, how often a full reminder repeats, and the reminder
text itself.

`_HANDOFF_REPLY_TYPES` and `_COMPLETION_INFO_RE` moved WITH the function that reads them —
`_message_satisfies_reply_contract` was their only code reader, measured with
`scripts/constant_readership.py`, so this is a sole-reader move rather than the accessor case.

Worth keeping together and away from the delivery machinery: these are the CONTRACT, and the strand
bugs in this subsystem have historically come from delivery paths disagreeing about what closes one.
A module you can read end to end is the point.
"""

from __future__ import annotations

import re
import time
from typing import Any, Optional

from service.api_core.dispatch_state import (
    _DISPATCH_TERMINAL_STATUSES,
    _is_delivery_only_claude_run,
)
from service.api_core.serialization import _row_require_reply
from service.clock import iso_to_epoch as _iso_to_epoch
from service.api_core.settings import DEFAULT_SETTINGS


_HANDOFF_REPLY_TYPES = {"response", "review", "error", "approval"}

_COMPLETION_INFO_RE = re.compile(
    r"\b(done|complete(?:d)?|finished|fixed|pushed|committed|shipped|merged|resolved|verified|ready|answered)\b",
    re.I,
)


def _is_operator_closed_contract(row) -> bool:
    if not row:
        return False
    status = str((row["status"] if "status" in row.keys() else "") or "").strip().lower()
    summary = str((row["summary"] if "summary" in row.keys() else "") or "").strip()
    return (
        status == "completed"
        and not _row_require_reply(row)
        and summary.startswith("Closed from Work Loop by dashboard operator.")
    )


def _message_satisfies_reply_contract(reply_type: str, subject: str = "", body: str = "") -> bool:
    msg_type = str(reply_type or "").strip().lower()
    if msg_type in _HANDOFF_REPLY_TYPES:
        return True
    # `info` closes a run ONLY when it signals completion (keyword) — an agent
    # may thread an `info` "ack / I'm looking" WITHOUT claiming the work is done,
    # which intentionally leaves the run open (see
    # test_threaded_non_answer_message_does_not_close_reply_contract). Reviewed
    # 2026-05-31 (holistic review "F4"): this is deliberate, NOT a stuck-run bug —
    # the operator-observed "Pending updates (N)" pile-up was QUEUED (never
    # claimed) runs, fixed by the release + channel-sidecar self-heal fixes.
    if msg_type == "info" and _COMPLETION_INFO_RE.search(f"{subject or ''}\n{body or ''}"):
        return True
    return False


def _contract_list_query(
    *,
    where_sql: str = "",
    order_sql: str = "ORDER BY r.requested_at DESC",
    limit_sql: str = "LIMIT ?",
) -> str:
    return f"""
        SELECT
            r.*,
            m.source AS message_source,
            m.body AS message_body,
            m.timestamp AS message_timestamp,
            rr.read_at AS source_read_at,
            result.body AS result_body,
            result.timestamp AS result_timestamp,
            COALESCE(reminder.reminder_count, 0) AS reminder_count,
            COALESCE(reminder.last_reminder_at, '') AS last_reminder_at
        FROM dispatch_runs r
        LEFT JOIN messages m ON m.id = r.message_id
        LEFT JOIN read_receipts rr ON rr.message_id = r.message_id AND rr.agent_id = r.target_agent
        LEFT JOIN messages result ON result.id = r.result_message_id
        LEFT JOIN (
            SELECT run_id, COUNT(*) AS reminder_count, MAX(created_at) AS last_reminder_at
            FROM dispatch_events
            WHERE event_type = 'reply_reminder'
            GROUP BY run_id
        ) reminder ON reminder.run_id = r.id
        WHERE (
            r.require_reply = 1
            OR r.message_type IN ('request','review','error')
            OR (r.priority IN ('high','urgent') AND r.message_type NOT IN ('info','response','approval'))
        )
        {where_sql}
        {order_sql}
        {limit_sql}
    """


def _contract_reminder_full_every(settings: dict[str, Any]) -> int:
    try:
        return max(0, int(settings.get("reply_reminder_full_every", DEFAULT_SETTINGS["reply_reminder_full_every"]) or 0))
    except (TypeError, ValueError):
        return int(DEFAULT_SETTINGS["reply_reminder_full_every"])


def _contract_reminder_body(row, *, full: bool = True) -> str:
    message_id = str(row["message_id"] or "").strip()
    target = str(row["target_agent"] or "").strip()
    sender = str(row["from_agent"] or "").strip()
    subject = str(row["subject"] or "").strip() or "(no subject)"
    read_hint = (
        f'comms_inbox(agentId="{target}", messageId="{message_id}")'
        if message_id
        else f'comms_run_status(runId="{row["id"]}")'
    )
    # The snippet MUST be a valid comms_send call: `body` is a REQUIRED zod field
    # (mcp/stdio/server.js:4472 `body: z.string()`), so it cannot be omitted. An earlier attempt
    # here moved the body out of the call and described it in prose — that produced a snippet an
    # agent could not run at all, which is strictly worse than a conventional placeholder. Keep the
    # placeholder inside the call.
    #
    # NO subject-based matching claim. `_link_reply_message_to_dispatch_run` matches on
    # `WHERE target_agent = ? AND message_id = ?` keyed on the reply's inReplyTo — it never reads
    # the subject. A previous version of this text told the agent "the subject matches it to the
    # run", which is simply false; do not re-add any variant of it.
    #
    # HONEST LIMIT for a run with no source message: every DASHBOARD-originated run
    # (Restart/Stop/Start) has no message row, so there is no id to thread to and the reply CANNOT
    # be linked by the matcher. Such a run is closed by the reconcile completed-without-reply path,
    # not by threading. Say so plainly rather than implying an anchor exists.
    reply_hint = (
        f'comms_send(from="{target}", to="{sender}", type="response", inReplyTo="{message_id}", '
        f'subject="Re: {subject}", body="<answer, blocker, or result>")'
        if message_id and sender
        else (
            f'comms_send(from="{target}", to="{sender or "dashboard"}", type="response", '
            f'subject="Re: {subject}", body="<answer, blocker, or result>") '
            f'(operator-initiated — no source message to thread to)'
        )
    )
    if not full:
        # LIGHT reminder (operator decision 2026-07-02): one line — the owed
        # message id + subject + the same comms_send/inReplyTo wiring the full
        # format uses, so the recipient can still reply to the right message.
        # No original body, no boilerplate. The message row itself still
        # carries in_reply_to, so threading is identical to a full reminder.
        return f'Reply owed to {message_id or row["id"]}: "{subject}" — {reply_hint}'
    # Terse on purpose (2026-06-18): efficacy comes from the reply ANCHOR, not prose. The
    # sender/subject/ids are already in the agent's inbox, so we don't restate them at length —
    # that was ~210 tokens of context burn per reminder (the system already reminds rarely).
    return (
        f'aify-comms reminder: "{subject}" from {sender} still needs an explicit reply (run {row["id"]}).\n'
        f"Reply to the ORIGINAL, not this nudge: {reply_hint}\n"
        f"Read it first if needed: {read_hint}\n"
        "If blocked, reply with the blocker, what you checked, and your next action."
    )


# v0.5.4: `_dispatch_reply_state` arrived from the control plane and `_dispatch_reply_pending` from
# `routers/dispatch_messages/shared.py` — two modules, one subject, which is why they are together now.
#
# READ THE SOURCE MODULE'S `def` CAREFULLY. My first attempt at this slice moved what looked like these
# two functions out of `dispatch_messages/shared.py`. Three of the four names I took from that module
# were delegating BORROW SHIMS, not implementations, so the leaves ended up importing the control plane
# — the one direction this architecture forbids — and every gate stayed green. Only
# `_dispatch_reply_pending` was a real body there. The rest came from the carrier, where they lived.

def _dispatch_reply_state(row) -> str:
    if str((row["result_message_id"] if row else "") or "").strip():
        return "sent"
    if not _row_require_reply(row):
        return "not_required"
    if _is_delivery_only_claude_run(row):
        return "awaiting"
    status = str((row["status"] if row else "") or "").strip().lower()
    if status in _DISPATCH_TERMINAL_STATUSES:
        return "pending"
    return "awaiting"


def _dispatch_reply_pending(row) -> bool:
    return _dispatch_reply_state(row) == "pending"


# v0.5.4: `_contract_reply_expected`, `_contract_state` and `_contract_reminder_due` arrived from the
# control plane, which completes this module. It already owned whether a message SATISFIES a contract and
# what a reminder says; it did not own whether a contract is currently open, what state it is in, or
# whether a reminder is due — so the three questions a reminder sweep asks were answered in two modules.
#
# `_run_contract_reminders_once` stays in the carrier for now: it reads the status cache. It becomes a
# caller of `_contract_reminder_due` rather than a co-owner of the rule.

def _contract_reply_expected(row) -> bool:
    if not row:
        return False
    if _is_operator_closed_contract(row):
        return False
    # Send creation has already normalized type defaults plus the explicit requireReply
    # override into this field. Re-inferring from type/priority here made an explicit
    # requireReply=false request actionable again and recreated reminder/reply debt.
    return _row_require_reply(row)


def _contract_state(row, *, settings: dict[str, Any], now_s: Optional[float] = None) -> dict[str, Any]:
    now_s = now_s or time.time()
    requested_s = _iso_to_epoch((row["requested_at"] if row and "requested_at" in row.keys() else "") or "")
    age_minutes = max(0.0, (now_s - requested_s) / 60.0) if requested_s else 0.0
    status = str((row["status"] if row and "status" in row.keys() else "") or "").strip().lower()
    result_message_id = str((row["result_message_id"] if row and "result_message_id" in row.keys() else "") or "").strip()
    reply_expected = _contract_reply_expected(row)
    reminder_minutes = max(1, int(settings.get("reply_reminder_minutes", DEFAULT_SETTINGS["reply_reminder_minutes"]) or DEFAULT_SETTINGS["reply_reminder_minutes"]))
    reminder_count = int((row["reminder_count"] if row and "reminder_count" in row.keys() else 0) or 0)
    source_read_at = str((row["source_read_at"] if row and "source_read_at" in row.keys() else "") or "").strip()
    same_agent = str((row["from_agent"] if row else "") or "") == str((row["target_agent"] if row else "") or "")

    if result_message_id:
        state = "answered"
    elif status in {"failed", "cancelled"}:
        state = "failed"
    elif status == "completed":
        state = "missing_reply" if reply_expected else "closed"
    elif status in {"claimed", "running"}:
        state = "working"
    elif status == "queued":
        state = "queued"
    elif source_read_at:
        state = "seen"
    else:
        state = "sent"

    overdue = bool(
        reply_expected
        and not result_message_id
        and status not in _DISPATCH_TERMINAL_STATUSES
        and age_minutes >= reminder_minutes
    )
    if overdue:
        state = "overdue"

    category = "self_wake" if same_agent else "direct"
    source = str((row["message_source"] if row and "message_source" in row.keys() else "") or "").strip().lower()
    if source == "channel":
        category = "channel"

    return {
        "state": state,
        "replyExpected": reply_expected,
        "overdue": overdue,
        "ageMinutes": round(age_minutes, 1),
        "reminderCount": reminder_count,
        "category": category,
        "actionable": bool(reply_expected and not result_message_id and category != "self_wake"),
    }


def _contract_reminder_due(
    row,
    *,
    settings: dict[str, Any],
    now_s: Optional[float] = None,
    ignore_repeat: bool = False,
) -> tuple[bool, str]:
    if not settings.get("reply_contracts_enabled", True):
        return False, "reply contract reminders are disabled"
    state = _contract_state(row, settings=settings, now_s=now_s)
    if not state["overdue"]:
        return False, f'contract state is {state["state"]}'
    max_count = max(0, int(settings.get("reply_reminder_max_count", 0) or 0))
    if max_count and state["reminderCount"] >= max_count:
        return False, f"max reminders reached ({state['reminderCount']}/{max_count})"
    last_reminder_at = str((row["last_reminder_at"] if row and "last_reminder_at" in row.keys() else "") or "").strip()
    if last_reminder_at and not ignore_repeat:
        repeat_minutes = max(1, int(settings.get("reply_reminder_repeat_minutes", DEFAULT_SETTINGS["reply_reminder_repeat_minutes"]) or DEFAULT_SETTINGS["reply_reminder_repeat_minutes"]))
        last_s = _iso_to_epoch(last_reminder_at)
        if last_s and ((now_s or time.time()) - last_s) < repeat_minutes * 60:
            return False, f"last reminder was less than {repeat_minutes} minutes ago"
    return True, ""
