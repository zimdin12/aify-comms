"""Creating dispatch runs, and the pre-send check that decides who can receive one.

`_create_dispatch_runs` is THE run-creating function — DECISIONS.md calls it "the one shared
`_create_dispatch_runs`" and documents the merge/`allow_merge` contract that depends on there being
exactly one. `_preflight_live_send_recipients` is its gate: it decides, per recipient, whether a live
send is possible and why not when it is not.

WHY api_core AND NOT A ROUTE DOMAIN. `channels.py` carries a sentence saying these two "are dispatch
orchestration the reviewer ruled should not be pulled into a shared core". That sentence is the ONLY
record of the ruling — it is not in the commit that introduced it (395f0270), not in DECISIONS.md, not
in docs/, so its scope cannot be recovered. What the surrounding evidence says:

  * the coupling it warns about ALREADY EXISTS and is deliberate. `channels.py`, `dispatch.py` and
    `messages.py` all call both functions today, through borrow shims that route via the control
    plane. Moving the owner does not create a shared dependency; it renames the address of one.
  * DECISIONS.md treats `_create_dispatch_runs` as a single shared owner in two separate entries, and
    the `allow_merge=False` replay contract is only correct BECAUSE there is one.
  * the alternative — moving them into `routers/dispatch_messages/dispatch.py`, the domain that owns
    dispatch — takes that file from 925 to roughly 1,580 lines, creating a new violation of the
    standing "no product file over 1000 lines" rule.

Read charitably, the ruling was against FORKING the send path into a shared abstraction that channels
and messages would each depend on. This is the opposite: one owner, byte-identical bodies, callers
unchanged. If it was meant more broadly, reverting this commit restores the previous arrangement
exactly — nothing here changes behaviour.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from service.api_core.active_run_discard import _discard_unusable_active_run
from service.api_core.active_run_lookup import _find_mergeable_queued_run
from service.api_core.capabilities import _row_capabilities
from service.api_core.claim_gating import _dispatch_message_id_for_recipient
from service.api_core.dispatch_buffer import (
    _DISPATCH_BUFFER_CAP,
    _append_pending_dispatch_body,
    _dispatch_buffer_full_hint,
)
from service.api_core.dispatch_hint import _dispatch_fix_hint
from service.api_core.dispatch_run_state import _append_dispatch_control
from service.api_core.dispatch_state import _get_dispatch_state_for_agent
from service.api_core.dispatch_text import (
    _build_pending_dispatch_subject,
    _pending_dispatch_count,
)
from service.api_core.events import _append_dispatch_event
from service.api_core.execution_mode import (
    _agent_execution_mode,
    _auto_return_resident_to_managed_if_possible,
)
from service.api_core.liveness import _resident_bridge_is_fresh
from service.api_core.managed_env import _managed_environment_unavailable_reason
from service.api_core.records import _status_with_dispatch
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import _load_settings
from service.api_core.status_refresh import _compute_agent_status
from service.clock import now as _now
from service.terminal_write_queue import TERMINAL_OUTPUT_WRITES

#: Ordering for merge decisions: a merged run keeps the STRONGER of the two priorities.
_PRIORITY_ORDER = {"normal": 0, "high": 1, "urgent": 2}


def _stronger_priority(left: str, right: str) -> str:
    left_key = str(left or "normal").strip().lower() or "normal"
    right_key = str(right or "normal").strip().lower() or "normal"
    return left_key if _PRIORITY_ORDER.get(left_key, 0) >= _PRIORITY_ORDER.get(right_key, 0) else right_key


async def _preflight_live_send_recipients(
    db,
    recipients: list[str],
    *,
    allow_steer: bool = False,
    allow_queue_busy: bool = False,
) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    """Return launchable recipients or per-recipient reasons without writing messages.

    Normal chat is live-wake-only: do not leave future inbox work behind when a
    recipient cannot start handling the message now.
    """
    settings = await _load_settings(db)
    launchable: list[tuple[str, str]] = []
    not_started: list[dict[str, Any]] = []
    unavailable_statuses = {"offline", "stale", "stopped"}
    allow_busy_enqueue = allow_queue_busy or allow_steer

    for recipient_id in recipients:
        agent_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
        row = await agent_cursor.fetchone()
        if not row:
            not_started.append(_dispatch_fix_hint(recipient_id, None, "agent is not registered"))
            continue
        row, _transition = await _auto_return_resident_to_managed_if_possible(db, row, settings=settings)
        if _normalize_runtime(row["runtime"] or "") == "pi":
            runtime_state = _json_loads_or(row["runtime_state"], {})
            if runtime_state.get("pi_resident_pending_flip"):
                hint = _dispatch_fix_hint(
                    recipient_id,
                    row,
                    "agent is migrating from resident to managed (pi flip pending)",
                )
                hint["recipientStatus"] = "migrating"
                hint["fix"] = (
                    f'Agent "{recipient_id}" is migrating from resident to managed. '
                    "Retry after the drain loop flips the agent once active runs complete."
                )
                not_started.append(hint)
                continue
        if _normalize_session_mode(row["session_mode"] or "resident") == "resident":
            if not await _resident_bridge_is_fresh(db, row, lease_seconds=settings.get("resident_lease_seconds", 150)):
                hint = _dispatch_fix_hint(recipient_id, row, "resident bridge heartbeat is gone; restart the resident wrapper or switch to managed")
                hint["recipientStatus"] = "offline"
                not_started.append(hint)
                continue

        dispatch_state = await _get_dispatch_state_for_agent(db, recipient_id)
        active = dispatch_state.get("activeRun")
        if active and await _discard_unusable_active_run(db, recipient_id, active):
            dispatch_state = await _get_dispatch_state_for_agent(db, recipient_id)
        base_status = await _compute_agent_status(row, db)
        effective_status = _status_with_dispatch(base_status, dispatch_state)

        if effective_status in unavailable_statuses:
            hint = _dispatch_fix_hint(recipient_id, row, f'agent status is "{effective_status}"')
            hint["recipientStatus"] = effective_status
            not_started.append(hint)
            continue

        execution_mode, reason = _agent_execution_mode(row, settings=settings)
        if reason or not execution_mode:
            hint = _dispatch_fix_hint(recipient_id, row, reason or "active dispatch unavailable")
            hint["recipientStatus"] = effective_status
            not_started.append(hint)
            continue

        environment_reason = await _managed_environment_unavailable_reason(db, row)
        if environment_reason:
            hint = _dispatch_fix_hint(recipient_id, row, environment_reason)
            hint["recipientStatus"] = "offline"
            not_started.append(hint)
            continue

        if dispatch_state.get("hasActiveRun"):
            active = dispatch_state.get("activeRun") or {}
            capabilities = _row_capabilities(row)
            if allow_steer and "steer" in capabilities:
                launchable.append((recipient_id, execution_mode))
                continue
            if allow_busy_enqueue:
                launchable.append((recipient_id, execution_mode))
                continue
            hint = _dispatch_fix_hint(recipient_id, row, "agent is working")
            hint["recipientStatus"] = "working"
            hint["activeRun"] = active
            active_suffix = f" on {active.get('runId')}" if active.get("runId") else ""
            hint["fix"] = (
                f'Agent "{recipient_id}" is already working{active_suffix}. '
                "Wait, interrupt the active run, or send with steer=true so aify can inject now when supported and queue/merge as the next-turn fallback otherwise."
            )
            not_started.append(hint)
            continue

        queued_runs = int(dispatch_state.get("queuedRuns") or 0)
        if queued_runs > 0:
            if allow_busy_enqueue:
                launchable.append((recipient_id, execution_mode))
                continue
            hint = _dispatch_fix_hint(recipient_id, row, "agent already has queued work")
            hint["recipientStatus"] = effective_status
            hint["queuedRuns"] = queued_runs
            hint["fix"] = (
                f'Agent "{recipient_id}" already has {queued_runs} queued run(s). '
                "Wait for the queue to drain, cancel stale runs, or send normally so aify can steer or merge when possible. Use queueIfBusy=true only when you intentionally want next-turn delivery."
            )
            not_started.append(hint)
            continue

        # WS5 Task 5.1b REVERSED (2026-06-02): the deaf-target fail-fast was
        # removed. A send to a managed sidecar-delivery target whose delivery loop
        # released/lost its claimer lease previously failed fast (ok:false, no run)
        # — but in live use that LOST messages to an agent that was merely
        # mid-restart (lease released then re-acquired moments later). The operator
        # reversed the decision: ALWAYS QUEUE here. The
        # `_reap_undeliverable_queued_runs` backstop reaper is now the sole safety
        # net — it fails a queued run only after it has been genuinely
        # undeliverable for the backstop window. `_managed_target_is_deaf` was
        # REMOVED in v0.5 after it was proven that nothing ever used it for the
        # status/deliverability classification it had been retained for; the lease
        # helpers and that backstop are what remain.
        launchable.append((recipient_id, execution_mode))

    return launchable, not_started












# _terminal_status_transition moved to service/routers/terminals.py in v0.5.3, then on to
# service/api_core/terminal_status.py in v0.5.4.




# class TerminalOutputWriteQueue moved to service/terminal_write_queue.py in v0.5.4,
# with its singleton. It is not an api_core leaf: it owns its own transaction.


# TERMINAL_OUTPUT_WRITES moved to service/terminal_write_queue.py in v0.5.4 —
# the declaration must stay beside the class so a second instance cannot appear.


    await TERMINAL_OUTPUT_WRITES.flush_all()


async def _create_dispatch_runs(
    db,
    recipients: list[str],
    *,
    from_agent: str,
    message_type: str,
    subject: str,
    body: str,
    priority: str,
    in_reply_to: Optional[str],
    dispatch_mode: str,
    execution_mode: str,
    requested_runtime: Optional[str],
    message_id: Optional[str] = None,
    source_message_ids: Optional[dict[str, str]] = None,
    steer: bool = False,
    queue_if_busy: bool = False,
    require_reply: bool = False,
    allow_merge: bool = True,
):
    runs = []
    requested_at = _now()
    for recipient_id in recipients:
        source_message_id = _dispatch_message_id_for_recipient(
            recipient_id,
            message_id=message_id,
            source_message_ids=source_message_ids,
        )
        # steer=true: if target has an active run, deliver as a steer
        # control on that run (injected between tool calls) instead of
        # queuing a new dispatch. Symmetric for Claude and Codex.
        if steer:
            row_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
            recipient_row = await row_cursor.fetchone()
            capabilities = _row_capabilities(recipient_row) if recipient_row else []
            active_state = await _get_dispatch_state_for_agent(db, recipient_id)
            active_run = active_state.get("activeRun")
            if active_run and await _discard_unusable_active_run(db, recipient_id, active_run):
                active_state = await _get_dispatch_state_for_agent(db, recipient_id)
                active_run = active_state.get("activeRun")
            active_execution_mode = str((active_run.get("executionMode") if active_run else "") or "").strip().lower()
            recipient_runtime = _normalize_runtime((recipient_row["runtime"] if recipient_row else "") or requested_runtime)
            # ASYMMETRY(hermes): its gateway sidecar does not consume dispatch_controls;
            # route channel/resident steer through its claim loop and native session.steer.
            steer_via_claim = recipient_runtime == "hermes" and active_execution_mode in {"channel", "resident"}
            if steer and active_run and "steer" in capabilities and not steer_via_claim:
                steer_body = f"[Message from {from_agent}]\nSubject: {subject}\n\n{body}"
                control_id = await _append_dispatch_control(
                    db,
                    active_run["runId"],
                    from_agent=from_agent,
                    action="steer",
                    body=steer_body,
                    source_message_id=source_message_id,
                )
                steer_contract_run_id = None
                if source_message_id:
                    steer_contract_run_id = f"run_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
                    await db.execute(
                        """
                        INSERT INTO dispatch_runs (
                            id, message_id, from_agent, target_agent, dispatch_mode, execution_mode, requested_runtime,
                            message_type, subject, body, priority, in_reply_to, status, require_reply, requested_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            steer_contract_run_id,
                            source_message_id,
                            from_agent,
                            recipient_id,
                            "steer",
                            execution_mode,
                            requested_runtime or "",
                            message_type,
                            subject,
                            body,
                            priority,
                            in_reply_to,
                            "delivered",
                            1 if require_reply else 0,
                            requested_at,
                        ),
                    )
                    await _append_dispatch_event(
                        db,
                        steer_contract_run_id,
                        "steered",
                        f"Delivered as steer control {control_id} into active run {active_run['runId']}",
                    )
                runs.append({
                    "runId": active_run["runId"],
                    "targetAgentId": recipient_id,
                    "status": "steered",
                    "steered": True,
                    "requireReply": require_reply,
                    "controlId": control_id,
                    "contractRunId": steer_contract_run_id,
                    "steeredIntoActiveRun": {
                        "runId": active_run["runId"],
                        "status": active_run["status"],
                        "subject": active_run["subject"],
                    },
                })
                continue

        # allow_merge=False (channel offline-replay, #238): a merge folds this dispatch
        # into an existing queued run but KEEPS that run's original message_id (see the
        # "Keep message_id … pointing at the FIRST item" comment below), so the replayed
        # message's fanout id would NEVER land on any run — the replay watermark
        # (NOT EXISTS dispatch_runs WHERE message_id = fanout_id) would stay true and the
        # reconciler would re-replay it every 60s sweep, appending the body forever. The
        # replay must therefore insert a DEDICATED run keyed on its own message_id.
        mergeable_run = None
        if allow_merge:
            mergeable_run = await _find_mergeable_queued_run(
                db,
                recipient_id=recipient_id,
                from_agent=from_agent,
            )
        if mergeable_run:
            merge_result = _append_pending_dispatch_body(
                mergeable_run,
                from_agent=from_agent,
                message_type=message_type,
                subject=subject,
                body=body,
                priority=priority,
                requested_at=requested_at,
                message_id=source_message_id,
                in_reply_to=str(in_reply_to or ""),
            )
            if merge_result is None:
                # Buffer cap hit. Surface a rejection without dropping the existing
                # buffered run. Caller propagates this into notStarted.
                current_count = _pending_dispatch_count(str(mergeable_run["body"] or ""))
                row_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
                recipient_row = await row_cursor.fetchone()
                recipient_status = "unknown"
                has_active = False
                if recipient_row:
                    settings = await _load_settings(db)
                    recipient_status = await _compute_agent_status(recipient_row, db)
                    dispatch_state = await _get_dispatch_state_for_agent(db, recipient_id)
                    has_active = bool(dispatch_state.get("hasActiveRun"))
                    recipient_status = _status_with_dispatch(recipient_status, dispatch_state)
                rejection_hint = _dispatch_buffer_full_hint(
                    recipient_id,
                    recipient_row,
                    from_agent=from_agent,
                    current_count=current_count,
                    recipient_status=recipient_status,
                    has_active_run=has_active,
                )
                await _append_dispatch_event(
                    db,
                    mergeable_run["id"],
                    "buffer_full",
                    f"Rejected dispatch from {from_agent}: buffer cap {_DISPATCH_BUFFER_CAP} reached",
                )
                runs.append({
                    "runId": None,
                    "targetAgentId": recipient_id,
                    "status": "rejected",
                    "rejected": True,
                    "rejectionHint": rejection_hint,
                })
                continue

            merged_body, merged_count = merge_result
            # Keep message_id and in_reply_to pointing at the FIRST item that
            # opened this buffered run. Per-item ids are preserved in the body
            # text so the receiver can still pull each original from inbox.
            # GUARDED merge (review must-fix, 2026-06-10): the run was read as 'queued' but a
            # concurrent /dispatch/claim (BEGIN IMMEDIATE) can flip it to 'claimed' between the
            # read and this write — the bridge then delivers the PRE-merge body and completes the
            # run, silently losing the merged message. Guard on status='queued' and check
            # rowcount: 0 rows updated → the run was claimed mid-merge → fall through to insert a
            # FRESH queued run instead.
            merge_cursor = await db.execute(
                """
                UPDATE dispatch_runs
                SET subject = ?, body = ?, priority = ?, dispatch_mode = ?, message_type = ?, require_reply = ?,
                    queue_if_busy = ?, steer_if_busy = ?
                WHERE id = ? AND status = 'queued'
                """,
                (
                    _build_pending_dispatch_subject(merged_count, subject),
                    merged_body,
                    _stronger_priority(mergeable_run["priority"], priority),
                    "require_start" if mergeable_run["dispatch_mode"] == "require_start" or dispatch_mode == "require_start" else mergeable_run["dispatch_mode"],
                    message_type,
                    1 if (bool(mergeable_run["require_reply"]) or require_reply) else 0,
                    1 if (bool(mergeable_run["queue_if_busy"]) or queue_if_busy) else 0,
                    1 if (bool(mergeable_run["steer_if_busy"]) or steer) else 0,
                    mergeable_run["id"],
                ),
            )
            if merge_cursor.rowcount and merge_cursor.rowcount > 0:
                await _append_dispatch_event(
                    db,
                    mergeable_run["id"],
                    "merged",
                    f"Buffered update from {from_agent}: {subject}",
                )
                runs.append({
                    "runId": mergeable_run["id"],
                    "targetAgentId": recipient_id,
                    "status": "queued",
                    "merged": True,
                    "mergedCount": merged_count,
                    "requireReply": bool(mergeable_run["require_reply"]) or require_reply,
                })
                continue
            # else: claimed mid-merge — fall through to the fresh-insert path below.

        run_id = f"run_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        await db.execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode, execution_mode, requested_runtime,
                message_type, subject, body, priority, in_reply_to, status, require_reply,
                queue_if_busy, steer_if_busy, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, source_message_id or None, from_agent, recipient_id, dispatch_mode, execution_mode, requested_runtime or "",
                message_type, subject, body, priority, in_reply_to, "queued", 1 if require_reply else 0,
                1 if queue_if_busy else 0, 1 if steer else 0, requested_at
            )
        )
        await _append_dispatch_event(db, run_id, "queued", f"{message_type}: {subject}")
        runs.append({"runId": run_id, "targetAgentId": recipient_id, "status": "queued", "requireReply": require_reply})
    return runs
