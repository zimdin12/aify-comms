"""The `messages` route surface: send, inbox, read, search, unsend and compaction.

v0.5.2l, the other half of the dispatch+messages package.

`send_message` is the hottest user-facing path in the product. In v0.5.2l it moved here WHOLE and
byte-identical, and this docstring said it was "not method-split here" — true when written, and no longer
true: v0.5.4 split the 278-line recipient-launch loop out to `api_core/dispatch_start.py`, which is what
brought this file under 1000 lines.

That sentence is corrected rather than deleted because the reasoning behind it still holds and constrains
what may happen next: the route is not to be reshaped, only to have verbatim blocks lifted out under the
inline-back proof in `service/tests/test_send_message_split_is_inert.py`. Its line count is deliberately
not restated here — measure the file.

Local helpers are used by message handlers and nothing else; anything shared with dispatch lives in
`shared.py`.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import HTTPException, Request

from service.api_core.send_preflight import _preflight_live_send_recipients
from service.api_core.reply_expectation import (
    _dispatch_requires_reply,
    _message_type_expects_reply,
)
from service.api_core.reply_threading import _thread_reply_onto_dispatch_runs
from service.api_core.send_refusal import _refuse_send_to_unstartable_recipients
from service.api_core.console_input_queue import _queue_console_dispatch_inputs
from service.api_core.routing import domain_router
from service.api_core.runtime import _normalize_runtime
from service.api_core.settings import _load_settings
from service.api_core.ws import _get_ws
from service.db import get_db
from service.ntfy import notify_operator

# Imported for ANNOTATIONS as well as calls -- see the note in dispatch.py.
from service.models import MessageSend
from service.api_core.dispatch_launch import _launch_recipients_for_dispatch
from service.api_core.dispatch_run_state import _finalize_dispatch_runs
from service.api_core.validation import _reject_sender_truncated_body
from service.api_core.agent_sessions import _touch_agent
from service.api_core.dispatch_runs import _create_dispatch_runs
from service.api_core.status_refresh import _get_recipient_info
from service.longpoll import _wake_agent
from service.reconcilers.dispatch_queue import _close_reconcilable_delivered_runs
from service.routers.dispatch_messages.shared import (
    _primary_result_message_id,
    _resolve_recipient_ids,
    _resolve_reply_parent_message_id,
)
from service.api_core.channel_delivery import (
    _apply_channel_routing_to_claude_runs,
)

logger = logging.getLogger("aify_comms.routers.dispatch_messages.messages")

router = domain_router()







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
                return await _refuse_send_to_unstartable_recipients(db, recipients, not_started, warnings)
            settings = await _load_settings(db)
            channel_backing_failed = set()
            await _launch_recipients_for_dispatch(channel_backing_failed, console_recipients, db, launchable_recipients, not_started, req, settings)
            launchable_recipients = [
                (recipient_id, execution_mode)
                for recipient_id, execution_mode in launchable_recipients
                if recipient_id not in console_recipients and recipient_id not in channel_backing_failed
            ]
            # ASYMMETRY: replies are never hard-rejected — see is_reply note
            # above. Fall through to persist + thread the reply.
            if not_started and not is_reply:
                return await _refuse_send_to_unstartable_recipients(db, recipients, not_started, warnings)

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

        await _thread_reply_onto_dispatch_runs(
            db, req, recipients, msg_id, ts, resolved_in_reply_to, linked_result_message_id,
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
        await _queue_console_dispatch_inputs(
            db, req, msg_id, recipients, console_recipients, console_deliveries, resolved_in_reply_to,
        )

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
