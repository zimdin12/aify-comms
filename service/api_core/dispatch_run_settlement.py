"""What a dispatch run that has just reached a terminal status has to settle.

Extracted from `update_dispatch_run` in `service/routers/dispatch_messages/dispatch.py` in v0.5.4;
`test_update_dispatch_run_split_is_inert.py` inlines the helper back and AST-compares against the
pre-split fixture. The body is at its original 12-space column so the literals inside are preserved
byte-for-byte.

WHY THIS BLOCK IS ITS OWN SUBJECT. Everything above it in the route is field-by-field UPDATE
assembly; this is the part that fires only once, when a run stops being live. It is not one action
but eight, and the order matters: fail the controls nobody will ever handle, RE-READ the row (the
mirrors below read columns the UPDATE just changed), mirror the handoff and the dashboard summary,
close the contracts steered off this run, report an async manager result, clear a phantom turn_busy,
apply a pending takeover, and run the contract reminders.

The re-read is the part worth naming: `refreshed_row` is not a convenience, it is why the mirrors see
the terminal status at all. Reusing `row` here would mirror the run's PREVIOUS state and the failure
would be silent -- a handoff message that says the run is still running.

`_apply_pending_resident_takeover_if_ready` travels with the block because this is its only caller.
"""
from __future__ import annotations

from fastapi import Request

from service.api_core.active_run_discard import _fail_pending_controls_for_run
from service.api_core.dashboard_run_report import (
    _maybe_report_async_manager_result_to_dashboard,
    _mirror_dashboard_run_summary_to_chat,
)
from service.api_core.dispatch_sweeps import (
    _mirror_missing_dispatch_handoff,
    _run_contract_reminders_once,
)
from service.api_core.serialization import _row_require_reply
from service.api_core.turn_state import _clear_turn_busy_if_no_open_reply_owing_run
from service.reconcilers.dispatch_lifecycle import _close_steered_contracts_for_parent_run


async def _apply_pending_resident_takeover_if_ready(db, agent_id: str) -> bool:
    # Manual ownership model: a resident CLI registration must not take over a
    # managed identity at a turn boundary. Operators use /session-mode.
    return False


async def _settle_terminated_dispatch_run(db, run_id: str, effective_status,
                                          now: str, request: Request) -> None:
            """The run reached a terminal status. Settle everything that depended on it being live.

            Called unconditionally and guarded INSIDE, which is what the block looked like before it
            moved -- the extract-method gate splices this body back over its call site verbatim, so
            hoisting the condition to the caller would break the round trip that proves the move
            changed nothing.

            Every argument is passed under the caller's own name for the same reason: inline-back does
            not substitute arguments, so it refuses a call whose argument name differs from the
            parameter it fills.
            """
            if effective_status in ("completed", "failed", "cancelled"):
                await _fail_pending_controls_for_run(
                    db,
                    run_id,
                    handled_at=now,
                    response_text=f'Run ended with status "{effective_status}" before the control could be handled.',
                )
                refreshed_cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
                refreshed_row = await refreshed_cursor.fetchone()
                mirrored_message_id = await _mirror_missing_dispatch_handoff(db, refreshed_row)
                dashboard_message_id = await _mirror_dashboard_run_summary_to_chat(db, refreshed_row)
                result_message_id = str((refreshed_row["result_message_id"] if refreshed_row else "") or mirrored_message_id or dashboard_message_id or "").strip()
                await _close_steered_contracts_for_parent_run(
                    db,
                    refreshed_row,
                    result_message_id=result_message_id,
                )
                await _maybe_report_async_manager_result_to_dashboard(db, refreshed_row)
                if refreshed_row:
                    # Send-deadlock fix (2026-06-02): an rr=0 channel/resident
                    # delivery that the bridge just marked completed is NOT
                    # sustained work — clear the recipient's turn_busy (which the
                    # delivery re-pulse left stamped) so a queued send isn't held
                    # behind a phantom turn for up to 120s. rr=1 runs keep their
                    # turn_busy and clear via _mark_dispatch_run_answered when the
                    # reply lands; the guard ensures we never clear while another
                    # rr=1 turn is still open (anti-feedback-loop invariant).
                    if (
                        effective_status == "completed"
                        and not _row_require_reply(refreshed_row)
                        and str((refreshed_row["execution_mode"] or "")).strip().lower() in {"channel", "resident"}
                    ):
                        await _clear_turn_busy_if_no_open_reply_owing_run(
                            db, refreshed_row["target_agent"], run_id
                        )
                    await _apply_pending_resident_takeover_if_ready(db, refreshed_row["target_agent"])
                    if effective_status == "completed":
                        await _run_contract_reminders_once(
                            db,
                            request=request,
                            target_agent_id=refreshed_row["target_agent"],
                            limit=25,
                            recent_only=True,
                        )
