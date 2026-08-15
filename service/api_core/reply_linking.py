"""Threading a reply back onto the dispatch run it answers.

RELOCATED, not rewritten, in v0.5.4 -- all four functions are byte-identical from
`service/routers/dispatch_messages/shared.py`. That module is 662 lines of which 455 are non-route
helpers: it lives under `routers/` and declares no routes at all, which is how a self-contained
cluster like this one ended up a layer too high.

THE MOVE UNBLOCKS A SPLIT, which is the fourth time in v0.5.4 that a leaf-shaped helper in a
router's shared module has stood in the way. `send_message`'s reply-threading block calls both link
writers, and an api_core leaf importing from `service.routers` is the cycle the layering exists to
prevent, so that block could not move while they lived there.

TWO ENTRY POINTS, TWO DIFFERENT QUESTIONS. `_link_reply_message_to_dispatch_run` is given the run to
close -- the sender said what it was replying to. `_link_unthreaded_reply_to_recent_dispatch_run`
has to GUESS, because the reply named nothing, so it looks for the most recent run from this sender
to this recipient inside a bounded window. Guessing wrong closes a run that is still owed an answer,
which is why the window is bounded and why both go through the same reply-contract check.

`_is_replaceable_auto_handoff_message` IS SHARED BY BOTH and is the reason they travelled together:
an auto-handoff message is a placeholder the service wrote on the agent's behalf, and a real reply
is allowed to replace it. A copy of that predicate per caller is the forked-constant class.
"""
from __future__ import annotations

from service.api_core.dispatch_run_state import _mark_dispatch_run_answered
from service.api_core.dispatch_state import _DISPATCH_TERMINAL_STATUSES
from service.api_core.dispatch_text import _auto_handoff_body_for_run, _auto_handoff_subject_for_run
from service.api_core.events import _append_dispatch_event
from service.api_core.reply_contract import _message_satisfies_reply_contract
from service.api_core.serialization import _iso_from_ms
from service.api_core.tuning import _UNTHREADED_HANDOFF_WINDOW_MS
from service.clock import now as _now


def _borrowed_unthreaded_handoff_window_ms():
    """BORROWED constant: one owner, never a copy (finding N7)."""

    return _UNTHREADED_HANDOFF_WINDOW_MS


def _is_replaceable_auto_handoff_message(existing_message, replied_run) -> bool:
    if not existing_message or not replied_run:
        return True
    existing_body = str((existing_message["body"] if "body" in existing_message.keys() else "") or "")
    if existing_body.startswith("Auto-mirrored dispatch "):
        return True
    return (
        existing_body == _auto_handoff_body_for_run(replied_run)
        and str((existing_message["subject"] if "subject" in existing_message.keys() else "") or "").strip()
        == _auto_handoff_subject_for_run(replied_run)
        and str((existing_message["from_agent"] if "from_agent" in existing_message.keys() else "") or "").strip()
        == str((replied_run["target_agent"] if "target_agent" in replied_run.keys() else "") or "").strip()
        and str((existing_message["to_agent"] if "to_agent" in existing_message.keys() else "") or "").strip()
        == str((replied_run["from_agent"] if "from_agent" in replied_run.keys() else "") or "").strip()
        and str((existing_message["in_reply_to"] if "in_reply_to" in existing_message.keys() else "") or "").strip()
        == str((replied_run["message_id"] if "message_id" in replied_run.keys() else "") or "").strip()
    )


async def _link_reply_message_to_dispatch_run(
    db,
    *,
    from_agent: str,
    resolved_in_reply_to: str,
    reply_message_id: str,
    reply_type: str,
    reply_body: str,
) -> bool:
    # A linked request may answer the current contract while asking a follow-up. Keep
    # non-answer info messages open; their completion semantics remain content-aware.
    if str(reply_type or "").strip().lower() != "request" and not _message_satisfies_reply_contract(
        reply_type,
        body=reply_body,
    ):
        return False
    run_cursor = await db.execute(
        """
        SELECT * FROM dispatch_runs
        WHERE target_agent = ? AND message_id = ?
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (from_agent, resolved_in_reply_to),
    )
    replied_run = await run_cursor.fetchone()
    if not replied_run:
        return False
    existing_result_id = str(replied_run["result_message_id"] or "").strip()
    if existing_result_id:
        existing_cursor = await db.execute("SELECT * FROM messages WHERE id = ?", (existing_result_id,))
        existing_message = await existing_cursor.fetchone()
        if not _is_replaceable_auto_handoff_message(existing_message, replied_run):
            return False

    current_status = str(replied_run["status"] or "").strip().lower()
    await _mark_dispatch_run_answered(
        db,
        replied_run["id"],
        reply_message_id,
        current_status,
        str(replied_run["execution_mode"] or ""),
    )
    await db.execute(
        """
        INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at)
        SELECT id, to_agent, ?
        FROM messages
        WHERE from_agent = ?
          AND to_agent = ?
          AND in_reply_to = ?
          AND dispatch_requested = 0
          AND body LIKE 'Auto-mirrored dispatch %'
        """,
        (_now(), from_agent, replied_run["from_agent"], replied_run["message_id"]),
    )
    handoff_note = (
        f"Result reply linked after run completion from {from_agent}"
        if current_status in _DISPATCH_TERMINAL_STATUSES
        else f"Result reply recorded from {from_agent}"
    )
    await _append_dispatch_event(db, replied_run["id"], "handoff", handoff_note)
    return True


async def _link_unthreaded_reply_to_recent_dispatch_run(
    db,
    *,
    from_agent: str,
    to_agent: str,
    reply_message_id: str,
    reply_type: str,
    reply_subject: str = "",
    reply_body: str = "",
    reply_timestamp_ms: int,
) -> bool:
    if not _message_satisfies_reply_contract(reply_type, subject=reply_subject, body=reply_body):
        return False
    if not from_agent or not to_agent or not reply_message_id:
        return False

    latest_requested_at = _iso_from_ms(reply_timestamp_ms)
    earliest_requested_at = _iso_from_ms(max(0, reply_timestamp_ms - _borrowed_unthreaded_handoff_window_ms()))
    run_cursor = await db.execute(
        """
        SELECT * FROM dispatch_runs
        WHERE target_agent = ?
          AND from_agent = ?
          AND status IN ('delivered', 'claimed', 'running', 'completed', 'failed', 'cancelled')
          AND requested_at >= ?
          AND requested_at <= ?
          AND (
            require_reply = 1
            OR (
              dispatch_mode = 'terminal'
              AND runtime = 'claude-code'
              AND status IN ('claimed', 'running')
            )
          )
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (from_agent, to_agent, earliest_requested_at, latest_requested_at),
    )
    replied_run = await run_cursor.fetchone()
    if not replied_run:
        return False
    existing_result_id = str(replied_run["result_message_id"] or "").strip()
    if existing_result_id:
        existing_cursor = await db.execute("SELECT * FROM messages WHERE id = ?", (existing_result_id,))
        existing_message = await existing_cursor.fetchone()
        if not _is_replaceable_auto_handoff_message(existing_message, replied_run):
            return False

    await _mark_dispatch_run_answered(
        db,
        replied_run["id"],
        reply_message_id,
        str(replied_run["status"] or ""),
        str(replied_run["execution_mode"] or ""),
    )
    await _append_dispatch_event(
        db,
        replied_run["id"],
        "handoff",
        f"Unthreaded result reply linked from {from_agent}",
    )
    return True
