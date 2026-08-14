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
from service.api_core.dispatch_text import _auto_handoff_body_for_run
from service.api_core.execution_mode import _auto_return_resident_to_managed_if_possible
from service.api_core.spawn_request_state import _has_claimable_spawn_request
from service.api_core.events import _append_dispatch_event, _append_terminal_event
from service.api_core.routing import domain_router
from service.api_core.runtime import _NATIVE_MANAGED_RUNTIMES, _normalize_runtime, _normalize_session_mode
from service.api_core.dispatch_text import (  # v0.5.4 owner; re-exported for this package
    _auto_handoff_subject_for_run,
    _coldstart_refusal_message,
)
from service.api_core.reply_contract import (  # v0.5.4 owner; re-exported for this package
    _message_satisfies_reply_contract,
)
from service.api_core.serialization import (
    _clip_text,
    _dedupe_preserve,
    _iso_from_ms,
    _json_loads_or,
    _quote_untrusted_subject,
    _row_require_reply,
    _timestamp_sort_key,
)
from service.api_core.capabilities import _managed_via_wrapper_for_runtime
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings, _managed_terminal_backing_enabled
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.api_core.liveness import _has_live_managed_wrapper_child
from service.api_core.agent_sessions import (
    _agent_tombstone,
    _touch_agent,
    _touch_current_agent_session,
)
from service.api_core.dispatch_state import _get_dispatch_state_for_agent
from service.api_core.turn_state import _clear_turn_busy_if_no_open_reply_owing_run
from service.api_core.recovery_writes import _record_channel_sidecar_heartbeat
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
from service.api_core.dispatch_run_state import _mark_dispatch_run_answered
from service.reconcilers.dispatch_lifecycle import _close_steered_contracts_for_parent_run
from service.reconcilers.dispatch_queue import _close_reconcilable_delivered_runs
from service.status_engine import VALID_STATUSES
from service.env_status import environment_effective_status as _environment_effective_status
# Imported for the ANNOTATION as much as the call: under postponed evaluation an unresolved
# model name does not fail import, it fails a type-hint gate or a request at runtime.
from service.models import DispatchClaimRequest
from service.api_core.channel_delivery import _CHANNEL_CLAIM_RUNTIMES
from service.api_core.capabilities import _row_capabilities
from service.api_core.dispatch_state import _DISPATCH_TERMINAL_STATUSES

logger = logging.getLogger("aify_comms.routers.dispatch_messages.shared")








# _bridge_claim_block_reason moved to service/api_core/claim_gating.py in v0.5.4.


# _dispatch_conversation_context moved to service/api_core/claim_gating.py in v0.5.4.


# _dispatch_reply_pending moved to service/api_core/reply_contract.py in v0.5.4.




# _has_claimable_steerable_run moved to service/api_core/claim_gating.py in v0.5.4.


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














# _release_stale_console_owner_for_claim moved to service/api_core/claim_gating.py in v0.5.4.
















def _borrowed_unthreaded_handoff_window_ms():
    """BORROWED constant: one owner, never a copy (finding N7)."""

    return _UNTHREADED_HANDOFF_WINDOW_MS








# Was a borrow shim: the owner lived in the control plane, which this module cannot import at
# module level without a cycle. It moved to service/api_core/dispatch_runs.py in v0.5.4.
from service.api_core.dispatch_runs import _create_dispatch_runs  # noqa: E402



from service.api_core.message_store import _delete_messages_where  # noqa: E402









# Was a borrow shim: the owner lived in the control plane, which a router cannot import at
# module level without a cycle. It moved to service/api_core/status_refresh.py in v0.5.4, so
# a plain import works.
from service.api_core.status_refresh import _get_recipient_info  # noqa: E402




# Was a borrow shim: the owner lived in the control plane, which this module cannot import at
# module level without a cycle. It moved to service/api_core/dispatch_sweeps.py in v0.5.4.
from service.api_core.dispatch_sweeps import _mirror_missing_dispatch_handoff  # noqa: E402
from service.api_core.tuning import _UNTHREADED_HANDOFF_WINDOW_MS


# Was a borrow shim: the owner lived in the control plane, which this module cannot import at
# module level without a cycle. It moved to service/api_core/dispatch_runs.py in v0.5.4.
from service.api_core.dispatch_runs import _preflight_live_send_recipients  # noqa: E402



# Was a borrow shim: the owner lived in the control plane, which this module cannot import at
# module level without a cycle. It moved to service/api_core/dispatch_sweeps.py in v0.5.4.
from service.api_core.dispatch_sweeps import _run_contract_reminders_once  # noqa: E402






# _turn_busy_holds_delivery moved to service/api_core/claim_gating.py in v0.5.4.


# Was a borrow shim: the owner lived in the control plane, which a router cannot import at
# module level without a cycle. It moved to service/longpoll.py in v0.5.4 — the module that
# already owned the other waiter registry — so a plain import works.
from service.longpoll import _wake_agent  # noqa: E402


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
        if current_status in _DISPATCH_TERMINAL_STATUSES
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


async def _queue_console_dispatch_inputs(db, req, msg_id, recipients, console_recipients, console_deliveries, resolved_in_reply_to):
        """Queue the terminal `input` control that actually delivers a dispatch to a console session.

        Extracted from `send_message` in v0.5.4; `test_send_message_split_is_inert.py` inlines it back
        and AST-compares against the pre-split fixture, so the round trip is re-proved on every run.

        Body left at its original 8-space column. The same reason as the register_agent extractions:
        re-indenting would have re-indented the contents of the multi-line literals inside it, and the
        gate compares ASTs rather than accepting "the whitespace does not matter".

        THE PER-RECIPIENT MESSAGE ID is the subtle part. A fan-out send gives every recipient its OWN
        id (`{msg_id}-{recipient_id}`) but a single-recipient send reuses `msg_id` unchanged — so the
        common case threads against the id the caller already knows, while a fan-out cannot have two
        recipients replying against one id and collapsing into each other's thread.
        """
        if req.trigger:
            source_message_ids = {
                recipient_id: (f"{msg_id}-{recipient_id}" if len(recipients) > 1 else msg_id)
                for recipient_id in recipients
            }
            for recipient_id, terminal in console_recipients.items():
                terminal_id = str(terminal["terminal_id"] or "").strip()
                recipient_message_id = source_message_ids.get(recipient_id, msg_id)
                terminal_runtime = _normalize_runtime(terminal["runtime"] or "")
                control_id = await _append_terminal_control(
                    db,
                    terminal_id=terminal_id,
                    environment_id=terminal["environment_id"],
                    bridge_id=terminal["bridge_id"] or "",
                    action="input",
                    requested_by=req.from_agent,
                    body=_console_dispatch_input_body(
                        req,
                        recipient_id=recipient_id,
                        message_id=recipient_message_id,
                        bracketed_paste=True,
                    ),
                )
                submit_control_id = ""
                await _append_terminal_event(
                    db,
                    terminal_id,
                    "terminal_input_requested",
                    json.dumps({
                        "requestedBy": req.from_agent,
                        "controlId": control_id,
                        "submitControlId": submit_control_id,
                        "source": "message_send",
                        "messageId": recipient_message_id,
                    }),
                )
                contract_run_id = await _record_terminal_delivery_contract(
                    db,
                    source_message_id=recipient_message_id,
                    from_agent=req.from_agent,
                    recipient_id=recipient_id,
                    message_type=req.type,
                    subject=req.subject,
                    body=req.body,
                    priority=req.priority,
                    in_reply_to=resolved_in_reply_to,
                    require_reply=_dispatch_requires_reply(req.requireReply, default=_message_type_expects_reply(req.type)),
                    terminal_id=terminal_id,
                    control_id=control_id,
                    runtime=terminal["runtime"] or "",
                )
                console_deliveries.append({
                    "targetAgentId": recipient_id,
                    "terminalId": terminal_id,
                    "controlId": control_id,
                    "contractRunId": contract_run_id,
                    "status": "sent_to_console",
                })


# --- threading a reply that arrived without one --------------------------------------------------
#
# Moved here from `messages.py` in v0.5.4, byte-identical. It lands in THIS module rather than an
# api_core leaf because it needs three names declared here — `_borrowed_unthreaded_handoff_window_ms`,
# `_is_replaceable_auto_handoff_message` and `_message_satisfies_reply_contract`. Pushing it down
# would have meant importing those upward, which is the edge this series removes rather than adds.

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
