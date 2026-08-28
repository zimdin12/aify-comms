"""Reply-contract rules: what counts as an answer, and what the reminder says. PURE — no DB.

Layer-0 slice of the v0.5.4 decomposition. These five decide the terms: which message types satisfy
the contract, when a contract counts as operator-closed, how the overdue query is shaped, how often
a full reminder repeats, and the reminder text itself.

WHAT IS OWED AN ANSWER, exactly as `_contract_list_query` asks it -- and it is wider than the flag.
This paragraph used to read "a dispatch with `require_reply=1` is owed an answer", which is only
the first of three clauses:

    require_reply = 1
    OR message_type IN ('request', 'review', 'error')        <- REGARDLESS of require_reply
    OR priority IN ('high','urgent') AND type NOT IN ('info','response','approval')

So a sender that sets `requireReply=false` on a request, review or error is bound anyway: for those
three types the flag decides nothing. MEASURED on the operator's database 2026-08-27: 26 `request`
runs and 121 `error` runs carry require_reply=0 and are bound by the type clause.

WHETHER THAT IS RIGHT IS AN OPEN QUESTION, deliberately left open here. Honouring the flag would let
a notice opt out, which is what a caller setting it plainly intends; ignoring it means an error or a
request always gets acknowledged, which is what the reminder machinery exists for. Both are
defensible and the choice belongs to whoever owns the dispatch contract, not to a passing edit.
`test_the_reply_contract_rule_is_what_the_docstring_says.py` pins today's answer so a flip is a
visible diff rather than a drift.

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
from service.api_core.serialization import _quote_untrusted_subject, _row_require_reply
from service.clock import iso_to_epoch as _iso_to_epoch
from service.api_core.settings import DEFAULT_SETTINGS


_HANDOFF_REPLY_TYPES = {"response", "review", "error", "approval"}

_COMPLETION_INFO_RE = re.compile(
    r"\b(done|complete(?:d)?|finished|fixed|pushed|committed|shipped|merged|resolved|verified|ready|answered)\b",
    re.I,
)

#: Words that INVERT a completion keyword that follows them, or push it into the future. A bare
#: keyword search treats "not done yet" and "will report when done" as claims of completion; both
#: were verified closing a reply contract before this guard existed. See `_signals_completion`.
_COMPLETION_NEGATORS = re.compile(
    r"\b(?:not|no|never|nothing|none|isn|aren|wasn|weren|don|doesn|didn|haven|hasn|hadn|won|wouldn"
    r"|can|cannot|couldn|shouldn|yet|still|pending|blocked|unable|fail(?:ed|ing)?"
    r"|will|shall|when|once|until|after|before|if|unless|need|needs|needed|going|plan|plans"
    r"|almost|nearly|partially)\b",
    re.I,
)
#: How much text before a keyword is inspected for one of the above. Four words is enough for
#: "have not yet been done" and short enough that an unrelated earlier sentence does not veto.
_COMPLETION_LOOKBEHIND_WORDS = 4


def _signals_completion(text: str) -> bool:
    """Does this `info` message CLAIM the work is done — as opposed to merely mentioning it?

    Until 2026-08-16 this was a bare `_COMPLETION_INFO_RE.search`, and the keyword list is ordinary
    English (done, finished, fixed, ready, ...). Verified by calling the real function, EIGHT of nine
    realistic progress updates closed the reply contract, including every negation:

        "Not done yet - still investigating."          -> closed
        "I haven't finished; blocked on the DB lock."  -> closed
        "This is not fixed. Reopening."                -> closed
        "Still working on it, will report when done."  -> closed
        "Are you ready for the handoff?"               -> closed

    The declared rule directly above is that `info` closes a run ONLY when it signals completion, so
    these are failures against the stated intent, not a policy change. Their cost is asymmetric: a
    contract that closes too LATE gets a reply reminder, which is the system's designed recovery; one
    that closes too EARLY strands the sender believing an answer arrived, with nothing left to nudge.
    So this guard is deliberately eager to keep a contract OPEN.

    A keyword counts only when the few words before it neither negate it nor put it in the future,
    and only outside a question. One clean keyword anywhere is still enough — "Blocked on X. Fixed
    the parser though." should close nothing on the first clause and does close on the second.
    """
    haystack = str(text or "")
    for match in _COMPLETION_INFO_RE.finditer(haystack):
        before = haystack[: match.start()]
        # A question is asking about completion, not reporting it. Scope to the clause containing
        # the keyword: the text from the previous sentence break to the next one.
        tail = haystack[match.end():]
        clause_end = min(
            (i for i in (tail.find(c) for c in ".!?\n") if i >= 0), default=len(tail)
        )
        if "?" in tail[:clause_end + 1]:
            continue
        # Look back only within the SAME clause. Crossing a sentence boundary lets an unrelated
        # earlier statement veto a real claim — "Blocked on X earlier. Fixed the parser though."
        # reported nothing done, because `blocked` sat four words behind `Fixed`.
        clause_start = max(before.rfind(c) for c in ".!?\n;") + 1
        window = " ".join(re.split(r"\s+", before[clause_start:].strip())[-_COMPLETION_LOOKBEHIND_WORDS:])
        if _COMPLETION_NEGATORS.search(window):
            continue
        return True
    return False


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
    if msg_type == "info" and _signals_completion(f"{subject or ''}\n{body or ''}"):
        return True
    return False


def reply_reminder_minutes(settings: dict[str, Any]) -> int:
    """How long a reply may be owed before it is OVERDUE. One derivation, every surface.

    The reminder sweep and the Work Loop's `state=overdue` filter both read
    `reply_reminder_minutes`; the two analytics endpoints hardcoded 30 minutes. With the operator's
    setting at 10, a contract owed for 15 minutes got a reminder and appeared in the Work Loop as
    overdue while the analytics tile did not count it -- two numbers on two screens, both labelled
    "overdue", disagreeing by whatever the operator had chosen.

    Measured 2026-08-28: `reply_reminder_minutes` is 10 on the live service, so the hardcoded 30
    was already wrong by a factor of three. It showed as nothing only because no reply was owed at
    that moment -- a disagreement waiting for the first contract to sit for eleven minutes.
    """
    raw = settings.get(
        "reply_reminder_minutes", DEFAULT_SETTINGS["reply_reminder_minutes"]
    ) or DEFAULT_SETTINGS["reply_reminder_minutes"]
    return max(1, int(raw))


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
    # QUOTED with the shared quoter, not with hand-typed `"` characters. Every line below already
    # MEANT to quote this subject — it is another agent's free text arriving in the reader's context,
    # and the whole reason `_quote_untrusted_subject` exists is that a bare imperative there reads as
    # a command (operator-reported 2026-08-11). But `"{subject}"` written by hand is escapable: a
    # subject that itself contains a double quote closes the quotation, and the rest of it lands as
    # unquoted prose — the exact failure, re-entered through the punctuation. The shared function
    # neutralises embedded quotes and clips, which matters here because a subject is UNBOUNDED on
    # input (no `max_length` on the model, no zod max on the tool).
    quoted_subject = _quote_untrusted_subject(subject, 240)
    read_hint = (
        f'comms_inbox(agentId="{target}", messageId="{message_id}")'
        if message_id
        else f'comms_run_status(runId="{row["id"]}")'
    )
    # The snippet MUST be a valid comms_send call: `body` is a REQUIRED zod field
    # (mcp/stdio/server.js declares `body: z.string()`), so it cannot be omitted. An earlier attempt
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
        f'subject={_quote_untrusted_subject(f"Re: {subject}", 240)}, body="<answer, blocker, or result>")'
        if message_id and sender
        else (
            f'comms_send(from="{target}", to="{sender or "dashboard"}", type="response", '
            f'subject={_quote_untrusted_subject(f"Re: {subject}", 240)}, body="<answer, blocker, or result>") '
            f'(operator-initiated — no source message to thread to)'
        )
    )
    if not full:
        # LIGHT reminder (operator decision 2026-07-02): one line — the owed
        # message id + subject + the same comms_send/inReplyTo wiring the full
        # format uses, so the recipient can still reply to the right message.
        # No original body, no boilerplate. The message row itself still
        # carries in_reply_to, so threading is identical to a full reminder.
        return f'Reply owed to {message_id or row["id"]}: {quoted_subject} — {reply_hint}'
    # Terse on purpose (2026-06-18): efficacy comes from the reply ANCHOR, not prose. The
    # sender/subject/ids are already in the agent's inbox, so we don't restate them at length —
    # that was ~210 tokens of context burn per reminder (the system already reminds rarely).
    return (
        f'aify-comms reminder: {quoted_subject} from {sender} still needs an explicit reply (run {row["id"]}).\n'
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
    reminder_minutes = reply_reminder_minutes(settings)
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

def _contract_reminder_is_full(reminder_number: int, *, settings: dict[str, Any]) -> bool:
    """Reminder number N (1-based) gets the FULL format when full_every <= 1
    (always full) or N is a multiple of full_every. Everything in between is a
    LIGHT one-liner — reminders never stop firing (no backoff), they just get
    cheaper between the periodic full nudges."""
    full_every = _contract_reminder_full_every(settings)
    if full_every <= 1:
        return True
    if reminder_number <= 0:
        return True  # unknown ordinal — fail safe to the full format
    return reminder_number % full_every == 0
