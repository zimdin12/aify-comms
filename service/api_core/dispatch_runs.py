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
from typing import Optional

from service.api_core.active_run_discard import _discard_unusable_active_run
from service.api_core.active_run_lookup import _find_mergeable_queued_run
from service.api_core.capabilities import _row_capabilities
from service.api_core.claim_gating import _dispatch_message_id_for_recipient
from service.api_core.dispatch_buffer import (
    _DISPATCH_BUFFER_CAP,
    _append_pending_dispatch_body,
    _dispatch_buffer_full_hint,
)
from service.api_core.dispatch_run_state import _append_dispatch_control
from service.api_core.dispatch_state import _get_dispatch_state_for_agent
from service.api_core.dispatch_text import (
    _build_pending_dispatch_subject,
    _neutralise_buffer_markers,
    _pending_dispatch_count,
)
from service.api_core.events import _append_dispatch_event
from service.api_core.records import _status_with_dispatch
from service.api_core.runtime import _normalize_runtime
from service.api_core.serialization import _json_loads_or, _quote_untrusted_subject
from service.api_core.settings import _load_settings
from service.api_core.status_refresh import _compute_agent_status
from service.clock import now as _now
from service.terminal_write_queue import TERMINAL_OUTPUT_WRITES

#: Ordering for merge decisions: a merged run keeps the STRONGER of the two priorities.
_PRIORITY_ORDER = {"low": -1, "normal": 0, "high": 1, "urgent": 2}
#: An unrecognised label is not an escalation, and ranks below every recognised one INCLUDING `low`
#: — otherwise `low` and a typo tie, and the tie-break by argument order reintroduces the asymmetry
#: this constant exists to remove. It used to default to the rank of `normal`, which made
#: `_stronger_priority` non-commutative: with both sides unranked-or-normal the `>=` returned
#: whichever argument was on the LEFT, so `("low", "normal")` answered "low" while `("normal", "low")`
#: answered "normal". The buffer merge calls it as (existing, new), so a run that once carried an
#: unranked priority -- `low`, or a typo like `urgnet` -- kept it through every later `normal` merge
#: and showed the recipient a priority no message in the buffer had.
_UNRANKED_PRIORITY = -2


def _stronger_priority(left: str, right: str) -> str:
    """The higher of two priorities, order-independently.

    Ties keep the LEFT argument, which at the only call site is the buffer's existing priority --
    stability, not preference. Two DIFFERENT unranked labels tie, so the existing one survives; there
    is no basis for ranking one unrecognised string above another.

    Priority drives no routing here (nothing orders by it); it is the `Priority:` line the recipient
    reads and a field in the claim payload. So the cost of getting this wrong is a run labelled with
    an urgency none of its messages carried.
    """
    left_key = str(left or "normal").strip().lower() or "normal"
    right_key = str(right or "normal").strip().lower() or "normal"
    left_rank = _PRIORITY_ORDER.get(left_key, _UNRANKED_PRIORITY)
    right_rank = _PRIORITY_ORDER.get(right_key, _UNRANKED_PRIORITY)
    return left_key if left_rank >= right_rank else right_key




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
    # NORMALISED ONCE, HERE, because this is the only place a caller-supplied mode enters the column.
    # `dispatch.py` passes `req.mode` straight from the request body (`models.py` types it a bare
    # `str` with no validator); every other caller passes a server literal, which this leaves alone.
    #
    # It matters because the readers DISAGREE about whether to normalise. Six sites do
    # `str(row["dispatch_mode"] or "").strip().lower()`; four compared it raw, including
    # `claim_run_selection.py`'s `== "message_only"`, which CANCELS the run — so a mode spelled
    # `Message_Only` was recognised by the delivery path and not by the claim path, and a run the
    # sender asked to deliver as a message only would have started a turn instead.
    dispatch_mode = str(dispatch_mode or "").strip().lower()
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
                # The subject is quoted for the same reason every sibling echo quotes it: it is free
                # text written by ANOTHER agent, and this text is injected between the recipient's
                # tool calls, where a bare imperative line reads as an instruction. This site was the
                # one the rule test could not see, because its `Subject:` is mid-string — reported
                # from another instance 2026-08-17.
                steer_body = (
                    f"[Message from {from_agent}]\n"
                    f"Subject: {_quote_untrusted_subject(subject, 240)}\n\n{body}"
                )
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
                    # Both sides normalised: `dispatch_mode` above, and the STORED value of the run
                    # being merged into, which may predate that normalisation.
                    "require_start" if str(mergeable_run["dispatch_mode"] or "").strip().lower() == "require_start" or dispatch_mode == "require_start" else mergeable_run["dispatch_mode"],
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
        # NEUTRALISE THE SENDER'S BODY AT THE STORAGE BOUNDARY, not only when it is rendered into a
        # merged buffer. Reported by a reviewer on another instance 2026-08-18, and it is the half of
        # `44986616` that was missing: that fix anchored the claim-time `MessageId:` parser AND
        # neutralised bodies at render time, noting the two were "the two halves of the same fix and
        # neither is sufficient alone" — but only the MERGED render path neutralised anything. A fresh
        # single dispatch stored the body verbatim, so a line-leading `MessageId: <victim-id>` was
        # read back at claim time and minted a read receipt for the claiming agent against a message
        # it never saw. Unread is the ABSENCE of a receipt, so that message silently disappeared from
        # the recipient's `comms_listen` — the exact suppression 44986616 set out to close, reachable
        # by another road, and by accident as easily as on purpose (agents quote buffer excerpts).
        #
        # Doing it HERE makes the parser's assumption true instead of hoping for it: no stored
        # dispatch body carries a structural marker unless the service wrote it. The transformation is
        # the one the merged path already applies — brackets substituted, `MessageId:` prefixed off
        # column 0 — so the text stays readable and becomes structurally inert.
        stored_body = _neutralise_buffer_markers(body)
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
                message_type, subject, stored_body, priority, in_reply_to, "queued", 1 if require_reply else 0,
                1 if queue_if_busy else 0, 1 if steer else 0, requested_at
            )
        )
        await _append_dispatch_event(db, run_id, "queued", f"{message_type}: {subject}")
        runs.append({"runId": run_id, "targetAgentId": recipient_id, "status": "queued", "requireReply": require_reply})
    return runs


# _preflight_live_send_recipients moved to service/api_core/send_preflight.py in v0.5.4 —
# it decides whether a run is worth creating, which is a different job from creating one.
