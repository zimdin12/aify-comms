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

import json
import logging
import time
import uuid
from typing import Optional


from service.api_core.reply_expectation import (
    _dispatch_requires_reply,
    _message_type_expects_reply,
)
from service.api_core.events import _append_dispatch_event, _append_terminal_event
from service.api_core.runtime import _normalize_runtime
from service.api_core.serialization import (
    _dedupe_preserve,
    _quote_untrusted_subject,
)
from service.api_core.agent_sessions import (
    _touch_agent,
)
from service.clock import now as _now
from service.db import get_db
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state

# Resolved to their REAL owners, asked of the repo rather than guessed:
from service.api_core.events import _append_terminal_control
from service.reconcilers.dispatch_queue import _close_reconcilable_delivered_runs
from service.status_engine import VALID_STATUSES
# Imported for the ANNOTATION as much as the call: under postponed evaluation an unresolved
# model name does not fail import, it fails a type-hint gate or a request at runtime.
from service.models import DispatchClaimRequest

logger = logging.getLogger("aify_comms.routers.dispatch_messages.shared")








# _bridge_claim_block_reason moved to service/api_core/claim_gating.py in v0.5.4.


# _dispatch_conversation_context moved to service/api_core/claim_gating.py in v0.5.4.


# _dispatch_reply_pending moved to service/api_core/reply_contract.py in v0.5.4.




# _has_claimable_steerable_run moved to service/api_core/claim_gating.py in v0.5.4.


# _is_replaceable_auto_handoff_message moved to service/api_core/reply_linking.py in v0.5.4.














# _release_stale_console_owner_for_claim moved to service/api_core/claim_gating.py in v0.5.4.
















# _borrowed_unthreaded_handoff_window_ms moved to service/api_core/reply_linking.py in v0.5.4.








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


# Was a borrow shim: the owner lived in the control plane, which this module cannot import at
# module level without a cycle. It moved to service/api_core/dispatch_runs.py in v0.5.4.
from service.api_core.dispatch_runs import _preflight_live_send_recipients  # noqa: E402



# Was a borrow shim: the owner lived in the control plane, which this module cannot import at
# module level without a cycle. It moved to service/api_core/dispatch_sweeps.py in v0.5.4.






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


# _dispatch_requires_reply moved to service/api_core/reply_expectation.py in v0.5.4.


# _link_reply_message_to_dispatch_run moved to service/api_core/reply_linking.py in v0.5.4.


# _message_type_expects_reply moved to service/api_core/reply_expectation.py in v0.5.4.


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
# It arrived here from `messages.py` in v0.5.4 and left again in the same release, for
# `service/api_core/reply_linking.py`. The reason it stopped here on the way is worth keeping: it
# needed three names DECLARED in this module, and pushing it down would have meant importing them
# upward. What changed is that all three went with it — two were used by nothing else, and
# `_message_satisfies_reply_contract` already had an api_core owner. The obstacle was the cluster
# being split across layers, not the function's depth.

# _link_unthreaded_reply_to_recent_dispatch_run moved to service/api_core/reply_linking.py in v0.5.4.


async def _queue_console_inputs_for_dispatch(db, req, message_id, console_recipients, console_deliveries,
                                             source_message_ids, resolved_in_reply_to):
        """Queue the terminal `input` control that delivers a DISPATCH to a console session.

        Extracted from `create_dispatch` in v0.5.4; `test_create_dispatch_split_is_inert.py` inlines it
        back and AST-compares against the pre-split fixture. Body at its original 8-space column so the
        literals inside are preserved byte-for-byte.

        IT IS A NEAR-TWIN OF `_queue_console_dispatch_inputs` ABOVE, and that is recorded rather than
        merged. Fifty-one of the fifty-three lines are identical; the two that are not are:

            source_message_ids.get(recipient_id, msg_id)   vs   (..., message_id)   — a rename
            "source": "message_send"                       vs   "source": "dispatch" — a VALUE

        The second is real: the delivery contract records which path produced it, and collapsing the two
        would either lose that or need it threaded through as a parameter. That is a behaviour-shaped
        change, not a byte-identical move, so it is not being smuggled into a refactor slice.

        `test_console_input_queueing_twins_agree.py` pins the pair: the two bodies must stay identical
        MODULO exactly those two substitutions, so a fix applied to one and not the other fails.
        """
        for recipient_id, terminal in console_recipients.items():
            terminal_id = str(terminal["terminal_id"] or "").strip()
            recipient_message_id = source_message_ids.get(recipient_id, message_id)
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
                    "source": "dispatch",
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
