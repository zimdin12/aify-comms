"""The `messages` route surface: send, inbox, read, search, unsend and compaction.

v0.5.2l, the other half of the dispatch+messages package.

`send_message` (614 lines) is the hottest user-facing path in the product and moves WHOLE,
byte-identical. It is not method-split here.

Local helpers are used by message handlers and nothing else; anything shared with dispatch lives in
`shared.py`.
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
from service.api_core.spawn_request_state import _has_claimable_spawn_request
from service.api_core.events import _append_dispatch_event, _append_terminal_event
from service.api_core.routing import domain_router
from service.api_core.runtime import _NATIVE_MANAGED_RUNTIMES, _normalize_runtime, _normalize_session_mode
from service.api_core.dispatch_text import COLDSTART_REFUSED_PREFIX
from service.api_core.serialization import (
    _clip_text,
    _dedupe_preserve,
    _iso_from_ms,
    _json_loads_or,
    _quote_untrusted_subject,
    _row_require_reply,
    _timestamp_sort_key,
)
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings, _managed_terminal_backing_enabled
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.clock import iso_to_epoch as _iso_to_epoch
from service.clock import now as _now
from service.db import SQLITE_CLAIM_BUSY_TIMEOUT_MS, get_db
from service.ntfy import notify_operator
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
from service.status_engine import apply_event

# Imported for ANNOTATIONS as well as calls -- see the note in dispatch.py.
from service.models import ConversationClearRequest, MessageSend
from service.api_core.agent_sessions import (
    _agent_tombstone,
    _touch_current_agent_session,
)
from service.api_core.dispatch_state import _get_dispatch_state_for_agent
from service.api_core.recovery_writes import _record_channel_sidecar_heartbeat
from service.api_core.serialization import _machine_ids_same_host
from service.routers.dispatch_messages.shared import (
    VALID_STATUSES,
    _append_dispatch_control,
    _append_terminal_control,
    _auto_handoff_subject_for_run,
    _borrowed_unthreaded_handoff_window_ms,
    _clear_turn_busy_if_no_open_reply_owing_run,
    _close_reconcilable_delivered_runs,
    _close_steered_contracts_for_parent_run,
    _coldstart_refusal_message,
    _console_dispatch_input_body,
    _create_dispatch_runs,
    _delete_messages_by_ids,
    _delete_messages_where,
    _dispatch_requires_reply,
    _finalize_dispatch_runs,
    _get_blocking_active_run,
    _get_recipient_info,
    _has_live_managed_wrapper_child,
    _is_replaceable_auto_handoff_message,
    _link_reply_message_to_dispatch_run,
    _managed_environment_unavailable_reason,
    _managed_via_wrapper_for_runtime,
    _mark_dispatch_run_answered,
    _message_satisfies_reply_contract,
    _message_type_expects_reply,
    _mirror_missing_dispatch_handoff,
    _preflight_live_send_recipients,
    _primary_result_message_id,
    _record_terminal_delivery_contract,
    _reject_sender_truncated_body,
    _resolve_recipient_ids,
    _resolve_reply_parent_message_id,
    _run_contract_reminders_once,
    _touch_agent,
    _wake_agent,
)
from service.api_core.channel_delivery import _CHANNEL_MANAGED_RUNTIMES
from service.api_core.channel_delivery import (
    _apply_channel_routing_to_claude_runs,
    _insert_messages_via_console,
)
from service.api_core.terminal_ownership import _active_terminal_for_agent
from service.api_core.dispatch_start import (
    _coldstart_spawn_request_for_dispatch,
    _ensure_managed_pty_for_dispatch,
)
from service.api_core.spawn_request_state import _has_claimable_spawn_request
from service.api_core.active_run_discard import _fail_pending_controls_for_run
from service.api_core.execution_mode import _agent_execution_mode, _auto_return_resident_to_managed_if_possible
from service.api_core.reply_contract import (
    _dispatch_reply_pending,
    _dispatch_reply_state,
)
from service.api_core.dispatch_text import _pending_dispatch_count
from service.api_core.dispatch_state import _is_delivery_only_claude_run
from service.api_core.claim_gating import (
    _bridge_claim_block_reason,
    _dispatch_conversation_context,
    _has_claimable_steerable_run,
    _release_stale_console_owner_for_claim,
    _turn_busy_holds_delivery,
)
from service.api_core.claim_gating import _mark_dispatch_source_messages_read
from service.api_core.agent_sessions import _adopt_live_resident_driver
from service.api_core.dispatch_hint import _dispatch_fix_hint

logger = logging.getLogger("aify_comms.routers.dispatch_messages.messages")

router = domain_router()


async def _cancel_queued_dispatch_runs_for_message_ids(db, message_ids: list[str], *, chunk_size: int = 250) -> list[str]:
    pending = _dedupe_preserve([str(message_id or "").strip() for message_id in message_ids if str(message_id or "").strip()])
    if not pending:
        return []

    cancelled_ids = []
    finished_at = _now()
    summary = "Cancelled because source message was unsent."
    for start in range(0, len(pending), chunk_size):
        chunk = pending[start:start + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        cursor = await db.execute(
            f"SELECT id FROM dispatch_runs WHERE status = 'queued' AND message_id IN ({placeholders})",
            chunk,
        )
        run_ids = [str(row["id"]) for row in await cursor.fetchall()]
        if not run_ids:
            continue
        run_placeholders = ",".join("?" for _ in run_ids)
        await db.execute(
            f"UPDATE dispatch_runs SET status = 'cancelled', summary = ?, finished_at = ? WHERE id IN ({run_placeholders})",
            (summary, finished_at, *run_ids),
        )
        for run_id in run_ids:
            await _append_dispatch_event(db, run_id, "cancelled", summary)
        cancelled_ids.extend(run_ids)
    return cancelled_ids


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


@router.post("/messages/cleanup/orphan-unread")
async def cleanup_orphan_unread_messages(request: Request):
    """Delete unread inbox messages addressed to removed agents."""
    db = await get_db()
    try:
        deleted = await _delete_messages_where(
            db,
            """
            id IN (
                SELECT m.id
                FROM messages m
                LEFT JOIN agents a ON a.id = m.to_agent
                LEFT JOIN read_receipts r ON r.message_id = m.id AND r.agent_id = m.to_agent
                WHERE m.to_agent IS NOT NULL AND a.id IS NULL AND r.message_id IS NULL
            )
            """,
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws and deleted:
            await ws.broadcast("messages_cleaned", {"kind": "orphan_unread", "deleted": deleted})
        return {"ok": True, "deleted": deleted}
    finally:
        await db.close()


@router.post("/messages/conversation/clear")
async def clear_direct_conversation(req: ConversationClearRequest, request: Request):
    agent_id = str(req.agentId or "").strip()
    peer_id = str(req.peerId or "").strip()
    if not agent_id or not peer_id:
        raise HTTPException(400, "Need agentId and peerId")
    validate_name(agent_id, "agent ID")
    validate_name(peer_id, "peer agent ID")

    db = await get_db()
    try:
        deleted = await _delete_messages_where(
            db,
            """
            source = 'direct'
            AND channel IS NULL
            AND (
                (from_agent = ? AND to_agent = ?)
                OR (from_agent = ? AND to_agent = ?)
            )
            """,
            (agent_id, peer_id, peer_id, agent_id),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("conversation_cleared", {"agentId": agent_id, "peerId": peer_id, "deleted": deleted})
        return {"ok": True, "agentId": agent_id, "peerId": peer_id, "deleted": deleted}
    finally:
        await db.close()


@router.get("/messages/inbox/{agent_id}")
async def get_inbox(
    agent_id: str, request: Request,
    filter: str = Query("unread", pattern="^(unread|read|all)$"),
    fromAgent: Optional[str] = None, fromRole: Optional[str] = None,
    type: Optional[str] = None, limit: int = Query(200, ge=1, le=1000),
    mode: str = Query("full", pattern="^(full|headers)$"),
    messageId: Optional[str] = None,
    peek: Optional[str] = None,
):
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        include_body = mode != "headers"
        if messageId:
            base = """SELECT m.*, r.read_at FROM messages m
                      LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                      WHERE m.to_agent = ? AND m.id = ?"""
            params = [agent_id, agent_id, messageId]
        else:
            # Build query
            if filter == "unread":
                base = """SELECT m.*, NULL as read_at FROM messages m
                          LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                          WHERE m.to_agent = ? AND r.message_id IS NULL"""
                params = [agent_id, agent_id]
            elif filter == "read":
                base = """SELECT m.*, r.read_at FROM messages m
                          JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                          WHERE m.to_agent = ?"""
                params = [agent_id, agent_id]
            else:
                base = """SELECT m.*, r.read_at FROM messages m
                          LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                          WHERE m.to_agent = ?"""
                params = [agent_id, agent_id]

        if fromAgent:
            base += " AND m.from_agent = ?"
            params.append(fromAgent)
        if fromRole:
            base += " AND m.from_agent IN (SELECT id FROM agents WHERE role = ?)"
            params.append(fromRole)
        if type:
            base += " AND m.type = ?"
            params.append(type)

        base += " ORDER BY m.timestamp DESC LIMIT ?"
        params.append(1 if messageId else limit)

        cursor = await db.execute(base, params)
        rows = await cursor.fetchall()

        # Count total (without limit)
        count_q = base.replace("SELECT m.*, NULL as read_at", "SELECT COUNT(*)").replace("SELECT m.*, r.read_at", "SELECT COUNT(*)")
        count_q = count_q[:count_q.rfind("LIMIT")]
        c = await db.execute(count_q, params[:-1])
        total = (await c.fetchone())[0]

        messages = []
        for row in rows:
            msg = _serialize_inbox_message(row, include_body=include_body)
            # Include parent message context for replies
            if row["in_reply_to"]:
                pc = await db.execute("SELECT from_agent, subject, body FROM messages WHERE id = ?", (row["in_reply_to"],))
                parent = await pc.fetchone()
                if parent:
                    msg["parentContext"] = {"from": parent["from_agent"], "subject": parent["subject"], "preview": (parent["body"] or "")[:100]}
            messages.append(msg)

        # Mark as read + update status (unless peek)
        if not peek:
            now = _now()
            unread_found = 0
            for msg in messages:
                if not msg["read"]:
                    unread_found += 1
                    await db.execute(
                        "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                        (msg["id"], agent_id, now)
                    )
            # Complete stuck dispatch runs linked to messages we just read.
            # Only claimed/running (stuck from dead bridges) — NOT queued.
            # Queued dispatches should be left for the bridge to claim and
            # execute as a turn. Completing them here would prevent the wake.
            if unread_found > 0:
                read_msg_ids = [msg["id"] for msg in messages if not msg["read"]]
                for msg_id in read_msg_ids:
                    await db.execute(
                        """
                        UPDATE dispatch_runs
                        SET status = 'completed', summary = 'Message read via inbox', finished_at = ?
                        WHERE message_id = ? AND target_agent = ? AND status IN ('claimed', 'running')
                        """,
                        (now, msg_id, agent_id),
                    )

            # Smart status: got messages = working, no messages = idle
            new_status = "working" if unread_found > 0 else "idle"
            await db.execute(
                "UPDATE agents SET last_seen = ?, status = CASE WHEN status = 'stopped' THEN status ELSE ? END WHERE id = ?",
                (now, new_status, agent_id)
            )
            await db.commit()

        return {"total": total, "showing": len(messages), "messages": messages}
    finally:
        await db.close()


@router.get("/messages/recent")
async def recent_messages(
    request: Request,
    limit: int = Query(80, ge=1, le=250),
):
    """Recent human-scale message activity without channel fanout duplicates."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT m.*, rr.read_at AS read_at
            FROM messages m
            LEFT JOIN read_receipts rr ON rr.message_id = m.id AND rr.agent_id = m.to_agent
            WHERE
              (m.source = 'direct' AND m.to_agent IS NOT NULL)
              OR (m.source = 'channel' AND m.to_agent IS NULL)
            ORDER BY m.timestamp DESC
            LIMIT ?
            """,
            (limit,),
        )
        messages = []
        for row in await cursor.fetchall():
            messages.append({
                "id": row["id"],
                "from": row["from_agent"],
                "to": row["to_agent"],
                "channel": row["channel"],
                "source": row["source"],
                "type": row["type"],
                "subject": row["subject"],
                # Full body so the dashboard chat renders complete messages — the bubble
                # reads `m.body` and previously fell back to the 240-char `preview`, so
                # EVERY message was truncated to 240 chars in the conversation view
                # (operator-reported 2026-07-10). `preview` is kept for the light DM-rail
                # one-liner; `body` carries the real content.
                "body": row["body"] or "",
                "preview": _clip_text(row["body"] or "", 240),
                "priority": row["priority"],
                "timestamp": row["timestamp"],
                "inReplyTo": row["in_reply_to"],
                "dispatchRequested": bool(row["dispatch_requested"]) if "dispatch_requested" in row.keys() else False,
                # Recipient-perspective read state (rr.agent_id = to_agent) so the dashboard's
                # unread badges work; channel rows (to_agent NULL) have no receipt → read=False.
                "read": ("read_at" in row.keys()) and (row["read_at"] is not None),
                "readAt": row["read_at"] if "read_at" in row.keys() else None,
            })
        return {"ok": True, "messages": messages, "total": len(messages)}
    finally:
        await db.close()


@router.get("/messages/search")
async def search_messages(
    request: Request, query: str = "",
    agentId: Optional[str] = None,
    scope: str = Query("all", pattern="^(inbox|shared|all)$"),
    limit: int = Query(10, ge=1, le=100),
):
    db = await get_db()
    try:
        q = f"%{query.lower()}%"
        results = []
        # What was ACTUALLY consulted. Returned to the caller because an empty result from this
        # endpoint was being read as "no such message exists" when messages had never been
        # searched at all — see below. A search that cannot say what it searched cannot support an
        # absence claim, and this one was being used to license work on exactly that basis.
        searched: list[str] = []
        skipped: list[str] = []

        if scope in ("inbox", "all"):
            if agentId:
                # BOTH DIRECTIONS. This was `to_agent = ?` only, so an agent could not find
                # messages it had SENT. Reported 2026-08-10 by sc-manager, who searched for a term
                # it had dispatched itself and got nothing: of 101 messages containing "P0-Q", 49
                # were TO it (findable) and 52 were FROM it (invisible). "My own record" plainly
                # includes what I said, not just what I was told.
                cursor = await db.execute(
                    "SELECT * FROM messages WHERE (to_agent = ? OR from_agent = ?) "
                    "AND (LOWER(subject) LIKE ? OR LOWER(body) LIKE ? OR LOWER(from_agent) LIKE ?) "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (agentId, agentId, q, q, q, limit)
                )
                for row in await cursor.fetchall():
                    results.append({
                        "type": "message", "id": row["id"], "from": row["from_agent"],
                        "to": row["to_agent"], "subject": row["subject"],
                        "preview": (row["body"] or "")[:150],
                    })
                searched.append("messages")
            else:
                # NO agentId MEANS MESSAGES WERE NEVER SEARCHED, and the old response gave no sign
                # of it — it just returned artifact hits, or nothing. That silence is what makes
                # this dangerous rather than merely limited: a caller using this to check "was
                # this already ruled?" reads the empty result as "no", and proceeds. It FAILS
                # OPEN. Naming the omission is the fix; the access model is unchanged.
                skipped.append("messages (no agentId supplied — messages were NOT searched)")

        if scope in ("shared", "all"):
            cursor = await db.execute(
                "SELECT * FROM shared_artifacts WHERE LOWER(name) LIKE ? OR LOWER(description) LIKE ? LIMIT ?",
                (q, q, limit)
            )
            for row in await cursor.fetchall():
                results.append({
                    "type": "shared", "name": row["name"], "from": row["from_agent"],
                    "description": row["description"], "size": row["size"],
                })
            searched.append("shared")

        return {
            "results": results[:limit],
            "total": len(results),
            "searched": searched,
            "skipped": skipped,
        }
    finally:
        await db.close()


@router.post("/messages/send")
async def send_message(req: MessageSend, request: Request):
    if not req.to and not req.toRole:
        raise HTTPException(400, "Need 'to' or 'toRole'")
    _reject_sender_truncated_body(req.body)
    db = await get_db()
    try:
        await _touch_agent(db, req.from_agent)
        # NOTE: do NOT clear turn_busy here based on the agent sending a
        # message. The agent might send a reply and then keep working
        # (more tool calls, more analysis, more messages) — clearing on
        # response would flip status to "active" while real work is
        # still happening. Turn-end is a harness-level signal: each
        # runtime delivers its own (codex turn/completed, pi agent_end,
        # hermes process exit, opencode SDK turn-complete). Resident
        # claude under claude-channel.js needs its Stop hook to call
        # the bridge — see install.sh's claude wrapper installation.
        # Idempotency (#240): a bridge send that hit a transient socket error may have
        # actually landed server-side. /messages/send is otherwise non-idempotent (a fresh
        # msg_id per call), so a retry would DOUBLE-send — which is why the bridge excluded
        # it from its retry list and instead DROPPED the send, stranding owed replies. With
        # an optional clientNonce, a retry of the same logical send collapses to the original
        # message: look it up by (from_agent, client_nonce) and short-circuit with the SAME
        # messageId so the bridge can retry safely. Scoped per sender; absent nonce = today's
        # behavior (old bridges omit it, so no dedup — fully backward compatible).
        client_nonce = str(req.clientNonce or "").strip()
        if client_nonce:
            prior = await (await db.execute(
                "SELECT id FROM messages WHERE from_agent = ? AND client_nonce = ? ORDER BY timestamp ASC LIMIT 1",
                (req.from_agent, client_nonce),
            )).fetchone()
            if prior is not None:
                return {
                    "ok": True,
                    "messageId": prior["id"],
                    "replayed": True,
                    "recipients": [],
                    "recipientStatus": {},
                    "dispatchRuns": [],
                    "notStarted": [],
                    "consoleDeliveries": [],
                    "warnings": [],
                }
        msg_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        ts = int(time.time() * 1000)
        resolved_in_reply_to, reply_parent_found = await _resolve_reply_parent_message_id(db, req.inReplyTo)
        warnings = []
        if req.inReplyTo and not reply_parent_found:
            warnings.append(
                f'inReplyTo "{req.inReplyTo}" did not match an existing message; message was sent unthreaded.'
            )

        # ASYMMETRY: replies bypass the live-wake hard-gate by design.
        # A reply must ALWAYS be persisted + threaded (and close its
        # require_reply run) even when the recipient can't be live-woken —
        # the recipient simply sees it in their inbox. Hard-rejecting a
        # reply because the recipient's bridge is stale dropped legitimate
        # replies (broke managed-hermes self-reply when the original
        # sender's resident bridge was stale) and left the require_reply
        # run open forever. The live-wake hard-gate below stays in force
        # only for NEW dispatches (requests/etc.), never for replies.
        # A reply is identified by a resolved inReplyTo OR type=="response".
        is_reply = bool(resolved_in_reply_to) or str(req.type or "").strip().lower() == "response"

        recipients = await _resolve_recipient_ids(db, to=req.to, to_role=req.toRole, from_agent=req.from_agent)

        if not recipients:
            return {"ok": False, "error": "No recipients found", "recipients": []}

        launchable_recipients = []
        not_started = []
        console_recipients = {}
        dispatch_recipients = [r for r in recipients if r != "dashboard"]
        if req.trigger:
            prefer_steer = (req.steer is not False) and not bool(req.queueIfBusy)
            allow_queue_busy = bool(req.queueIfBusy) or prefer_steer or str(req.type or "").strip().lower() == "response"
            launchable_recipients, not_started = await _preflight_live_send_recipients(
                db,
                dispatch_recipients,
                allow_steer=prefer_steer,
                allow_queue_busy=allow_queue_busy,
            )
            # ASYMMETRY: do NOT hard-reject a reply here. Replies fall through
            # to persist + thread regardless of recipient live-startability
            # (see is_reply note above). Only NEW dispatches hard-gate.
            if not_started and not is_reply:
                recipient_info = {}
                for r in recipients:
                    info = await _get_recipient_info(db, r)
                    if info:
                        recipient_info[r] = {
                            "status": info["status"],
                            "unread": info["unread"],
                            "runtime": info["runtime"],
                            "machineId": info["machineId"],
                        }
                await db.commit()
                return {
                    "ok": False,
                    "error": "Message was not sent because one or more recipients cannot start live work now.",
                    "recipients": recipients,
                    "recipientStatus": recipient_info,
                    "dispatchRuns": [],
                    "notStarted": not_started,
                    "consoleDeliveries": [],
                    "warnings": warnings,
                }
            settings = await _load_settings(db)
            channel_backing_failed = set()
            for recipient_id, _execution_mode in launchable_recipients:
                row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))).fetchone()
                if row:
                    row, _transition = await _auto_return_resident_to_managed_if_possible(db, row, settings=settings)
                if not row:
                    continue
                runtime = _normalize_runtime(row["runtime"] or "generic")
                # Queue only waits behind real active/queued work. For an idle
                # terminal-backed target, it should still use the normal live
                # delivery path instead of creating an orphan dispatch queue.
                if bool(req.queueIfBusy):
                    dispatch_state = await _get_dispatch_state_for_agent(db, recipient_id)
                    # Three signals of "currently busy":
                    # 1. hasActiveRun: tracked dispatch_run in claimed/running
                    # 2. queuedRuns > 0: prior queue already pending
                    # 3. raw turn_busy=1: the agent is mid-turn even if
                    #    no tracked dispatch_run is in flight. Operator-
                    #    reported 2026-05-22: queue button sent immediately
                    #    because require_reply=0 info messages auto-complete
                    #    their dispatch_run on delivery → hasActiveRun goes
                    #    false → queue fires the next message immediately
                    #    while the assistant is still working. turn_busy
                    #    is the harness-level signal that survives the
                    #    auto-completion.
                    # Raw signal, bounded ONLY by the anti-strand ceiling that also bounds the
                    # claim gate — otherwise an abandoned turn_busy=1 makes every later send to
                    # this agent queue behind a turn that already ended (and the claim gate then
                    # never releases it). See _turn_busy_holds_delivery.
                    try:
                        is_turn_busy = await _turn_busy_holds_delivery(db, recipient_id)
                    except Exception:
                        is_turn_busy = False
                    if (
                        dispatch_state.get("hasActiveRun")
                        or int(dispatch_state.get("queuedRuns") or 0) > 0
                        or is_turn_busy
                    ):
                        continue
                execution_mode = str(_execution_mode or "").strip().lower()
                # Native-managed runtimes (codex/pi/opencode/hermes) — only
                # route through PTY-input when the operator opted into
                # the legacy via-console delivery mode AND managed-
                # terminal-backing is enabled. Default
                # (insert_messages_via_console=false) falls through and
                # the dispatch is claimed by the runtime's native RPC
                # adapter (createCodexController, createPiController,
                # opencode SDK) on its /dispatch/claim poll.
                if runtime in _NATIVE_MANAGED_RUNTIMES:
                    # Wrapper-backed managed (operator-stated 2026-05-25): if
                    # the runtime is in managed_via_wrapper, the wrapper PTY
                    # MUST exist to claim — auto-spawn here so an available
                    # agent gets its console started on first message arrival
                    # (mirror of the operator's "send → console auto-starts
                    # → status flips" model).
                    if (
                        execution_mode == "channel"
                        and _managed_terminal_backing_enabled(settings)
                        and _managed_via_wrapper_for_runtime(settings, runtime)
                    ):
                        console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                        # FIX SET B2 (2026-06-03): for a wrapper-backed runtime a
                        # leftover RESIDENT-mode terminal_session must NOT short-
                        # circuit the managed coldstart. _active_terminal_for_agent /
                        # _ensure_managed_pty_for_dispatch would re-attach a PTY to
                        # that stale resident row (a resident `--resume`, NOT a
                        # managed-warm worker), so no `managed-wrapper-child` bridge
                        # ever registers and the 'channel' run is rejected
                        # `managed_wrapper_child_required` → queued forever (the
                        # lc-coder strand). Only a LIVE managed-wrapper-child proves a
                        # managed worker is actually backing this agent; absent it,
                        # drop the leftover terminal so the coldstart branch below
                        # fires and a managed-warm worker is spawned.
                        if console_terminal and not await _has_live_managed_wrapper_child(db, recipient_id):
                            console_terminal = None
                        if not console_terminal:
                            console_terminal = await _ensure_managed_pty_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                            )
                            # The PTY re-attach above can still resolve a leftover
                            # resident row; re-gate on the live wrapper-child so a
                            # non-managed terminal never suppresses the coldstart.
                            if console_terminal and not await _has_live_managed_wrapper_child(db, recipient_id):
                                console_terminal = None
                        if not console_terminal:
                            # Phase 2 lazy-autostart: no live wrapper PTY to
                            # back this agent (it was only registered, never
                            # run — the operator's `available` sc-coder case).
                            # Instead of rejecting, cold-start a spawn_request
                            # (auto-binding an online env when none is bound)
                            # so a bridge spawns the wrapper and claims this
                            # dispatch on its next poll. Only reject when no
                            # online environment can host the runtime.
                            # N8: collect WHY so a refusal reports its real cause, not the
                            # environment sentence that fired for all five of them.
                            _cs_reasons: list[str] = []
                            coldstarted = await _coldstart_spawn_request_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                                warnings=_cs_reasons,
                            )
                            if not coldstarted and not await _has_claimable_spawn_request(db, recipient_id):
                                not_started.append(
                                    _dispatch_fix_hint(
                                        recipient_id,
                                        row,
                                        _coldstart_refusal_message(_cs_reasons, runtime),
                                    )
                                )
                                channel_backing_failed.add(recipient_id)
                        # Do NOT add to console_recipients (that's the legacy
                        # PTY-input delivery path). Wrapper child bridge claims
                        # via /dispatch/claim once its in-process MCP boots.
                        # Just let the dispatch sit queued; it'll get picked up
                        # within a polling cycle (3s) once the wrapper is up.
                        continue
                    if (
                        execution_mode == "managed"
                        and _managed_terminal_backing_enabled(settings)
                        and _insert_messages_via_console(settings)
                        and runtime not in {"pi", "opencode"}
                    ):
                        console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                        if not console_terminal:
                            console_terminal = await _ensure_managed_pty_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                            )
                        if console_terminal:
                            console_recipients[recipient_id] = console_terminal
                            continue
                    continue
                # Managed Claude PTY-input branch — only fires when the
                # operator has opted into the legacy via-console delivery
                # mode (insert_messages_via_console=true). Default-false
                # routing flows through the channel branch below: the run
                # is left launchable with execution_mode='channel' (see
                # _apply_channel_routing_to_claude_runs after
                # _create_dispatch_runs) so claude-channel.js inside the
                # wrapper-hosted claude-aify claims it and emits the
                # message as a channel wake-up event.
                if (
                    runtime in _CHANNEL_MANAGED_RUNTIMES
                    and _execution_mode == "channel"
                    and _insert_messages_via_console(settings)
                ):
                    console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                    if not console_terminal:
                        console_terminal = await _ensure_managed_pty_for_dispatch(
                            db,
                            recipient_id,
                            runtime=runtime,
                            settings=settings,
                            requested_by=req.from_agent,
                        )
                    if console_terminal:
                        console_recipients[recipient_id] = console_terminal
                    else:
                        not_started.append(
                            _dispatch_fix_hint(
                                recipient_id,
                                row,
                                "Claude claude-aify backing PTY is unavailable; restart the environment bridge or recover the session.",
                            )
                        )
                        channel_backing_failed.add(recipient_id)
                    continue
                if runtime in _CHANNEL_MANAGED_RUNTIMES:
                    # Channel-mode managed Claude (insert_messages_via_console=false)
                    # needs a wrapper PTY running so claude-aify's
                    # claude-channel.js child actually polls
                    # /dispatch/claim for this agent and picks up the
                    # channel-routed dispatch. Without it, the run sits
                    # queued forever (originally observed in
                    # run_1779309370301). We don't inject input — the
                    # PTY is the host for the subscriber, not the
                    # delivery channel. Existing terminal is reused
                    # (slice-3 reuse semantics); only spawned if absent.
                    if (
                        not _insert_messages_via_console(settings)
                        and _managed_terminal_backing_enabled(settings)
                        and _execution_mode == "channel"
                    ):
                        existing = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                        # B2 parity (2026-06-12): a leftover non-managed terminal row must not
                        # suppress the cold start — only a LIVE managed-wrapper-child proves a
                        # worker actually backs this agent (same strand class as lc-coder).
                        if existing and not await _has_live_managed_wrapper_child(db, recipient_id):
                            existing = None
                        if not existing:
                            started = None
                            try:
                                started = await _ensure_managed_pty_for_dispatch(
                                    db,
                                    recipient_id,
                                    runtime=runtime,
                                    settings=settings,
                                    requested_by=req.from_agent,
                                )
                            except Exception:
                                started = None
                            if not started:
                                # ROOT-CAUSE-G PARITY (2026-06-12, graph-tech-lead strand):
                                # _ensure_managed_pty_for_dispatch returns None when the agent
                                # has no usable session row to launch into — exactly the state
                                # after an env-bridge restart retires every session. The native
                                # runtimes fall back to a cold-start spawn_request here; managed
                                # claude never did, so the channel run sat queued with a claimer
                                # that could never exist until the 180s backstop FAILED it.
                                coldstarted = False
                                # N8: declared OUTSIDE the try so a reason recorded before an
                                # exception is still reportable.
                                _cs_reasons_b: list[str] = []
                                try:
                                    coldstarted = await _coldstart_spawn_request_for_dispatch(
                                        db,
                                        recipient_id,
                                        runtime=runtime,
                                        settings=settings,
                                        requested_by=req.from_agent,
                                        warnings=_cs_reasons_b,
                                    )
                                except Exception as _cs_err:
                                    coldstarted = False
                                    _cs_reasons_b.append(
                                        f"{COLDSTART_REFUSED_PREFIX}cold-start raised: {_cs_err}")
                                if not coldstarted and not await _has_claimable_spawn_request(db, recipient_id):
                                    not_started.append(
                                        _dispatch_fix_hint(
                                            recipient_id,
                                            row,
                                            _coldstart_refusal_message(_cs_reasons_b, runtime),
                                        )
                                    )
                                    channel_backing_failed.add(recipient_id)
                    # Final safety (2026-07-04): a channel-managed claude dispatch must
                    # never strand until the 180s queued-run backstop. If — after the
                    # terminal reuse / PTY-ensure above — there is STILL no live
                    # managed-wrapper-child to run claude-channel.js AND no claimable
                    # spawn request, cold-start one now so a bridge spawns the wrapper
                    # and claims this run on its next poll (the aicm-lc-manager
                    # 'queued, never spawned' strand). Idempotent: a live claimer or a
                    # pending spawn short-circuits it, so no duplicate workers.
                    if recipient_id not in channel_backing_failed and (
                        not await _has_live_managed_wrapper_child(db, recipient_id)
                        and not await _has_claimable_spawn_request(db, recipient_id)
                    ):
                        try:
                            await _coldstart_spawn_request_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                            )
                        except Exception:
                            pass
                    continue
                console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                if not console_terminal:
                    console_terminal = await _ensure_managed_pty_for_dispatch(
                        db,
                        recipient_id,
                        runtime=runtime,
                        settings=settings,
                        requested_by=req.from_agent,
                    )
                if console_terminal:
                    console_recipients[recipient_id] = console_terminal
            launchable_recipients = [
                (recipient_id, execution_mode)
                for recipient_id, execution_mode in launchable_recipients
                if recipient_id not in console_recipients and recipient_id not in channel_backing_failed
            ]
            # ASYMMETRY: replies are never hard-rejected — see is_reply note
            # above. Fall through to persist + thread the reply.
            if not_started and not is_reply:
                recipient_info = {}
                for r in recipients:
                    info = await _get_recipient_info(db, r)
                    if info:
                        recipient_info[r] = {
                            "status": info["status"],
                            "unread": info["unread"],
                            "runtime": info["runtime"],
                            "machineId": info["machineId"],
                        }
                await db.commit()
                return {
                    "ok": False,
                    "error": "Message was not sent because one or more recipients cannot start live work now.",
                    "recipients": recipients,
                    "recipientStatus": recipient_info,
                    "dispatchRuns": [],
                    "notStarted": not_started,
                    "consoleDeliveries": [],
                    "warnings": warnings,
                }

        linked_result_message_id = _primary_result_message_id(msg_id, recipients)

        inserted_rows = 0
        for r in recipients:
            recipient_message_id = f"{msg_id}-{r}" if len(recipients) > 1 else msg_id
            dispatch_requested = 1 if req.trigger and r != "dashboard" else 0
            # INSERT OR IGNORE is the ATOMIC half of idempotency (#240): the upfront SELECT
            # is only a fast path and races under concurrent retries; the partial UNIQUE
            # index on (from_agent, client_nonce, to_agent) rejects a duplicate here, and
            # rowcount tells us whether THIS request actually wrote the row. (Empty nonce =
            # not in the index, so nonce-less sends always insert, exactly as before.)
            cursor = await db.execute(
                "INSERT OR IGNORE INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, in_reply_to, client_nonce, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (recipient_message_id,
                 req.from_agent, r, "direct", req.type, req.subject, req.body, req.priority, dispatch_requested, resolved_in_reply_to, client_nonce, ts)
            )
            inserted_rows += cursor.rowcount or 0

        # Lost the concurrent race (#240): a nonce was supplied but every row was ignored as
        # a duplicate → another (racing) request already committed this exact send. Return
        # its ORIGINAL messageId with ok:true and create NO dispatch runs (the winner made
        # them), so a retry that overlapped the first in-flight request never double-sends.
        if client_nonce and inserted_rows == 0:
            prior = await (await db.execute(
                "SELECT id FROM messages WHERE from_agent = ? AND client_nonce = ? ORDER BY timestamp ASC LIMIT 1",
                (req.from_agent, client_nonce),
            )).fetchone()
            return {
                "ok": True,
                "messageId": prior["id"] if prior is not None else msg_id,
                "replayed": True,
                "recipients": [],
                "recipientStatus": {},
                "dispatchRuns": [],
                "notStarted": [],
                "consoleDeliveries": [],
                "warnings": [],
            }

        if resolved_in_reply_to:
            await _link_reply_message_to_dispatch_run(
                db,
                from_agent=req.from_agent,
                resolved_in_reply_to=resolved_in_reply_to,
                reply_message_id=linked_result_message_id,
                reply_type=req.type,
                reply_body=req.body,
            )
        else:
            for r in recipients:
                recipient_message_id = f"{msg_id}-{r}" if len(recipients) > 1 else msg_id
                await _link_unthreaded_reply_to_recent_dispatch_run(
                    db,
                    from_agent=req.from_agent,
                    to_agent=r,
                    reply_message_id=recipient_message_id,
                    reply_type=req.type,
                    reply_subject=req.subject,
                    reply_body=req.body,
                    reply_timestamp_ms=ts,
                )

        dispatch_runs = []
        if req.trigger:
            require_reply = _dispatch_requires_reply(req.requireReply, default=_message_type_expects_reply(req.type))
            source_message_ids = {
                recipient_id: (f"{msg_id}-{recipient_id}" if len(recipients) > 1 else msg_id)
                for recipient_id in recipients
            }
            dispatch_runs = await _create_dispatch_runs(
                db,
                [recipient_id for recipient_id, _ in launchable_recipients],
                from_agent=req.from_agent,
                message_type=req.type,
                subject=req.subject,
                body=req.body,
                priority=req.priority,
                in_reply_to=resolved_in_reply_to,
                dispatch_mode="start_if_possible",
                execution_mode="managed",
                requested_runtime=None,
                message_id=msg_id if len(recipients) == 1 else None,
                source_message_ids=source_message_ids,
                steer=prefer_steer,
                queue_if_busy=bool(req.queueIfBusy),
                require_reply=require_reply,
            )
            dispatch_runs = await _finalize_dispatch_runs(db, dispatch_runs, launchable_recipients, not_started)
            await _apply_channel_routing_to_claude_runs(db, dispatch_runs, settings)

        console_deliveries = []
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

        # Gather recipient status info for sender context
        recipient_info = {}
        for r in recipients:
            info = await _get_recipient_info(db, r)
            if info:
                recipient_info[r] = {
                    "status": info["status"],
                    "unread": info["unread"],
                    "runtime": info["runtime"],
                    "machineId": info["machineId"],
                }

        await db.commit()
        # v0.4 C1 — AFTER commit, so a phone can never buzz for a message that rolled back, and the
        # enqueue can never roll one back. Deliberately OUTSIDE the `if ws:` below: the entire point
        # of the mobile alert is to reach the operator when no dashboard is connected, so gating it
        # on a live websocket would silence it exactly when it is the only channel left.
        # Sync, non-raising, network-free — see service/ntfy.py.
        notify_operator(
            "message_sent",
            {"id": msg_id, "from": req.from_agent, "to": recipients, "subject": req.subject},
        )
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("message_sent", {"id": msg_id, "from": req.from_agent, "to": recipients, "subject": req.subject})
            for r in recipients:
                await ws.notify_agent(r, "new_message", {"from": req.from_agent, "subject": req.subject})
            for run in dispatch_runs:
                if run.get("steered"):
                    continue
                await ws.broadcast("dispatch_queued", {"runId": run["runId"], "targetAgentId": run["targetAgentId"]})
            for delivery in console_deliveries:
                await ws.broadcast("terminal_control_requested", {"terminalId": delivery["terminalId"], "action": "input"})
        # Wake up any listening agents
        for r in recipients:
            _wake_agent(r)
        return {
            "ok": True,
            "messageId": msg_id,
            "recipients": recipients,
            "recipientStatus": recipient_info,
            "dispatchRuns": dispatch_runs,
            "notStarted": not_started,
            "consoleDeliveries": console_deliveries,
            "warnings": warnings,
        }
    finally:
        await db.close()


@router.post("/messages/{message_id}/read")
async def set_message_read_state(message_id: str, request: Request):
    body = await request.json()
    agent_id = str(body.get("agentId") or "").strip()
    read = bool(body.get("read", True))
    if not agent_id:
        raise HTTPException(400, "Need agentId")
    validate_name(agent_id, "agent ID")

    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, to_agent FROM messages WHERE id = ?", (message_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Message '{message_id}' not found")
        if row["to_agent"] != agent_id:
            raise HTTPException(403, f'Message "{message_id}" is not addressed to "{agent_id}"')

        if read:
            await db.execute(
                "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                (message_id, agent_id, _now()),
            )
        else:
            await db.execute(
                "DELETE FROM read_receipts WHERE message_id = ? AND agent_id = ?",
                (message_id, agent_id),
            )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("message_read_state", {"id": message_id, "agentId": agent_id, "read": read})
        return {"ok": True, "id": message_id, "agentId": agent_id, "read": read}
    finally:
        await db.close()


@router.delete("/messages/{message_id}")
async def unsend_message(message_id: str, request: Request):
    """Delete a message by ID. Also removes associated read receipts."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Message '{message_id}' not found")
        message_ids = [message_id]
        if (row["source"] or "") == "channel" and not (row["to_agent"] or ""):
            fanout_cursor = await db.execute(
                "SELECT id FROM messages WHERE id LIKE ? AND channel = ? AND source = 'channel'",
                (f"{message_id}-%", row["channel"] or ""),
            )
            message_ids.extend([fanout["id"] for fanout in await fanout_cursor.fetchall()])
        cancelled_dispatch_run_ids = await _cancel_queued_dispatch_runs_for_message_ids(db, message_ids)
        deleted = await _delete_messages_by_ids(db, message_ids)
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("message_deleted", {"id": message_id, "deleted": deleted})
            for run_id in cancelled_dispatch_run_ids:
                await ws.broadcast("dispatch_updated", {"runId": run_id, "status": "cancelled"})
        return {
            "ok": True,
            "id": message_id,
            "deleted": deleted,
            "cancelledDispatchRuns": len(cancelled_dispatch_run_ids),
            "cancelledDispatchRunIds": cancelled_dispatch_run_ids,
        }
    finally:
        await db.close()
