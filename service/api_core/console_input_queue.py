"""Delivering a message into a console session, and recording what that promised.

RELOCATED, not rewritten, in v0.5.4 -- all four functions are byte-identical from
`service/routers/dispatch_messages/shared.py`. With the reply-linking cluster gone earlier in this
release, this was the last of that module's non-route mass: it declares no routes and never did.

A CONSOLE DELIVERY IS A TERMINAL `input` CONTROL. There is no API for "tell this agent something"
when its session is a TUI -- the message is typed into the terminal, and the run that tracks it is
a contract saying an answer is expected back. `_record_terminal_delivery_contract` is what writes
that contract, and it is 107 of these 277 lines because getting it wrong strands the send: the
keystrokes land, nothing tracks them, and the sender waits forever on a run that was never created.

THE TWO QUEUE ENTRY POINTS SHARE ONE BODY since 0.7.0: they were twins, fifty-one of fifty-three
lines identical, pinned by a test that compared them, and the dispatch one now delegates to the other
with its `source`. In `shared.py` they had sat 200 lines apart, and a fix applied to one and not the
other was silent.
"""
from __future__ import annotations

from service.api_core.vocabulary import RUNTIMES_THAT_TRACK_A_TURN

import json
import time
import uuid
from typing import Optional

from service.api_core.events import (
    _append_dispatch_event,
    _append_terminal_control,
    _append_terminal_event,
)
from service.api_core.reply_expectation import (
    _dispatch_requires_reply,
    _message_type_expects_reply,
)
from service.api_core.runtime import _normalize_runtime
from service.api_core.dispatch_text import _neutralise_buffer_markers
from service.api_core.message_view import _describe_sender
from service.api_core.serialization import _quote_untrusted_subject
from service.clock import now as _now
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state

# Imported for the ANNOTATION. Under postponed evaluation a missing model does not fail import --
# it silently demotes a request body to a query parameter, which is why this repo keeps model
# imports even where they look unused.
from service.models import DispatchRequest


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
    if normalized_runtime in RUNTIMES_THAT_TRACK_A_TURN:
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

    tracks_active_turn = normalized_runtime in RUNTIMES_THAT_TRACK_A_TURN
    status = "running" if tracks_active_turn else "delivered"

    # THE SAME STORAGE-BOUNDARY RULE `_create_dispatch_runs` applies, and this writer did not.
    #
    # The invariant the claim-time parser relies on is "no stored dispatch body carries a structural
    # marker unless the service wrote it". That is a property of the COLUMN, so it is only true if
    # every writer of the column holds it. Three writers take a sender's body; one of them
    # neutralised it. This row is inserted with `status='running'` and a non-empty `message_id`, which
    # is precisely the selection `POST /contracts/hygiene/repair-read-receipts` iterates — so a body
    # opening with the buffer header would have had its forged `MessageId:` lines read back and turned
    # into read receipts for this agent against messages it never saw.
    stored_body = _neutralise_buffer_markers(body)
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
            stored_body,
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


def _console_dispatch_input_body(req: DispatchRequest, *, recipient_id: str, message_id: str, bracketed_paste: bool = True,
                                 sender: str = "") -> str:
    subject = str(req.subject or "").strip()
    body = str(req.body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    message = "\n".join(
        part for part in [
            "AIFY dashboard message",
            f"From: {sender or req.from_agent}",
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


async def _queue_console_dispatch_inputs(db, req, msg_id, recipients, console_recipients, console_deliveries, resolved_in_reply_to):
    """Queue the terminal `input` controls that deliver a SENT message to its console recipients.

    THE PER-RECIPIENT MESSAGE ID is the subtle part. A fan-out send gives every recipient its OWN
    id (`{msg_id}-{recipient_id}`) but a single-recipient send reuses `msg_id` unchanged — so the
    common case threads against the id the caller already knows, while a fan-out cannot have two
    recipients replying against one id and collapsing into each other's thread.
    """
    if not req.trigger:
        return
    source_message_ids = {
        recipient_id: (f"{msg_id}-{recipient_id}" if len(recipients) > 1 else msg_id)
        for recipient_id in recipients
    }
    await _queue_console_inputs_for_dispatch(
        db, req, msg_id, console_recipients, console_deliveries, source_message_ids,
        resolved_in_reply_to, source="message_send",
    )


async def _queue_console_inputs_for_dispatch(db, req, message_id, console_recipients, console_deliveries,
                                             source_message_ids, resolved_in_reply_to, *, source="dispatch"):
    """Queue the terminal `input` control that delivers a message or dispatch to a console session.

    `source` is recorded on the `terminal_input_requested` event and says which path produced the
    delivery: "dispatch" for create_dispatch, "message_send" for send_message (through
    `_queue_console_dispatch_inputs` above).
    """
    sender = await _describe_sender(db, req.from_agent, getattr(req, "origin", ""),
                               machine=getattr(req, "_external_machine", ""))
    for recipient_id, terminal in console_recipients.items():
        terminal_id = str(terminal["terminal_id"] or "").strip()
        recipient_message_id = source_message_ids.get(recipient_id, message_id)
        # NO `terminal_runtime`. It normalised the recipient's runtime here and dropped
        # it: `_append_terminal_control` takes terminal_id, environment_id, bridge_id,
        # action, requested_by, body, cols and rows -- no runtime. Passing one is a
        # behaviour change to what a control carries, not a cleanup, so the dead line
        # goes and the question stays open.
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
                sender=sender,
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
                "source": source,
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
