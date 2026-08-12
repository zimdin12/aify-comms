"""Helpers owned by the dispatch+messages pair, and every borrow the pair still needs.

v0.5.2l. Two things live here, and the distinction is the whole point of the package:

OWNED (8 helpers). Used by dispatch handlers AND message handlers, and by nothing else.
Splitting dispatch and messages into separate modules would have made each of these a borrow in BOTH
— two shims, no owner, and a consolidation tag owed later. Moving the pair together lets them have a
real owner now. That is why the reviewer ruled for one combined tag.

BORROWED (33 names). Defined once here so `dispatch.py` and `messages.py` share one shim
rather than declaring their own. Each is still used by `agents`, by router-internal code, or by an
already-moved module borrowing it through the router — established by FOLLOWING THE SHIMS, not by raw
caller count. That distinction mattered: several names that looked local to this pair are borrowed by
channels, spawn_requests, sessions or the reconcilers, and moving them would have broken those.

Nearly all of it retires with `agents`, the last domain.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException, Query, Request

from service import longpoll
from service.api_core.events import _append_dispatch_event, _append_terminal_event
from service.api_core.routing import domain_router
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.serialization import (
    _clip_text,
    _dedupe_preserve,
    _iso_from_ms,
    _json_loads_or,
    _quote_untrusted_subject,
    _row_require_reply,
    _timestamp_sort_key,
)
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.clock import iso_to_epoch as _iso_to_epoch
from service.clock import now as _now
from service.db import SQLITE_CLAIM_BUSY_TIMEOUT_MS, get_db
from service.ntfy import notify_operator
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
from service.status_engine import apply_event

# Resolved to their REAL owners, asked of the repo rather than guessed:
from service.api_core.events import _append_terminal_control
from service.api_core.serialization import _machine_ids_same_host
from service.db import _NATIVE_MANAGED_RUNTIMES
from service.reconcilers.dispatch_lifecycle import _close_steered_contracts_for_parent_run, _mark_dispatch_run_answered
from service.reconcilers.dispatch_queue import _close_reconcilable_delivered_runs
from service.status_engine import VALID_STATUSES

logger = logging.getLogger("aify_comms.routers.dispatch_messages.shared")

async def _adopt_live_resident_driver(*a, **k):
    from service.routers.api_v2 import _adopt_live_resident_driver as _impl

    return await _impl(*a, **k)


async def _agent_tombstone(*a, **k):
    from service.routers.api_v2 import _agent_tombstone as _impl

    return await _impl(*a, **k)


def _auto_handoff_subject_for_run(*a, **k):
    from service.routers.api_v2 import _auto_handoff_subject_for_run as _impl

    return _impl(*a, **k)


def _auto_handoff_body_for_run(*a, **k):
    from service.routers.api_v2 import _auto_handoff_body_for_run as _impl

    return _impl(*a, **k)


async def _bridge_claim_block_reason(*a, **k):
    from service.routers.api_v2 import _bridge_claim_block_reason as _impl

    return await _impl(*a, **k)


async def _dispatch_conversation_context(*a, **k):
    from service.routers.api_v2 import _dispatch_conversation_context as _impl

    return await _impl(*a, **k)


def _dispatch_reply_pending(row) -> bool:
    return _dispatch_reply_state(row) == "pending"


def _dispatch_reply_state(*a, **k):
    from service.routers.api_v2 import _dispatch_reply_state as _impl

    return _impl(*a, **k)


async def _has_claimable_steerable_run(*a, **k):
    from service.routers.api_v2 import _has_claimable_steerable_run as _impl

    return await _impl(*a, **k)


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


async def _mark_dispatch_source_messages_read(*a, **k):
    from service.routers.api_v2 import _mark_dispatch_source_messages_read as _impl

    return await _impl(*a, **k)


def _message_satisfies_reply_contract(*a, **k):
    from service.routers.api_v2 import _message_satisfies_reply_contract as _impl

    return _impl(*a, **k)


def _pending_dispatch_count(*a, **k):
    from service.routers.api_v2 import _pending_dispatch_count as _impl

    return _impl(*a, **k)


async def _record_channel_sidecar_heartbeat(*a, **k):
    from service.routers.api_v2 import _record_channel_sidecar_heartbeat as _impl

    return await _impl(*a, **k)


async def _release_stale_console_owner_for_claim(*a, **k):
    from service.routers.api_v2 import _release_stale_console_owner_for_claim as _impl

    return await _impl(*a, **k)


def _borrowed_active_run_bridge_stale_seconds():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.routers.api_v2 import ACTIVE_RUN_BRIDGE_STALE_SECONDS

    return ACTIVE_RUN_BRIDGE_STALE_SECONDS


def _borrowed_turn_busy_backstop_seconds():
    """BORROWED constant: one owner, never a copy (finding N7).

    This one must stay a borrow even though `_turn_busy_holds_delivery` moved here: the ceiling has
    to equal the status engine's `in_turn` clamp, four other api_v2 readers depend on it, and
    `test_turn_busy_delivery_ceiling` asserts the parity against `api_v2`. A copy here would let the
    delivery gate and the status clamp drift apart, which is the strand the ceiling exists to bound.
    """
    from service.routers.api_v2 import TURN_BUSY_BACKSTOP_SECONDS

    return TURN_BUSY_BACKSTOP_SECONDS


def _borrowed_coldstart_refused_prefix():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.routers.api_v2 import COLDSTART_REFUSED_PREFIX

    return COLDSTART_REFUSED_PREFIX


def _borrowed_channel_claim_runtimes():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.routers.api_v2 import _CHANNEL_CLAIM_RUNTIMES

    return _CHANNEL_CLAIM_RUNTIMES


def _borrowed_channel_managed_runtimes():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.routers.api_v2 import _CHANNEL_MANAGED_RUNTIMES

    return _CHANNEL_MANAGED_RUNTIMES


def _borrowed_dispatch_terminal_statuses():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.routers.api_v2 import _DISPATCH_TERMINAL_STATUSES

    return _DISPATCH_TERMINAL_STATUSES


def _borrowed_merged_dispatch_header():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.routers.api_v2 import _MERGED_DISPATCH_HEADER

    return _MERGED_DISPATCH_HEADER


def _borrowed_unthreaded_handoff_window_ms():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.routers.api_v2 import _UNTHREADED_HANDOFF_WINDOW_MS

    return _UNTHREADED_HANDOFF_WINDOW_MS



async def _active_terminal_for_agent(*a, **k):
    from service.routers.api_v2 import _active_terminal_for_agent as _impl

    return await _impl(*a, **k)


def _agent_execution_mode(*a, **k):
    from service.routers.api_v2 import _agent_execution_mode as _impl

    return _impl(*a, **k)


async def _append_dispatch_control(*a, **k):
    from service.routers.api_v2 import _append_dispatch_control as _impl

    return await _impl(*a, **k)


async def _apply_channel_routing_to_claude_runs(*a, **k):
    from service.routers.api_v2 import _apply_channel_routing_to_claude_runs as _impl

    return await _impl(*a, **k)


async def _auto_return_resident_to_managed_if_possible(*a, **k):
    from service.routers.api_v2 import _auto_return_resident_to_managed_if_possible as _impl

    return await _impl(*a, **k)


async def _clear_turn_busy_if_no_open_reply_owing_run(*a, **k):
    from service.routers.api_v2 import _clear_turn_busy_if_no_open_reply_owing_run as _impl

    return await _impl(*a, **k)


def _coldstart_refusal_message(*a, **k):
    from service.routers.api_v2 import _coldstart_refusal_message as _impl

    return _impl(*a, **k)


async def _coldstart_spawn_request_for_dispatch(*a, **k):
    from service.routers.api_v2 import _coldstart_spawn_request_for_dispatch as _impl

    return await _impl(*a, **k)


async def _create_dispatch_runs(*a, **k):
    from service.routers.api_v2 import _create_dispatch_runs as _impl

    return await _impl(*a, **k)


async def _delete_messages_by_ids(*a, **k):
    from service.routers.api_v2 import _delete_messages_by_ids as _impl

    return await _impl(*a, **k)


async def _delete_messages_where(*a, **k):
    from service.routers.api_v2 import _delete_messages_where as _impl

    return await _impl(*a, **k)


def _dispatch_fix_hint(*a, **k):
    from service.routers.api_v2 import _dispatch_fix_hint as _impl

    return _impl(*a, **k)


async def _ensure_managed_pty_for_dispatch(*a, **k):
    from service.routers.api_v2 import _ensure_managed_pty_for_dispatch as _impl

    return await _impl(*a, **k)


async def _fail_pending_controls_for_run(*a, **k):
    from service.routers.api_v2 import _fail_pending_controls_for_run as _impl

    return await _impl(*a, **k)


async def _finalize_dispatch_runs(*a, **k):
    from service.routers.api_v2 import _finalize_dispatch_runs as _impl

    return await _impl(*a, **k)


async def _get_blocking_active_run(*a, **k):
    from service.routers.api_v2 import _get_blocking_active_run as _impl

    return await _impl(*a, **k)


async def _get_dispatch_state_for_agent(*a, **k):
    from service.routers.api_v2 import _get_dispatch_state_for_agent as _impl

    return await _impl(*a, **k)


async def _get_recipient_info(*a, **k):
    from service.routers.api_v2 import _get_recipient_info as _impl

    return await _impl(*a, **k)


async def _has_claimable_spawn_request(*a, **k):
    from service.routers.api_v2 import _has_claimable_spawn_request as _impl

    return await _impl(*a, **k)


async def _has_live_managed_wrapper_child(*a, **k):
    from service.routers.api_v2 import _has_live_managed_wrapper_child as _impl

    return await _impl(*a, **k)


def _insert_messages_via_console(*a, **k):
    from service.routers.api_v2 import _insert_messages_via_console as _impl

    return _impl(*a, **k)


def _is_delivery_only_claude_run(*a, **k):
    from service.routers.api_v2 import _is_delivery_only_claude_run as _impl

    return _impl(*a, **k)


async def _managed_environment_unavailable_reason(*a, **k):
    from service.routers.api_v2 import _managed_environment_unavailable_reason as _impl

    return await _impl(*a, **k)


def _managed_terminal_backing_enabled(*a, **k):
    from service.routers.api_v2 import _managed_terminal_backing_enabled as _impl

    return _impl(*a, **k)


def _managed_via_wrapper_for_runtime(*a, **k):
    from service.routers.api_v2 import _managed_via_wrapper_for_runtime as _impl

    return _impl(*a, **k)


async def _mirror_missing_dispatch_handoff(*a, **k):
    from service.routers.api_v2 import _mirror_missing_dispatch_handoff as _impl

    return await _impl(*a, **k)


async def _preflight_live_send_recipients(*a, **k):
    from service.routers.api_v2 import _preflight_live_send_recipients as _impl

    return await _impl(*a, **k)


def _reject_sender_truncated_body(*a, **k):
    from service.routers.api_v2 import _reject_sender_truncated_body as _impl

    return _impl(*a, **k)


async def _run_contract_reminders_once(*a, **k):
    from service.routers.api_v2 import _run_contract_reminders_once as _impl

    return await _impl(*a, **k)


async def _touch_agent(*a, **k):
    from service.routers.api_v2 import _touch_agent as _impl

    return await _impl(*a, **k)


async def _touch_current_agent_session(*a, **k):
    from service.routers.api_v2 import _touch_current_agent_session as _impl

    return await _impl(*a, **k)


async def _turn_busy_holds_delivery(db, agent_id: str) -> bool:
    """True when the RAW turn_busy flag may still hold delivery back.

    The delivery gates (send-time queue decision + /dispatch/claim) key on the raw
    harness signal on purpose: "explicit queue" means exactly "after this turn", and
    re-deriving that through status or a short window is what made queued sends land
    mid-turn (#236). So this helper does NOT reinterpret the signal — it only applies
    the SAME anti-strand ceiling the status engine already applies to `in_turn`
    (TURN_BUSY_BACKSTOP_SECONDS, see the constant's own note).

    Why a ceiling is required (regression found 2026-07-26): the gates are pure-raw,
    but nothing guarantees turn_busy is ever cleared.
      * The dead-bridge sweeper (_clear_turn_busy_for_dead_bridges) deliberately
        SKIPS turn_bridge_id IN ('', 'user-prompt-submit') — i.e. every hook-driven
        resident-claude turn — and skips any turn whose bridge is still alive.
      * A missed turn-END (killed harness, hook error, or a transcript classifier
        that keeps reading in-flight) therefore latches turn_busy=1 permanently.
    Past the ceiling, status ALREADY stops reporting `working` (derive() clamps
    in_turn in both the push and poll paths). Holding delivery past that point makes
    the two disagree permanently: the dashboard shows an idle agent whose queued work
    can never be claimed. For a target WITHOUT `steer` the claim gate returns early,
    so that agent goes permanently deaf to every dispatch.

    A genuinely long turn is unaffected: the bridge turn detectors KEEP-FRESH re-stamp
    turn-start, so turn_updated_at keeps advancing for as long as real work runs. Only
    an ABANDONED flag ages out — which is exactly the strand this bounds.
    """
    try:
        row = await (await db.execute(
            "SELECT turn_busy, turn_updated_at FROM agent_turn_state WHERE agent_id = ?",
            (agent_id,),
        )).fetchone()
    except Exception:
        # Unreadable turn state must never block delivery — better to deliver.
        return False
    if not row or not int((row["turn_busy"] if "turn_busy" in row.keys() else 0) or 0):
        return False
    seen = _iso_to_epoch(str(row["turn_updated_at"] or ""))
    if not seen:
        # MISSING/UNPARSEABLE timestamp → do NOT hold (fixed 2026-07-26, review follow-up).
        # The first cut returned True here "to trust the raw flag", which quietly reproduced the
        # exact strand this helper exists to prevent: a latched turn_busy=1 whose turn_updated_at
        # is empty or malformed has NOTHING to age against, so it would hold delivery forever and
        # a non-steer target would stay permanently deaf — with no ceiling to rescue it.
        #
        # Releasing is the correct asymmetry. Every writer stamps turn_updated_at via _now()
        # (the /turn-start, /heartbeat and reconcile paths all do), so a blank or unparseable
        # value means a corrupt row, not a live turn. The worst case from releasing is ONE
        # message delivered mid-turn, which the harness queues or the reply reconciles; the worst
        # case from holding is an agent that never receives work again. Prefer the recoverable
        # failure.
        return False
    # A FUTURE timestamp must not hold either (review R4, 2026-07-26). `now - seen` goes NEGATIVE
    # for a clock-skewed or bad write, which trivially satisfies `<= CEILING` — so the flag would
    # hold delivery forever, the exact permanent strand this ceiling exists to bound. Requiring a
    # non-negative age closes it: only an age genuinely inside the window holds.
    age = datetime.now(timezone.utc).timestamp() - seen
    return 0 <= age <= _borrowed_turn_busy_backstop_seconds()


def _wake_agent(*a, **k):
    from service.routers.api_v2 import _wake_agent as _impl

    return _impl(*a, **k)


def _console_dispatch_input_body(req: DispatchRequest, *, recipient_id: str, message_id: str, bracketed_paste: bool = True) -> str:
    subject = str(req.subject or "").strip()
    body = str(req.body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    message = "\n".join(
        part for part in [
            "AIFY dashboard message",
            f"From: {req.from_agent}",
            f"To: {recipient_id}",
            f"Type: {req.type}",
            # Quoted like every other echo — see _quote_untrusted_subject. This one has
            # From/To framing around it, so it is the least dangerous site; one rule
            # beats four judgement calls about how much framing is enough.
            f"Subject: {_quote_untrusted_subject(subject, 240)}" if subject else "",
            f"MessageId: {message_id}",
            "",
            body,
            "",
            "Reply in the dashboard when appropriate, using the available aify-comms tools.",
        ] if part != ""
    )
    if bracketed_paste:
        return f"\x1b[200~{message}\x1b[201~\r"
    return f"{message}\r"


def _dispatch_requires_reply(explicit: Optional[bool], *, default: bool) -> bool:
    if explicit is None:
        return bool(default)
    return bool(explicit)


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
        if current_status in _borrowed_dispatch_terminal_statuses()
        else f"Result reply recorded from {from_agent}"
    )
    await _append_dispatch_event(db, replied_run["id"], "handoff", handoff_note)
    return True


def _message_type_expects_reply(message_type: str) -> bool:
    return (message_type or "").strip().lower() in {"request", "review", "error"}


def _primary_result_message_id(message_id: str, recipients: list[str]) -> str:
    if len(recipients) == 1:
        return message_id
    if not recipients:
        return message_id
    return f"{message_id}-{recipients[0]}"


async def _record_terminal_delivery_contract(
    db,
    *,
    source_message_id: str,
    from_agent: str,
    recipient_id: str,
    message_type: str,
    subject: str,
    body: str,
    priority: str,
    in_reply_to: Optional[str],
    require_reply: bool,
    terminal_id: str,
    control_id: str,
    runtime: str = "",
) -> str:
    run_id = f"run_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    requested_at = _now()
    normalized_runtime = _normalize_runtime(runtime or "")
    existing_active_turn = None
    if normalized_runtime in {"claude-code", "codex", "hermes", "opencode", "pi"}:
        active_cursor = await db.execute(
            """
            SELECT id
            FROM dispatch_runs
            WHERE target_agent = ?
              AND dispatch_mode = 'terminal'
              AND execution_mode = 'managed'
              AND runtime = ?
              AND status IN ('claimed', 'running')
            ORDER BY COALESCE(started_at, claimed_at, requested_at) ASC
            LIMIT 1
            """,
            (recipient_id, normalized_runtime),
        )
        existing_active_turn = await active_cursor.fetchone()
    if existing_active_turn:
        parent_run_id = str(existing_active_turn["id"] or "").strip()
        await _append_dispatch_event(
            db,
            parent_run_id,
            "terminal_delivered",
            f"Additional dashboard input delivered into terminal {terminal_id} with control {control_id}",
        )
        await _append_dispatch_event(
            db,
            parent_run_id,
            "terminal_coalesced",
            f"Coalesced message {source_message_id or 'unknown'} into active terminal-backed turn",
        )
        if source_message_id:
            await db.execute(
                "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                (source_message_id, recipient_id, requested_at),
            )
        await _invalidate_agent_live_state(db, recipient_id)
        return parent_run_id

    tracks_active_turn = normalized_runtime in {"claude-code", "codex", "hermes", "opencode", "pi"}
    status = "running" if tracks_active_turn else "delivered"
    await db.execute(
        """
        INSERT INTO dispatch_runs (
            id, message_id, from_agent, target_agent, dispatch_mode, execution_mode, requested_runtime, runtime,
            message_type, subject, body, priority, in_reply_to, status, require_reply, requested_at, started_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id,
            source_message_id or None,
            from_agent,
            recipient_id,
            "terminal",
            "managed",
            "",
            normalized_runtime,
            message_type,
            subject,
            body,
            priority,
            in_reply_to,
            status,
            1 if require_reply else 0,
            requested_at,
            requested_at if tracks_active_turn else None,
        ),
    )
    await _append_dispatch_event(
        db,
        run_id,
        "terminal_delivered",
        f"Delivered into terminal {terminal_id} with control {control_id}",
    )
    if tracks_active_turn:
        await _append_dispatch_event(
            db,
            run_id,
            "running",
            "Awaiting explicit reply from terminal-backed turn",
        )
    if source_message_id:
        await db.execute(
            "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
            (source_message_id, recipient_id, requested_at),
        )
    await _invalidate_agent_live_state(db, recipient_id)
    return run_id


async def _resolve_recipient_ids(db, *, to: Optional[str], to_role: Optional[str], from_agent: str) -> list[str]:
    recipients: list[str] = []
    if to:
        recipients.append(to)
    if to_role:
        cursor = await db.execute("SELECT id FROM agents WHERE role = ? AND id != ?", (to_role, from_agent))
        recipients.extend([row["id"] for row in await cursor.fetchall()])
    return _dedupe_preserve(recipients)


async def _resolve_reply_parent_message_id(db, reply_id: Optional[str]) -> tuple[Optional[str], bool]:
    candidate = str(reply_id or "").strip()
    if not candidate:
        return None, True

    cursor = await db.execute("SELECT id FROM messages WHERE id = ? LIMIT 1", (candidate,))
    row = await cursor.fetchone()
    if row:
        return candidate, True

    cursor = await db.execute("SELECT message_id FROM dispatch_runs WHERE id = ? LIMIT 1", (candidate,))
    row = await cursor.fetchone()
    resolved = str((row["message_id"] if row else "") or "").strip()
    if resolved:
        return resolved, True

    return None, False
